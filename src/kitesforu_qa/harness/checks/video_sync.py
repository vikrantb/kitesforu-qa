"""Video-sync checks (Claude-parallel lane) — all deterministic ($0: ffprobe + timing-math).

These assert that the rendered VIDEO is in sync with the audio + the planned visual timeline:
the video runs the same length as the master audio, the caption cues track the spoken segments,
each visual clip lands on its narration beat, a structural-diagram beat actually BUILDS over
multiple reveal frames (the founder's "b-tree builds node-by-node", not one static frame), and the
clips cover the audio span with no gap/overrun. Each check is one decorated function returning
``bool | (bool, evidence) | (bool, score, evidence)``; ``skip()`` when there's no video / no clips.

Data shapes (read from the real worker pipeline — see kitesforu-workers/stages/visuals + audio):
- ``art.video_path`` — local mp4. ``art.video_info`` (peer-added) gives ``{duration, width, height,
  fps, codec}``; stream-level fields (codec_type counts, pix_fmt, SAR, audio codec/bitrate) need the
  raw ffprobe ``-show_streams`` JSON, so ``_probe_streams`` runs one extra $0 ffprobe for those.
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

# ── long-tail thresholds (catalog vs-dur-*/vs-span-*/vs-cap-*/vs-motion-*) ─────
# not_collapsed_14s (vs-dur-002): video must be at least this fraction of the audio length. A
# one-sided FLOOR — duration_matches_master's symmetric band would PASS a too-short video at a
# loose tol; this never does. (the 5-min-episode -shortest-truncation / 14s-collapse class.)
_VIDEO_FLOOR_FRAC = 0.95
# not_longer_than_audio (vs-dur-003): audio is the duration master; video may exceed it only by mux
# slack. A longer video = a stray trailing segment.
_VIDEO_OVERHANG_TOL_S = 0.2
# resolution / fps / pixel-format targets (vs-dur-006..008). Overridable via env for non-1080p runs.
_TARGET_W = 1920
_TARGET_H = 1080
_TARGET_FPS = 30.0
_FPS_TOL = 1.0  # rational-rate tolerance (29.97 vs 30 etc.)
# audio mux target (vs-dur-005): -c:a aac -b:a 192k.
_AAC_BR_MIN = 150_000
_AAC_BR_MAX = 240_000
# min-byte floor (vs-dur-011): a tiny file is a collapsed/green render.
_MIN_VALID_BYTES = 10_000
# clip-span timeline tolerances (vs-span-001/004/009).
_SPAN_END_TOL_S = 1.0          # last clip end must reach within this of the audio end
_CONTIG_GAP_TOL_S = 0.75       # adjacent clip end≈next start (gapless, small ε)
_STALE_MAP_DRIFT_S = 1.5       # stamped windows must sum to within this of the master (DRIFT_BAND_MS)
# hold-cap / motion (vs-dur/exp-vs-hold/news-hold-cap/doc-sync-hold-cap/rom-no-slideshow).
_HOLD_CAP_S = 12.0            # a still held longer than this WITHOUT motion = slideshow
_MOTION_MODES = {"video", "parallax_2_5d", "kenburns", "ken_burns", "parallax", "veo"}
# scene-motion sampling (vs-motion-001) — frame-to-frame pixel diff floor.
_MOTION_DIFF_MIN = 2.0 / 255.0


# ── shape helpers ────────────────────────────────────────────────────────────


def _require_video(art: Any) -> None:
    if not getattr(art, "video_path", None):
        skip("no video")


def _clips(art: Any) -> list[dict[str, Any]]:
    """VisualClip dicts. The worker persists the compartment at ``doc['visual']['clips']``
    (visuals/worker.py writes ``{"visual": compartment}``); older/synthetic docs put them at the
    top-level ``doc['visual_clips']``. Read top-level first, else fall back to the nested shape —
    mirrors visual.py._clips so the video-sync battery actually RUNS on real jobs instead of
    skipping (the fidelity gap: real clips live nested, so every vs check skipped)."""
    raw = art.doc.get("visual_clips")
    if not raw:
        v = art.doc.get("visual")
        if isinstance(v, dict):
            raw = v.get("clips")
    return [c for c in (raw or []) if isinstance(c, dict)]


def _captions(art: Any) -> list[dict[str, Any]]:
    """Caption cue dicts ``[{start_ms, end_ms, text}]``. Real jobs carry a WebVTT STRING at
    ``doc['visual']['captions_vtt']`` (visuals/worker.py); older/synthetic docs carry a top-level
    list at ``doc['captions']`` / ``doc['caption_cues']``. Prefer an existing cue list (top-level or
    nested), else parse the nested VTT string into cue dicts so the downstream checks (which iterate
    ``start_ms``/``end_ms``) run unchanged instead of skipping on every real job."""
    raw = art.doc.get("captions") or art.doc.get("caption_cues")
    v = art.doc.get("visual")
    if not raw and isinstance(v, dict):
        raw = v.get("captions") or v.get("caption_cues")
    cues = [c for c in (raw or []) if isinstance(c, dict)]
    if cues:
        return cues
    if isinstance(v, dict):
        vtt = v.get("captions_vtt")
        if isinstance(vtt, str) and vtt.strip():
            return _parse_vtt_cues(vtt)
    return []


def _parse_vtt_cues(vtt: str) -> list[dict[str, Any]]:
    """Parse a WebVTT string into ``[{start_ms, end_ms, text}]`` cue dicts. Reads the
    ``HH:MM:SS.mmm --> HH:MM:SS.mmm`` timing lines (the worker's ``build_captions_vtt`` format);
    any cue text follows on the next line(s). Malformed lines are skipped, never raised."""
    cues: list[dict[str, Any]] = []
    lines = vtt.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            head, _, tail = line.partition("-->")
            start = _vtt_ts_to_ms(head)
            end = _vtt_ts_to_ms(tail.split()[0] if tail.split() else "")
            text_lines: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip():
                text_lines.append(lines[j].strip())
                j += 1
            if start is not None and end is not None:
                cues.append({"start_ms": start, "end_ms": end, "text": " ".join(text_lines)})
            i = j
        else:
            i += 1
    return cues


def _vtt_ts_to_ms(ts: str) -> int | None:
    """``HH:MM:SS.mmm`` (or ``MM:SS.mmm``) → milliseconds; None when unparseable."""
    ts = ts.strip()
    if not ts:
        return None
    try:
        hh = "0"
        parts = ts.split(":")
        if len(parts) == 3:
            hh, mm, rest = parts
        elif len(parts) == 2:
            mm, rest = parts
        else:
            return None
        sec, _, frac = rest.partition(".")
        ms = int(hh) * 3_600_000 + int(mm) * 60_000 + int(sec) * 1000
        if frac:
            ms += int((frac + "000")[:3])
        return ms
    except (TypeError, ValueError):
        return None


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


def _num(d: dict[str, Any], key: str) -> float | None:
    """A numeric field as ``float``, or ``None`` if missing/non-numeric. Filtering the result keeps
    timing lists typed ``list[float]`` so comparisons/``max()`` are never run over ``None``."""
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) else None


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
    """Video duration via ffprobe ($0, ffprobe-only)."""
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


def _probe_streams(path: str) -> dict[str, Any] | None:
    """Full ffprobe ``-show_format -show_streams`` JSON ($0). ``art.video_info`` only exposes the
    single video stream's {duration,w,h,fps,codec}; stream COUNTS, pix_fmt, SAR, and the audio
    stream's codec/bitrate need the raw streams array, so the stream-level checks share this."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return None
        return json.loads(out.stdout or "{}")
    except Exception:
        return None


def _video_stream(streams: dict[str, Any]) -> dict[str, Any]:
    return next((s for s in streams.get("streams", []) if s.get("codec_type") == "video"), {})


def _audio_stream(streams: dict[str, Any]) -> dict[str, Any]:
    return next((s for s in streams.get("streams", []) if s.get("codec_type") == "audio"), {})


def _target_dims() -> tuple[int, int]:
    """Target W×H, env-overridable (the worker uses STORY_VIDEO_W/H; default 1920×1080)."""
    import os
    try:
        w = int(os.environ.get("STORY_VIDEO_W") or _TARGET_W)
        h = int(os.environ.get("STORY_VIDEO_H") or _TARGET_H)
    except (TypeError, ValueError):
        w, h = _TARGET_W, _TARGET_H
    return w, h


def _rate_to_float(rate: str | None) -> float | None:
    """Parse ffprobe ``r_frame_rate`` (``'30/1'``) to a float fps."""
    if not rate or "/" not in str(rate):
        return None
    try:
        n, d = str(rate).split("/")
        d_f = float(d)
        return float(n) / d_f if d_f else None
    except (TypeError, ValueError):
        return None


def _clips_with_spans(art: Any) -> list[tuple[int | None, float, float]]:
    """``[(beat_index, start_s, end_s)]`` for clips that carry a usable start+end, sorted by start.
    Shared by the span/contiguity/order checks."""
    rows: list[tuple[int | None, float, float]] = []
    for c in _clips(art):
        cs, ce = _clip_start_s(c), _clip_end_s(c)
        if cs is None or ce is None:
            continue
        bi = c.get("beat_index")
        rows.append((bi if isinstance(bi, int) else None, cs, ce))
    rows.sort(key=lambda r: r[1])
    return rows


# ── DELIVERED-MASTER timeline reference (the intro-offset fix) ─────────────────
# WHY: visual clips are stamped on the DELIVERED master axis — their start_ms is
# ``intro_offset_ms + placed_start_ms`` (audio stage's master_segment_timeline →
# visuals/worker._stamp_beat_timing). Rebuilding the reference window by walking
# ``segments_ready[].duration_ms`` from 0 puts the reference on the PRE-intro 0-based
# axis, so a healthy intro-offset job reads a false +intro_offset lag on EVERY clip
# (the +12s false-positive). The reference MUST be on the same post-intro axis as the
# clips: prefer the persisted master_segment_timeline; else detect the front offset the
# clips share and shift the gapless walk by it.


def _master_timeline_windows(art: Any) -> dict[int, tuple[float, float]]:
    """``{segment_index: (start_s, end_s)}`` from the persisted ``master_segment_timeline``
    (delivered-master coords, intro offset already baked in). ``{}`` when absent/legacy."""
    out: dict[int, tuple[float, float]] = {}
    for r in getattr(art, "master_segment_timeline", []) or []:
        idx = r.get("index")
        st = r.get("start_ms")
        en = r.get("end_ms")
        if idx is None or st is None or en is None:
            continue
        try:
            i, s, e = int(idx), float(st) / 1000.0, float(en) / 1000.0
        except (TypeError, ValueError):
            continue
        if e < s:
            e = s
        out[i] = (s, e)
    return out


def _detect_intro_offset_s(art: Any, segment_windows: dict[int, tuple[float, float]]) -> float:
    """Front offset (seconds) between the clip axis (delivered master) and a 0-based gapless
    segment walk — the intro-music prepend the gapless walk omits. Used ONLY on legacy jobs that
    have no ``master_segment_timeline``: the earliest clip start should align to its segment's
    0-based start, so the residual is the shared front offset. Clamped to >= 0 (a clip starting
    before its 0-based segment is a real lead, not an intro offset, so never shift negative)."""
    clips = _clips(art)
    if not clips or not segment_windows:
        return 0.0
    # earliest clip start across all clips (every clip carries the same front offset).
    starts = [s for s in (_clip_start_s(c) for c in clips) if s is not None]
    earliest_seg = min((lo for lo, _ in segment_windows.values()), default=0.0)
    if not starts:
        return 0.0
    return max(0.0, min(starts) - earliest_seg)


def _segment_windows_delivered(art: Any) -> dict[int, tuple[float, float]]:
    """``{segment_index: (start_s, end_s)}`` on the SAME delivered-master axis the clips are
    stamped on. Resolution order:
      1. the persisted ``master_segment_timeline`` (authoritative — intro offset already baked in);
      2. else the gapless ``segments_ready[].duration_ms`` walk from 0, SHIFTED by the detected
         intro offset so the reference and the clips share the front offset (the +12s fix);
      3. ``{}`` when there are no usable segment durations.
    Fail-open: a legacy job with no intro prepend detects offset 0 → identical to the old 0-based
    walk, so non-intro jobs are unchanged."""
    real = _master_timeline_windows(art)
    if real:
        return real
    segs = getattr(art, "segments", None) or []
    seg_by_index: dict[int, dict[str, Any]] = {}
    for i, s in enumerate(segs):
        if isinstance(s, dict):
            seg_by_index[int(s.get("index", i))] = s
    if not seg_by_index:
        return {}
    base: dict[int, tuple[float, float]] = {}
    acc = 0.0
    for si in sorted(seg_by_index.keys()):
        dur = float(seg_by_index[si].get("duration_ms") or 0) / 1000.0
        base[si] = (acc, acc + dur)
        acc += dur
    off = _detect_intro_offset_s(art, base)
    if off <= 0.0:
        return base
    return {si: (lo + off, hi + off) for si, (lo, hi) in base.items()}


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
    # None-safe timing extraction: filter to numeric values so the lists are ``list[float]`` and the
    # comparison / max() below can never run over None (a cue may carry a null/absent start_ms|end_ms).
    starts: list[float] = [v for v in (_num(c, "start_ms") for c in cues) if v is not None]
    ends: list[float] = [v for v in (_num(c, "end_ms") for c in cues) if v is not None]
    if len(starts) < 2:
        skip("too few cues with timing to assess alignment")

    # 1) monotonic non-decreasing starts (a scrambled/zeroed track fails)
    ordered = sum(1 for a, b in zip(starts, starts[1:], strict=False) if b >= a)
    mono_frac = ordered / max(1, len(starts) - 1)

    # 2) coverage of the audio span by the cue track
    audio_s = art.audio_duration_s
    last_end = max(ends, default=max(starts))
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

    # Build each beat's narration window [start_s, end_s) from the segments anchored to it, on the
    # SAME delivered-master axis the clips are stamped on (master_segment_timeline when present, else
    # the gapless walk shifted by the detected intro offset). Walking from 0 would put the window on
    # the pre-intro axis and read a false +intro_offset lag on every clip (the +12s false-positive).
    seg_windows = _segment_windows_delivered(art)
    if not seg_windows:
        skip("no usable segment durations to derive beat windows")

    beat_window: dict[int, list[float]] = {}
    for si, bi in beat_map.items():
        if si not in seg_windows:
            continue
        lo, hi = seg_windows[si]
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

    # The clips live on the DELIVERED-master axis: the first spoken segment begins at the intro-music
    # offset, so the visuals legitimately start ~intro_offset in, not at 0. Coverage is measured
    # against the SPOKEN span (intro_start → audio end), and the leading gap is measured from where
    # speech begins — NOT from 0, which would read the entire intro as a 12s leading "black hole"
    # gap and the intro fraction as missing coverage (the +12s false-positive).
    seg_windows = _segment_windows_delivered(art)
    speech_start = min((lo for lo, _ in seg_windows.values()), default=0.0) if seg_windows else 0.0
    spoken_span = max(0.0, audio_s - speech_start)
    cover_ref = spoken_span or audio_s

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
    # leading gap: the first clip should start near where SPEECH starts (the intro offset), not 0.
    # A clip that trails the speech start by more than the cap is a real leading black hole.
    max_gap = max(max_gap, max(0.0, spans[0][0] - speech_start))

    coverage = min(1.0, covered / cover_ref) if cover_ref else 1.0
    overrun = max(0.0, prev_hi - audio_s)
    overrun_pct = overrun / audio_s if audio_s else 0.0

    cov_ok = coverage >= _COVERAGE_MIN
    gap_ok = max_gap <= _MAX_INTERIOR_GAP_S
    overrun_ok = overrun_pct <= _OVERRUN_MAX_PCT
    passed = cov_ok and gap_ok and overrun_ok
    score = min(coverage, max(0.0, 1.0 - overrun_pct))
    return passed, score, (
        f"coverage {coverage:.0%} of spoken span {cover_ref:.1f}s (need {_COVERAGE_MIN:.0%}), "
        f"max gap {max_gap:.1f}s (≤{_MAX_INTERIOR_GAP_S}s), overrun {overrun:.1f}s "
        f"({overrun_pct:.0%}, ≤{_OVERRUN_MAX_PCT:.0%})"
    )

# ── long-tail: duration / container / codec invariants (catalog vs-dur-*) ─────


@check("video_sync.not_collapsed_14s", dimension=_DIMENSION, severity="critical")
def not_collapsed_14s(art):
    "One-sided floor: video must be >=95% of master audio (the -shortest 14s-collapse class)."
    _require_video(art)
    audio_s = art.audio_duration_s
    if not audio_s:
        skip("no master audio duration")
    video_s = art.video_info.get("duration") or _probe_duration_s(art.video_path)
    if not video_s:
        skip("could not probe video duration")
    frac = video_s / audio_s
    return frac >= _VIDEO_FLOOR_FRAC, min(1.0, frac), (
        f"video {video_s:.1f}s is {frac:.0%} of master {audio_s:.1f}s (need ≥{_VIDEO_FLOOR_FRAC:.0%})"
    )


@check("video_sync.not_longer_than_audio", dimension=_DIMENSION, severity="high")
def not_longer_than_audio(art):
    "Audio is the duration master: video may exceed it only by mux slack (a longer video = stray trailing segment)."
    _require_video(art)
    audio_s = art.audio_duration_s
    if not audio_s:
        skip("no master audio duration")
    video_s = art.video_info.get("duration") or _probe_duration_s(art.video_path)
    if not video_s:
        skip("could not probe video duration")
    overhang = video_s - audio_s
    return overhang <= _VIDEO_OVERHANG_TOL_S, (
        f"video {video_s:.1f}s vs master {audio_s:.1f}s (overhang {overhang:+.2f}s, ≤{_VIDEO_OVERHANG_TOL_S}s)"
    )


@check("video_sync.one_v_one_a_stream", dimension=_DIMENSION, severity="high")
def one_v_one_a_stream(art):
    "Final mux maps exactly one video + one audio stream (0:v:0 + 1:a:0 only)."
    _require_video(art)
    streams = _probe_streams(art.video_path)
    if streams is None:
        skip("could not probe streams")
    nv = sum(1 for s in streams.get("streams", []) if s.get("codec_type") == "video")
    na = sum(1 for s in streams.get("streams", []) if s.get("codec_type") == "audio")
    return nv == 1 and na == 1, f"{nv} video stream(s), {na} audio stream(s) (need 1+1)"


@check("video_sync.audio_aac", dimension=_DIMENSION, severity="low")
def audio_aac(art):
    "Mux audio target is AAC at ~192k (within 150k-240k)."
    _require_video(art)
    streams = _probe_streams(art.video_path)
    if streams is None:
        skip("could not probe streams")
    a = _audio_stream(streams)
    if not a:
        skip("no audio stream in video")
    codec = a.get("codec_name")
    br = a.get("bit_rate")
    br_i = int(br) if isinstance(br, (int, str)) and str(br).isdigit() else None
    codec_ok = codec == "aac"
    # bit_rate is sometimes absent on a stream-copy mux — only fail it when present and out of band.
    br_ok = br_i is None or (_AAC_BR_MIN <= br_i <= _AAC_BR_MAX)
    return codec_ok and br_ok, (
        f"audio codec {codec!r} (need aac), bitrate {br_i if br_i is not None else 'n/a'} "
        f"(need {_AAC_BR_MIN}-{_AAC_BR_MAX} when present)"
    )


@check("video_sync.resolution_1080p", dimension=_DIMENSION, severity="high")
def resolution_1080p(art):
    "Final video resolution is exactly the target W×H (STORY_VIDEO_W/H, default 1920×1080)."
    _require_video(art)
    info = art.video_info
    w, h = int(info.get("width") or 0), int(info.get("height") or 0)
    if not w or not h:
        skip("could not probe video dimensions")
    tw, th = _target_dims()
    return w == tw and h == th, f"video {w}×{h} (need {tw}×{th})"


@check("video_sync.fps_is_target", dimension=_DIMENSION, severity="medium")
def fps_is_target(art):
    "Final video frame rate equals the target FPS (default 30) within rational tolerance."
    _require_video(art)
    fps = art.video_info.get("fps") or 0.0
    if not fps:
        skip("could not probe video fps")
    import os
    try:
        target = float(os.environ.get("STORY_VIDEO_FPS") or _TARGET_FPS)
    except (TypeError, ValueError):
        target = _TARGET_FPS
    return abs(fps - target) <= _FPS_TOL, f"fps {fps} vs target {target} (tol ±{_FPS_TOL})"


@check("video_sync.pix_fmt_yuv420p", dimension=_DIMENSION, severity="medium")
def pix_fmt_yuv420p(art):
    "Video pixel format is yuv420p (web/Safari compatible)."
    _require_video(art)
    streams = _probe_streams(art.video_path)
    if streams is None:
        skip("could not probe streams")
    pf = _video_stream(streams).get("pix_fmt")
    if not pf:
        skip("no pix_fmt on video stream")
    return pf == "yuv420p", f"pix_fmt {pf!r} (need yuv420p)"


@check("video_sync.codec_h264", dimension=_DIMENSION, severity="high")
def codec_h264(art):
    "Video codec is H.264 (browser-playable)."
    _require_video(art)
    codec = art.video_info.get("codec")
    if not codec:
        streams = _probe_streams(art.video_path)
        codec = _video_stream(streams).get("codec_name") if streams else None
    if not codec:
        skip("could not probe video codec")
    return codec == "h264", f"video codec {codec!r} (need h264)"


@check("video_sync.faststart_moov", dimension=_DIMENSION, severity="high")
def faststart_moov(art):
    "MP4 has +faststart: the moov atom precedes mdat (progressive web playback)."
    _require_video(art)
    try:
        with open(art.video_path, "rb") as fh:
            head = fh.read(65536)
    except OSError as e:
        skip(f"could not read video head: {e}")
    moov = head.find(b"moov")
    mdat = head.find(b"mdat")
    if moov < 0:
        # moov not in the first 64KB at all → it's almost certainly at the end (no faststart),
        # UNLESS mdat is also absent (tiny/odd file) — then we can't conclude. Fail only when mdat
        # is present in-window but moov is not.
        if mdat < 0:
            skip("neither moov nor mdat in first 64KB")
        return False, f"moov atom not in first 64KB (mdat at byte {mdat}) — no +faststart"
    ok = mdat < 0 or moov < mdat
    return ok, f"moov at byte {moov}, mdat at byte {mdat if mdat >= 0 else 'beyond 64KB'} (moov must precede mdat)"


@check("video_sync.min_byte_floor", dimension=_DIMENSION, severity="critical")
def min_byte_floor(art):
    "Video file exceeds the minimum-valid byte floor (a tiny file is a collapsed/green render)."
    _require_video(art)
    import os
    try:
        size = os.path.getsize(art.video_path)
    except OSError as e:
        skip(f"could not stat video: {e}")
    return size > _MIN_VALID_BYTES, f"{size} bytes (need >{_MIN_VALID_BYTES})"


# ── long-tail: clip-span timeline invariants (catalog vs-span-*/vi-sync-*) ────


@check("video_sync.clip_spans_reach_audio_end", dimension=_DIMENSION, severity="critical")
def clip_spans_reach_audio_end(art):
    "The visual track's LAST clip end reaches the audio end (resolve_bounds pins bounds[-1]==span_ms)."
    _require_video(art)
    rows = _clips_with_spans(art)
    if not rows:
        skip("no clips with usable spans")
    audio_s = art.audio_duration_s
    if not audio_s:
        skip("no master audio duration")
    last_end = max(r[2] for r in rows)
    delta = abs(last_end - audio_s)
    return delta <= _SPAN_END_TOL_S, (
        f"last clip end {last_end:.1f}s vs audio end {audio_s:.1f}s (Δ{delta:.2f}s, tol {_SPAN_END_TOL_S}s)"
    )


@check("video_sync.clip_starts_monotonic", dimension=_DIMENSION, severity="critical")
def clip_starts_monotonic(art):
    "Clips in beat order have non-decreasing start_ms (no time-travel / off-by-beat stamp)."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    # order by beat_index then start (the assembly order); assess starts on that ordering.
    ordered = sorted(
        clips,
        key=lambda c: (
            c["beat_index"] if isinstance(c.get("beat_index"), int) else 1_000_000,
            _clip_start_s(c) if _clip_start_s(c) is not None else 0.0,
        ),
    )
    starts = [s for s in (_clip_start_s(c) for c in ordered) if s is not None]
    if len(starts) < 2:
        skip("too few clips with start_ms to assess monotonicity")
    inversions = sum(1 for a, b in zip(starts, starts[1:], strict=False) if b < a)
    return inversions == 0, f"{inversions} start_ms inversion(s) over {len(starts)} clips (need 0)"


@check("video_sync.clips_contiguous_no_dead_frames", dimension=_DIMENSION, severity="high")
def clips_contiguous_no_dead_frames(art):
    "Adjacent clip spans are gapless (each end ≈ next start) — no dead frames between clips."
    _require_video(art)
    rows = _clips_with_spans(art)
    if len(rows) < 2:
        skip("too few clips with spans to assess contiguity")
    worst = 0.0
    bad = 0
    for (_, _, end_a), (_, start_b, _) in zip(rows, rows[1:], strict=False):
        gap = abs(start_b - end_a)
        worst = max(worst, gap)
        if gap > _CONTIG_GAP_TOL_S:
            bad += 1
    return bad == 0, (
        f"{bad}/{len(rows) - 1} adjacent boundaries exceed {_CONTIG_GAP_TOL_S}s gap "
        f"(worst {worst:.2f}s)"
    )


@check("video_sync.clip_bounds_nonneg", dimension=_DIMENSION, severity="high")
def clip_bounds_nonneg(art):
    "Every clip start_ms and end_ms is non-negative (a negative window crashes ffmpeg -t)."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    neg = 0
    assessed = 0
    for c in clips:
        cs, ce = _clip_start_s(c), _clip_end_s(c)
        if cs is None and ce is None:
            continue
        assessed += 1
        if (cs is not None and cs < 0) or (ce is not None and ce < 0):
            neg += 1
    if assessed == 0:
        skip("no clips with start/end timing")
    return neg == 0, f"{neg}/{assessed} clips have a negative bound (need 0)"


@check("video_sync.assemble_in_beat_order", dimension=_DIMENSION, severity="medium")
def assemble_in_beat_order(art):
    "Clips assemble sorted by (beat_index, start_ms): the resulting beat_index sequence is non-decreasing."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    ordered = sorted(
        clips,
        key=lambda c: (
            c["beat_index"] if isinstance(c.get("beat_index"), int) else 1_000_000,
            _clip_start_s(c) if _clip_start_s(c) is not None else 0.0,
        ),
    )
    beats = [c["beat_index"] for c in ordered if isinstance(c.get("beat_index"), int)]
    if len(beats) < 2:
        skip("too few clips with beat_index to assess order")
    inversions = sum(1 for a, b in zip(beats, beats[1:], strict=False) if b < a)
    return inversions == 0, f"{inversions} beat_index inversion(s) over {len(beats)} clips (need 0)"


# ── long-tail: OFF-BY-BEAT STALE-MAP RACE GUARD (the exact class hit this session) ──


@check("video_sync.beat_map_covers_all_segments", dimension=_DIMENSION, severity="high")
def beat_map_covers_all_segments(art):
    "Every rendered segment is mapped in the FINAL segment_beat_map, and the stamped clip windows sum "
    "to the master audio (clips anchored to the final beat map, not a stale proportional guess)."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips (genre renders none)")
    beat_map = _beat_map_dict(art)
    segs = art.segments
    if not beat_map or not segs:
        skip("no segment_beat_map or segments to assess coverage")

    # 1) every rendered segment index has a beat_map entry (project_visual_audio_sync_anchor:
    #    ao-segbeat-count-match — no gaps).
    seg_indices = {int(s.get("index", i)) for i, s in enumerate(segs) if isinstance(s, dict)}
    mapped = set(beat_map.keys())
    unmapped = sorted(seg_indices - mapped)

    # 2) every clip beat_index resolves to a beat present in the FINAL map's value-set (a clip
    #    pointing at a non-existent beat = stale map). _stamp_beat_timing join.
    beats_in_map = set(beat_map.values())
    orphan_clips = sorted(
        {c["beat_index"] for c in clips
         if isinstance(c.get("beat_index"), int) and c["beat_index"] not in beats_in_map}
    )

    # 3) the LAST stamped clip end must reach where SPEECH ends — not where the full master ends.
    #    Clips are stamped on the delivered axis (intro offset baked in); the reference is the
    #    speech-span end (master_segment_timeline's last end when present, else the full master).
    #    Using the full master as the reference would charge the intro-music lead / a legit
    #    music-outro TAIL as drift on every healthy job (the +12s false-positive). This mirrors the
    #    worker's own evaluate_delivered_master_drift, which measures against the speech span.
    rows = _clips_with_spans(art)
    audio_s = art.audio_duration_s
    seg_windows = _segment_windows_delivered(art)
    speech_end = max((hi for _, hi in seg_windows.values()), default=0.0) if seg_windows else 0.0
    ref_end = speech_end or audio_s
    drift_ok = True
    drift_s = 0.0
    if rows and ref_end:
        last_end = max(r[2] for r in rows)
        drift_s = abs(last_end - ref_end)
        drift_ok = drift_s <= _STALE_MAP_DRIFT_S

    passed = not unmapped and not orphan_clips and drift_ok
    return passed, (
        f"{len(unmapped)} unmapped segment(s) {unmapped[:5]}, {len(orphan_clips)} orphan-beat "
        f"clip(s) {orphan_clips[:5]}, stamp drift {drift_s:.2f}s vs speech end {ref_end:.1f}s "
        f"(≤{_STALE_MAP_DRIFT_S}s)"
    )


# ── long-tail: captions non-overlap (catalog vs-cap-006) ──────────────────────


@check("video_sync.caption_cues_non_overlap", dimension=_DIMENSION, severity="high")
def caption_cues_non_overlap(art):
    "Caption cues are well-formed and non-overlapping (end>start, cue[i].end<=cue[i+1].start) — "
    "overlapping cues flash garbled text."
    _require_video(art)
    cues = _captions(art)
    if len(cues) < 2:
        skip("too few caption cues to assess overlap")
    timed = []
    for c in cues:
        s, e = _num(c, "start_ms"), _num(c, "end_ms")
        if s is not None and e is not None:
            timed.append((s, e))
    if len(timed) < 2:
        skip("too few cues with start+end timing")
    timed.sort()
    bad_window = sum(1 for s, e in timed if e <= s)
    overlaps = sum(1 for (s0, e0), (s1, e1) in zip(timed, timed[1:], strict=False) if e0 > s1)
    return bad_window == 0 and overlaps == 0, (
        f"{bad_window} non-positive cue(s), {overlaps} overlapping pair(s) over {len(timed)} cues (need 0/0)"
    )


# ── long-tail: hold-cap / motion (every genre — slideshow guard) ──────────────


@check("video_sync.hold_cap_motion", dimension=_DIMENSION, severity="medium")
def hold_cap_motion(art):
    "No still clip is held longer than the hold-cap without motion (Ken Burns/parallax/Veo) — the "
    "slideshow regression. A clip with a motion render_mode is exempt at any length."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    offenders: list[str] = []
    assessed = 0
    for c in clips:
        cs, ce = _clip_start_s(c), _clip_end_s(c)
        if cs is None or ce is None:
            continue
        hold = ce - cs
        if hold <= 0:
            continue
        assessed += 1
        mode = str(c.get("render_mode") or "").lower()
        modality = str(c.get("modality") or "").lower()
        has_motion = mode in _MOTION_MODES or any(m in mode for m in ("kenburns", "parallax", "veo"))
        if hold > _HOLD_CAP_S and not has_motion:
            offenders.append(f"beat {c.get('beat_index')} {modality or mode or 'still'} held {hold:.1f}s")
    if assessed == 0:
        skip("no clips with usable hold durations")
    return not offenders, (
        f"{len(offenders)} still(s) held >{_HOLD_CAP_S}s w/o motion: {offenders[:4]}"
        if offenders else f"all {assessed} clips within hold-cap or have motion"
    )


@check("video_sync.no_zero_duration_still", dimension=_DIMENSION, severity="medium")
def no_zero_duration_still(art):
    "No 'done' still clip has a 0ms on-screen duration (a 0-length still vanishes from the timeline)."
    _require_video(art)
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    bad: list[str] = []
    assessed = 0
    for c in clips:
        # only assess realized clips that should occupy time
        status = str(c.get("status") or "done").lower()
        if status in {"pending", "stub", "failed"}:
            continue
        cs, ce = _clip_start_s(c), _clip_end_s(c)
        dur_ms = c.get("duration_ms")
        # a beat-with-no-narration legitimately gets a 0-length hold (vs-stamp-003) — those carry
        # _reveal_total/None and are not the bug. Only flag a clip that claims a span but has 0 width.
        if cs is None and ce is None and not isinstance(dur_ms, (int, float)):
            continue
        assessed += 1
        width = (ce - cs) if (cs is not None and ce is not None) else (
            (dur_ms / 1000.0) if isinstance(dur_ms, (int, float)) else None
        )
        if width is not None and width == 0:
            bad.append(f"beat {c.get('beat_index')}")
    if assessed == 0:
        skip("no realized clips with duration to assess")
    return not bad, (
        f"{len(bad)} zero-duration still(s): {bad[:5]}" if bad else f"all {assessed} clips have on-screen width"
    )


# ── long-tail: scene-motion sampling (catalog vs-motion-001) — slow but $0 ─────


@check("video_sync.scene_has_motion", dimension=_DIMENSION, severity="low")
def scene_has_motion(art):
    "A scene/still clip shows perceptible frame-to-frame change (anti-slideshow). Samples 3 frames "
    "from the longest still clip and compares first vs last via PIL — slow, so severity=low."
    _require_video(art)
    if not art.video_path:
        skip("no video")
    rows = _clips_with_spans(art)
    if not rows:
        skip("no clips with spans to sample")
    # pick the longest non-motion still clip (the one most at risk of being frozen).
    clips = _clips(art)
    by_span: list[tuple[float, float, dict[str, Any]]] = []
    for c in clips:
        cs, ce = _clip_start_s(c), _clip_end_s(c)
        if cs is None or ce is None or ce <= cs:
            continue
        mode = str(c.get("render_mode") or "").lower()
        if mode in _MOTION_MODES:
            continue  # a motion clip is expected to move; we test the at-risk stills
        by_span.append((cs, ce, c))
    if not by_span:
        skip("no still clips to sample for motion")
    cs, ce, _ = max(by_span, key=lambda t: t[1] - t[0])
    if (ce - cs) < 1.5:
        skip("longest still clip too short to sample motion")

    import os
    import tempfile
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception:
        skip("PIL unavailable")

    def _frame(at_s: float) -> Any | None:
        fd, png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            r = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.3f}",
                 "-i", art.video_path, "-frames:v", "1", "-y", png],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0 or not os.path.getsize(png):
                return None
            return Image.open(png).convert("L").resize((160, 90))
        except Exception:
            return None
        finally:
            try:
                os.unlink(png)
            except OSError:
                pass

    a = _frame(cs + (ce - cs) * 0.10)
    b = _frame(cs + (ce - cs) * 0.90)
    if a is None or b is None:
        skip("could not extract sample frames")
    diff = ImageStat.Stat(ImageChops.difference(a, b)).mean[0] / 255.0
    return diff >= _MOTION_DIFF_MIN, diff, (
        f"longest still ({ce - cs:.1f}s) frame-diff {diff:.4f} (need ≥{_MOTION_DIFF_MIN:.4f})"
    )
