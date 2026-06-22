"""Visual-images checks (Claude-parallel lane) — all deterministic ($0, PIL/pixel-math only).

These assert on the RENDERED scene/diagram images of a job that opted into visuals (story-visuals
tiers, explainer videos). Every check reads the local files in ``art.image_paths`` with Pillow and
does pure pixel/dimension math — NO LLM, NO cloud vision (those are the separate JUDGE layer). A job
with no visuals (audio-only podcast, genre/tier that never renders images) ``skip()``s the whole
battery — a skip is NOT a failure.

The doc-side expectation (how many images SHOULD exist) is derived from ``doc['visual_clips']`` —
each clip is a VisualClip (``beat_index``, ``aspect_ratio``, ``asset_uri``, ``render_mode``) — and
from the visual ``beat_map``, so a renderer that silently drops images is caught.

GAP-2 (flagged to Claude-main, not patched here): there is no per-image accessor on the Artifact —
only ``art.image_paths`` (may be empty even when ``doc['visual_clips']`` exist) and
``art.has_visuals``. All dimension/blank/text analysis is therefore derived in-check via PIL. Pillow
(``PIL 11.3.0``) is importable in this env but is NOT declared in ``pyproject.toml`` dependencies —
a packaging gap that must be fixed (add ``Pillow``) or the visual battery import-fails in clean CI.
"""
from __future__ import annotations

import re
from typing import Any

from ..check import check, skip

# ── named thresholds (tunable; the SSOT for what "good" means visually) ──────
_DIMENSION = "visual-images"
_ASPECT_TARGET = 16 / 9          # the platform's only render aspect (VisualClip default "16:9")
_ASPECT_TOLERANCE = 0.06         # ±6% — allows tiny letterbox-pad rounding around 1.778
_BLANK_STDDEV_MIN = 6.0         # 0..255 per-channel luma stddev below this == a flat/blank frame
_TEXT_EDGE_FRACTION_MAX = 0.04  # fraction of high-contrast "ink-like" edge pixels above which a
                                # scene image is suspected of carrying baked-in text (image models
                                # garble text — scene images must be pictorial; real diagrams are
                                # mermaid/HTML, not AI-rendered). Calibrated: pictorial frames score
                                # ~0.000–0.003, text-dense frames ~0.08+ (clean bimodal split), so
                                # 0.04 separates them with a wide margin. Heuristic only — see note.
_MIN_IMAGE_PX = 64              # an image smaller than this on either side is a stub/thumbnail, not
                                # a rendered scene.

# ── doc-side (VisualClip) field knowledge ────────────────────────────────────
# Real jobs (verified in tests/test_check_batteries.py::_visual_clips) carry render_mode="image"
# and modality in {"scene","diagram"}; the design/catalog also uses {"still","video",
# "parallax_2_5d"} and {"scene_image","chart","video_hero"}. Every membership set below is the
# UNION of both so production data is never false-failed; field-dependent checks skip() when the
# field is entirely absent (older job shape) instead of failing.
_RENDER_MODE_STILL = {"image", "still"}
_RENDER_MODE_VIDEO = {"video", "parallax_2_5d", "video_hero"}
_RENDER_MODE_KNOWN = _RENDER_MODE_STILL | _RENDER_MODE_VIDEO
_MODALITY_SCENE = {"scene", "scene_image"}
_MODALITY_STRUCTURAL = {"diagram", "chart"}
_MODALITY_KNOWN = _MODALITY_SCENE | _MODALITY_STRUCTURAL | {"video_hero"}
_STATUS_DONE = {"done", "ready", "completed"}
_STATUS_PENDING = {"pending", "stub", "failed", "queued", "in_progress"}
_KNOWN_MODEL_IDS = {
    "gemini-2.5-flash-image", "imagen-4.0-fast", "imagen-4.0", "imagen-3.0",
    "nano-banana", "gemini-2.0-flash-image", "veo-3", "veo-2",
}
_HASH_RE = re.compile(r"^[0-9a-f]{16,64}$")
_URI_RE = re.compile(r"^(gs://|https://)")
# diagram/card frames render on a near-navy background; pillarbox + fill tests sample it.
_BG_RGB = (11, 16, 32)
_BG_TOL = 40                    # per-channel |px - BG| above which a pixel is "ink" (non-background)
_FILL_MIN_FRAC = 0.20          # diagram must paint >20% of the frame (catches the inline-block strip)
_BLANK_FILL_MIN = 0.012        # diagram must paint at least ~1.2% (near-empty-on-navy floor)
_DIAGRAM_MIN_W = 1600          # diagram/card PNG floor (px)
_DIAGRAM_MIN_H = 900
_DIAGRAM_DENSITY_CAP = 0.40    # at most 40% of clips may be structural diagrams/charts (+1 slack)
_VIDEO_W = 1920
_VIDEO_H = 1080


