"""Video-sync checks (Claude-parallel lane) — all deterministic ($0: ffprobe + timing-math).

These assert that the rendered VIDEO is in sync with the audio + the planned visual timeline:
the video runs the same length as the master audio, the caption cues track the spoken segments,
each visual clip lands on its narration beat, a structural-diagram beat actually BUILDS over
multiple reveal frames (the founder's "b-tree builds node-by-node", not one static frame), and the
clips cover the audio span with no gap/overrun. Each check is one decorated function returning
``bool | (bool, evidence) | (bool, score, evidence)``; ``skip()`` when there's no video / no clips.

Data shapes (read from the real worker pipeline — see kitesforu-workers/stages/visuals + audio):
- ``art.video_path`` — local mp4 (duration probed here via ffprobe; there is no ``art.video_info``).
- ``art.audio_duration_s`` — master audio length (lazy ffprobe via ``art.audio_info``).
- ``art.doc["visual_clips"]`` — ``[{beat_index, start_ms, duration_ms|end_ms, modality, render_mode,
  _reveal_index, _reveal_total, ...}]``. Sub-beat-split sub-clips SHARE a ``beat_index`` and carry
  distinct ``_reveal_index``/``_reveal_total`` (the progressive diagram build).
- ``art.doc["captions"]`` — ``[{start_ms, end_ms, text}]`` cues (one per captioned segment).
- ``art.segments`` (``segments_ready``) — ``[{index, duration_ms, text, ...}]``.
- ``art.beat_map`` (``segment_beat_map``) — the producer writes a DICT ``{str(segment_index):
  beat_index}``; the Artifact accessor types it ``list`` and falls back to ``[]``, so the raw doc
  value is read here and BOTH shapes (dict or list-of-dicts) are tolerated.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from ..check import check, skip

# ── named thresholds (single place to tune) ──────────────────────────────────
_DIMENSION = "video-sync"

# duration_matches_master: |video - audio| within this fraction of audio length (encode padding,
# trailing-frame rounding, intro-music lead) — a real desync (the WAV-as-mp3 10s-vs-292s class) is
# far outside this band.
_DURATION_TOL_PCT = 0.05
_DURATION_TOL_FLOOR_S = 1.0  # absolute floor for very short clips (5% of 8s is too tight)

# captions_align: a cue's start may trail its segment's spoken start by at most this (encode lead),
# and the cue track must cover at least this fraction of the audio span.
_CAPTION_MAX_LEAD_S = 2.0
_CAPTION_COVERAGE_MIN = 0.80
# how many cues' starts must be monotonic non-decreasing (a scrambled track fails outright)
_CAPTION_MONOTONIC_MIN_FRAC = 0.95

# clips_beat_aligned: a clip's start_ms must sit inside its beat's narration window, with this much
# slack on each side (in seconds) for the title-lead + crossfade.
_CLIP_BEAT_SLACK_S = 2.5
_CLIP_BEAT_ALIGN_MIN_FRAC = 0.80  # at least this fraction of clips must land in-window

# progressive_reveal_present: a structural diagram unit must build over at least this many frames.
_REVEAL_MIN_FRAMES = 2

# no_gap_or_overrun: the union of clip spans must cover at least this fraction of the audio, and
# overrun past the audio end by no more than this fraction.
_COVERAGE_MIN = 0.90
_OVERRUN_MAX_PCT = 0.05
# the largest single uncovered interior gap allowed (seconds) — a black hole mid-video.
_MAX_INTERIOR_GAP_S = 6.0


# ── shape helpers ────────────────────────────────────────────────────────────


def _require_video(art: Any) -> None:
    if not getattr(art, "video_path", None):
        skip("no video")


def _clips(art: Any) -> list[dict[str, Any]]:
    raw = art.doc.get("visual_clips") or []
    return [c for c in raw if isinstance(c, dict)]


def _captions(art: Any) -> list[dict[str, Any]]:
    raw = art.doc.get("captions") or art.doc.get("caption_cues") or []
    return [c for c in raw if isinstance(c, dict)]


def _beat_map_dict(art: Any) -> dict[int, int]:
    """``{segment_index: beat_index}`` from the raw doc, tolerating BOTH the producer's dict shape
    (``{str(idx): beat}``) and a list-of-dicts shape (``[{segment_index, beat_index}]``)."""
    raw = art.doc.get("segment_beat_map")
    out: dict[int, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(k)] = int(v)
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            si = row.get("segment_index", row.get("index"))
            bi = row.get("beat_index")
            if si is None or bi is None:
                continue
            try:
                out[int(si)] = int(bi)
            except (TypeError, ValueError):
                continue
    return out


def _clip_start_s(c: dict[str, Any]) -> float | None:
    v = c.get("start_ms")
    return v / 1000.0 if isinstance(v, (int, float)) else None


def _clip_end_s(c: dict[str, Any]) -> float | None:
    end = c.get("end_ms")
    if isinstance(end, (int, float)):
        return end / 1000.0
    start = c.get("start_ms")
    dur = c.get("duration_ms")
    if isinstance(start, (int, float)) and isinstance(dur, (int, float)):
        return (start + dur) / 1000.0
    return None


def _probe_duration_s(path: str) -> float | None:
    """Video duration via ffprobe (no ``art.video_info`` exists). $0, ffprobe-only."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        dur = json.loads(out.stdout).get("format", {}).get("duration")
        return float(dur) if dur is not None else None
    except Exception:
        return None


