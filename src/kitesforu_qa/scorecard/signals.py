"""Deterministic ($0) signal extractors over an ``Artifact`` — the shared substrate for the axes.

Every function here is pure over the already-fetched job doc (+ the locally-downloaded video/audio):
no network, no LLM, no spend. Each axis in ``axes.py`` composes these. Reading the doc's real shapes
is delegated to the harness ``Artifact`` accessors (``visual_clips``, ``segments``, ``costs``,
``master_segment_timeline``, ``stages``, ``dialogue``…) wherever they exist, so a doc-shape change is
absorbed in ONE place (``harness/artifact.py``) rather than re-encoded here.
"""
from __future__ import annotations

import collections
import subprocess
from typing import Any

# Motion render modes — MIRRORS kitesforu-workers stages/visuals + the harness video_sync._MOTION_MODES.
# A clip in one of these renders visible in-frame movement (an engine-rendered motion event), as
# opposed to a static "still" card.
MOTION_RENDER_MODES = frozenset({
    "video", "parallax_2_5d", "parallax", "kenburns", "ken_burns", "veo", "video_hero", "motion",
})

# Modality → canonical bucket for the modality-mix entropy. The proposal's four buckets are
# {ai_photo, chart/diagram, kinetic text, scene}; we map every producer modality onto one of them.
_MODALITY_BUCKET = {
    "scene_image": "ai_photo",
    "ai_photo": "ai_photo",
    "photo": "ai_photo",
    "scene": "scene",
    "diagram": "diagram",
    "chart": "diagram",
    "kinetic_type": "kinetic_text",
    "kinetic_text": "kinetic_text",
    "text": "kinetic_text",
    "card": "kinetic_text",
    "video_hero": "scene",
}
MODALITY_BUCKETS = ("ai_photo", "diagram", "kinetic_text", "scene")


# ── identity ──────────────────────────────────────────────────────────────────


def is_short(art: Any) -> bool:
    """True when the artifact is a born-short (9:16). Signals, any of: doc ``format`` ==
    'short_video'; a short_video/is_short flag on inputs/audio_config; a 9:16 karaoke canvas; a
    short target duration (<= ~1.5 min)."""
    doc = art.doc
    if str(doc.get("format") or "").lower() == "short_video":
        return True
    for blob_name in ("inputs", "audio_config", "episode_profile"):
        blob = doc.get(blob_name)
        if isinstance(blob, dict) and (blob.get("short_video") or blob.get("is_short")):
            return True
    ka = (art.visual or {}).get("karaoke_ass")
    if isinstance(ka, str) and "PlayResX: 1080" in ka and "PlayResY: 1920" in ka:
        return True
    inp = doc.get("inputs") or {}
    try:
        dm = float(inp.get("duration_min") or 0)
        if 0 < dm <= 1.5:
            return True
    except (TypeError, ValueError):
        pass
    return False


# ── hook ──────────────────────────────────────────────────────────────────────


def hook_line(art: Any) -> str:
    """The FIRST spoken line — the hook. Prefers the canonical dialogue; falls back to the first
    rendered segment's text."""
    for d in art.dialogue:
        if isinstance(d, dict):
            t = str(d.get("text") or d.get("line") or "").strip()
            if t:
                return t
    for s in art.segments:
        if isinstance(s, dict):
            t = str(s.get("text_full") or s.get("text") or s.get("text_preview") or "").strip()
            if t:
                return t
    return ""


def word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def first_speech_ms(art: Any) -> float | None:
    """Delivered-master offset (ms) at which the first spoken segment begins — the earliest a viewer
    hears a word. Read from the persisted ``master_segment_timeline`` (intro offset already baked in).
    ``None`` on legacy jobs that never stamped it (then the caller degrades honestly)."""
    tl = art.master_segment_timeline
    starts = [float(r["start_ms"]) for r in tl if isinstance(r, dict) and r.get("start_ms") is not None]
    return min(starts) if starts else None


def first_beat_clip(art: Any) -> dict[str, Any] | None:
    """The clip on beat 0 (the first frame the scroller lands on). ``None`` when no clips exist."""
    clips = [c for c in art.visual_clips if isinstance(c, dict)]
    if not clips:
        return None
    with_beat = [c for c in clips if isinstance(c.get("beat_index"), int)]
    if with_beat:
        return min(with_beat, key=lambda c: (c["beat_index"], c.get("start_ms") or 0))
    return min(clips, key=lambda c: c.get("start_ms") or 0)