# ── doc-side (VisualClip) helpers — read art.doc, no PIL/network ──────────────


def _clips(art) -> list[dict[str, Any]]:
    """VisualClip dicts. Real jobs put them at doc['visual_clips']; tolerate the nested
    doc['visual']['clips'] shape too (fidelity note)."""
    raw = art.doc.get("visual_clips")
    if not raw:
        v = art.doc.get("visual")
        if isinstance(v, dict):
            raw = v.get("clips")
    return [c for c in (raw or []) if isinstance(c, dict)]


def _require_clips(art) -> list[dict[str, Any]]:
    clips = _clips(art)
    if not clips:
        skip("no visual clips on doc")
    return clips


def _modality(c: dict[str, Any]) -> str:
    return str(c.get("modality") or "").strip().lower()


def _render_mode(c: dict[str, Any]) -> str:
    return str(c.get("render_mode") or "").strip().lower()


def _status(c: dict[str, Any]) -> str:
    return str(c.get("status") or "").strip().lower()


def _uri(c: dict[str, Any]) -> str:
    return str(c.get("asset_uri") or "").strip()


def _is_structural(c: dict[str, Any]) -> bool:
    return _modality(c) in _MODALITY_STRUCTURAL


def _is_scene(c: dict[str, Any]) -> bool:
    return _modality(c) in _MODALITY_SCENE


def _beat_set(art) -> set[int]:
    """The set of planned beat indices from the segment_beat_map (dict or list shape)."""
    raw = art.doc.get("segment_beat_map")
    out: set[int] = set()
    if isinstance(raw, dict):
        for v in raw.values():
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict) and row.get("beat_index") is not None:
                try:
                    out.add(int(row["beat_index"]))
                except (TypeError, ValueError):
                    continue
    return out


def _field_present(clips: list[dict[str, Any]], key: str) -> bool:
    """True if ANY clip carries a non-empty value for ``key`` (else the field is absent on this
    job shape and the field-dependent check skip()s rather than false-failing)."""
    return any(str(c.get(key) or "").strip() for c in clips)


def _diagram_image_paths(art) -> list[str]:
    """Local image files that look like diagram/card renders (the catalog's fill/blank/pad targets).
    Without a per-clip→file mapping on the Artifact, infer from the filename — diagram/card renders
    are named with those tokens by the renderer. Falls back to empty (→ skip) when none match."""
    out = []
    for p in art.image_paths:
        name = p.rsplit("/", 1)[-1].lower()
        if "diagram" in name or "card" in name or "chart" in name or "btree" in name:
            out.append(p)
    return out


def _require_visuals(art) -> None:
    """skip() the check when this job never rendered visuals (audio-only / non-visual tier)."""
    if not art.has_visuals:
        skip("job has no visuals")


def _open_images(art):
    """Yield (path, PIL.Image) for each loadable local image. Unloadable paths are skipped so a
    single corrupt file doesn't mask the others (not_corrupt is the check that fails on them)."""
    from PIL import Image
    for p in art.image_paths:
        try:
            img = Image.open(p)
            img.load()
            yield p, img
        except Exception:  # noqa: BLE001 — counted by not_corrupt, ignored elsewhere
            continue


def _expected_image_count(art) -> int:
    """How many rendered images this job SHOULD have produced.

    Prefer the VisualClip records that actually carry an asset (asset_uri set), since stub/diagram
    clips legitimately have no AI image. Fall back to the count of visual beats in the beat_map.
    """
    clips = art.doc.get("visual_clips") or []
    with_asset = sum(
        1 for c in clips
        if isinstance(c, dict) and str(c.get("asset_uri") or "").strip()
    )
    if with_asset:
        return with_asset
    if clips:
        return len([c for c in clips if isinstance(c, dict)])
    return len(art.beat_map)


# ── checks ───────────────────────────────────────────────────────────────────


@check("visual.images_present", dimension="visual-images", severity="high")
def images_present(art):
    "A job that opted into visuals must have at least one rendered image downloaded."
    _require_visuals(art)
    n = len(art.image_paths)
    return n > 0, f"{n} image files (visual_clips={len(art.doc.get('visual_clips') or [])})"


