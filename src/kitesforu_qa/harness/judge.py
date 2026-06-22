"""Judge layer — LLM-judged checks for the deeper quality the deterministic checks can't see.

Founder: an explainer can be technically complete (deterministic checks all pass) yet still lack
INSIGHT — "handwavy, more banter than substance, not the real details behind the explanation."
That's a judgment call no keyword check can make. A JUDGE check builds a prompt (the question + the
relevant artifact excerpt); a LOCAL agent scores it. Here that agent is the kqa ``LLMEvaluator``
(local Gemini, ~$0.001/script — the dev subscription, NOT product cloud API per the founder).

A judge check fn returns a ``JudgePrompt`` (question + the artifact text to judge, or None to skip).
``run_judges`` sends it to the evaluator → {passed, score, reason} → a ``CheckResult``. Judge checks
are ``kind='judge'`` so the $0 deterministic battery skips them; run them separately (they cost a
fraction of a cent each and are reserved for the qualitative dimensions).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .check import _REGISTRY, Check, CheckResult


@dataclass
class JudgePrompt:
    question: str          # the single thing to assess
    excerpt: str           # the artifact text to judge
    rubric: str = ""       # optional scoring guidance


_JUDGE_INSTRUCTION = (
    "You are a STRICT quality judge for generated audio explainer/podcast content. Assess ONLY the "
    "specific question. Respond with JSON: "
    '{"passed": <bool>, "score": <float 0-1>, "reason": "<one sentence>"}. '
    "Be critical: default to passed=false if the content is generic, handwavy, surface-level, or "
    "more pleasantries than substance."
)


def judge_check(
    id: str,
    dimension: str,
    *,
    genre: str | None = None,
    severity: str = "medium",
    description: str = "",
):
    """Register a JUDGE check. The fn(art) returns a JudgePrompt (or None to skip)."""
    def deco(fn: Callable[[Any], Any]):
        _REGISTRY.append(
            Check(id, dimension, fn, genre=genre, severity=severity, kind="judge",
                  description=description or (fn.__doc__ or "").strip())
        )
        return fn
    return deco


def run_judges(art: Any, checks: list[Check], evaluator: Any = None) -> list[CheckResult]:
    """Run judge checks via a LOCAL evaluator (default: kqa LLMEvaluator / local Gemini).

    If the evaluator can't be constructed (no API key in a clean env), judge checks SKIP rather than
    fail — the deterministic battery is the gate; judges are an enrichment.
    """
    if evaluator is None:
        try:
            from ..integrations.llm import LLMEvaluator
            evaluator = LLMEvaluator()
        except Exception as e:  # noqa: BLE001
            return [
                CheckResult(c.id, True, 1.0, c.severity, c.dimension, c.genre,
                            f"judge evaluator unavailable: {e}", skipped=True)
                for c in checks if c.kind == "judge"
            ]
    results: list[CheckResult] = []
    for c in checks:
        if c.kind != "judge":
            continue
        try:
            jp = c.fn(art)
        except Exception as e:  # noqa: BLE001
            results.append(CheckResult(c.id, False, 0.0, c.severity, c.dimension, c.genre,
                                       f"judge prompt error: {e}", error=str(e)))
            continue
        if not isinstance(jp, JudgePrompt) or not jp.excerpt.strip():
            results.append(CheckResult(c.id, True, 1.0, c.severity, c.dimension, c.genre, skipped=True))
            continue
        rubric = f"RUBRIC: {jp.rubric}\n" if jp.rubric else ""
        prompt = (
            f"{_JUDGE_INSTRUCTION}\n\nQUESTION: {jp.question}\n{rubric}\nCONTENT:\n{jp.excerpt[:6000]}"
        )
        try:
            v = evaluator.evaluate_json(prompt)
            results.append(CheckResult(
                c.id, bool(v.get("passed", False)), float(v.get("score", 0.0) or 0.0),
                c.severity, c.dimension, c.genre, str(v.get("reason", ""))[:200],
            ))
        except Exception as e:  # noqa: BLE001 — a flaky judge must not fail the run
            results.append(CheckResult(c.id, True, 1.0, c.severity, c.dimension, c.genre,
                                       f"judge call failed: {e}", skipped=True))
    return results


def judge_checks_for(art: Any) -> list[Check]:
    """The judge checks applicable to this artifact's genre (alias-aware, via checks_for)."""
    from .check import checks_for
    return checks_for(genre=art.genre, kind="judge")


# ── explainer-insight judges (the founder's deep-quality concern) ─────────────


def _is_explainer(art) -> bool:
    g = (art.genre or "").lower()
    return "explain" in g or "educational" in g or "tutorial" in g


@judge_check("explainer.genuine_understanding", dimension="content", genre="explainer", severity="high")
def genuine_understanding(art):
    "Would a curious novice come away genuinely understanding the MECHANISM, not just buzzwords?"
    if not _is_explainer(art) or not art.script_text:
        return None
    return JudgePrompt(
        question=("Would a curious beginner come away genuinely understanding HOW this works (the "
                  "actual mechanism + the why), or only hearing terms named without being explained?"),
        excerpt=art.script_text,
        rubric="pass ONLY if the core mechanism is explained with cause-and-effect, not just named.",
    )


@judge_check("explainer.real_insight_not_handwavy", dimension="content", genre="explainer", severity="high")
def real_insight_not_handwavy(art):
    "Real INSIGHT/detail vs handwavy surface-level (the founder's exact complaint)."
    if not _is_explainer(art) or not art.script_text:
        return None
    return JudgePrompt(
        question=("Is this explainer DENSE with real insight and specific detail — the actual "
                  "details behind the explanation — or is it handwavy, generic, and more host "
                  "banter/pleasantries than substance?"),
        excerpt=art.script_text,
        rubric="pass ONLY if it delivers meaty specifics + a concrete worked example; fail if generic.",
    )


@judge_check("explainer.practical_examples", dimension="content", genre="explainer", severity="medium")
def practical_examples(art):
    "Are the examples REAL and PRACTICAL (a listener can picture them), not abstract filler?"
    if not _is_explainer(art) or not art.script_text:
        return None
    return JudgePrompt(
        question="Does it use concrete, practical, real-world examples a listener can picture (not abstract hand-waving)?",
        excerpt=art.script_text,
        rubric="pass ONLY if at least one example is specific + relatable.",
    )
