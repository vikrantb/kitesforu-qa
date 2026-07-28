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

# LONG-FORM CORRECTNESS Part D, Layer 2 — the OCR visual-fit check (founder's cut/overflow class:
# title/labels/subtitle chopped at the frame edge, e.g. by the video_assembler push-zoom). $0 CPU
# (ffmpeg seek + pytesseract), no LLM. Samples the LAST ~500ms of each structural-diagram beat — the
# worst-case post-zoom moment — because a progressive push-zoom crops MORE as a hold continues.
_OCR_CONF_MIN = 40              # pytesseract word confidence floor (0-100); below this is noise
_EDGE_CROP_PX = 3               # a word within this many px of the frame edge is LITERALLY clipped
_TEXT_SAFE_MARGIN_FRAC = 0.08   # the softer SMPTE-style advisory margin (matches workers Layer 1's
                                 # DEFAULT_MARGIN_FRAC by deliberate convention, not coupling)
_WORST_CASE_TAIL_S = 0.5        # "last ~500ms" of a beat's on-screen window
_MAX_DIAGRAM_BEATS_SAMPLED = 12  # bounded (orchestra-oe): evenly subsampled, never unbounded

# Diagram-weave adjacency caps — MIRROR the workers' modality_selector.weave() invariant
# (stages/visuals/modality_selector.py: _MAX_ADJACENT=1 fiction, _MAX_ADJACENT_NONFICTION=2). The
# producer deliberately allows a 2-long diagram run for non-fiction/educational ("a short diagram run
# reads fine"), so the check must allow the same longest-run length instead of forbidding any pair.
_MAX_ADJACENT_FICTION = 1
_MAX_ADJACENT_NONFICTION = 2
# The genres the workers treat as fiction (RESEARCH_FICTION_GENRES, prompt_generator/service.py) —
# everything else (educational/explainer/tech_review/news/...) is non-fiction. Normalized lower, no
# separators, so "science_fiction"/"sci-fi" all collapse to a member.
_FICTION_GENRES = frozenset({
    "romance", "thriller", "horror", "comedy", "storytelling", "fantasy", "scifi",
    "sciencefiction", "mystery", "bedtime", "bedtimestory", "adventure", "drama",
    "fairytale", "fable", "mythology", "suspense", "paranormal", "fiction",
    "truecrime", "noir",
})


def _is_fiction_genre(art) -> bool:
    """True if this job's genre is one the producer weaves as fiction (tight ≤1 adjacency).
    Non-fiction/educational (the default) gets the looser ≤2 run the producer allows."""
    g = "".join(ch for ch in (art.genre or "").strip().lower() if ch.isalnum())
    return g in _FICTION_GENRES


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


def _basename(s: str) -> str:
    """The last path segment of a uri/path, lowercased (the clip↔file correlation key)."""
    return s.rsplit("/", 1)[-1].strip().lower()


def _diagram_image_paths(art) -> list[str]:
    """Local image files that are diagram/card renders (the catalog's fill/blank/pad targets).

    PREFERRED mapping (modality, drift-free): correlate each structural clip
    (``modality in {'diagram','chart'}``) to its downloaded file by ``asset_uri`` basename — real
    renders are content-hash-named (``d97b2026.png``), so a filename-token heuristic never matches
    them and these four pixel checks would silently skip on every real job. When the operator's
    ``image_paths`` basenames line up with the clips' asset_uri basenames, this finds the real diagram
    files regardless of naming.

    FALLBACK (filename tokens): if no structural clip's basename maps onto a downloaded file (e.g. the
    operator named the files differently and didn't tag them, or the doc carries no clips), fall back
    to the legacy token heuristic (``diagram``/``card``/``chart``/``btree`` in the filename) so a
    modality-tagged operator path still works. When neither yields anything the consuming check skips
    with a clear note rather than treating the diagram dimension as silently N/A."""
    # PREFERRED: modality → asset_uri basename → matching downloaded file.
    diagram_basenames = {
        _basename(_uri(c)) for c in _clips(art) if _is_structural(c) and _uri(c)
    }
    if diagram_basenames:
        by_modality = [p for p in art.image_paths if _basename(p) in diagram_basenames]
        if by_modality:
            return by_modality
    # FALLBACK: legacy filename-token heuristic (modality-tagged operator paths / no correlatable uri).
    out = []
    for p in art.image_paths:
        name = _basename(p)
        if "diagram" in name or "card" in name or "chart" in name or "btree" in name:
            out.append(p)
    return out


