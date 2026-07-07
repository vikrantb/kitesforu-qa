"""scorer.py — orchestrates the 8 SHORT SCORECARD axes over one Artifact into the final verdict.

The single public entry point, ``score_short``, is what ``scripts/short_scorecard.py`` (and any
future caller — the closed-loop engine's ship gate) calls. It composes ``axes.py`` (the per-axis
scoring functions) with ``rubric.py`` (the floor/weight/ship-bar math) into one JSON-serialisable
dict matching the proposal's contract: ``{axes, weighted_total, ship, missing_instrumentation}``.
"""
from __future__ import annotations

from typing import Any

from . import axes as _axes
from .config import ScorecardConfig
from .rubric import AxisResult, evaluate

# Axis name -> scoring function. Order matches the proposal's 8-axis numbering (rubric.RUBRIC).
_AXIS_FUNCS: dict[str, Any] = {
    "hook_stop_power": _axes.score_hook_stop_power,
    "substance_novelty": _axes.score_substance_novelty,
    "visual_truth": _axes.score_visual_truth,
    "modality_mix": _axes.score_modality_mix,
    "motion_density": _axes.score_motion_density,
    "sync_exactness": _axes.score_sync_exactness,
    "audio_feel": _axes.score_audio_feel,
    "cost_safety": _axes.score_cost_safety,
}


def score_axes(art: Any, cfg: ScorecardConfig | None = None) -> dict[str, AxisResult]:
    """Run all 8 axis-scoring functions over ``art`` and return ``{axis_name: AxisResult}``."""
    cfg = cfg or ScorecardConfig()
    return {name: fn(art, cfg) for name, fn in _AXIS_FUNCS.items()}


def score_short(art: Any, cfg: ScorecardConfig | None = None) -> dict[str, Any]:
    """Score one rendered short end-to-end: run every axis, compute the ship verdict, and return
    the full SHORT SCORECARD as a JSON-serialisable dict.

    ``cfg`` defaults to ``ScorecardConfig()`` — every paid axis (judge/VLM) OFF, so a bare call is
    always $0. Pass a ``ScorecardConfig(enable_judge=True, judge_fn=...)`` (etc.) to escalate a
    specific axis to its authoritative T2 tier.
    """
    cfg = cfg or ScorecardConfig()
    axes = score_axes(art, cfg)
    verdict = evaluate(axes)
    return {
        "job_id": getattr(art, "job_id", None),
        "axes": {name: result.to_dict() for name, result in axes.items()},
        "weighted_total": verdict.weighted_total,
        "weighted_total_basis": verdict.weighted_total_basis,
        "ship": verdict.ship,
        "ship_reasons": verdict.reasons,
        "missing_instrumentation": verdict.missing_instrumentation,
        "provisional_axes": verdict.provisional_axes,
    }