@check("visual.not_corrupt", dimension="visual-images", severity="high")
def not_corrupt(art):
    "Every image file must open + decode in PIL (a truncated/corrupt render is unusable)."
    _require_visuals(art)
    if not art.image_paths:
        skip("no image files to decode")
    from PIL import Image
    bad = []
    for p in art.image_paths:
        try:
            Image.open(p).load()
        except Exception as e:  # noqa: BLE001
            bad.append(f"{p.rsplit('/', 1)[-1]}: {type(e).__name__}")
    return len(bad) == 0, (f"{len(bad)} corrupt: {bad}" if bad else f"{len(art.image_paths)} images decode")


@check("visual.not_blank", dimension="visual-images", severity="high")
def not_blank(art):
    "No image may be a flat/blank frame (the 16:9-blank-pad regression) — pixel variance must exist."
    _require_visuals(art)
    loaded = list(_open_images(art))
    if not loaded:
        skip("no loadable images")
    from PIL import ImageStat
    blanks = []
    worst = 255.0
    for p, img in loaded:
        stat = ImageStat.Stat(img.convert("L"))
        sd = stat.stddev[0] if stat.stddev else 0.0
        worst = min(worst, sd)
        if sd < _BLANK_STDDEV_MIN:
            blanks.append(f"{p.rsplit('/', 1)[-1]}(sd={sd:.1f})")
    ok = len(blanks) == 0
    return ok, f"{len(blanks)} blank (min luma stddev {worst:.1f}, floor {_BLANK_STDDEV_MIN})"


@check("visual.aspect_16_9", dimension="visual-images", severity="medium")
def aspect_16_9(art):
    "Each rendered image should be ~16:9 (the platform's only video aspect; pad, never crop)."
    _require_visuals(art)
    loaded = list(_open_images(art))
    if not loaded:
        skip("no loadable images")
    off = []
    for p, img in loaded:
        w, h = img.size
        if not w or not h:
            off.append(f"{p.rsplit('/', 1)[-1]}(0px)")
            continue
        ar = w / h
        if abs(ar - _ASPECT_TARGET) / _ASPECT_TARGET > _ASPECT_TOLERANCE:
            off.append(f"{p.rsplit('/', 1)[-1]}({w}x{h}={ar:.2f})")
    ok = len(off) == 0
    score = 1.0 - (len(off) / len(loaded))
    return ok, score, f"{len(off)}/{len(loaded)} off 16:9 ({_ASPECT_TARGET:.2f}±{_ASPECT_TOLERANCE:.0%}): {off}"


@check("visual.count_reasonable", dimension="visual-images", severity="medium")
def count_reasonable(art):
    "Rendered image count should match the visual beats/clips (a renderer that drops images is a bug)."
    _require_visuals(art)
    expected = _expected_image_count(art)
    if expected <= 0:
        skip("no expected visual count derivable (no visual_clips / beat_map)")
    got = len(art.image_paths)
    # tolerate ±1 (diagram/stub clips, a single redelivery in flight); a >1 gap is a real drop.
    ok = abs(got - expected) <= 1
    score = min(1.0, got / expected) if expected else 1.0
    return ok, score, f"{got} images vs {expected} expected (visual_clips/beats)"


@check("visual.text_free_heuristic", dimension="visual-images", severity="low")
def text_free_heuristic(art):
    """Scene images should be pictorial/text-free; a $0 PIL edge-density heuristic flags
    suspiciously text-heavy frames (image models garble baked-in text → 'AUNCHOR'). NOTE: coarse
    proxy — a precise guard needs the existing workers scene_text_guard / an OCR judge tier."""
    _require_visuals(art)
    loaded = list(_open_images(art))
    if not loaded:
        skip("no loadable images")
    from PIL import ImageFilter
    suspicious = []
    worst = 0.0
    for p, img in loaded:
        w, h = img.size
        if w < _MIN_IMAGE_PX or h < _MIN_IMAGE_PX:
            continue
        gray = img.convert("L")
        # FIND_EDGES then threshold: text is dense, sharp, high-contrast strokes → a high fraction
        # of strong-edge pixels. Photos/illustrations have softer, sparser edges.
        edges = gray.filter(ImageFilter.FIND_EDGES)
        hist = edges.histogram()
        total = sum(hist) or 1
        strong = sum(hist[200:])  # near-white edge response = sharp stroke boundary
        frac = strong / total
        worst = max(worst, frac)
        if frac > _TEXT_EDGE_FRACTION_MAX:
            suspicious.append(f"{p.rsplit('/', 1)[-1]}(edge={frac:.2f})")
    ok = len(suspicious) == 0
    return ok, f"{len(suspicious)} text-suspect (max edge frac {worst:.2f}, ceiling {_TEXT_EDGE_FRACTION_MAX}): {suspicious}"