def _require_diagram_paths(art) -> list[str]:
    """The diagram/card image files for the pixel fill/blank/pad/dims checks, or a CLEAR skip.

    F3 fidelity: real renders are content-hash-named, so ``_diagram_image_paths`` maps them by clip
    MODALITY (``diagram``/``chart``) → ``asset_uri`` basename, falling back to the filename-token
    heuristic. When neither correlates a downloaded file we skip with an explicit note — NOT the old
    "identifiable by name" wording, which read like the dimension was N/A and let these four checks
    (incl. the critical fill/blank guards) silently no-op on every real job.

    LOADER CONTRACT: the operator/loader that populates ``art.image_paths`` should download each
    structural clip's asset under its ``asset_uri`` basename (so the modality mapping correlates) OR
    name diagram files with a diagram/card/chart/btree token (so the fallback matches). Untagged,
    non-correlatable diagram images cannot be pixel-checked and will surface this skip."""
    paths = _diagram_image_paths(art)
    if not paths:
        skip("no modality-tagged diagram image available "
             "(no structural clip's asset_uri basename matched a downloaded file, and no "
             "diagram/card/chart/btree filename token — loader should tag diagram images)")
    return paths


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

    Reads clips through the SAME ``_clips()`` helper every other visual check uses (which resolves the
    nested ``doc['visual']['clips']`` shape real jobs use, not only the top-level ``visual_clips`` that
    is None on real jobs) — otherwise this always fell through to ``len(beat_map)`` and never saw the
    real rendered-clip count."""
    clips = _clips(art)
    with_asset = sum(1 for c in clips if str(c.get("asset_uri") or "").strip())
    if with_asset:
        return with_asset
    if clips:
        return len(clips)
    return len(art.beat_map)


# ── checks ───────────────────────────────────────────────────────────────────


@check("visual.images_present", dimension="visual-images", severity="high")
def images_present(art):
    "A job that opted into visuals must have at least one rendered image (doc-side, asset-aware)."
    _require_visuals(art)
    # DOC-SIDE coverage via the nested-aware _clips() helper (the same one clip_per_beat uses → real
    # jobs nest clips at doc['visual']['clips'], NOT the top-level doc['visual_clips']=0 that the old
    # evidence mis-reported). This is the load-bearing assertion: at least one clip carries a real
    # asset, regardless of whether the offline scorecard downloaded the pixels.
    clips = _clips(art)
    with_asset = sum(1 for c in clips if _uri(c) and _status(c) in _STATUS_DONE) or \
        sum(1 for c in clips if _uri(c))
    # Offline runs never download images (image_paths is structurally empty — no loader fills it from
    # asset_uri). Skip cleanly rather than false-fail: pixel presence is owned by not_corrupt/not_blank
    # when images ARE downloaded; doc-side coverage is asserted here + by visual.clip_per_beat.
    if not art.image_paths:
        skip(f"no images downloaded (offline scorecard); doc-side coverage asserted "
             f"({with_asset} clip(s) with asset_uri of {len(clips)} clips; "
             f"doc-side coverage owned by visual.clip_per_beat)")
    if not clips:
        # Operator passed raw images with no doc clips to assert against → fall back to the original
        # image-presence signal (the images themselves are the evidence).
        return len(art.image_paths) > 0, f"{len(art.image_paths)} image files (no doc clips)"
    return with_asset > 0, (f"{len(art.image_paths)} image files; "
                            f"{with_asset}/{len(clips)} clips carry an asset_uri")


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
    # ``expected`` reads clips through _clips() (nested-aware → the real 18 on doc['visual']['clips'],
    # not the top-level doc['visual_clips']=0 that mis-reported the 4d1700ee contradiction).
    expected = _expected_image_count(art)
    if expected <= 0:
        skip("no expected visual count derivable (no visual_clips / beat_map)")
    got = len(art.image_paths)
    # Asset-absent guard: offline scorecards never download images (image_paths structurally empty),
    # so a "0 got vs N expected" comparison here is comparing two DIFFERENT planes (local files vs doc
    # clips) and false-fails. Skip cleanly — doc-side completeness is owned by visual.clip_per_beat;
    # this check's got-vs-expected only catches a "downloaded-but-renderer-dropped" gap, which needs
    # the pixels present.
    if got == 0:
        skip(f"no images downloaded (offline run); {expected} clips carry assets doc-side "
             f"(coverage owned by visual.clip_per_beat)")
    # tolerate ±1 (diagram/stub clips, a single redelivery in flight); a >1 gap is a real drop.
    ok = abs(got - expected) <= 1
    score = min(1.0, got / expected) if expected else 1.0
    return ok, score, f"{got} images vs {expected} expected (visual clips)"


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
    "Visuals must weave: structural-diagram beats may not run longer than the genre's cap (a wall "
    "of charts). Mirrors the producer's modality_selector.weave() — fiction ≤1, non-fiction ≤2."
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
    # longest run of consecutive structural beats (NOT a count of adjacent pairs) — this is the
    # quantity the producer caps. A 2-long run for educational is on-spec; a 3-long run is a wall.
    longest = cur = 0
    for v in order:
        cur = cur + 1 if v else 0
        longest = max(longest, cur)
    max_adjacent = _MAX_ADJACENT_FICTION if _is_fiction_genre(art) else _MAX_ADJACENT_NONFICTION
    kind = "fiction" if _is_fiction_genre(art) else "non-fiction"
    return longest <= max_adjacent, (
        f"longest structural-diagram run {longest} beats over {len(order)} beats "
        f"(cap {max_adjacent} for {kind})")


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
    paths = _require_diagram_paths(art)
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
    paths = _require_diagram_paths(art)
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
    paths = _require_diagram_paths(art)
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
    paths = _require_diagram_paths(art)
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
                px = rgb.load()
                if px is None:
                    continue  # no pixel access (decode quirk) — can't sample borders, skip frame
                assessed += 1
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
    if px is None:
        skip("could not access frame pixels to sample border")
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


# ── named regression guard: cut/overflow text-edge-crop (LONG-FORM CORRECTNESS Part D, Layer 2) ──
#
# The MEASURED-QUALITY-ENGINE half of the two-layer gate: workers Layer 1 (render/safe_area_verify.py,
# safe_area_bbox_phase + verify_text_safe_margin) catches an overflow at RENDER time, $0, no OCR, but
# only sees the deterministic diagram/card render — not what the final MUXED VIDEO looks like after
# video_assembler's push-zoom crops a still progressively over its hold. This layer DETECTS that class
# on the delivered artifact: it OCRs the actual rendered pixels of the LAST ~500ms of each structural
# (diagram/chart) beat's on-screen window — the worst-case post-zoom moment — and flags any confident
# word that touches the frame edge. This is what turns the founder's exact bug (title/labels/subtitle
# chopped at the frame edge) into something the quality engine SEES and scores, per the
# artifact-verification.md rule ("ffmpeg-extract frames and OCR them") — previously written but never
# automated as a gate.


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


def _structural_clip_spans(art) -> list[tuple[int, float, float]]:
    """``[(beat_index, start_s, end_s)]`` for STRUCTURAL (diagram/chart) clips with a usable span,
    ONE (widest) span per beat — reveal sub-clips share a ``beat_index``, so a diagram that builds
    over N reveal frames is still ONE on-screen window, from its first reveal's start to its last
    reveal's end. Sorted by beat_index (deterministic sampling order)."""
    by_beat: dict[int, list[float]] = {}
    for c in _clips(art):
        if not _is_structural(c):
            continue
        bi = c.get("beat_index")
        if not isinstance(bi, int):
            continue
        cs, ce = _clip_start_s(c), _clip_end_s(c)
        if cs is None or ce is None or ce <= cs:
            continue
        if bi not in by_beat:
            by_beat[bi] = [cs, ce]
        else:
            by_beat[bi][0] = min(by_beat[bi][0], cs)
            by_beat[bi][1] = max(by_beat[bi][1], ce)
    return sorted((bi, lo, hi) for bi, (lo, hi) in by_beat.items())


