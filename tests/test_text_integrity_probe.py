"""The acceptance gate sees text CUT on screen (`scripts/text_integrity.py`).

Course 8a64fcff's four repaired lessons passed the gate PASS_DETERMINISTIC on 2026-10-08 while hero
critic Elena found cut text on all four (review section 2): a central question ending in "...", myth
and reality cards cut by their own box, a takeaway that never finishes, and titles cropped by a
camera push ("hoosing Your Strongest Major", "ifficulty Bonus Myth").

The fixtures are the delivered gate frames themselves (540x304, 3 s apart), copied from those four
lessons, with a minimal job doc per lesson: the blueprint lines and each segment's `text_full`.
Beside each frame is the TSV that tesseract 5.5.2 produced for it with the probe's own invocation
(`tesseract <png> stdout tsv`), so the probe's logic is pinned without the binary installed. One
test runs the real binary and is skipped where it is absent.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import text_integrity as ti  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "text_integrity"


def _cached_ocr(png: str) -> list[dict]:
    with open(Path(png).with_suffix(".tsv"), newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t", quoting=csv.QUOTE_NONE))
    return [w for w in rows if (w.get("text") or "").strip()]


def _doc(job8: str) -> dict:
    return json.loads((FIX / f"{job8}.doc.json").read_text())


def _sources(job8: str) -> list:
    return [(s, *ti._squeeze(s)) for s in ti.source_lines(_doc(job8))]


def _kinds(name: str) -> set[str]:
    found = ti.frame_findings(str(FIX / f"{name}.png"), _sources(name.split("_")[0]), ocr=_cached_ocr)
    return {f["kind"] for f in found}


# Cited in the review, section 2: the frame, the finding it must raise, and what is cut on it.
CITED = [
    ("cdcf1150_f_057", "ELLIPSIS", "ep4: the central question ends 'when does it create avoidable...'"),
    ("2eddeaaf_f_107", "PREFIX", "ep2: a 'best major' card stops at 'one whose complete four-year'"),
    ("2eddeaaf_f_198", "EDGE", "ep2: a camera push crops the title to 'hoosing Your Strongest Major'"),
    ("56f276db_f_149", "EDGE", "ep5: a camera push crops the title to 'ifficulty Bonus Myth'"),
    ("56f276db_f_217", "PREFIX", "ep5: the takeaway stops at '...this plan often produces'"),
    ("c6c180c8_f_170", "PREFIX", "ep6: the myth card is cut by its own box"),
]

# Frames from the same lessons whose text is whole (each checked by eye): a title card, a short
# label that OPENS a longer spoken sentence ("Advanced science and biochemistry"), a key-idea
# card, the completed reveal of a sentence, a flow chart, and a step chart mid-build.
WHOLE = ["2eddeaaf_f_128", "2eddeaaf_f_133", "2eddeaaf_f_237", "c6c180c8_f_180", "c6c180c8_f_217",
         "cdcf1150_f_134"]


@pytest.mark.parametrize("name,kind,what", CITED, ids=[c[0] for c in CITED])
def test_a_cited_frame_is_flagged(name: str, kind: str, what: str):
    got = _kinds(name)
    assert kind in got, f"{name} ({what}) raised {sorted(got) or 'nothing'}, not {kind}: the gate passes it."


@pytest.mark.parametrize("name", WHOLE)
def test_a_frame_whose_text_is_whole_stays_clean(name: str):
    found = ti.frame_findings(str(FIX / f"{name}.png"), _sources(name.split("_")[0]), ocr=_cached_ocr)
    assert not found, f"{name} shows whole text but was flagged: {found}"


def test_a_reveal_is_not_a_cut():
    """A card that writes its sentence on over a few seconds shows an opening of it first. f_179 shows
    'Engineering major: calculus and physics covered, but' and f_180 the whole sentence."""
    frames = [str(FIX / "c6c180c8_f_179.png"), str(FIX / "c6c180c8_f_180.png")]
    alone = ti.check_frames(frames[:1], _doc("c6c180c8"), ocr=_cached_ocr, workers=1)
    assert "PREFIX" in {f["kind"] for f in alone["flagged"].get("c6c180c8_f_179.png", [])}, (
        "control: on its own, f_179 is the opening of a longer authored sentence")
    both = ti.check_frames(frames, _doc("c6c180c8"), ocr=_cached_ocr, workers=1)
    assert both["flagged"] == {}, f"the sentence finishes on the next frame, so nothing is cut: {both['flagged']}"


def test_ocr_dropping_letters_from_small_type_is_not_a_cut():
    """The turn figure ep4 b17, rendered whole by the workers fix, OCR'd at 540 px: tesseract drops
    letters all through the sentence ('isa', 'fitand', 'insti', 'before si'), so the block is ~12%
    shorter than the line it shows. Aligned against only the block's length, it reads as cut."""
    src = ("Changing majors is a planning trade-off: it can improve academic fit and protect future "
           "performance, but only if the student audits prerequisites, credits, timing, and "
           "institutional policies before switching.")
    block = ("Changing majors isa planning trade-off: it can improve academic fitand protect future "
             "performance, but only i student audits prereq credits, timing, and insti policies before si")
    assert ti._cut_prefix(block, [(src, *ti._squeeze(src))], ti._squeeze(block)[0]) is None


