"""Monotony metric — catch 'slideshow, not a short' offline, pre-ship (born-short QA Step F).

The hero-user corpus audit (2026-07-15) scored delivered shorts 1.0/5 because each renders 1-3 DISTINCT
visuals held/repeated across N beats (shot_specs pinned at 3; rendered_motion_clips=0). This deterministic
metric reads a job's ``visual.clips`` and flags that class BEFORE it ships, so QA no longer needs a hero
corpus to find it. $0, pure (no LLM, no render). See proposals/born-short-corpus-backlog-2026-07-15/.
"""
from __future__ import annotations

from typing import Any


def monotony_metric(
    clips: list[dict[str, Any]], *, beat_count: int | None = None,
    rendered_motion_clips: int | None = None,
) -> dict[str, Any]:
    """Compute {distinct_asset_count, clip_count, distinct_ratio, onscreen_motion_count, is_monotone,
    reasons}. ``is_monotone`` is True when the short is effectively a slideshow — too few DISTINCT
    assets for its beats and/or no real motion. Deterministic; empty clips → is_monotone True (nothing
    rendered)."""
    clips = [c for c in (clips or []) if isinstance(c, dict)]
    uris = [str(c.get("asset_uri") or "").strip() for c in clips]
    distinct = len({u for u in uris if u})
    n = len(clips)
    modes = [str(c.get("render_mode") or "").lower() for c in clips]
    # "onscreen motion" = a real motion clip, NOT a still or a Ken-Burns/parallax pan over a still
    still_like = {"still", "parallax_2_5d", "parallax", "ken_burns", "kenburns", ""}
    motion = rendered_motion_clips if rendered_motion_clips is not None else sum(
        1 for m in modes if m not in still_like
    )
    reasons: list[str] = []
    # (1) distinct-asset floor: a short needs >= min(beat_count, 3) distinct visuals, and always > 2.
    need = max(3, min(beat_count, 8)) if beat_count else 3
    if distinct <= 2:
        reasons.append(f"only {distinct} distinct asset(s) across {n} clips — a slideshow")
    elif beat_count and distinct < min(beat_count, need):
        reasons.append(f"{distinct} distinct assets for {beat_count} beats — beats collapse onto few visuals")
    # (2) zero real motion (everything a still/Ken-Burns pan)
    if motion == 0 and n > 0:
        reasons.append("0 real motion clips — every beat is a still or a pan over a still")
    return {
        "distinct_asset_count": distinct,
        "clip_count": n,
        "distinct_ratio": round(distinct / n, 3) if n else 0.0,
        "onscreen_motion_count": motion,
        "beat_count": beat_count,
        "is_monotone": bool(reasons),
        "reasons": reasons,
    }


def metric_from_job_doc(job: dict[str, Any]) -> dict[str, Any]:
    """Convenience: pull clips + beat_count + motion from a podcast_jobs doc and run the metric."""
    vis = (job.get("visual") or {}) if isinstance(job, dict) else {}
    sbm = job.get("segment_beat_map") if isinstance(job, dict) else None
    bc = len(sbm) if isinstance(sbm, (list, dict)) and sbm else None
    rmc = vis.get("rendered_motion_clips")
    return monotony_metric(vis.get("clips") or [], beat_count=bc,
                           rendered_motion_clips=int(rmc) if isinstance(rmc, (int, float)) else None)
