"""The acceptance gate must look at EVERY frame, so a short defect anywhere in the episode is found.

`.claude/rules/02-done.md` relies on this gate because it "scores every frame across the full
duration rather than one hero frame". Its first version scored a PREFIX (floor division took the
first twelve frames of a 13..23-frame video); the fix spread twelve samples across the video. That
still left it blind to anything short: twelve samples of a 138-frame episode miss a defect that
lasts a few seconds, and the edge rule only fired when a THIRD of the samples clipped.

Measured 2026-10-11 (frame census, job 04c014a2, 138 frames): kinetic text cut at the frame edge
at 54-57 s and 63-66 s, gate verdict PASS_DETERMINISTIC. The first adversary to read the frames
saw it at once. The gate now inspects every frame and names WHEN content is cut.

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
    made = {}
    for name, im in (("clean", clean), ("clipped", clipped)):
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
    assert "714-717s" in runs[0]["msg"], f"the run must name its time; got {runs[0]['msg']!r}"
    # The rate rule alone would never have fired: 2 of 240 is far below a third.
    assert not [i for i in gate._pixel_invariants(paths) if i["msg"].startswith("EDGE-CLIP:")]


def test_the_04c014a2_shape_two_short_runs_mid_episode(frames):
    """Clipped at 54-57 s and 63-66 s of a 138-frame episode, clean everywhere else."""
    gate = _load_gate()
    clipped = {18, 19, 21, 22}
    paths = [frames["clipped"] if i in clipped else frames["clean"] for i in range(138)]
    runs = _issues(gate, paths)
    assert runs and "54-57s" in runs[0]["msg"] and "63-66s" in runs[0]["msg"], runs


def test_a_single_clipped_frame_is_not_a_run(frames):
    """A slide-in crossing the edge for one sampled frame is motion, not a held cut."""
    gate = _load_gate()
    paths = [frames["clean"]] * 30 + [frames["clipped"]] + [frames["clean"]] * 30
    assert not _issues(gate, paths)


def test_a_run_on_photo_beats_is_exempt(frames):
    gate = _load_gate()
    paths = [frames["clean"]] * 10 + [frames["clipped"]] * 3 + [frames["clean"]] * 10
    clips = [{"start_ms": i * 3000, "duration_ms": 3000,
              "modality": "scene_image" if 10 <= i < 13 else "diagram"} for i in range(23)]
    assert not _issues(gate, paths, clips), "a photo bleeds to the edge by design"


def test_runs_are_maximal_and_respect_the_minimum():
    gate = _load_gate()
    assert gate._runs([3, 4, 9, 10, 11, 20], min_len=2) == [(3, 4), (9, 11)]
    assert gate._runs([5], min_len=2) == []
    assert gate._runs([], min_len=2) == []
    assert gate._runs([1, 2, 3], min_len=3) == [(1, 3)]


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