def test_the_same_line_cut_short_is_flagged():
    """The control for the test above: the same line, shown only up to 'but only if the student'."""
    src = ("Changing majors is a planning trade-off: it can improve academic fit and protect future "
           "performance, but only if the student audits prerequisites, credits, timing, and "
           "institutional policies before switching.")
    block = "Changing majors is a planning trade-off: it can improve academic fit and protect future performance, but only if the student"
    got = ti._cut_prefix(block, [(src, *ti._squeeze(src))], ti._squeeze(block)[0])
    assert got and got[0] == src and got[1] == "auditsprerequisitescredits"


_COLON = ("Because we spent episodes two and three on popular majors: the assumption that one checklist "
          "works everywhere does not survive a second school's website.")
_COMMA = ("Because we spent episodes two and three on popular majors, the assumption that one checklist "
          "works everywhere does not survive a second school's website.")


@pytest.mark.parametrize("src,block,why", [
    (_COLON, "Because we spent episodes two and three on popular majors:", "it stops at the line's colon"),
    (_COMMA, "Because we spent episodes two and three on popular majors", "the line's next mark is a comma"),
    (_COMMA, "Because we spent episodes", "too short to tell a cut from a label"),
    (_COMMA, "Because we spent episodes two and three on popular majors.", "it ends a sentence on screen"),
])
def test_a_block_that_ends_where_a_line_may_end_is_not_cut(src: str, block: str, why: str):
    srcs = [(src, *ti._squeeze(src))]
    assert ti._cut_prefix(block, srcs, ti._squeeze(block)[0]) is None, why
    cut = block.rstrip(":.") + " the assumption that one"  # control: the same line, stopped mid-clause
    assert ti._cut_prefix(cut, srcs, ti._squeeze(cut)[0]) is not None or len(block.split()) < 6, why


def test_a_comma_inside_an_open_parenthesis_is_still_a_cut():
    src = "AMCAS overall GPA and BCPM (biology, chemistry, physics, math) GPA as two separate reported numbers"
    block = "AMCAS overall GPA and BCPM (biology, chemistry"
    got = ti._cut_prefix(block, [(src, *ti._squeeze(src))], ti._squeeze(block)[0])
    assert got and got[0] == src


def test_a_block_whose_continuation_is_on_the_frame_is_not_cut():
    """OCR splits one text into two blocks (a line wraps into another column): the first block is
    an opening of the line, and the line's next words are on the same frame."""
    src = ("A student in a less impressive major who executes this plan often produces a stronger "
           "application than one who chose the major for its label.")
    block = "A student in a less impressive major who executes this plan often"
    srcs = [(src, *ti._squeeze(src))]
    frame = ti._squeeze(block + " produces a stronger application than one who chose")[0]
    assert ti._cut_prefix(block, srcs, frame) is None
    assert ti._cut_prefix(block, srcs, ti._squeeze(block)[0]) is not None, "control: alone, it is cut"


def _row(text: str, x: int, y: int, w: int = 40, h: int = 12, line: int = 1, conf: float = 90.0) -> dict:
    return {"block_num": "1", "par_num": "1", "line_num": str(line), "left": str(x), "top": str(y),
            "width": str(w), "height": str(h), "conf": str(conf), "text": text}


def _frame(tmp_path: Path) -> str:
    from PIL import Image

    p = tmp_path / "f_001.png"
    Image.new("RGB", (540, 304), "black").save(p)
    return str(p)


def test_ellipsis_needs_a_real_word_before_the_dots(tmp_path: Path):
    """'ae...' is tesseract reading a faint glyph (ep2 f_053); 'avoidable...' is a length cap."""
    png = _frame(tmp_path)
    noise = [_row("the", 100, 100), _row("ae...", 150, 100)]
    capped = [_row("create", 100, 100), _row("avoidable...", 150, 100)]
    assert ti.frame_findings(png, [], ocr=lambda _p: noise) == []
    assert [f["kind"] for f in ti.frame_findings(png, [], ocr=lambda _p: capped)] == ["ELLIPSIS"]


def test_edge_needs_a_confident_word_on_the_edge(tmp_path: Path):
    png = _frame(tmp_path)
    on_edge = [_row("hoosing", 0, 100)]
    faint = [_row("hoosing", 0, 100, conf=20.0)]
    inside = [_row("Choosing", 30, 100)]
    assert [f["kind"] for f in ti.frame_findings(png, [], ocr=lambda _p: on_edge)] == ["EDGE"]
    assert ti.frame_findings(png, [], ocr=lambda _p: faint) == []
    assert ti.frame_findings(png, [], ocr=lambda _p: inside) == []