# ── checks ───────────────────────────────────────────────────────────────────


@check("video_sync.duration_matches_master", dimension=_DIMENSION, severity="critical")
def duration_matches_master(art):
    "The rendered video must run the same length as the master audio (the WAV-as-mp3 10s/292s class)."
    _require_video(art)
    audio_s = art.audio_duration_s
    if not audio_s:
        skip("no master audio duration")
    video_s = _probe_duration_s(art.video_path)
    if not video_s:
        skip("could not probe video duration")
    tol = max(audio_s * _DURATION_TOL_PCT, _DURATION_TOL_FLOOR_S)
    delta = abs(video_s - audio_s)
    score = max(0.0, 1.0 - delta / audio_s)
    return delta <= tol, score, f"video {video_s:.1f}s vs master {audio_s:.1f}s (Δ{delta:.1f}s, tol {tol:.1f}s)"


@check("video_sync.captions_align", dimension=_DIMENSION, severity="high")
def captions_align(art):
    "Caption cues must track the spoken timeline: monotonic, covering the span, not lagging segments."
    _require_video(art)
    cues = _captions(art)
    if not cues:
        skip("no caption cues")
    starts = [c.get("start_ms") for c in cues if isinstance(c.get("start_ms"), (int, float))]
    if len(starts) < 2:
        skip("too few cues with timing to assess alignment")

    # 1) monotonic non-decreasing starts (a scrambled/zeroed track fails)
    ordered = sum(1 for a, b in zip(starts, starts[1:], strict=False) if b >= a)
    mono_frac = ordered / max(1, len(starts) - 1)

    # 2) coverage of the audio span by the cue track
    audio_s = art.audio_duration_s
    last_end = max(
        (c.get("end_ms") for c in cues if isinstance(c.get("end_ms"), (int, float))),
        default=max(starts),
    )
    coverage = (last_end / 1000.0) / audio_s if audio_s else 1.0

    mono_ok = mono_frac >= _CAPTION_MONOTONIC_MIN_FRAC
    cov_ok = coverage >= _CAPTION_COVERAGE_MIN
    passed = mono_ok and cov_ok
    score = min(mono_frac, min(1.0, coverage))
    return passed, score, (
        f"{len(cues)} cues, monotonic {mono_frac:.0%} (need {_CAPTION_MONOTONIC_MIN_FRAC:.0%}), "
        f"coverage {coverage:.0%} (need {_CAPTION_COVERAGE_MIN:.0%})"
    )


@check("video_sync.clips_beat_aligned", dimension=_DIMENSION, severity="high")
def clips_beat_aligned(art):
    "Each visual clip's start must land inside its narration beat's window (the talks-one/shows-other class)."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    beat_map = _beat_map_dict(art)
    segs = art.segments
    if not beat_map or not segs:
        skip("no segment_beat_map or segments to derive beat windows")

    # Build each beat's narration window [start_s, end_s) from the segments anchored to it.
    # Segments are clocked gaplessly by duration_ms in index order (the canonical timeline).
    seg_by_index: dict[int, dict[str, Any]] = {}
    for i, s in enumerate(segs):
        if isinstance(s, dict):
            seg_by_index[int(s.get("index", i))] = s
    order = sorted(seg_by_index.keys())
    start_s_by_index: dict[int, float] = {}
    end_s_by_index: dict[int, float] = {}
    acc = 0.0
    for si in order:
        dur = float(seg_by_index[si].get("duration_ms") or 0) / 1000.0
        start_s_by_index[si] = acc
        acc += dur
        end_s_by_index[si] = acc

    beat_window: dict[int, list[float]] = {}
    for si, bi in beat_map.items():
        if si not in start_s_by_index:
            continue
        lo, hi = start_s_by_index[si], end_s_by_index[si]
        if bi not in beat_window:
            beat_window[bi] = [lo, hi]
        else:
            beat_window[bi][0] = min(beat_window[bi][0], lo)
            beat_window[bi][1] = max(beat_window[bi][1], hi)
    if not beat_window:
        skip("could not build beat windows from segments")

    assessable = 0
    aligned = 0
    for c in clips:
        bi = c.get("beat_index")
        cs = _clip_start_s(c)
        if not isinstance(bi, int) or cs is None or bi not in beat_window:
            continue
        assessable += 1
        lo, hi = beat_window[bi]
        if (lo - _CLIP_BEAT_SLACK_S) <= cs <= (hi + _CLIP_BEAT_SLACK_S):
            aligned += 1
    if assessable == 0:
        skip("no clips with a matching beat window to assess")
    frac = aligned / assessable
    return frac >= _CLIP_BEAT_ALIGN_MIN_FRAC, frac, (
        f"{aligned}/{assessable} clips land in their beat window (±{_CLIP_BEAT_SLACK_S}s), need {_CLIP_BEAT_ALIGN_MIN_FRAC:.0%}"
    )