# ── catalog long-tail: clip↔beat coverage (doc-side) ─────────────────────────


@check("visual.clip_per_beat", dimension=_DIMENSION, severity="critical")
def clip_per_beat(art):
    "Renderer must emit one clip per planned beat (stub, never drop) — clip beats cover beat_map."
    _require_visuals(art)
    clips = _require_clips(art)
    beats = _beat_set(art)
    if not beats:
        skip("no segment_beat_map beats to compare against")
    clip_beats = {int(c["beat_index"]) for c in clips
                  if isinstance(c.get("beat_index"), int)}
    missing = beats - clip_beats
    ok = not missing and len(clips) >= len(beats)
    return ok, (f"{len(clip_beats)} clip-beats vs {len(beats)} planned beats; "
                f"{len(clips)} clips (need ≥{len(beats)}); missing beats {sorted(missing)}")


@check("visual.no_orphan_clip", dimension=_DIMENSION, severity="high")
def no_orphan_clip(art):
    "No clip may point at a beat_index absent from the planned beat_map (a stale/orphan clip)."
    _require_visuals(art)
    clips = _require_clips(art)
    beats = _beat_set(art)
    if not beats:
        skip("no segment_beat_map beats to validate against")
    orphans = sorted({int(c["beat_index"]) for c in clips
                      if isinstance(c.get("beat_index"), int)
                      and int(c["beat_index"]) not in beats})
    return not orphans, f"{len(orphans)} orphan clip beat(s) not in beat_map: {orphans}"


# ── catalog long-tail: status / asset invariants (doc-side) ──────────────────


@check("visual.done_clip_has_uri", dimension=_DIMENSION, severity="critical")
def done_clip_has_uri(art):
    "A 'done' clip with no asset_uri is a silent drop — every done clip must carry a non-empty uri."
    _require_visuals(art)
    clips = _require_clips(art)
    if not _field_present(clips, "status"):
        skip("clips carry no status field (older job shape)")
    bad = [c.get("beat_index") for c in clips if _status(c) in _STATUS_DONE and not _uri(c)]
    return not bad, f"{len(bad)} done clip(s) with empty asset_uri (beats {bad})"


@check("visual.stub_clip_empty_uri", dimension=_DIMENSION, severity="low")
def stub_clip_empty_uri(art):
    "Inverse invariant: a pending/stub/failed clip should NOT already carry an asset_uri."
    _require_visuals(art)
    clips = _require_clips(art)
    if not _field_present(clips, "status"):
        skip("clips carry no status field (older job shape)")
    bad = [c.get("beat_index") for c in clips if _status(c) in _STATUS_PENDING and _uri(c)]
    return not bad, f"{len(bad)} non-done clip(s) already carrying an asset_uri (beats {bad})"


@check("visual.uri_scheme_valid", dimension=_DIMENSION, severity="high")
def uri_scheme_valid(art):
    "Every non-empty asset_uri must be a gs:// or https:// URL (no bare paths / broken refs)."
    _require_visuals(art)
    clips = _require_clips(art)
    uris = [_uri(c) for c in clips if _uri(c)]
    if not uris:
        skip("no clips carry an asset_uri")
    bad = [u for u in uris if not _URI_RE.match(u)]
    return not bad, f"{len(bad)}/{len(uris)} asset_uri with bad scheme: {bad[:3]}"


@check("visual.uri_ext_matches_mode", dimension=_DIMENSION, severity="medium")
def uri_ext_matches_mode(art):
    "A still render_mode's asset must be .png; a video render_mode's asset must be .mp4."
    _require_visuals(art)
    clips = _require_clips(art)
    if not _field_present(clips, "render_mode"):
        skip("clips carry no render_mode field")
    bad = []
    assessed = 0
    for c in clips:
        u, rm = _uri(c), _render_mode(c)
        if not u or not rm:
            continue
        assessed += 1
        if rm in _RENDER_MODE_STILL and not u.lower().endswith(".png"):
            bad.append(f"{c.get('beat_index')}:still→{u.rsplit('.', 1)[-1]}")
        elif rm in _RENDER_MODE_VIDEO and not u.lower().endswith(".mp4"):
            bad.append(f"{c.get('beat_index')}:video→{u.rsplit('.', 1)[-1]}")
    if assessed == 0:
        skip("no clips with both a uri and a render_mode")
    return not bad, f"{len(bad)}/{assessed} uri ext mismatched render_mode: {bad[:3]}"