def _extract_frame_at(video_path: str, at_s: float):
    """ONE frame at ``at_s`` via ffmpeg seek + single-frame extract → ``PIL.Image | None``. Mirrors
    ``video_sync._frame``/``visual._video_mid_frame``'s established $0 ffmpeg-extract pattern.
    Fail-open: any ffmpeg/decode failure returns ``None`` so the caller skips just THIS beat, never
    the whole check."""
    import os
    import subprocess
    import tempfile

    from PIL import Image
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, at_s):.3f}",
             "-i", video_path, "-frames:v", "1", "-y", tmp_path],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0 or not os.path.getsize(tmp_path):
            return None
        img = Image.open(tmp_path)
        img.load()
        return img
    except Exception:  # noqa: BLE001 — extraction is best-effort; the caller fails-open on None
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _diagram_worst_case_frames(art) -> list[tuple[int, Any]]:
    """``[(beat_index, PIL.Image)]`` for up to :data:`_MAX_DIAGRAM_BEATS_SAMPLED` structural-diagram
    beats, each sampled at its WORST-CASE moment — the last :data:`_WORST_CASE_TAIL_S` of its
    on-screen window (a progressive push-zoom crops MORE the longer a still holds, so the tail is
    where a cut/overflow is most likely to have become visible). Evenly subsampled across ALL beats
    (not just the first N) so a long episode's check isn't biased toward its opening beats — bounded,
    orchestra-oe. $0 CPU (ffmpeg + PIL), no LLM."""
    spans = _structural_clip_spans(art)
    if not spans:
        return []
    if len(spans) > _MAX_DIAGRAM_BEATS_SAMPLED:
        step = len(spans) / _MAX_DIAGRAM_BEATS_SAMPLED
        spans = [spans[int(i * step)] for i in range(_MAX_DIAGRAM_BEATS_SAMPLED)]
    out: list[tuple[int, Any]] = []
    for bi, lo, hi in spans:
        at = max(lo, hi - _WORST_CASE_TAIL_S)
        img = _extract_frame_at(art.video_path, at)
        if img is not None:
            out.append((bi, img))
    return out


