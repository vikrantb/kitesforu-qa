"""The acceptance gate must look at EVERY frame, so a short defect anywhere in the episode is found.

`.claude/rules/02-done.md` relies on this gate because it "scores every frame across the full
duration rather than one hero frame". Its first version scored a PREFIX (floor division took the
first twelve frames of a 13..23-frame video); the fix spread twelve samples across the video. That
still left it blind to anything short: twelve samples of a 138-frame episode miss a defect that
lasts a few seconds, and the edge rule only fired when a THIRD of the samples clipped.

Measured 2026-10-11 (frame census, 9 real episodes): checking every frame instead of twelve newly
flags 2 of the 9. (The census's PASS verdicts themselves came from a venv that had drifted without
Pillow; see `test_a_gate_that_cannot_look_does_not_pass`.) The gate now inspects every frame and
names WHEN, and on which edge, content is cut.

The fixtures are generated, never committed (synthetic frames; this repository is public).
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

# Declared dependencies (pyproject: numpy, Pillow), so imported, never importorskip'd: a skip hid
# that the qa venv had drifted without Pillow, and the gate then passed jobs it had not looked at.
import numpy as np
import PIL.Image as PIL_Image
import PIL.ImageDraw as PIL_ImageDraw

_GATE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "acceptance_gate.py"
_W, _H = 960, 540


def _load_gate():
    spec = importlib.util.spec_from_file_location("acceptance_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def frames(tmp_path):
    clean = PIL_Image.new("L", (_W, _H), 18)
    PIL_ImageDraw.Draw(clean).rectangle([200, 190, 750, 350], fill=235)
    clipped = PIL_Image.new("L", (_W, _H), 18)
    dr = PIL_ImageDraw.Draw(clipped)
    dr.rectangle([-60, 190, 150, 350], fill=235)
    for i in range(6):
        dr.rectangle([-50, 210 + i * 20, 130, 220 + i * 20], fill=30)
    clipped_right = clipped.transpose(PIL_Image.FLIP_LEFT_RIGHT)
    made = {}
    for name, im in (("clean", clean), ("clipped", clipped), ("clipped_right", clipped_right)):
        p = tmp_path / f"{name}.png"
        im.save(p)
        made[name] = str(p)
    return made


def _issues(gate, paths, clips=None, tag="EDGE-CLIP RUN"):
    return [i for i in gate._pixel_invariants(paths, clips) if tag in i["msg"]]


def test_a_short_run_at_the_very_end_of_a_long_episode_is_found_with_its_time(frames):
    gate = _load_gate()
    paths = [frames["clean"]] * 238 + [frames["clipped"]] * 2
    runs = _issues(gate, paths)
    assert runs, "two clipped frames at the end of a 240-frame episode were not seen"
    # Frames 238-239 cover 714-720 s: the span runs to the END of the last frame's window.
    assert "714-720s (left)" in runs[0]["msg"], f"the run must name its time and edge; got {runs[0]['msg']!r}"
    # The rate rule alone would never have fired: 2 of 240 is far below a third.
    assert not [i for i in gate._pixel_invariants(paths) if i["msg"].startswith("EDGE-CLIP:")]


def test_two_short_runs_mid_episode_are_both_named(frames):
    """A synthetic case: frames 18-19 and 21-22 of a 138-frame episode clipped, the rest clean."""
    gate = _load_gate()
    clipped = {18, 19, 21, 22}
    paths = [frames["clipped"] if i in clipped else frames["clean"] for i in range(138)]
    runs = _issues(gate, paths)
    assert runs and "54-60s" in runs[0]["msg"] and "63-69s" in runs[0]["msg"], runs


def test_a_single_clipped_frame_is_not_a_run(frames):
    """A slide-in crossing the edge for one sampled frame is motion, not a held cut."""
    gate = _load_gate()
    paths = [frames["clean"]] * 30 + [frames["clipped"]] + [frames["clean"]] * 30
    assert not _issues(gate, paths)


def test_alternating_edges_are_a_pan_not_a_held_cut(frames):
    """Left, right, left, right: something touches an edge on every frame, but never the same
    one twice running. The first version chained these into one long 'cut'."""
    gate = _load_gate()
    paths = [frames["clean"]] * 10 + [frames["clipped"], frames["clipped_right"]] * 3 + [frames["clean"]] * 10
    assert not _issues(gate, paths)


def test_the_same_edge_held_is_a_run(frames):
    gate = _load_gate()
    paths = [frames["clean"]] * 10 + [frames["clipped_right"]] * 3 + [frames["clean"]] * 10
    runs = _issues(gate, paths)
    assert runs and "(right)" in runs[0]["msg"], runs


@pytest.mark.parametrize("footage", [
    {"modality": "scene_image"},
    {"modality": "video_hero"},
    {"modality": "motion", "ai_generated": True},
])
def test_footage_is_exempt_whatever_its_label(frames, footage):
    """A photo, an illustration or a Veo clip bleeds to the edge by design. The first version
    exempted scene_image alone, and Veo footage made 19 of 151 run frames in the census."""
    gate = _load_gate()
    paths = [frames["clean"]] * 10 + [frames["clipped"]] * 3 + [frames["clean"]] * 10
    clips = [{"start_ms": i * 3000, "duration_ms": 3000, **(footage if 10 <= i < 13 else {"modality": "diagram"})}
             for i in range(23)]
    assert not _issues(gate, paths, clips), footage


def test_a_frame_is_looked_up_at_the_middle_of_its_window(frames):
    """ffmpeg's fps=1/3 takes frame i from inside [3i, 3i+3) s (measured ~3i+1.3 s). A clip that
    covers only 1.0-3.0 s holds frame 0 and nothing else; looking frame 0 up at 0 s missed it."""
    gate = _load_gate()
    assert gate._frame_time_ms(0) == 1500 and gate._frame_time_ms(10) == 31500
    photo_then_diagram = [{"start_ms": 1000, "duration_ms": 2000, "modality": "scene_image"}] + [
        {"start_ms": 3000 * i, "duration_ms": 3000, "modality": "diagram"} for i in range(1, 6)]
    paths = [frames["clipped"]] + [frames["clean"]] * 5
    issues = gate._pixel_invariants(paths, photo_then_diagram)
    rate = [i for i in issues if i["msg"].startswith("EDGE-CLIP:")]
    assert not rate, "frame 0 is a photo beat and must be exempt"


def test_frames_are_ordered_by_number_not_by_string(tmp_path):
    gate = _load_gate()
    for n in (1, 2, 101, 999, 1000, 1001):
        (tmp_path / f"f_{n}.png").write_bytes(b"")
    names = [pathlib.Path(p).name for p in gate._frames_in_order(str(tmp_path))]
    assert names == ["f_1.png", "f_2.png", "f_101.png", "f_999.png", "f_1000.png", "f_1001.png"], names


def test_runs_are_maximal_share_an_edge_and_respect_the_minimum():
    gate = _load_gate()
    L, R = frozenset({"left"}), frozenset({"right"})
    LR = frozenset({"left", "right"})
    assert gate._runs([(3, L), (4, L), (9, LR), (10, R), (11, R), (20, L)]) == [(3, 4, L), (9, 11, R)]
    assert gate._runs([(5, L), (6, R)]) == []
    assert gate._runs([(5, L)]) == []
    assert gate._runs([]) == []


def test_no_frames_is_a_blocker_not_a_pass(frames, tmp_path):
    gate = _load_gate()
    assert gate._pixel_invariants([])[0]["sev"] == "BLOCKER"
    junk = tmp_path / "f_1.png"
    junk.write_bytes(b"not a png")
    issues = gate._pixel_invariants([str(junk)] * 3)
    assert issues and issues[0]["sev"] == "BLOCKER" and "NO FRAMES INSPECTED" in issues[0]["msg"], issues


def test_a_gate_that_cannot_look_does_not_pass(frames, monkeypatch):
    """Without Pillow the invariants used to return [] and the verdict read PASS_DETERMINISTIC."""
    import builtins

    gate = _load_gate()
    real_import = builtins.__import__

    def _no_pil(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("No module named 'PIL'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_pil)
    issues = gate._pixel_invariants([frames["clean"]] * 3)
    assert issues and issues[0]["sev"] == "BLOCKER", issues
    assert "PIXEL INVARIANTS DID NOT RUN" in issues[0]["msg"]