@check("visual.content_hash_present", dimension=_DIMENSION, severity="medium")
def content_hash_present(art):
    "Every 'done' clip should carry a content_hash (dedup/cache key)."
    _require_visuals(art)
    clips = _require_clips(art)
    if not _field_present(clips, "content_hash"):
        skip("clips carry no content_hash field (older job shape)")
    done = [c for c in clips if _status(c) in _STATUS_DONE] or clips
    bad = [c.get("beat_index") for c in done if not str(c.get("content_hash") or "").strip()]
    return not bad, f"{len(bad)} done clip(s) missing content_hash (beats {bad})"


@check("visual.content_hash_len", dimension=_DIMENSION, severity="low")
def content_hash_len(art):
    "A present content_hash must look like a hex digest (no truncated/garbage keys)."
    _require_visuals(art)
    clips = _require_clips(art)
    hashes = [str(c.get("content_hash") or "").strip() for c in clips
              if str(c.get("content_hash") or "").strip()]
    if not hashes:
        skip("no clips carry a content_hash")
    bad = [h for h in hashes if not _HASH_RE.match(h)]
    return not bad, f"{len(bad)}/{len(hashes)} content_hash not a hex digest: {bad[:3]}"


@check("visual.aspect_field_16_9", dimension=_DIMENSION, severity="high")
def aspect_field_16_9(art):
    "Each clip's declared aspect_ratio field must be '16:9' (doc-side complement of pixel aspect)."
    _require_visuals(art)
    clips = _require_clips(art)
    if not _field_present(clips, "aspect_ratio"):
        skip("clips carry no aspect_ratio field")
    bad = [f"{c.get('beat_index')}:{c.get('aspect_ratio')}"
           for c in clips
           if str(c.get("aspect_ratio") or "").strip()
           and str(c.get("aspect_ratio")).strip() != "16:9"]
    return not bad, f"{len(bad)} clip(s) not declared 16:9: {bad[:5]}"


@check("visual.render_mode_enum", dimension=_DIMENSION, severity="medium")
def render_mode_enum(art):
    "Every present render_mode must be a known enum value (still/image/video/parallax_2_5d)."
    _require_visuals(art)
    clips = _require_clips(art)
    present = {_render_mode(c) for c in clips if _render_mode(c)}
    if not present:
        skip("clips carry no render_mode field")
    bad = sorted(present - _RENDER_MODE_KNOWN)
    return not bad, f"unknown render_mode value(s): {bad}"


@check("visual.modality_valid", dimension=_DIMENSION, severity="medium")
def modality_valid(art):
    "Every present modality must be a known value (scene/scene_image/diagram/chart/video_hero)."
    _require_visuals(art)
    clips = _require_clips(art)
    present = {_modality(c) for c in clips if _modality(c)}
    if not present:
        skip("clips carry no modality field")
    bad = sorted(present - _MODALITY_KNOWN)
    return not bad, f"unknown modality value(s): {bad}"


# ── catalog long-tail: diagram weave / density / authoring source ────────────


@check("visual.no_adjacent_diagrams", dimension=_DIMENSION, severity="medium")
def no_adjacent_diagrams(art):
    "Visuals must weave: no two consecutive beats are both structural diagrams/charts (wall of charts)."
    _require_visuals(art)
    clips = _require_clips(art)
    # one representative modality per beat, in beat order
    by_beat: dict[int, bool] = {}
    for c in clips:
        bi = c.get("beat_index")
        if isinstance(bi, int):
            by_beat[bi] = by_beat.get(bi, False) or _is_structural(c)
    if len(by_beat) < 2:
        skip("fewer than 2 distinct beats to assess adjacency")
    order = [by_beat[b] for b in sorted(by_beat)]
    runs = sum(1 for a, b in zip(order, order[1:], strict=False) if a and b)
    return runs == 0, f"{runs} adjacent diagram/diagram beat pair(s) over {len(order)} beats"