def _ocr_words(img) -> list[tuple[str, int, int, int, int]]:
    """``[(text, x, y, w, h)]`` for OCR'd words with confidence >= :data:`_OCR_CONF_MIN`. Raises if
    pytesseract / the tesseract-ocr binary is unavailable — callers catch that PER-FRAME and treat an
    all-frames failure as ``skip()`` (fail-open: no OCR available must never FAIL the gate)."""
    import pytesseract

    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    texts = data.get("text") or []
    out: list[tuple[str, int, int, int, int]] = []
    for i in range(len(texts)):
        word = str(texts[i]).strip()
        if not word:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        if conf < _OCR_CONF_MIN:
            continue
        try:
            x, y = int(data["left"][i]), int(data["top"][i])
            ww, hh = int(data["width"][i]), int(data["height"][i])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        out.append((word, x, y, ww, hh))
    return out


@check("visual.text_not_edge_cropped", dimension=_DIMENSION, severity="critical")
def text_not_edge_cropped(art):
    """CRITICAL: OCR'd text on a rendered diagram/chart beat must not touch the frame edge — the
    founder's cut/overflow class (title/labels/subtitle chopped, e.g. by a downstream push-zoom).
    Samples the LAST ~500ms of each structural-diagram beat's on-screen window (worst-case post-zoom).
    """
    _require_visuals(art)
    if not getattr(art, "video_path", None):
        skip("no video to frame-extract")
    frames = _diagram_worst_case_frames(art)
    if not frames:
        skip("no structural-diagram clip with a usable on-screen span")
    clipped: list[str] = []
    assessed = 0
    for bi, img in frames:
        w, h = img.size
        if w <= 0 or h <= 0:
            continue
        try:
            words = _ocr_words(img)
        except Exception as exc:  # noqa: BLE001 — no OCR on THIS frame; other frames still assessed
            _ = exc
            continue
        assessed += 1
        for word, x, y, ww, hh in words:
            if (x <= _EDGE_CROP_PX or y <= _EDGE_CROP_PX
                    or (x + ww) >= (w - _EDGE_CROP_PX) or (y + hh) >= (h - _EDGE_CROP_PX)):
                clipped.append(f"beat {bi}: {word!r} touches the frame edge")
    if assessed == 0:
        skip("pytesseract/tesseract-ocr unavailable (or OCR failed on every sampled frame)")
    ok = not clipped
    return ok, (f"{len(clipped)} edge-touching word(s) over {assessed} beat(s) sampled "
                f"(±{_EDGE_CROP_PX}px floor): {clipped[:5]}")


