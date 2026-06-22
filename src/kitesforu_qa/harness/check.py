"""The Check primitive — the granular unit of the quality harness.

A check is ONE small assertion about a generated Artifact. Thousands register via the ``@check``
decorator and run in batteries (per genre x dimension) that aggregate into the existing kqa
``StageResult`` / ``QAResult`` — so the harness plugs into the kqa pipeline + improvement-feedback
loop rather than a parallel framework. Deterministic checks ($0, pure Python over the doc/audio/
images) are the bulk; ``judge`` checks defer to a LOCAL agent (see ``harness/judge.py``).

Authoring a check is one decorated function — the contract every check (yours or a peer's) uses:

    @check("explainer.has_example", dimension="content", genre="explainer", severity="high")
    def has_example(art):
        "An explainer must give at least one concrete example."
        n = art.script_text.lower().count("for example") + art.script_text.lower().count("imagine")
        return n >= 1, f"{n} example markers"

The fn receives an ``Artifact`` and returns: ``bool`` | ``(bool, evidence)`` |
``(bool, score, evidence)`` | ``CheckResult`` | ``None``. Raise ``harness.skip(reason)`` when the
check does not apply (e.g. no audio / no visuals) — that records a skip, not a failure.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Severity = str  # critical | high | medium | low
Kind = str      # deterministic | judge


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    score: float = 1.0            # 0..1 (1 = perfect)
    severity: Severity = "medium"
    dimension: str = "general"
    genre: str | None = None
    evidence: str = ""
    skipped: bool = False
    error: str | None = None


CheckReturn = bool | tuple | CheckResult | None


class _Skip(Exception):  # noqa: N818 — a control-flow signal (like StopIteration), not an error
    """Raised inside a check to mark it not-applicable for this artifact."""


def skip(reason: str = "") -> None:
    """Call inside a check to mark it not-applicable (records a skip, not a failure)."""
    raise _Skip(reason)


def _coerce(c: Check, out: CheckReturn) -> CheckResult:
    if isinstance(out, CheckResult):
        return out
    if out is None:
        return CheckResult(c.id, True, 1.0, c.severity, c.dimension, c.genre, skipped=True)
    if isinstance(out, bool):
        return CheckResult(c.id, out, 1.0 if out else 0.0, c.severity, c.dimension, c.genre)
    if isinstance(out, tuple) and len(out) == 2:
        passed, ev = out
        return CheckResult(c.id, bool(passed), 1.0 if passed else 0.0, c.severity, c.dimension, c.genre, str(ev))
    if isinstance(out, tuple) and len(out) == 3:
        passed, score, ev = out
        return CheckResult(c.id, bool(passed), float(score), c.severity, c.dimension, c.genre, str(ev))
    raise TypeError(f"check {c.id} returned unsupported type {type(out)!r}")


@dataclass
class Check:
    id: str
    dimension: str
    fn: Callable[[Any], CheckReturn]
    genre: str | None = None          # None = applies to EVERY genre (shared check)
    severity: Severity = "medium"
    kind: Kind = "deterministic"
    description: str = ""

    def run(self, art: Any) -> CheckResult:
        """Run the check against an Artifact. NEVER raises — a crashing check becomes a failed
        CheckResult (a broken check must not take down the battery)."""
        try:
            out = self.fn(art)
        except _Skip as s:
            return CheckResult(self.id, True, 1.0, self.severity, self.dimension, self.genre, str(s), skipped=True)
        except Exception as e:  # noqa: BLE001
            return CheckResult(self.id, False, 0.0, self.severity, self.dimension, self.genre, f"check raised: {e}", error=str(e))
        return _coerce(self, out)


# ── registry ────────────────────────────────────────────────────────────────
_REGISTRY: list[Check] = []


def check(
    id: str,
    dimension: str,
    *,
    genre: str | None = None,
    severity: Severity = "medium",
    kind: Kind = "deterministic",
    description: str = "",
):
    """Register a check. See module docstring for the fn contract."""
    def deco(fn: Callable[[Any], CheckReturn]):
        _REGISTRY.append(
            Check(id, dimension, fn, genre=genre, severity=severity, kind=kind,
                  description=description or (fn.__doc__ or "").strip())
        )
        return fn
    return deco


def register(c: Check) -> Check:
    """Register a pre-built Check object (for checks generated programmatically)."""
    _REGISTRY.append(c)
    return c


def all_checks() -> list[Check]:
    return list(_REGISTRY)


def checks_for(
    genre: str | None = None,
    dimension: str | None = None,
    kind: Kind | None = None,
) -> list[Check]:
    """Select the check-set: every genre-agnostic check (genre is None) PLUS the given genre's own.
    Optionally filter by dimension and/or kind."""
    out: list[Check] = []
    for c in _REGISTRY:
        if dimension is not None and c.dimension != dimension:
            continue
        if kind is not None and c.kind != kind:
            continue
        if c.genre is not None and genre is not None and c.genre != genre:
            continue
        out.append(c)
    return out


def clear_registry() -> None:
    """Test helper — reset the registry between tests."""
    _REGISTRY.clear()
