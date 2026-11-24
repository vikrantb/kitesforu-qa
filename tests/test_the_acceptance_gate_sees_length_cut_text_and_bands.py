"""The acceptance gate's L0 PASS must mean the four things a release battery's critics found it could not see.

A 16:9 explainer passed L0 with four defects a viewer saw at once: it ran 25% over its ask, about fifteen labels
ended in a baked "…", a lighter canvas band showed along the bottom for 4 s, and its last frame cut a card off the
right and bottom edges. Each probe below fires on a synthetic twin of one defect and stays quiet on its look-alike:

* LENGTH: the video against ``inputs.duration_min``, the ask ±20% (the producer's ask ledger reads the same field
  with the same tolerance; the mirror is pinned in ``test_qa_mirrors_match_production.py``);
* TRUNCATED TEXT: OCR'd words that end in an ellipsis;
* EDGE BAND / LETTERBOX: a flat band behind a full-width seam, on any side, from one frame. A dark full-frame card
  is NOT a letterbox: the old rule read one as a letterbox on three real jobs;
* EDGE-CLIP: a faint straight edge cut by the frame counts, and a cut on the last frame needs no partner frame.

The fixtures are generated, never committed: synthetic frames and videos, as this repository is public.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import uuid

import numpy as np
import PIL.Image as PIL_Image
import PIL.ImageDraw as PIL_ImageDraw
import PIL.ImageFont as PIL_ImageFont
import pytest

_QA = pathlib.Path(__file__).resolve().parents[1]
_GATE = _QA / "scripts" / "acceptance_gate.py"
sys.path.insert(0, str(_QA / "src"))

from kitesforu_qa.edge_band import edge_bands  # noqa: E402
from kitesforu_qa.faint_edge_cut import faint_cut_edges  # noqa: E402
from kitesforu_qa.truncated_text import ends_its_line, is_cut_word  # noqa: E402

needs_tesseract = pytest.mark.skipif(not shutil.which("tesseract"), reason="the tesseract binary reads the text")
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg/ffprobe synthesize the video")


@pytest.fixture()
def gate(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    spec = importlib.util.spec_from_file_location(f"acceptance_gate_{uuid.uuid4().hex}", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── synthetic frames ──────────────────────────────────────────────────────────────────────────────────────────

def _slide(w=540, h=304, bg=29):
    """A dark diagram slide: a stack of bars and two label rows, all inside the margins."""
    im = PIL_Image.new("L", (w, h), bg)
    dr = PIL_ImageDraw.Draw(im)
    for i, fill in enumerate((120, 160, 140, 180)):
        dr.rectangle([130, 20 + i * 50, 300, 65 + i * 50], fill=fill)
        dr.rectangle([320, 30 + i * 50, 470, 40 + i * 50], fill=200)
    return im


def _canvas_band(w=540, h=304):
    """The slide shifted up over a lighter canvas: the bottom 16% is flat, one level brighter left to right."""
    im = np.asarray(_slide(w, h), dtype=float).copy()
    top = int(h * 0.84)
    im[top:, :] = 51 + np.linspace(-2, 2, w)
    return PIL_Image.fromarray(im.astype(np.uint8))


def _dark_card(w=540, h=960):
    """A full-frame dark kinetic-text card: a soft vertical gradient, a block of text lines in the upper middle that
    never spans the width, and dark empty space above and below it."""
    g = np.tile(np.linspace(14, 8, h)[:, None], (1, w))
    im = PIL_Image.fromarray(g.astype(np.uint8))
    dr = PIL_ImageDraw.Draw(im)
    for i in range(8):
        dr.rectangle([26, 110 + i * 66, 26 + (420 if i % 3 else 300), 160 + i * 66], fill=235)
    return im


def _letterboxed(w=540, h=960):
    im = PIL_Image.new("L", (w, h), 25)
    dr = PIL_ImageDraw.Draw(im)
    dr.rectangle([0, 0, w, 115], fill=0)
    dr.rectangle([0, h - 115, w, h], fill=0)
    dr.ellipse([176, 300, 368, 495], outline=150, width=2)
    return im


def _pillarboxed(w=540, h=304):
    im = PIL_Image.new("L", (w, h), 30)
    dr = PIL_ImageDraw.Draw(im)
    dr.rectangle([0, 0, 180, h], fill=0)
    dr.rectangle([360, 0, w, h], fill=0)
    dr.rectangle([200, 100, 340, 200], fill=170)
    return im


def _inset_box(w=540, h=304):
    """A big box inset 18 px from each side on a flat background: flat above and below, but no full-width seam."""
    im = PIL_Image.new("L", (w, h), 20)
    PIL_ImageDraw.Draw(im).rectangle([18, 33, w - 19, 247], fill=150)
    return im


def _frosted_card(x0, y0, x1, y1, w=540, h=304):
    """A frosted-glass card 16 levels over a dark slide: every edge is under the old 28-level step."""
    im = PIL_Image.new("L", (w, h), 30)
    PIL_ImageDraw.Draw(im).rectangle([x0, y0, x1, y1], fill=46)
    return im


def _texture(w=540, h=304):
    """Photo-like texture: scattered steps everywhere, no straight line through a margin."""
    rng = np.random.default_rng(7)
    return PIL_Image.fromarray(np.clip(60 + rng.normal(0, 6, (h, w)), 0, 255).astype(np.uint8))


def _gray(im) -> np.ndarray:
    return np.asarray(im.convert("L"), dtype=float)


@pytest.fixture()
def frames(tmp_path):
    made = {}
    for name, im in (("slide", _slide()), ("canvas_band", _canvas_band()), ("dark_card", _dark_card()),
                     ("letterboxed", _letterboxed()), ("pillarboxed", _pillarboxed()), ("inset_box", _inset_box()),
                     ("card_cut", _frosted_card(430, 150, 600, 400)),
                     ("card_inside", _frosted_card(300, 100, 460, 250))):
        p = tmp_path / f"{name}.png"
        im.save(p)
        made[name] = str(p)
    return made


def _tagged(issues, tag):
    return [i for i in issues if i["msg"].startswith(tag)]


# ── EDGE BAND / LETTERBOX ─────────────────────────────────────────────────────────────────────────────────────

def test_a_one_sided_lighter_band_on_one_frame_is_found(gate, frames):
    issues = gate._pixel_invariants([frames["slide"]] * 2 + [frames["canvas_band"]] + [frames["slide"]] * 22)
    band = _tagged(issues, "EDGE BAND")
    assert band and "6-9s (bottom 16%)" in band[0]["msg"] and band[0]["sev"] == "MAJOR", issues
    assert not _tagged(issues, "LETTERBOX")


def test_a_dark_full_frame_card_is_not_a_letterbox(gate, frames):
    """The false positive: dark empty space above and below a card's text is flat, but no seam crosses the frame."""
    issues = gate._pixel_invariants([frames["dark_card"]] * 12)
    assert not _tagged(issues, "LETTERBOX") and not _tagged(issues, "EDGE BAND"), issues
    assert edge_bands(_gray(_dark_card())) == {}


