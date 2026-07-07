"""kitesforu_qa.scorecard — the SHORT SCORECARD: a $0/¢ machine-scorable rubric for 9:16 shorts.

Phase 1b of ``social-shorts-next-level-2026-07-06`` (see
``kitesforu-docs/proposals/social-shorts-next-level-2026-07-06/SYSTEMATIC-ARCHITECTURE.json`` ->
``result.north_star_rubric`` for the canonical spec this module implements). Scores a rendered
short on 8 axes (0-100 each) and computes a SHIP verdict: ship only when every axis clears its
floor AND the weighted total clears the ship bar — never on a fabricated/missing score.

Typical usage::

    from kitesforu_qa.harness.artifact import Artifact
    from kitesforu_qa.scorecard import ScorecardConfig, score_short

    art = Artifact.from_doc(job_doc, video_path="episode_video_captioned.mp4")
    result = score_short(art, ScorecardConfig())  # $0 baseline (judge/VLM axes OFF)
"""
from .config import ScorecardConfig
from .rubric import (
    RUBRIC,
    RUBRIC_BY_NAME,
    SHIP_TOTAL_MIN,
    AxisResult,
    AxisSpec,
    ShipVerdict,
    evaluate,
)
from .scorer import score_axes, score_short

__all__ = [
    "RUBRIC",
    "RUBRIC_BY_NAME",
    "SHIP_TOTAL_MIN",
    "AxisResult",
    "AxisSpec",
    "ScorecardConfig",
    "ShipVerdict",
    "evaluate",
    "score_axes",
    "score_short",
]