def is_photo_first_frame(clip: dict[str, Any] | None) -> bool:
    """True when the first frame is a photo/high-contrast VISUAL (a scroll-stopper), not a plain text
    card. A scene/ai-photo modality, OR any motion render mode (parallax/video/Ken-Burns), qualifies;
    a kinetic-text/card modality on a still does not."""
    if not clip:
        return False
    modality = str(clip.get("modality") or "").strip().lower()
    render_mode = str(clip.get("render_mode") or "").strip().lower()
    if _MODALITY_BUCKET.get(modality) in ("ai_photo", "scene"):
        return True
    if render_mode in MOTION_RENDER_MODES:
        return True
    return False


# ── modality mix ──────────────────────────────────────────────────────────────


def modality_bucket_counts(art: Any) -> collections.Counter:
    """Distinct-BEAT modality distribution across the four canonical buckets (+ 'other' for unknown).
    Counted per distinct beat so a diagram that builds over N reveal sub-clips counts once."""
    by_beat: dict[Any, str] = {}
    for c in art.visual_clips:
        if not isinstance(c, dict):
            continue
        key = c.get("beat_index")
        if key is None:
            key = ("_no_beat", c.get("start_ms"))
        modality = str(c.get("modality") or "").strip().lower()
        render_mode = str(c.get("render_mode") or "").strip().lower()
        bucket = _MODALITY_BUCKET.get(modality)
        if bucket is None:
            # infer from render mode when modality is unset (a motion render is a scene, not 'other')
            bucket = "scene" if render_mode in MOTION_RENDER_MODES else "other"
        # prefer a concrete bucket over a previously-inferred 'other' for the same beat
        if key not in by_beat or by_beat[key] == "other":
            by_beat[key] = bucket
    return collections.Counter(by_beat.values())


# ── motion ────────────────────────────────────────────────────────────────────


def motion_beats(art: Any) -> tuple[int, int, list[dict[str, Any]]]:
    """(``n_motion_beats``, ``n_total_beats``, motion_clip_records) from renderer provenance.

    A beat is a motion beat when ANY of its clips carries a motion ``render_mode`` or a non-empty
    ``motion_preset``. Counted per distinct beat (a multi-reveal build is one beat)."""
    motion_by_beat: dict[Any, dict[str, Any]] = {}
    all_beats: set[Any] = set()
    for c in art.visual_clips:
        if not isinstance(c, dict):
            continue
        key = c.get("beat_index")
        if key is None:
            key = ("_no_beat", c.get("start_ms"))
        all_beats.add(key)
        render_mode = str(c.get("render_mode") or "").strip().lower()
        preset = str(c.get("motion_preset") or "").strip().lower()
        has_motion = render_mode in MOTION_RENDER_MODES or (preset not in ("", "none", "static"))
        if has_motion and key not in motion_by_beat:
            motion_by_beat[key] = {
                "beat_index": c.get("beat_index"),
                "render_mode": render_mode,
                "motion_preset": c.get("motion_preset"),
            }
    return len(motion_by_beat), len(all_beats), list(motion_by_beat.values())


def rendered_motion_clips_counter(art: Any) -> int | None:
    """The producer's own ``visual.rendered_motion_clips`` counter, if stamped. Used only to SURFACE
    a provenance contradiction (render_mode says motion but this says 0) as evidence — not to score."""
    v = art.visual
    rmc = v.get("rendered_motion_clips") if isinstance(v, dict) else None
    return int(rmc) if isinstance(rmc, (int, float)) else None


def video_duration_s(art: Any) -> float | None:
    """Rendered video duration (s) via the Artifact's cached ffprobe, else a direct probe."""
    d = float(art.video_info.get("duration") or 0.0) if art.video_info else 0.0
    if d > 0:
        return d
    if getattr(art, "video_path", None):
        return _probe_duration_s(art.video_path)
    return None


def _probe_duration_s(path: str) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        import json
        dur = json.loads(out.stdout).get("format", {}).get("duration")
        return float(dur) if dur is not None else None
    except Exception:
        return None