def test_the_dark_card_did_fool_the_rule_it_replaces():
    """PREMISE: the old letterbox rule (a dark band top AND bottom, 16 levels under the middle) fires on the card, so
    the test above proves the new rule tells them apart rather than passing a card no rule would flag."""
    im = _gray(_dark_card())
    band = int(im.shape[0] * 0.10)
    top, bot, mid = im[:band], im[-band:], im[band:-band]
    assert mid.mean() - max(top.mean(), bot.mean()) > 16 and top.std() < 9 and bot.std() < 9


@pytest.mark.parametrize("name", ["letterboxed", "pillarboxed"])
def test_bands_on_two_opposite_edges_are_a_letterbox(gate, frames, name):
    issues = gate._pixel_invariants([frames[name]] * 6)
    assert _tagged(issues, "LETTERBOX: 6/6"), issues
    assert not _tagged(issues, "EDGE BAND"), "a letterbox is reported once, not again as edge bands"


def test_a_box_inset_from_the_sides_is_no_band(frames):
    assert edge_bands(_gray(_inset_box())) == {}
    assert edge_bands(_gray(_slide())) == {}


# ── EDGE-CLIP: a faint straight edge, and the last frame ──────────────────────────────────────────────────────

def test_a_faint_card_cut_by_the_frame_is_found_where_the_old_step_is_blind():
    im = _gray(_frosted_card(430, 150, 600, 400))
    assert int((np.abs(np.diff(im, axis=0)) > 28).sum()) == 0, "premise: no step clears the old 28 levels"
    assert faint_cut_edges(im) == frozenset({"right", "bottom"})