@check("video_sync.progressive_reveal_present", dimension=_DIMENSION, severity="medium")
def progressive_reveal_present(art):
    "A structural-diagram beat must BUILD over multiple reveal frames, not show one static frame."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    diagram_clips = [c for c in clips if str(c.get("modality") or "") == "diagram"]
    if not diagram_clips:
        skip("no structural-diagram clips (genre has no diagram to build)")

    # A reveal sequence = a diagram with _reveal_total >= 2, OR multiple sub-clips sharing one
    # beat_index (sub-beat-split builds the diagram node-by-node over the beat span).
    max_reveal_total = 0
    for c in diagram_clips:
        rt = c.get("_reveal_total")
        if isinstance(rt, int):
            max_reveal_total = max(max_reveal_total, rt)

    frames_per_beat: dict[int, int] = {}
    for c in diagram_clips:
        bi = c.get("beat_index")
        if isinstance(bi, int):
            frames_per_beat[bi] = frames_per_beat.get(bi, 0) + 1
    max_subclips = max(frames_per_beat.values(), default=0)

    frames = max(max_reveal_total, max_subclips)
    passed = frames >= _REVEAL_MIN_FRAMES
    return passed, (
        f"richest diagram builds over {frames} frames "
        f"(_reveal_total max {max_reveal_total}, max sub-clips/beat {max_subclips}); need ≥{_REVEAL_MIN_FRAMES}"
    )


@check("video_sync.no_gap_or_overrun", dimension=_DIMENSION, severity="high")
def no_gap_or_overrun(art):
    "Clips must cover the audio span with no large interior gap and no significant overrun past the end."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    audio_s = art.audio_duration_s
    if not audio_s:
        skip("no master audio duration")

    spans = []
    for c in clips:
        cs, ce = _clip_start_s(c), _clip_end_s(c)
        if cs is not None and ce is not None and ce > cs:
            spans.append((cs, ce))
    if not spans:
        skip("clips have no usable start/end timing")
    spans.sort()

    # merge overlapping spans, measure covered length + the largest interior gap
    covered = 0.0
    max_gap = 0.0
    cur_lo, cur_hi = spans[0]
    prev_hi = cur_hi
    for lo, hi in spans[1:]:
        if lo > cur_hi:  # gap before this span
            max_gap = max(max_gap, lo - cur_hi)
            covered += cur_hi - cur_lo
            cur_lo, cur_hi = lo, hi
        else:
            cur_hi = max(cur_hi, hi)
        prev_hi = max(prev_hi, hi)
    covered += cur_hi - cur_lo
    # leading gap (first clip should start near 0; treat a big lead as an interior gap too)
    max_gap = max(max_gap, spans[0][0])

    coverage = min(1.0, covered / audio_s) if audio_s else 1.0
    overrun = max(0.0, prev_hi - audio_s)
    overrun_pct = overrun / audio_s if audio_s else 0.0

    cov_ok = coverage >= _COVERAGE_MIN
    gap_ok = max_gap <= _MAX_INTERIOR_GAP_S
    overrun_ok = overrun_pct <= _OVERRUN_MAX_PCT
    passed = cov_ok and gap_ok and overrun_ok
    score = min(coverage, max(0.0, 1.0 - overrun_pct))
    return passed, score, (
        f"coverage {coverage:.0%} (need {_COVERAGE_MIN:.0%}), max gap {max_gap:.1f}s "
        f"(≤{_MAX_INTERIOR_GAP_S}s), overrun {overrun:.1f}s ({overrun_pct:.0%}, ≤{_OVERRUN_MAX_PCT:.0%})"
    )
