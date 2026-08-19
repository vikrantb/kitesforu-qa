"""Invariant C must tell a CLIPPED frame edge from a bright FULL-BLEED BACKDROP.

WHY THIS EXISTS. The probe used to count BRIGHT pixels in the edge columns. Three separate
attempts at scoping the defect across the fleet were thrown away because that number is not
interpretable: a backdrop legitimately fills the margin, so "13 of 40 frames carry margin pixels"
says nothing about whether anything was cut off. The measured witness: a smooth bright backdrop
scores 61560 bright pixels in the left margin and is perfectly fine.

WHAT ACTUALLY SEPARATES THEM is vertical structure. A backdrop — however bright, however it ramps
— changes smoothly, so no two vertically adjacent pixels differ much. Content cut by the frame has
hard horizontal boundaries where the box or text row starts and stops.

The fixtures are GENERATED here rather than committed: a checked-in PNG can drift from the code
that made it, and the whole point is that the three cases are unambiguous by construction.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

np = pytest.importorskip("numpy")
PIL_Image = pytest.importorskip("PIL.Image")
PIL_ImageDraw = pytest.importorskip("PIL.ImageDraw")

_GATE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "acceptance_gate.py"

_W, _H = 1920, 1080


def _load_gate():
    spec = importlib.util.spec_from_file_location("acceptance_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _backdrop(path):
    """Bright, SMOOTH, full-bleed — touches both margins. MUST NOT flag."""
    g = np.tile(np.linspace(210, 255, _W, dtype=float), (_H, 1))
    PIL_Image.fromarray(g.astype(np.uint8)).save(path)


def _clean(path):
    """Content well inside every margin. MUST NOT flag."""
    im = PIL_Image.new("L", (_W, _H), 18)
    PIL_ImageDraw.Draw(im).rectangle([400, 380, 1500, 700], fill=235)
    im.save(path)


def _clipped_left(path):
    """A box AND its text rows running off the LEFT edge. MUST flag.

    Note the box spans the entire margin, so it is horizontally UNIFORM there — an earlier
    horizontal-gradient attempt scored this 0 and was discarded.
    """
    im = PIL_Image.new("L", (_W, _H), 18)
    dr = PIL_ImageDraw.Draw(im)
    dr.rectangle([-120, 380, 300, 700], fill=235)
    for i in range(6):
        dr.rectangle([-100, 420 + i * 40, 260, 440 + i * 40], fill=30)
    im.save(path)


@pytest.fixture()
def frames(tmp_path):
    made = {}
    for name, fn in (("backdrop", _backdrop), ("clean", _clean), ("clipped", _clipped_left)):
        p = tmp_path / f"{name}.png"
        fn(p)
        made[name] = str(p)
    return made


def _edge_clip_issues(gate, paths):
    # The gate needs >= max(2, n//3) flagged frames to raise, so feed the same frame repeatedly:
    # this test is about the per-frame DISCRIMINATION, not the aggregation threshold.
    issues = gate._pixel_invariants(paths * 6)
    return [i for i in issues if "EDGE-CLIP" in i["msg"]]


def test_a_clipped_edge_is_flagged(frames):
    gate = _load_gate()
    assert _edge_clip_issues(gate, [frames["clipped"]]), (
        "content cut by the frame edge was not flagged — this is the defect the invariant exists for"
    )


def test_a_bright_full_bleed_backdrop_is_NOT_flagged(frames):
    """The false positive that made three fleet scopes uninterpretable."""
    gate = _load_gate()
    assert not _edge_clip_issues(gate, [frames["backdrop"]]), (
        "a smooth bright backdrop was flagged as clipped content — this is the exact false "
        "positive that made the old brightness-based probe unusable"
    )


def test_clean_content_is_NOT_flagged(frames):
    gate = _load_gate()
    assert not _edge_clip_issues(gate, [frames["clean"]])


def test_the_backdrop_really_is_bright_enough_to_fool_a_brightness_test(frames):
    """PREMISE. If the backdrop fixture were dim, the test above would pass for the wrong reason
    and prove nothing. It must be the case that the OLD measure would have flagged it."""
    im = np.asarray(PIL_Image.open(frames["backdrop"]).convert("L"), dtype=float)
    old_measure = int((im[:, :3] >= 200).sum())
    assert old_measure >= 12, (
        f"backdrop fixture only scores {old_measure} on the old brightness measure; it is not a "
        f"real test of the false positive"
    )


def test_the_clipped_fixture_is_horizontally_uniform_in_the_margin(frames):
    """PREMISE. Pins WHY the discarded horizontal-gradient attempt failed, so nobody re-proposes
    it: inside the 3% margin the clipped box has no horizontal steps at all."""
    im = np.asarray(PIL_Image.open(frames["clipped"]).convert("L"), dtype=float)
    m = max(3, int(im.shape[1] * 0.03))
    h_steps = int((np.abs(np.diff(im[:, :m], axis=1)) > 28).sum())
    assert h_steps == 0, f"expected 0 horizontal steps in the margin, got {h_steps}"


# ─────────────────────────────────────────────────────────────────────────────────────────────
# MODALITY AWARENESS — the diagram rule must not run on photo beats
#
# The gate reads frames from the DELIVERED MASTER, which interleaves diagram beats with generated
# photography. The pipeline's OWN edge checker (`log_unsafe_bbox`) is called only from
# `diagram/render.py`; it never inspects a photo, because a photo legitimately bleeds to every
# edge. Running the diagram rule over photo frames made the gate flag 6 of 12 frames labelled
# clean by eye.
#
# Measured against four labelled jobs via `visual.clips[].modality`:
#   6cae642d REAL   diagram 13 · scene_image  1      131546af FALSE  scene_image 24 · diagram 1
#   c533260d REAL   diagram 14 · scene_image  3      db02c066 FALSE  scene_image 17 · diagram 3
# Photo-dominated jobs are the false positives. The pipeline already LABELS every beat, so no
# pixel heuristic is needed — two were tried and neither separated the classes.
# ─────────────────────────────────────────────────────────────────────────────────────────────

def _clips(modality, n=12, interval_ms=3000):
    """One clip per sampled frame, covering the timeline back to back."""
    return [{"start_ms": i * interval_ms, "duration_ms": interval_ms, "modality": modality}
            for i in range(n)]


def test_a_photo_beat_is_exempt_from_the_diagram_edge_rule(frames):
    gate = _load_gate()
    paths = [frames["clipped"]] * 6
    # Same pixels, same rule — only the authored label differs.
    assert gate._pixel_invariants(paths), "premise: these frames DO trip the rule unlabelled"
    issues = [i for i in gate._pixel_invariants(paths, _clips("scene_image"))
              if "EDGE-CLIP" in i["msg"]]
    assert not issues, (
        "a scene_image beat was flagged for content at the frame edge. A photo bleeds to the edge "
        "by design and the pipeline's own checker never inspects one; this is the false positive "
        "that made 6 of 12 hand-labelled-clean frames fail."
    )


def test_a_diagram_beat_is_STILL_flagged(frames):
    """The half that makes the exemption safe. If only the test above existed, deleting the rule
    entirely would satisfy it."""
    gate = _load_gate()
    issues = [i for i in gate._pixel_invariants([frames["clipped"]] * 6, _clips("diagram"))
              if "EDGE-CLIP" in i["msg"]]
    assert issues, "a diagram beat with content cut at the edge was not flagged"


def test_an_UNKNOWN_modality_is_still_checked(frames):
    """`modality is None` occurs on real clips. Reading absence as "photo" would silently disable
    the gate on exactly the jobs whose records are incomplete — the failure mode that has cost
    this codebase a 30-day outage and a month of dark image budget. Absence means UNKNOWN."""
    gate = _load_gate()
    for clips in (_clips(None), None, []):
        issues = [i for i in gate._pixel_invariants([frames["clipped"]] * 6, clips)
                  if "EDGE-CLIP" in i["msg"]]
        assert issues, f"an unlabelled beat was silently exempted (clips={clips!r})"


def test_the_denominator_is_what_was_CHECKED_not_what_was_sampled(frames):
    """A photo-heavy job must not become harder to flag.

    Six frames: two diagrams that are genuinely clipped, four photos. Dividing by the full sample
    needs `max(2, 6//3)=2` hits — which passes here by luck — but at 12 photos and 2 diagrams it
    would need 4 hits from 2 checkable frames, i.e. unflaggable. The threshold therefore divides
    by the frames the rule actually ran on.
    """
    gate = _load_gate()
    paths = [frames["clipped"]] * 2 + [frames["backdrop"]] * 10
    clips = ([{"start_ms": 0, "duration_ms": 3000, "modality": "diagram"},
              {"start_ms": 3000, "duration_ms": 3000, "modality": "diagram"}]
             + [{"start_ms": (i + 2) * 3000, "duration_ms": 3000, "modality": "scene_image"}
                for i in range(10)])
    issues = [i for i in gate._pixel_invariants(paths, clips) if "EDGE-CLIP" in i["msg"]]
    assert issues, (
        "2 clipped diagram frames among 10 exempt photos did not flag — the threshold is still "
        "dividing by the sampled count, so photo-heavy jobs get a weaker gate"
    )
    assert "/2 checked frames" in issues[0]["msg"], (
        "the message should report the CHECKED denominator so the number is interpretable; got: "
        + issues[0]["msg"]
    )