def test_a_faint_card_inside_the_frame_texture_and_a_band_seam_are_not_cuts():
    assert faint_cut_edges(_gray(_frosted_card(300, 100, 460, 250))) == frozenset()
    texture = _gray(_texture())
    m = max(3, int(texture.shape[1] * 0.03))
    assert int((np.abs(np.diff(texture[:, -m:], axis=0)) > 12).sum()) >= 12, "premise: a plain count would flag it"
    assert faint_cut_edges(texture) == frozenset()
    assert faint_cut_edges(_gray(_canvas_band())) == frozenset(), "a full-width seam belongs to the band probe"


@pytest.mark.parametrize("x0", [40, 20])  # the card spans 0.926 and 0.963 of the width
def test_a_wide_faint_card_cut_by_the_frame_is_reported_by_one_probe(x0):
    """Between the faint probe's old seam span (0.90) and the band probe's (0.97), a wide card cut at the edge was
    a seam to one probe and not a band to the other: neither reported it. One seam span closes that gap."""
    from kitesforu_qa.edge_band import edge_bands

    im = _gray(_frosted_card(x0, 150, 600, 400))
    assert "bottom" in faint_cut_edges(im) or "bottom" in edge_bands(im), (faint_cut_edges(im), edge_bands(im))


def test_a_cut_on_the_last_frame_is_found_alone(gate, frames):
    issues = gate._pixel_invariants([frames["card_inside"]] * 10 + [frames["card_cut"]])
    end = _tagged(issues, "EDGE-CLIP AT THE END")
    assert end and "30-33s" in end[0]["msg"] and "bottom/right" in end[0]["msg"], issues
    assert not _tagged(issues, "EDGE-CLIP RUN")


def test_the_same_cut_mid_episode_is_still_motion(gate, frames):
    issues = gate._pixel_invariants([frames["card_inside"]] * 5 + [frames["card_cut"]] + [frames["card_inside"]] * 5)
    assert not _tagged(issues, "EDGE-CLIP"), issues


def test_a_run_that_reaches_the_end_is_reported_once(gate, frames):
    issues = gate._pixel_invariants([frames["card_inside"]] * 8 + [frames["card_cut"]] * 3)
    assert _tagged(issues, "EDGE-CLIP RUN") and not _tagged(issues, "EDGE-CLIP AT THE END"), issues


@pytest.mark.parametrize("modality,flagged", [("scene_image", False), ("diagram", True)])
def test_the_tail_after_the_last_clip_is_that_clip(gate, frames, modality, flagged):
    """The master holds the last clip's frame past the end of the timeline, so the last frame (sampled at 31.5 s)
    is graded as that clip: exempt when it is a picture, checked when it is a diagram."""
    clips = [{"start_ms": i * 3000, "duration_ms": 3000, "modality": "diagram"} for i in range(9)]
    clips.append({"start_ms": 27000, "duration_ms": 3000, "modality": modality})
    issues = gate._pixel_invariants([frames["card_inside"]] * 10 + [frames["card_cut"]], clips)
    assert bool(_tagged(issues, "EDGE-CLIP AT THE END")) is flagged, issues


def test_clip_lookup_reads_the_tail_as_the_last_clip_and_a_gap_as_unknown(gate):
    a = {"start_ms": 0, "duration_ms": 1000, "modality": "diagram"}
    b = {"start_ms": 2000, "duration_ms": 1000, "modality": "scene_image"}
    assert gate._clip_at([a, b], 500) is a and gate._clip_at([a, b], 2500) is b
    assert gate._clip_at([a, b], 1500) is None, "a gap between clips stays unknown"
    assert gate._clip_at([b, a], 3000) is b and gate._clip_at([a, b], 99000) is b
    assert gate._clip_at([], 99000) is None and gate._clip_at(None, 99000) is None