@check("visual.text_in_safe_area", dimension=_DIMENSION, severity="low")
def text_in_safe_area(art):
    """ADVISORY: OCR'd text on a rendered diagram/chart beat should stay within an
    :data:`_TEXT_SAFE_MARGIN_FRAC` safe-area margin — the softer SMPTE-style companion to the
    critical edge-touch guard above (catches a title that's technically on-frame but crowding the
    edge, before it becomes a hard crop on the next zoom-cap regression)."""
    _require_visuals(art)
    if not getattr(art, "video_path", None):
        skip("no video to frame-extract")
    frames = _diagram_worst_case_frames(art)
    if not frames:
        skip("no structural-diagram clip with a usable on-screen span")
    outside: list[str] = []
    assessed = 0
    for bi, img in frames:
        w, h = img.size
        if w <= 0 or h <= 0:
            continue
        try:
            words = _ocr_words(img)
        except Exception as exc:  # noqa: BLE001 — no OCR on THIS frame; other frames still assessed
            _ = exc
            continue
        assessed += 1
        lo_x, hi_x = w * _TEXT_SAFE_MARGIN_FRAC, w * (1.0 - _TEXT_SAFE_MARGIN_FRAC)
        lo_y, hi_y = h * _TEXT_SAFE_MARGIN_FRAC, h * (1.0 - _TEXT_SAFE_MARGIN_FRAC)
        for word, x, y, ww, hh in words:
            if x < lo_x or y < lo_y or (x + ww) > hi_x or (y + hh) > hi_y:
                outside.append(f"beat {bi}: {word!r}")
    if assessed == 0:
        skip("pytesseract/tesseract-ocr unavailable (or OCR failed on every sampled frame)")
    ok = not outside
    return ok, (f"{len(outside)} word(s) outside the {_TEXT_SAFE_MARGIN_FRAC:.0%} safe area over "
                f"{assessed} beat(s) sampled: {outside[:5]}")


# ── PICTORIAL SHARE — does a "diagram" DEPICT, or is it type? (stroke-based) ──────
# Founder, 2026-07-27: "pass means its not just if things just work, check a couple
# quality as well". Counting DISTINCT assets is not enough — twelve different TEXT
# CARDS are twelve distinct assets and still a wall of typography.
#
# ⚠️ THE FIRST ATTEMPT AT THIS CHECK WAS WRONG AND WAS REVERTED (#80 -> #81). It scored
# COLOUR VARIETY, assuming "card = flat gradient, figure = rich". Our renderers are the
# OPPOSITE: the card renderer paints a purple gradient + watermark leaf (many colours),
# the diagram renderer uses a flat studio palette (few). It measured gradient smoothness
# and was inverted in BOTH directions — its top-scoring "depiction" was narration set in
# type; its bottom-scoring "typography" was a real flowchart. Do NOT reintroduce a
# colour-variety gate.
#
# What actually separates the two is STROKE GEOMETRY, which no palette choice affects: a
# box border or an arrow is a LONG CONTINUOUS run of ink across a row; a glyph never is.
# "Ink" is measured against each frame's OWN modal background colour, so the check is
# palette-independent by construction — the exact property the reverted version lacked.
#
# CALIBRATED on 15 hand-labelled real renders from job 38e591f2 (each one VIEWED):
#     typography  0.042 - 0.189   (11 frames, incl. a bold full-width headline)
#     depictions  0.589 - 0.856   (4 frames: wired flowcharts)
# The gap between 0.189 and 0.589 is empty; the threshold sits in the middle of it.
# A photographic scene render scores HIGH (most of the frame differs from its modal
# colour) — correct, a photo depicts. A near-blank frame scores low; blankness is
# already covered by ``visual.diagram_not_blank``.
_PICTORIAL_MIN_STROKE = 0.35
_PICTORIAL_MIN_SHARE = 0.50
_STROKE_INK_TOL = 40