def ffmpeg_scene_change_count(video_path: str, threshold: float = 0.3) -> int | None:
    """Count scene-changes in the video via ffmpeg's scene detector — the $0 motion PROXY used ONLY
    when renderer motion provenance is absent. ``None`` on any failure (fail-open)."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", video_path,
             "-filter:v", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        # showinfo prints one "pts_time" line per selected (scene-change) frame on stderr.
        return out.stderr.count("pts_time:")
    except Exception:
        return None


# ── sync (word-timestamp track detection) ─────────────────────────────────────


def word_timestamp_track(art: Any) -> dict[str, Any] | None:
    """Locate a persisted GROUND-TRUTH word-level timestamp track (Phase-1a alignment), or ``None``.

    A real word-timestamp track is what axis 6 (sync exactness) needs to measure karaoke drift
    against the ACTUAL spoken word. The current pipeline persists none — the karaoke ``.ass`` is
    char-proportional ESTIMATION and ``captions_vtt`` is segment-level, neither of which is ground
    truth. This detector deliberately does NOT accept those as a word track (that would let the axis
    grade estimation against itself). It looks only for an explicit per-word ``{word,start,end}``
    array under the known candidate locations. Returns ``{"words": count, "pairs": [(word, start_ms)]}``."""
    candidates: list[Any] = [
        art.doc.get("word_timestamps"),
        art.doc.get("word_alignment"),
        art.doc.get("alignment"),
        (art.visual or {}).get("word_timestamps"),
        (art.visual or {}).get("word_timings"),
    ]
    for seg in art.segments:
        if isinstance(seg, dict):
            candidates.append(seg.get("words") or seg.get("word_timestamps") or seg.get("alignment"))
    for c in candidates:
        pairs = _extract_word_pairs(c)
        if pairs:
            return {"words": len(pairs), "pairs": pairs}
    return None


def karaoke_word_track(art: Any) -> dict[str, Any] | None:
    """Locate a persisted RENDERED karaoke per-word DISPLAY-timestamp track (the timing the
    renderer actually drew, as opposed to the ground-truth ``word_timestamp_track``), or ``None``.

    This is deliberately a SEPARATE lookup from ``word_timestamp_track`` — axis 6 needs both sides
    of the comparison (truth vs rendered) and neither is a substitute for the other. The pipeline
    does not persist this today either (the karaoke ``.ass`` bakes per-character ``\\t`` timing
    into the subtitle text, not a queryable per-word array), so this legitimately returns ``None``
    on real jobs until a renderer stamps it."""
    candidates: list[Any] = [
        (art.visual or {}).get("karaoke_word_timestamps"),
        (art.visual or {}).get("karaoke_words"),
        art.doc.get("karaoke_word_timestamps"),
    ]
    for c in candidates:
        pairs = _extract_word_pairs(c)
        if pairs:
            return {"words": len(pairs), "pairs": pairs}
    return None


def _extract_word_pairs(obj: Any) -> list[tuple[str, float]]:
    """``[{word|text|token, start|start_ms|start_s|t0|ts}, ...]`` -> ``[(word, start_ms), ...]``.
    Returns ``[]`` when ``obj`` doesn't look like a word-level timestamp array (the caller treats
    an empty list as "not found", never as a zero-word track)."""
    if isinstance(obj, dict):
        obj = obj.get("words") or obj.get("word_timestamps")
    if not isinstance(obj, list) or not obj:
        return []
    first = obj[0]
    if not isinstance(first, dict):
        return []
    has_word = any(k in first for k in ("word", "text", "token"))
    has_time = any(k in first for k in ("start", "start_ms", "start_s", "t0", "ts"))
    if not (has_word and has_time):
        return []
    out: list[tuple[str, float]] = []
    for item in obj:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or item.get("text") or item.get("token") or "")
        start_ms: float | None = None
        if item.get("start_ms") is not None:
            start_ms = _float_or_none(item.get("start_ms"))
        elif item.get("start_s") is not None:
            s = _float_or_none(item.get("start_s"))
            start_ms = s * 1000.0 if s is not None else None
        elif item.get("start") is not None:
            # ambiguous unit; treat bare "start"/"t0"/"ts" as SECONDS (the common convention).
            s = _float_or_none(item.get("start"))
            start_ms = s * 1000.0 if s is not None else None
        elif item.get("t0") is not None:
            s = _float_or_none(item.get("t0"))
            start_ms = s * 1000.0 if s is not None else None
        elif item.get("ts") is not None:
            s = _float_or_none(item.get("ts"))
            start_ms = s * 1000.0 if s is not None else None
        if start_ms is not None:
            out.append((word, start_ms))
    return out


# ── research grounding (substance novelty's $0 heuristic signal) ──────────────


def research_grounding(art: Any) -> dict[str, Any]:
    """Signals for whether this render's substance traces to research grounding, or is a pure
    model-weights invention: the router's chosen ``research_mode`` (none/llm/web) plus whether the
    planner then SKIPPED the actual research (e.g. ``reason: evergreen_topic``) — a route of "llm"
    with ``research_skipped=True`` means no grounding actually happened despite the route name, so
    the two signals must be read together, never the route alone."""
    rp = _stage(art, "job-research-planner")
    route = rp.get("route") if isinstance(rp.get("route"), dict) else {}
    result = rp.get("result") if isinstance(rp.get("result"), dict) else {}
    research_mode = route.get("research_mode")
    research_skipped = result.get("research_skipped")
    grounded = bool(research_mode) and research_mode != "none" and not bool(research_skipped)
    return {
        "research_mode": research_mode,
        "research_skipped": research_skipped,
        "skip_reason": result.get("reason"),
        "grounded": grounded,
    }


# ── audio / music ─────────────────────────────────────────────────────────────


def music_signals(art: Any) -> dict[str, Any]:
    """Music planning + delivery signals from the doc: the music_director's planned cue count /
    coverage / cold-open / safe-default flag, plus the listening-QA delivery verdict (detected +
    quiet-floor margin). Used by axis 7 alongside the spectral audio judge."""
    md = _stage(art, "music_director")
    listening = (((art.stages.get("job-audio") or {}).get("qa") or {}).get("listening") or {})
    music = listening.get("music") if isinstance(listening, dict) else None
    music = music if isinstance(music, dict) else {}
    return {
        "planned_cue_count": _int_or_none(md.get("cue_count")),
        "coverage_pct": _float_or_none(md.get("coverage_pct")),
        "music_recommended": md.get("music_recommended"),
        "fallback_safe_default": md.get("fallback_safe_default"),
        "cold_open_present": md.get("cold_open_present"),
        "expected": music.get("expected"),
        "detected": music.get("detected"),
        "observed_margin_db": _float_or_none(music.get("observed_margin_db")),
        "method": music.get("method"),
        "fail": music.get("fail"),
    }


# ── cost / safety ─────────────────────────────────────────────────────────────


def cost_total_usd(art: Any) -> float | None:
    """Authoritative per-short spend rollup (``costs.total_usd_estimate``). ``None`` if absent."""
    return _float_or_none(art.costs.get("total_usd_estimate"))


def cost_estimate_cents(art: Any) -> float | None:
    """The separate ``cost_estimate_cents`` field — surfaced only to flag when it disagrees with the
    rollup (an observability discrepancy), never as the authoritative number."""
    return _float_or_none(art.doc.get("cost_estimate_cents"))


def safety_verdict(art: Any) -> dict[str, Any] | None:
    """A persisted rating-independent SAFETY-floor verdict, or ``None`` when none is stamped.

    Checks the known candidate locations (a top-level safety/moderation block, or a safety sub-verdict
    on the quality gate). Returns ``None`` when the pipeline never wrote one — axis 8 then reports the
    safety half as missing instrumentation rather than assuming a pass."""
    for key in ("safety", "moderation", "content_safety", "safety_floor"):
        blob = art.doc.get(key)
        if isinstance(blob, dict) and blob:
            return blob
    qg = _stage(art, "quality_gate")
    for key in ("safety", "moderation", "safety_floor"):
        blob = qg.get(key)
        if isinstance(blob, dict) and blob:
            return blob
    return None


# ── tiny helpers ──────────────────────────────────────────────────────────────


def _stage(art: Any, name: str) -> dict[str, Any]:
    s = (art.stages or {}).get(name)
    return s if isinstance(s, dict) else {}


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