# ── TRUNCATED TEXT ────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word,cut", [
    ("th…", True), ("fr...", True), ("wav..", True), ("re-ent..", True), ("(or..", True), ("refr…”", True),
    ("years.", False), ("etc.", False), ("U.S.", False), ("...", False), ("…", False), ("Fragment)", False)])
def test_a_cut_word_ends_in_an_ellipsis_after_a_letter(word, cut):
    assert is_cut_word(word) is cut


def _labels(tmp_path, name, lines):
    im = PIL_Image.new("L", (540, 304), 25)
    dr = PIL_ImageDraw.Draw(im)
    font = PIL_ImageFont.load_default(size=12)
    for i, text in enumerate(lines):
        dr.text((40, 40 + i * 50), text, fill=220, font=font)
    p = tmp_path / f"{name}.png"
    im.save(p)
    return str(p)


@needs_tesseract
def test_a_label_cut_with_an_ellipsis_is_found_once_across_frames(gate, tmp_path):
    cut = _labels(tmp_path, "cut", ["Light bends at the bou…", "Prism (Glass Wedge)"])
    issues = gate._truncated_text_issues([cut] * 3)
    assert len(issues) == 1 and issues[0]["sev"] == "MAJOR", issues
    msg = issues[0]["msg"]
    assert msg.startswith("TRUNCATED TEXT: 1 distinct word(s)") and "on 3/3 frame(s)" in msg and "'bou" in msg, msg


@needs_tesseract
def test_whole_labels_and_a_sentence_are_not_cut(gate, tmp_path):
    whole = _labels(tmp_path, "whole", ["Light bends at the boundary", "A prism splits white light."])
    assert gate._truncated_text_issues([whole] * 2) == []


@needs_tesseract
def test_an_ellipsis_inside_a_line_is_authored_not_cut(gate, tmp_path):
    """A renderer cuts at the END of its box. "Well… nobody" keeps going on the same line: an authored pause."""
    spoken = _labels(tmp_path, "spoken", ["Well… nobody saw that coming."])
    assert gate._truncated_text_issues([spoken] * 2) == []


def test_a_word_ends_its_line_unless_another_follows_close_on_the_same_line():
    word = (100, 50, 60, 30)
    assert ends_its_line(word, [word])
    assert not ends_its_line(word, [word, (175, 52, 40, 28)]), "a word 15 px on, on the same line"
    assert ends_its_line(word, [word, (400, 50, 90, 30)]), "the next column of the diagram, far along the line"
    assert ends_its_line(word, [word, (175, 120, 40, 28)]), "a word on the next line"
    assert ends_its_line(word, [word, (20, 50, 60, 30)]), "a word BEFORE it on the line"


def test_a_gate_that_cannot_read_the_text_does_not_pass_it(gate, tmp_path, monkeypatch):
    import kitesforu_qa.harness.checks.visual as visual

    def no_tesseract(img):
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(visual, "_ocr_words", no_tesseract)
    frame = _labels(tmp_path, "any", ["Light bends at the bou…"])
    issues = gate._truncated_text_issues([frame] * 2)
    assert [i["sev"] for i in issues] == ["MAJOR"] and "TRUNCATED-TEXT PROBE DID NOT RUN" in issues[0]["msg"], issues


# ── LENGTH ────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("duration,sev", [
    (74.93, "MAJOR"), (72.0, None), (70.0, None), (48.0, None), (47.0, "MAJOR"), (35.0, "BLOCKER")])
def test_a_video_is_held_to_the_ask_plus_or_minus_twenty_percent(gate, duration, sev):
    issues = gate._length_issues(duration, 60.0)
    assert [i["sev"] for i in issues] == ([sev] if sev else []), issues
    if sev:
        assert issues[0]["msg"].startswith(f"LENGTH: {duration:.1f}s against a 60s ask"), issues


