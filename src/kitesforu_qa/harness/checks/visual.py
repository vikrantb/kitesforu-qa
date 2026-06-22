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

from ..check import check, skip

# ── named thresholds (tunable; the SSOT for what "good" means visually) ──────
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