@check("visual.diagram_density_cap", dimension=_DIMENSION, severity="low")
def diagram_density_cap(art):
    "Structural diagrams/charts must not dominate — capped at ~40% of distinct BEATS (+1 slack)."
    _require_visuals(art)
    clips = _require_clips(art)
    if not _field_present(clips, "modality"):
        skip("clips carry no modality field")
    # count by DISTINCT beat, not by reveal sub-clip — a diagram that builds over N reveal frames is
    # still ONE diagram beat, so sub-clips must not inflate the density.
    by_beat: dict[int, bool] = {}
    for c in clips:
        bi = c.get("beat_index")
        if isinstance(bi, int):
            by_beat[bi] = by_beat.get(bi, False) or _is_structural(c)
    n = len(by_beat) or len(clips)
    d = sum(1 for v in by_beat.values() if v) if by_beat else sum(1 for c in clips if _is_structural(c))
    allowed = _DIAGRAM_DENSITY_CAP * n + 1
    return d <= allowed, f"{d}/{n} structural beats ({d / n:.0%}); cap {_DIAGRAM_DENSITY_CAP:.0%}+1"


@check("visual.diagram_is_crisp_not_ai", dimension=_DIMENSION, severity="high")
def diagram_is_crisp_not_ai(art):
    "Diagrams/charts are mermaid/HTML (crisp), never AI images — they must carry no model_id."
    _require_visuals(art)
    clips = _require_clips(art)
    diagrams = [c for c in clips if _is_structural(c)]
    if not diagrams:
        skip("no structural diagram/chart clips")
    if not _field_present(clips, "model_id"):
        skip("clips carry no model_id field (cannot distinguish AI vs authored)")
    bad = [c.get("beat_index") for c in diagrams if str(c.get("model_id") or "").strip()]
    return not bad, f"{len(bad)} diagram/chart clip(s) carry an AI model_id (should be authored): {bad}"


@check("visual.scene_image_has_model_id", dimension=_DIMENSION, severity="medium")
def scene_image_has_model_id(art):
    "A done scene image is AI-rendered, so it must record which model produced it."
    _require_visuals(art)
    clips = _require_clips(art)
    if not _field_present(clips, "model_id"):
        skip("clips carry no model_id field")
    scenes = [c for c in clips if _is_scene(c)
              and (_status(c) in _STATUS_DONE or not _field_present(clips, "status"))]
    if not scenes:
        skip("no done scene-image clips")
    bad = [c.get("beat_index") for c in scenes if not str(c.get("model_id") or "").strip()]
    return not bad, f"{len(bad)} done scene image(s) missing model_id (beats {bad})"


@check("visual.model_id_known", dimension=_DIMENSION, severity="low")
def model_id_known(art):
    "Any recorded model_id should be a known image/video model (catches typos/garbage ids)."
    _require_visuals(art)
    clips = _require_clips(art)
    ids = {str(c.get("model_id") or "").strip() for c in clips if str(c.get("model_id") or "").strip()}
    if not ids:
        skip("no clips carry a model_id")
    bad = sorted(ids - _KNOWN_MODEL_IDS)
    return not bad, f"unknown model_id(s): {bad}"


# ── catalog long-tail: pixel-side dimensions / fill / pad (PIL + ffprobe) ─────


@check("visual.video_dims_1080p", dimension=_DIMENSION, severity="high")
def video_dims_1080p(art):
    "When a hero video exists it must render at 1920x1080 (the platform's only output resolution)."
    if not getattr(art, "video_path", None) or not art.video_info:
        skip("no video to check dimensions")
    w, h = art.video_info.get("width"), art.video_info.get("height")
    if not w or not h:
        skip("video_info has no dimensions")
    ok = (w == _VIDEO_W and h == _VIDEO_H)
    return ok, f"video {w}x{h} (need {_VIDEO_W}x{_VIDEO_H})"


@check("visual.png_min_dims", dimension=_DIMENSION, severity="low")
def png_min_dims(art):
    "Diagram/card PNGs must be at least 1600x900 (a tiny render is an unreadable strip)."
    _require_visuals(art)
    paths = _diagram_image_paths(art)
    if not paths:
        skip("no diagram/card image files identifiable by name")
    from PIL import Image
    small = []
    for p in paths:
        try:
            with Image.open(p) as img:
                w, h = img.size
        except Exception:  # noqa: BLE001 — corruption is not_corrupt's job
            continue
        if w < _DIAGRAM_MIN_W or h < _DIAGRAM_MIN_H:
            small.append(f"{p.rsplit('/', 1)[-1]}({w}x{h})")
    return not small, f"{len(small)} diagram(s) below {_DIAGRAM_MIN_W}x{_DIAGRAM_MIN_H}: {small[:3]}"


