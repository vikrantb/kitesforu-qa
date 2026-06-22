"""Battery — run a set of checks against an Artifact -> a kqa StageResult (+ a full scorecard).

The bridge: granular ``CheckResult``s aggregate into the existing kqa ``StageResult`` so the harness
reuses the kqa pipeline, reporting, and improvement-feedback loop instead of a parallel framework.
A battery gates ``passed`` on the critical+high checks only — medium/low contribute to the score
(advisory) but never fail the gate, so the bulk of cheap nice-to-have checks can't block a ship.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..models.results import StageResult
from .check import Check, CheckResult, checks_for

_GATING = {"critical", "high"}


def run_checks(art: Any, checks: list[Check]) -> list[CheckResult]:
    return [c.run(art) for c in checks]


def to_stage_result(stage_name: str, results: list[CheckResult]) -> StageResult:
    active = [r for r in results if not r.skipped]
    failed = [r for r in active if not r.passed]
    gating_failed = [r for r in failed if r.severity in _GATING]
    issues = [f"[{r.severity}] {r.check_id}: {r.evidence}" for r in failed]
    avg = round(sum(r.score for r in active) / len(active), 3) if active else 1.0
    return StageResult(
        stage_name=stage_name,
        passed=(len(gating_failed) == 0),
        data={
            "score": avg,
            "n_checks": len(active),
            "n_skipped": len(results) - len(active),
            "n_failed": len(failed),
            "checks": [asdict(r) for r in results],
        },
        issues=issues,
    )


def run_dimension(art: Any, dimension: str, *, genre: str | None = None) -> StageResult:
    """Run every deterministic check in a dimension (genre-agnostic + this genre's) -> StageResult."""
    cs = checks_for(genre=genre or art.genre, dimension=dimension, kind="deterministic")
    return to_stage_result(dimension, run_checks(art, cs))


def run_scorecard(art: Any, *, dimensions: list[str] | None = None) -> dict[str, StageResult]:
    """Run all deterministic checks for the artifact's genre, grouped by dimension ->
    {dimension: StageResult}. Each StageResult plugs straight into ``QAResult.add_stage_result``."""
    cs = checks_for(genre=art.genre, kind="deterministic")
    if dimensions:
        cs = [c for c in cs if c.dimension in dimensions]
    by_dim: dict[str, list[CheckResult]] = {}
    for c in cs:
        by_dim.setdefault(c.dimension, []).append(c.run(art))
    return {dim: to_stage_result(dim, rs) for dim, rs in by_dim.items()}


def scorecard_summary(scorecard: dict[str, StageResult]) -> dict[str, Any]:
    """Human + machine readable rollup of a scorecard."""
    total = sum(s.data.get("n_checks", 0) for s in scorecard.values())
    failed = sum(s.data.get("n_failed", 0) for s in scorecard.values())
    return {
        "passed": all(s.passed for s in scorecard.values()),
        "dimensions": {
            d: {"passed": s.passed, "score": s.data.get("score"), "failed": s.data.get("n_failed")}
            for d, s in scorecard.items()
        },
        "total_checks": total,
        "total_failed": failed,
        "all_issues": [i for s in scorecard.values() for i in s.issues],
    }
