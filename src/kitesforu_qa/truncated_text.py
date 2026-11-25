"""Finds the words on a frame that end in an ellipsis: text a renderer cut short to fit its box.

A diagram engine that cannot fit a label shortens it and bakes a trailing "…" into the pixels (a side label that
ends "grows fa…"). Nothing downstream can recover the words, and the gate's pixel probes cannot see them: the
text is inside the frame, on a clean card. Reading them needs OCR, and the gate reuses the one this repo already
runs, the tesseract CLI behind the harness's edge-crop check (:func:`kitesforu_qa.harness.checks.visual._ocr_words`,
the same confidence and glyph-size floors), so no dependency is added.

Gate frames are 540 px wide, where a diagram's side labels are about 10 px tall: tesseract reads them only after
the frame is scaled up :data:`UPSCALE` times (measured on a 16:9 diagram frame: at 1x it read one of its two cut
labels, and without its ellipsis; at 2x and 3x it read both, each ending in dots, at confidence 85 to 95).

An ellipsis can be authored (a quote that trails off, a sentence split across two cards), so a hit is a finding
for the reviewer to confirm, not a verdict. An ellipsis with more words after it on the same line is never a cut
(a renderer cuts at the end of its box), so it is not counted: on stored real frames that removed the one hit that
was plainly authored, a kinetic card's spoken pause with the sentence going on after it. Measured on 656 stored
gate frames of real jobs (35 frame sets), with at least one hit frame per set opened by eye: hits in 11 sets, each
in a diagram's side label, a timeline label or a kinetic text card, and none on a picture. One may still be
authored: a kinetic card's sentence that trails off into the next card.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

#: How many times a gate frame is scaled up before OCR.
UPSCALE = 3
#: Frames read at once: each read is its own tesseract process.
_PARALLEL_READS = 4

#: A word that ends in an ellipsis after a letter or digit: "…", or the two or more dots tesseract reads it as,
#: with any closing quote or bracket after it. A lone "..." or a sentence's single full stop is not one.
_CUT_WORD = re.compile(r"[^\W_](?:…|\.{2,})[\"'’”)\]]*$")
#: A word further along the same line, closer than this many glyph heights, means the ellipsis is inside the line
#: ("Well… nobody saw"): a renderer cuts text at the END of its box, never in the middle of a line.
_SAME_LINE_GAP_HEIGHTS = 3.0


class FrameText(NamedTuple):
    """One frame's reading: its index, the words that end in an ellipsis, and whether OCR ran on it at all."""
    index: int
    cut_words: list[str]
    read: bool


def is_cut_word(word: str) -> bool:
    """True when ``word`` ends in an ellipsis after a letter or digit."""
    return bool(_CUT_WORD.search(word))


def ends_its_line(box: tuple[int, int, int, int], boxes: list[tuple[int, int, int, int]]) -> bool:
    """True when no word in ``boxes`` (``(x, y, w, h)``) follows ``box`` on the same line: one that overlaps half its
    height and starts to its right within :data:`_SAME_LINE_GAP_HEIGHTS` glyph heights."""
    x, y, w, h = box
    for ox, oy, _, oh in boxes:
        overlap = min(y + h, oy + oh) - max(y, oy)
        if overlap >= 0.5 * min(h, oh) and x + w <= ox <= x + w + _SAME_LINE_GAP_HEIGHTS * h:
            return False
    return True


def _read_frame(index: int, path: str) -> FrameText:
    from PIL import Image

    from kitesforu_qa.harness.checks.visual import _ocr_words

    try:
        with Image.open(path) as frame:
            gray = frame.convert("L")
        words = _ocr_words(gray.resize((gray.width * UPSCALE, gray.height * UPSCALE), Image.LANCZOS))
    except Exception:  # noqa: BLE001 — an unreadable frame or a missing tesseract: this frame was not read
        return FrameText(index, [], False)
    boxes = [tuple(box) for _, *box in words]
    return FrameText(index, [w for w, *box in words if is_cut_word(w) and ends_its_line(tuple(box), boxes)], True)


def read_cut_words(frames: list[str]) -> list[FrameText]:
    """Each frame's reading, in frame order."""
    with ThreadPoolExecutor(max_workers=_PARALLEL_READS) as pool:
        return list(pool.map(_read_frame, range(len(frames)), frames))


def cut_word_key(word: str) -> str:
    """The word without its ellipsis or closing quote, lower-cased: the same cut label read on several frames is
    one finding."""
    return re.sub(r"(?:…|\.{2,})[\"'’”)\]]*$", "", word).strip("\"'‘“(").lower()


def summarise(readings: list[FrameText]) -> dict[str, Any]:
    """How many frames were read, and each distinct cut word with the frames it was read on."""
    words: dict[str, dict[str, Any]] = {}
    for reading in readings:
        for word in reading.cut_words:
            entry = words.setdefault(cut_word_key(word), {"shown": word, "frames": []})
            if reading.index not in entry["frames"]:
                entry["frames"].append(reading.index)
    return {"read": sum(r.read for r in readings), "words": list(words.values())}