def _max_ink_run_frac(img) -> float:
    """Longest contiguous ink run in EITHER axis, as a fraction of that axis. Pure PIL+numpy.

    BOTH axes, and that is load-bearing (found by looking at a misgraded frame, 2026-07-28).
    A horizontal-only version was calibrated on 9:16 born-shorts, where a box border spans
    most of the width. On a 16:9 EPISODE the same diagram is proportionally narrower, so a
    real sequence diagram (tele -> model -> alert -> play -> contain, labelled arrows)
    scored 0.178 horizontally and was called typography — while its lifelines ran 0.517 of
    the HEIGHT. Scoring max(h, v) classifies it correctly and leaves every portrait case
    unchanged (typography 0.058/0.189, flowchart 0.589 either way).
    """
    import numpy as np

    small = img.convert("RGB").resize((360, 640))
    a = np.asarray(small).astype(int)
    vals, counts = np.unique(a.reshape(-1, 3), axis=0, return_counts=True)
    bg = vals[counts.argmax()]
    ink = np.abs(a - bg).max(axis=2) > _STROKE_INK_TOL

    def _longest(mask) -> int:
        best = 0
        for row in mask:
            edges = np.flatnonzero(
                np.diff(np.concatenate(([0], row.view(np.int8), [0])))
            )
            if edges.size:
                runs = edges[1::2] - edges[0::2]
                if runs.size:
                    best = max(best, int(runs.max()))
        return best

    h, w = ink.shape
    return max(_longest(ink) / float(w or 1), _longest(ink.T) / float(h or 1))


@check("visual.pictorial_share", dimension=_DIMENSION, severity="high")
def pictorial_share(art):
    "A majority of structural visuals must DEPICT something, not just set text."
    _require_visuals(art)
    paths = _require_diagram_paths(art)
    from PIL import Image
    typography: list[str] = []
    depicting = 0
    assessed = 0
    for p in paths:
        try:
            with Image.open(p) as img:
                stroke = _max_ink_run_frac(img)
        except Exception:  # noqa: BLE001 — unreadable file; other assets still assessed
            continue
        assessed += 1
        if stroke >= _PICTORIAL_MIN_STROKE:
            depicting += 1
        else:
            typography.append(f"{p.rsplit('/', 1)[-1]}({stroke:.2f})")
    if assessed == 0:
        skip("no readable structural renders to assess")
    share = depicting / assessed
    return share >= _PICTORIAL_MIN_SHARE, (
        f"{depicting}/{assessed} structural visuals depict ({share:.0%}; "
        f"want >={_PICTORIAL_MIN_SHARE:.0%}) — typography: {typography[:3]}"
    )


# ── CADENCE — is anything HAPPENING, or is one frame held forever? ───────────────
# Founder, 2026-07-28, on job eaf99101: "the visuals were horrible, a boring screen
# and same image dancing. havent i told u i need to see a new fresh beautiful image
# every 2 seconds or zoom to some other part. see there has to be always something
# happening."
#
# THIS AXIS WAS COMPLETELY UNMEASURED. The battery scored WHAT an image is (pictorial
# share, legibility, fill, safe-area) and never whether it CHANGES. So a job could pass
# every visual check while holding ONE still frame for 67 SECONDS — which is exactly
# what eaf99101 delivered:
#     mean hold 7.58s · median 4.95s · MAX 67.39s · 26 of 48 clips over 4s
#     rendered_motion_clips 0 · render_modes {still: 48} · 31 of 48 with NO motion_preset
# The only alarm was the founder watching it. That is the failure this check ends.
#
# Two independent ways to be boring, so two signals:
#   (a) DWELL — a single asset holding the frame far past the ~2s target.
#   (b) STILLNESS — nothing moving: no motion clips AND no Ken Burns preset, so even a
#       long hold has no drift to carry it.
# Thresholds are deliberately LENIENT vs the founder's 2s ask: this is a regression
# alarm, not a style gate. A job that trips these is broken, not merely unambitious.
_CADENCE_MAX_HOLD_S = 12.0        # any single clip longer than this is a dead frame
_CADENCE_MEAN_HOLD_S = 9.0        # sustained slowness across the whole video
# NOTE: "nothing MOVES" is deliberately NOT scored here — it is a different axis and it
# belongs to ``visual.motion_not_silently_dead`` below, which carries the min-clip guard
# needed to tell a still-by-design artifact from a dead feature. One check, one axis.


def _clip_seconds(c: dict[str, Any]) -> float:
    for k in ("duration_ms", "duration_millis"):
        try:
            v = float(c.get(k) or 0)
            if v > 0:
                return v / 1000.0
        except (TypeError, ValueError):
            continue
    return 0.0