def test_a_video_with_no_measured_length_is_unjudged_never_passed(gate):
    issues = gate._length_issues(0.0, 60.0)
    assert [i["sev"] for i in issues] == ["MAJOR"] and issues[0]["msg"].startswith("LENGTH UNJUDGED"), issues
    assert gate._length_issues(0.0, None) == []


@pytest.mark.parametrize("inputs,asked", [
    ({"duration_min": 1.0}, 60.0), ({"duration_min": 0.6}, 36.0), ({}, None), ({"duration_min": 0}, None),
    ({"duration_min": True}, None), ({"duration_min": "1"}, None), (None, None)])
def test_the_ask_is_inputs_duration_min(gate, inputs, asked):
    assert gate._asked_seconds({"inputs": inputs}) == asked
    issues = gate._length_issues(74.93, gate._asked_seconds({"inputs": inputs}))
    if asked is None:
        assert issues == [], issues
    else:  # 74.93 s is +25% on a 60 s ask and +108% on a 36 s ask: both outside ±20%
        assert [i["sev"] for i in issues] == ["MAJOR"], issues
        assert issues[0]["msg"].startswith(f"LENGTH: 74.9s against a {asked:.0f}s ask"), issues


@needs_ffmpeg
@pytest.mark.parametrize("minutes,flagged", [(0.1, True), (0.2, False), (None, False)])
def test_the_gate_holds_the_video_it_fetched_to_the_ask(gate, monkeypatch, tmp_path, minutes, flagged):
    """Through ``run_gate``: the length is read from the fetched master, against the doc's own ask."""
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x303030:s=540x960:d=12", "-vf", "drawbox=x=190:y=420:w=160:h=120:color=0xb0b0b0:t=fill",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
                   check=True, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    doc = {"topic": "a synthetic topic",
           "visual": {"status": "done", "video_status": "ready", "video_url": "https://synthetic.invalid/v.mp4",
                      "clips": [{"start_ms": 0, "duration_ms": 12000, "modality": "diagram", "aspect_ratio": "9:16"}]}}
    if minutes is not None:
        doc["inputs"] = {"duration_min": minutes}
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: dict(doc))
    monkeypatch.setattr(gate, "_fetch_master", lambda url, path: bool(shutil.copyfile(video, path)))
    out = gate.run_gate(f"synthetic-{uuid.uuid4().hex}", frames_dir=str(tmp_path / "frames"))
    length = _tagged(out["issues"], "LENGTH")
    assert bool(length) is flagged, out["issues"]
    assert out["asked_s"] == (None if minutes is None else minutes * 60.0)
    if flagged:
        assert length[0]["surface"] == "watch" and "against a 6s ask (+100%)" in length[0]["msg"], length
        assert out["verdict"] == "REVIEW"


@needs_ffmpeg
@needs_tesseract
def test_the_gate_reads_cut_text_on_the_video_it_fetched(gate, monkeypatch, tmp_path):
    """Through ``run_gate``: a label cut with an ellipsis, held for the whole video, is read off its frames."""
    label = _labels(tmp_path, "label", ["Light bends at the bou…", "Prism (Glass Wedge)"])
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", label, "-t", "9",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
                   check=True, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    doc = {"topic": "a synthetic topic",
           "visual": {"status": "done", "video_status": "ready", "video_url": "https://synthetic.invalid/v.mp4",
                      "clips": [{"start_ms": 0, "duration_ms": 9000, "modality": "diagram", "aspect_ratio": "16:9"}]}}
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: dict(doc))
    monkeypatch.setattr(gate, "_fetch_master", lambda url, path: bool(shutil.copyfile(video, path)))
    out = gate.run_gate(f"synthetic-{uuid.uuid4().hex}", frames_dir=str(tmp_path / "frames"))
    cut = _tagged(out["issues"], "TRUNCATED TEXT")
    assert cut and cut[0]["surface"] == "watch" and "'bou" in cut[0]["msg"], out["issues"]
    assert out["verdict"] == "REVIEW"