def test_one_frame_is_not_an_issue_two_are():
    one = {"status": "ran", "frames_checked": 9, "flagged": {"f_001.png": [{"kind": "EDGE", "text": "x"}]}}
    two = {"status": "ran", "frames_checked": 9, "flagged": {
        "f_001.png": [{"kind": "EDGE", "text": "x"}], "f_002.png": [{"kind": "EDGE", "text": "x"}]}}
    assert ti.issues(one) == []
    got = ti.issues(two)
    assert len(got) == 1 and got[0]["sev"] == "MAJOR" and got[0]["msg"].startswith("TEXT-CUT EDGE: 2/9")


def test_ocr_that_cannot_run_is_never_a_pass(tmp_path: Path):
    png = _frame(tmp_path)

    def broken(_p: str) -> list:
        raise RuntimeError("tesseract: command not found")

    res = ti.check_frames([png, png], {}, ocr=broken, workers=1)
    assert res["status"] == "skipped" and res["frames_checked"] == 0 and res["frames_failed"] == 2
    got = ti.issues(res)
    assert len(got) == 1 and got[0]["sev"] == "MAJOR" and "NOT checked" in got[0]["msg"]


def test_photo_frames_are_skipped_not_read(tmp_path: Path):
    frames = []
    for i in range(3):
        (tmp_path / str(i)).mkdir()
        frames.append(_frame(tmp_path / str(i)))
    read: list[str] = []

    def ocr(p: str) -> list:
        read.append(p)
        return []

    res = ti.check_frames(frames, {}, skip=lambda i: i == 1, ocr=ocr, workers=1)
    assert res["frames_checked"] == 2 and res["frames_skipped_photo"] == 1
    assert frames[1] not in read and set(read) == {frames[0], frames[2]}


def _gate_run(monkeypatch, tmp_path: Path, names: list[str], clips: list[dict]) -> dict:
    """`run_gate` end to end with only its I/O replaced: the job doc, the download, the probe of the
    video's size, and the frame extraction return the fixture frames; OCR reads their cached TSVs."""
    import acceptance_gate as gate

    frames = []
    for n in names:
        dst = tmp_path / f"f_{len(frames) + 1:03d}.png"
        shutil.copyfile(FIX / f"{n}.png", dst)
        shutil.copyfile(FIX / f"{n}.tsv", dst.with_suffix(".tsv"))
        frames.append(str(dst))
    doc = dict(_doc(names[0].split("_")[0]))
    doc["visual"] = {"video_url": "https://example.invalid/v.mp4", "clips": clips}

    def fake_run(cmd, check=False):  # the download: write a non-empty file where curl would
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"mp4")

    monkeypatch.setattr(gate, "_fetch_job", lambda _j: doc)
    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(gate, "_probe_dims", lambda _p: (540, 304, 3.0 * len(frames)))
    monkeypatch.setattr(gate, "_extract_frames", lambda _mp4, _d: frames)
    monkeypatch.setattr(gate, "_pixel_invariants", lambda _f, _c=None: [])
    monkeypatch.setattr(ti, "tesseract_words", _cached_ocr)
    return gate.run_gate("job-under-test", frames_dir=str(tmp_path))


def test_the_gate_reports_a_cut_takeaway(monkeypatch, tmp_path: Path):
    got = _gate_run(monkeypatch, tmp_path, ["56f276db_f_217", "56f276db_f_217"], clips=[])
    msgs = [i["msg"] for i in got["issues"]]
    assert got["verdict"] == "REVIEW" and any(m.startswith("TEXT-CUT PREFIX: 2/2") for m in msgs), msgs
    assert got["text_integrity"]["frames_checked"] == 2


def test_the_gate_passes_whole_text(monkeypatch, tmp_path: Path):
    got = _gate_run(monkeypatch, tmp_path, ["2eddeaaf_f_237", "2eddeaaf_f_237"], clips=[])
    assert got["verdict"] == "PASS_DETERMINISTIC", got["issues"]
    assert got["text_integrity"]["status"] == "ran" and got["text_integrity"]["frames_checked"] == 2


def test_the_gate_does_not_read_a_photo_frame(monkeypatch, tmp_path: Path):
    """The same cut takeaway, under a `scene_image` clip: a generated picture is skipped, as the
    pixel rule skips it."""
    photo = [{"start_ms": 0, "duration_ms": 60_000, "modality": "scene_image"}]
    got = _gate_run(monkeypatch, tmp_path, ["56f276db_f_217", "56f276db_f_217"], clips=photo)
    assert got["verdict"] == "PASS_DETERMINISTIC", got["issues"]
    assert got["text_integrity"]["frames_skipped_photo"] == 2


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="the tesseract binary is not installed")
@pytest.mark.parametrize("name,kind,what", CITED, ids=[c[0] for c in CITED])
def test_the_real_binary_flags_a_cited_frame(name: str, kind: str, what: str):
    found = ti.frame_findings(str(FIX / f"{name}.png"), _sources(name.split("_")[0]))
    assert kind in {f["kind"] for f in found}, f"{name} ({what}) with live tesseract: {found}"