def _non_bg_fraction(img) -> float:
    """Fraction of pixels whose colour differs from the navy diagram background by > tol on any
    channel. Downsampled for speed; pure PIL, $0."""
    small = img.convert("RGB").resize((160, 90))
    px = small.load()
    w, h = small.size
    br, bg, bb = _BG_RGB
    ink = 0
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if abs(r - br) > _BG_TOL or abs(g - bg) > _BG_TOL or abs(b - bb) > _BG_TOL:
                ink += 1
    return ink / (w * h)


@check("visual.diagram_fills_frame", dimension=_DIMENSION, severity="critical")
def diagram_fills_frame(art):
    "Diagrams must FILL the frame (>20% painted) — catches the inline-block 'tiny strip' regression."
    _require_visuals(art)
    paths = _diagram_image_paths(art)
    if not paths:
        skip("no diagram/card image files identifiable by name")
    from PIL import Image
    thin = []
    worst = 1.0
    for p in paths:
        try:
            with Image.open(p) as img:
                frac = _non_bg_fraction(img)
        except Exception:  # noqa: BLE001
            continue
        worst = min(worst, frac)
        if frac < _FILL_MIN_FRAC:
            thin.append(f"{p.rsplit('/', 1)[-1]}({frac:.0%})")
    return not thin, f"{len(thin)} diagram(s) fill <{_FILL_MIN_FRAC:.0%} (worst {worst:.0%}): {thin[:3]}"


@check("visual.diagram_not_blank", dimension=_DIMENSION, severity="critical")
def diagram_not_blank(art):
    "Every diagram/card must paint at least ~1.2% non-background (near-empty-on-navy floor)."
    _require_visuals(art)
    paths = _diagram_image_paths(art)
    if not paths:
        skip("no diagram/card image files identifiable by name")
    from PIL import Image
    blank = []
    worst = 1.0
    for p in paths:
        try:
            with Image.open(p) as img:
                frac = _non_bg_fraction(img)
        except Exception:  # noqa: BLE001
            continue
        worst = min(worst, frac)
        if frac < _BLANK_FILL_MIN:
            blank.append(f"{p.rsplit('/', 1)[-1]}({frac:.1%})")
    return not blank, f"{len(blank)} near-blank diagram(s) <{_BLANK_FILL_MIN:.1%} (worst {worst:.1%}): {blank[:3]}"