def _clip_moves(c: dict[str, Any]) -> bool:
    """A clip is MOVING if it is a video/motion render, or carries a Ken Burns preset."""
    if _render_mode(c) in _RENDER_MODE_VIDEO:
        return True
    preset = str(c.get("motion_preset") or "").strip().lower()
    return bool(preset) and preset not in {"none", "static", "hold"}


@check("visual.cadence", dimension=_DIMENSION, severity="high")
def cadence(art):
    "Something must keep HAPPENING — no dead frames, and the visuals must move."
    clips = _require_clips(art)
    durs = [_clip_seconds(c) for c in clips]
    durs = [d for d in durs if d > 0]
    if not durs:
        skip("no clip durations on this artifact")
    longest = max(durs)
    mean_hold = sum(durs) / len(durs)
    moving = sum(1 for c in clips if _clip_moves(c))
    problems: list[str] = []
    if longest > _CADENCE_MAX_HOLD_S:
        problems.append(f"dead frame {longest:.1f}s (max {_CADENCE_MAX_HOLD_S:.0f}s)")
    if mean_hold > _CADENCE_MEAN_HOLD_S:
        problems.append(f"mean hold {mean_hold:.1f}s (max {_CADENCE_MEAN_HOLD_S:.0f}s)")
    return not problems, (
        f"mean {mean_hold:.1f}s · median {sorted(durs)[len(durs)//2]:.1f}s · "
        f"max {longest:.1f}s · moving {moving}/{len(clips)}"
        + (f" — {'; '.join(problems)}" if problems else "")
    )


# ── FLAG-ON-BUT-ZERO-OUTPUT — the silent feature death class ─────────────────────
# Founder, 2026-07-28: "dont keep repeating same mistakes again and again. everytime
# you encounter an issue create a recurring mechanism so that next time the validation
# and also the fix can be learnt."
#
# THE CLASS: a feature flag reads ON, the code path is live, and the delivered artifact
# contains ZERO of what the feature produces. Nothing fails, nothing logs an error, and
# the only alarm is the founder watching a bad video. It has now happened twice to the
# SAME feature — motion died fleet-wide on 2026-07-23, and on job eaf99101
# (2026-07-28) ENABLE_STORY_VISUALS_MOTION / MOTION_AUTHOR / MOTION_CRAFT / GENRE_MOTION
# were ALL true while `rendered_motion_clips` was 0 and every one of 48 clips was a still.
#
# A counter that a live feature can never legitimately leave at zero IS the alarm. This
# check reads the counters the pipeline already writes, so it costs nothing and cannot
# drift from what shipped.
#
# Scoped honestly: it fires only when the artifact ALSO looks like motion was expected
# (multiple clips, none of them moving). A genuinely still-by-design artifact with one
# clip is not a regression.
_ZERO_OUTPUT_MIN_CLIPS = 6


@check("visual.motion_not_silently_dead", dimension=_DIMENSION, severity="high")
def motion_not_silently_dead(art):
    "A live motion feature must not deliver zero motion across a whole video."
    clips = _require_clips(art)
    if len(clips) < _ZERO_OUTPUT_MIN_CLIPS:
        skip(f"only {len(clips)} clip(s) — too few to call motion dead")
    moving = [c for c in clips if _clip_moves(c)]
    if moving:
        return True, f"{len(moving)}/{len(clips)} clips move — motion is alive"
    # Zero moving clips across a multi-clip video. Report the counters so the reader
    # can tell "feature off" from "feature on and broken" without another query.
    doc = getattr(art, "doc", None) or {}
    v = doc.get("visual") if isinstance(doc, dict) else None
    v = v if isinstance(v, dict) else {}
    counters = {
        k: v.get(k)
        for k in ("rendered_motion_clips", "rendered_local_motion_clips",
                  "rendered_paid_images")
        if k in v
    }
    return False, (
        f"ZERO of {len(clips)} clips move (no motion render, no Ken Burns preset). "
        f"Pipeline counters: {counters or 'absent'}. If the motion flags are ON this is "
        f"the silent-feature-death class, not a config choice — check the flag-to-effect "
        f"path, not the flag."
    )


