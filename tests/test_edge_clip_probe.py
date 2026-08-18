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