@check("visual.diagram_padded_not_cropped", dimension=_DIMENSION, severity="high")
def diagram_padded_not_cropped(art):
    "A tall diagram forced to 16:9 must be pillarboxed (navy side borders), never cropped."
    _require_visuals(art)
    paths = _diagram_image_paths(art)
    if not paths:
        skip("no diagram/card image files identifiable by name")
    from PIL import Image
    br, bg, bb = _BG_RGB
    cropped = []
    assessed = 0
    for p in paths:
        try:
            with Image.open(p) as img:
                rgb = img.convert("RGB")
                w, h = rgb.size
                if not w or not h:
                    continue
                # only meaningful for frames that ARE 16:9 (the forced-aspect output)
                if abs((w / h) - _ASPECT_TARGET) / _ASPECT_TARGET > _ASPECT_TOLERANCE:
                    continue
                assessed += 1
                px = rgb.load()
                ys = range(0, h, max(1, h // 30))
                left_bg = sum(1 for y in ys
                              if abs(px[0, y][0] - br) <= _BG_TOL
                              and abs(px[0, y][1] - bg) <= _BG_TOL
                              and abs(px[0, y][2] - bb) <= _BG_TOL)
                right_bg = sum(1 for y in ys
                               if abs(px[w - 1, y][0] - br) <= _BG_TOL
                               and abs(px[w - 1, y][1] - bg) <= _BG_TOL
                               and abs(px[w - 1, y][2] - bb) <= _BG_TOL)
                n = len(list(ys))
                if n and (left_bg / n < 0.6 or right_bg / n < 0.6):
                    cropped.append(f"{p.rsplit('/', 1)[-1]}(L{left_bg}/{n} R{right_bg}/{n})")
        except Exception:  # noqa: BLE001
            continue
    if assessed == 0:
        skip("no 16:9 diagram frames to check for pillarbox padding")
    return not cropped, f"{len(cropped)}/{assessed} diagram(s) not pillarboxed (likely cropped): {cropped[:3]}"


def _video_mid_frame(art):
    """Extract one mid-point frame of the hero video to PIL via ffmpeg → PNG. $0. None on failure."""
    import subprocess
    import tempfile

    from PIL import Image
    dur = float(art.video_info.get("duration") or 0)
    if not dur:
        return None
    ts = max(0.1, dur / 2.0)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{ts:.2f}",
             "-i", art.video_path, "-frames:v", "1", "-y", tmp.name],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0:
            return None
        img = Image.open(tmp.name)
        img.load()
        return img
    except Exception:  # noqa: BLE001
        return None


@check("visual.video_no_black_bars", dimension=_DIMENSION, severity="medium")
def video_no_black_bars(art):
    "A hero video must crop-fill the frame — its outer border must not be a uniform black letterbox."
    if not getattr(art, "video_path", None) or not art.video_info:
        skip("no video to check for letterboxing")
    img = _video_mid_frame(art)
    if img is None:
        skip("could not extract a mid frame")
    rgb = img.convert("RGB")
    w, h = rgb.size
    if w < 4 or h < 4:
        skip("frame too small to sample border")
    px = rgb.load()
    def _row_black(y):
        s = range(0, w, max(1, w // 60))
        return all(max(px[x, y]) < 8 for x in s)
    def _col_black(x):
        s = range(0, h, max(1, h // 60))
        return all(max(px[x, y]) < 8 for y in s)
    top = _row_black(0) and _row_black(1)
    bottom = _row_black(h - 1) and _row_black(h - 2)
    left = _col_black(0) and _col_black(1)
    right = _col_black(w - 1) and _col_black(w - 2)
    bars = [n for n, v in (("top", top), ("bottom", bottom), ("left", left), ("right", right)) if v]
    return not bars, f"uniform-black border(s): {bars or 'none'}"


@check("visual.video_sar_1_1", dimension=_DIMENSION, severity="medium")
def video_sar_1_1(art):
    "The hero video's sample aspect ratio must be 1:1 so the player never stretches it."
    if not getattr(art, "video_path", None):
        skip("no video to probe SAR")
    import json
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-print_format", "json",
             "-show_streams", art.video_path],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            skip("ffprobe failed")
        streams = json.loads(out.stdout or "{}").get("streams", [])
    except Exception:  # noqa: BLE001
        skip("could not probe video SAR")
    if not streams:
        skip("no video stream")
    sar = str(streams[0].get("sample_aspect_ratio") or "").strip()
    if not sar or sar == "0:1":
        skip("SAR not reported (treated as square)")
    ok = sar in ("1:1",)
    return ok, f"sample_aspect_ratio={sar} (need 1:1)"


# ── named regression guard: image-model text garbling (this session's bug class) ──


@check("visual.scene_images_text_free_guard", dimension=_DIMENSION, severity="high")
def scene_images_text_free_guard(art):
    """REGRESSION GUARD (image-model text-garbling class): AI scene images must be pictorial and
    text-free — image models can't render legible text and produce garbled glyphs ('AUNCHOR').
    Real diagrams are authored mermaid/HTML, not AI images, so this guard scopes to scene-image
    files only (diagram/card files excluded) and asserts a low baked-in-text edge density. Stricter
    than text_free_heuristic, which scans ALL images indiscriminately."""
    _require_visuals(art)
    # scope to scene images: every loadable image that is NOT a diagram/card/chart file
    diagram = set(_diagram_image_paths(art))
    scene_paths = [p for p in art.image_paths if p not in diagram]
    if not scene_paths:
        skip("no scene-image files to guard (all images are authored diagrams/cards)")
    from PIL import Image, ImageFilter
    suspicious = []
    worst = 0.0
    assessed = 0
    for p in scene_paths:
        try:
            with Image.open(p) as img:
                w, h = img.size
                if w < _MIN_IMAGE_PX or h < _MIN_IMAGE_PX:
                    continue
                gray = img.convert("L")
                edges = gray.filter(ImageFilter.FIND_EDGES)
                hist = edges.histogram()
        except Exception:  # noqa: BLE001 — corruption is not_corrupt's job
            continue
        assessed += 1
        total = sum(hist) or 1
        frac = sum(hist[200:]) / total
        worst = max(worst, frac)
        if frac > _TEXT_EDGE_FRACTION_MAX:
            suspicious.append(f"{p.rsplit('/', 1)[-1]}(edge={frac:.2f})")
    if assessed == 0:
        skip("no assessable scene images")
    ok = not suspicious
    return ok, (f"{len(suspicious)}/{assessed} scene image(s) text-suspect "
                f"(max edge frac {worst:.2f}, ceiling {_TEXT_EDGE_FRACTION_MAX}): {suspicious[:3]}")