# ── VISUAL MONOTONY — "the same image dancing" ───────────────────────────────────
# Founder, 2026-07-27, on job eaf99101: "the visuals were horrible, a boring screen and
# same image dancing. havent i told u i need to see a new fresh beautiful image every 2
# seconds or zoom to some other part."
#
# WHY DURATION ALONE (visual.cadence, above) CANNOT SEE THIS. eaf99101's 67-second beat WAS
# correctly re-cut into 8 sub-windows of ~8.4s each. Every per-clip duration therefore looks
# healthy. The problem is that all 8 sub-windows show THE SAME PICTURE. Cadence measures how
# long a CLIP lasts; monotony measures how long an IMAGE lasts. A job can pass cadence and
# still be one diagram for two minutes.
#
# MEASURED FLEET BASELINE (2026-07-27, n=85 completed jobs with >=5 clips and >=30s runtime,
# counting ONLY clips that actually carry a picture):
#     median top-1 asset share = 49% of runtime
#     median top-3 asset share = 95%
#     median distinct assets/min = 3.2
#     8+ jobs had top-1 share = 100% -- ONE image for the entire video
#     (6f7a697b 68s, dbcf37b8 77s, 831123a1 52s -- each exactly ONE real asset)
# A separate 3 jobs are worse still and are NOT monotony: every clip failed with
# skipped_no_asset, yet the job is `completed` with visuals_quality 40 and zero delivered
# pictures (a14129ec). Keying on `str(hash or uri)` would map those to the string "None" and
# report a tidy "1 unique image" -- which is why _asset_key returns "" and this check skips
# them outright. Never let "no images" masquerade as "one image".
# eaf99101 itself measured top-1 = 28%, 29 unique, 4.8/min -- i.e. BETTER than the median job.
# The founder complained about an above-average episode; the fleet is worse.
#
# THRESHOLDS ARE DELIBERATELY NOT SET FROM THAT DISTRIBUTION. Calibrating to the median would
# bless the defect -- half the fleet would "pass" while showing one image for half the runtime.
# They are set from the FOUNDER'S STANDARD, loosened to a floor a reasonable episode clears:
# his stated want is a fresh image every ~2s (30/min); these fire only at genuine monotony.
# Expect most CURRENT jobs to fail. That is the correct, actionable signal, not a mis-set gate.
_MONOTONY_TOP1_MAX = 0.25    # no single image may own more than a quarter of the runtime
_MONOTONY_TOP3_MAX = 0.60    # nor three images more than 60%
_MONOTONY_MIN_PER_MIN = 6.0  # at least one fresh image every ~10s on average


def _asset_key(c: dict[str, Any]) -> str:
    """Identity of the PICTURE a clip shows. content_hash is authoritative (two clips rendering
    identical specs dedupe to the same hash and therefore the same pixels); asset_uri is the
    fallback for clips that predate hashing."""
    return str(c.get("content_hash") or c.get("asset_uri") or "")


@check("visual.monotony", dimension=_DIMENSION, severity="high")
def monotony(art):
    "No small set of images may own the runtime — the 'same image dancing' defect."
    clips = _require_clips(art)
    secs: dict[str, float] = {}
    for c in clips:
        key = _asset_key(c)
        if not key:
            continue
        secs[key] = secs.get(key, 0.0) + _clip_seconds(c)
    total = sum(secs.values())
    if total <= 0 or not secs:
        skip("no per-clip durations or asset identities on this artifact")
    ranked = sorted(secs.values(), reverse=True)
    top1 = ranked[0] / total
    top3 = sum(ranked[:3]) / total
    per_min = len(secs) / (total / 60.0)

    problems: list[str] = []
    if top1 > _MONOTONY_TOP1_MAX:
        problems.append(f"one image owns {top1:.0%} of runtime (max {_MONOTONY_TOP1_MAX:.0%})")
    if top3 > _MONOTONY_TOP3_MAX and len(secs) > 3:
        problems.append(f"top-3 own {top3:.0%} (max {_MONOTONY_TOP3_MAX:.0%})")
    if per_min < _MONOTONY_MIN_PER_MIN:
        problems.append(f"{per_min:.1f} distinct images/min (min {_MONOTONY_MIN_PER_MIN:.0f})")

    return not problems, (
        f"{len(secs)} distinct images over {total:.0f}s · {per_min:.1f}/min · "
        f"top-1 {top1:.0%} · top-3 {top3:.0%}"
        + (f" — {'; '.join(problems)}" if problems else "")
    )
