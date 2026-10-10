"""On-screen TEXT INTEGRITY, read from the delivered frames: is any text cut?

THE BLIND SPOT THIS CLOSES (course 8a64fcff, 2026-10-08). The founder's four repaired lessons
passed the acceptance gate (`PASS_DETERMINISTIC`, no issues) while hero critic Elena found cut text
on all four: a central question ending "...when does it create avoidable…" (ep4 cdcf1150, on 20 of
the 172 non-photo gate frames), myth/reality cards cut by their own box, a takeaway that stops "...this plan often
produces", titles cropped by a camera push ("hoosing Your Strongest Major"). The gate's only text
rule was `_pixel_invariants`' EDGE-CLIP, and it could not see any of it:

* it read 12 of ~250 frames, and fired only when a THIRD of those showed edge structure — a
  defect on 10 frames of a 12-minute lesson cannot reach that bar;
* it only knows the FRAME edge. Text cut by its own card, by a length cap's ellipsis or by a word
  budget never touches the frame edge at all.

WHAT THIS READS. Every non-photo frame the gate extracts, OCR'd with the tesseract CLI ($0, the same
binary `kitesforu_qa.harness.checks.visual` shells; that module keeps a filtered word list for its
own edge check, and this one needs lines and blocks). Three findings, each from the frame's pixels:

* ``EDGE``     a confident word touches the frame edge: text cut by the frame or by a camera move;
* ``ELLIPSIS`` a line ends in "…" / "...": a length cap's marker on screen;
* ``PREFIX``   a block of on-screen text is the OPENING of a longer line the job itself authored
  (blueprint ``central_question``/``must_cover``/``worldview_shift``/``succes_plan.concrete``/
  ``takeaway``, or a sentence of a segment's ``text_full``), it stops where that line does NOT end
  (no sentence or clause boundary), and the line's continuation appears nowhere else on the frame.
  That is a sentence cut by a budget or clipped by its box — the class OCR alone cannot see,
  because the clipped half-line is never read.

The continuation test is what keeps a multi-line title from flagging its own first line: a block
OCR splits off a longer text still has its next words on screen; a cut one does not.

Photo frames (``scene_image``) are skipped, as the gate's pixel rule already does: a generated
picture can carry legible-looking text at its edges by design.
"""
from __future__ import annotations

import csv
import io
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Any

#: Word confidence below which a token is ignored for EDGE (tesseract 0-100) — the harness's own
#: floor (`checks.visual._OCR_CONF_MIN`), so the two edge checks agree on what a word is.
EDGE_CONF = 40
#: A word whose box is within this many pixels of a frame edge touches it.
EDGE_PX = 1
#: A block shorter than this (squeezed alphanumerics), or with fewer words, is too short to call a
#: cut prefix: a complete short label ("Advanced science and biochemistry") can open a longer
#: spoken sentence, measured on ep2 2eddeaaf f_130-f_138, and a cut card statement is longer.
MIN_PREFIX_CHARS = 24
MIN_PREFIX_WORDS = 6
#: How close OCR'd text must be to the source's opening to count as it (SequenceMatcher ratio).
PREFIX_RATIO = 0.86
#: The source must continue for at least this many alphanumerics past the block, and this many
#: whole words past the word the block ends in, or it is not cut.
MIN_REST_CHARS = 8
MIN_REST_WORDS = 2
#: How much longer than the OCR'd block the source text it shows may be: the share of characters
#: OCR drops from small type at the gate's 540 px (ep4 cdcf1150 b17 lost ~12%).
OCR_DROP_SLACK = 1.3
#: Characters that end a complete clause: a block stopping right before one is not cut. A comma
#: ends one only when no parenthesis is left open ("…(biology, chemistry" is cut mid-parenthesis).
_CLAUSE_END = ".!?:;—–("
#: A kind is reported once it is seen on this many frames: one frame can be OCR noise.
MIN_FRAMES = 2
#: How many following frames (3 s apart) may show the rest of a revealing sentence.
REVEAL_LOOKAHEAD = 3

Word = tuple[str, int, int, int, int, float]  # text, x0, y0, x1, y1, conf


def tesseract_words(png_path: str) -> list[dict[str, Any]]:
    """The tesseract CLI's TSV rows for ``png_path`` that carry a word. Raises when the binary is
    missing or fails, so the caller can SKIP (never pass) the check."""
    r = subprocess.run(["tesseract", png_path, "stdout", "tsv"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"tesseract rc={r.returncode}: {r.stderr[-200:]}")
    rows = list(csv.DictReader(io.StringIO(r.stdout), delimiter="\t", quoting=csv.QUOTE_NONE))
    return [w for w in rows if (w.get("text") or "").strip()]


def _squeeze(s: str) -> tuple[str, list[int]]:
    """Lower-case alphanumerics only, with each kept character's index in ``s``."""
    out, idx = [], []
    for i, ch in enumerate(s):
        c = ch.lower()
        if c.isalnum():
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def source_lines(doc: dict[str, Any]) -> list[str]:
    """Every line the job itself authored for the screen or the voice: blueprint fields, and each
    SENTENCE of every segment's spoken ``text_full`` (a card is minted from one sentence)."""
    bp = ((doc.get("preferences") or {}).get("_nonfiction_blueprint") or {})
    out: list[str] = []

    def add(v: Any) -> None:
        s = " ".join(str(v or "").split())
        if s:
            out.append(s)

    add(bp.get("central_question"))
    add(bp.get("takeaway"))
    ws = bp.get("worldview_shift") or {}
    add(ws.get("listener_believes_before"))
    add(ws.get("listener_believes_after"))
    for c in ((bp.get("succes_plan") or {}).get("concrete") or []):
        add(c)
    for sp in bp.get("segment_plan") or []:
        for m in (sp or {}).get("must_cover") or []:
            add(m)
    for seg in doc.get("segments_ready") or []:
        if isinstance(seg, dict):
            for sent in re.split(r"(?<=[.!?])\s+", str(seg.get("text_full") or "")):
                add(sent)
    return out


def _blocks(rows: Sequence[dict[str, Any]]) -> list[str]:
    """On-screen text blocks: tesseract's paragraphs, split into columns where a line has a gap
    much wider than its text is tall (two cards side by side read as one line), then re-joined top
    to bottom within each column."""
    lines: dict[tuple[str, str, str], list[Word]] = {}
    for w in rows:
        try:
            conf = float(w["conf"])
            x, y, ww, hh = int(w["left"]), int(w["top"]), int(w["width"]), int(w["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if conf < 0:
            continue
        key = (w.get("block_num", ""), w.get("par_num", ""), w.get("line_num", ""))
        lines.setdefault(key, []).append((w["text"].strip(), x, y, x + ww, y + hh, conf))
    segments: list[tuple[tuple[str, str], list[Word]]] = []
    for (b, p, _l), words in lines.items():
        words.sort(key=lambda t: t[1])
        h = max(1, sorted(t[4] - t[2] for t in words)[len(words) // 2])
        cur: list[Word] = []
        for t in words:
            if cur and t[1] - cur[-1][3] > 2.0 * h:
                segments.append(((b, p), cur))
                cur = []
            cur.append(t)
        if cur:
            segments.append(((b, p), cur))
    columns: list[list[list[Word]]] = []
    for (_bp, seg) in sorted(segments, key=lambda s: (min(t[2] for t in s[1]), min(t[1] for t in s[1]))):
        x0 = min(t[1] for t in seg)
        y0 = min(t[2] for t in seg)
        h = max(1, max(t[4] - t[2] for t in seg))
        for col in columns:
            last = col[-1]
            lx0 = min(t[1] for t in last)
            ly1 = max(t[4] for t in last)
            if abs(x0 - lx0) <= 3 * h and -h <= y0 - ly1 <= 1.6 * h:
                col.append(seg)
                break
        else:
            columns.append([seg])
    out = []
    for col in columns:
        text = " ".join(" ".join(t[0] for t in seg) for seg in col)
        out.append(re.sub(r"(\w)-\s+(\w)", r"\1-\2", text))  # "pre- med" -> "pre-med"
    return out


def _cut_prefix(block: str, sources: Sequence[tuple[str, str, list[int]]],
                frame_squeezed: str) -> tuple[str, str] | None:
    """``(the authored line, its next words squeezed)`` when ``block`` is a cut opening of that
    line, else None."""
    bs, _ = _squeeze(block)
    words = re.findall(r"[A-Za-z0-9]+", block)
    if len(bs) < MIN_PREFIX_CHARS or len(words) < MIN_PREFIX_WORDS:
        return None
    if re.search(r"[.!?][\"'”’)\]]*$", block.strip()):
        return None  # the on-screen text ends a sentence itself: complete as shown
    open_paren = block.count("(") > block.count(")")
    for src, ss, idx in sources:
        if len(ss) < len(bs) + MIN_REST_CHARS:
            continue
        # OCR at 540 px DROPS characters from small type ("isa", "fitand", "insti", "before si"), so a
        # block can be much shorter than the source text it shows. Aligned against a window only as
        # long as the block, the match stops short of where the block really ends and a sentence
        # shown whole reads as cut (ep4 cdcf1150 b17's turn figure, rendered by the fix).
        head = ss[: int(len(bs) * OCR_DROP_SLACK) + 6]
        if SequenceMatcher(None, bs[:15], head[:15]).quick_ratio() < 0.7:
            continue
        sm = SequenceMatcher(None, bs, head, autojunk=False)
        if sm.ratio() * (len(bs) + len(head)) / (2 * len(bs)) < PREFIX_RATIO:
            continue  # how much of the BLOCK the source's opening accounts for
        blocks = [b for b in sm.get_matching_blocks() if b.size]
        if not blocks or blocks[0].b > 3:
            continue  # it must be the source's OPENING, not a match somewhere inside it
        last = blocks[-1]
        shown = last.b + last.size  # source characters the block accounts for
        if len(ss) - shown < MIN_REST_CHARS:
            continue
        end = idx[shown - 1]  # the last source character the block shows
        rest = src[end + 1:]
        # A block ending INSIDE a source word ("...policies before si" for "before switching") is
        # OCR clipping small type, not a cut: count only what lies past the end of that word, and
        # call it cut only if whole words of the line remain unshown.
        tail = re.sub(r"^\w*", "", rest)
        if len(re.findall(r"[A-Za-z0-9]+", tail)) < MIN_REST_WORDS:
            continue
        nxt_ch = rest.lstrip()[:1]
        if src[end] in _CLAUSE_END or nxt_ch in tuple(_CLAUSE_END) or (nxt_ch == "," and not open_paren):
            continue  # it stops where a sentence or clause stops: whole, not cut
        nxt, _ = _squeeze(" ".join(rest.split()[:3]))
        if len(nxt) >= 6 and nxt in frame_squeezed:
            continue  # the continuation IS on screen: OCR split a longer block, nothing is cut
        return src, nxt
    return None


def frame_findings(png_path: str, sources: Sequence[tuple[str, str, list[int]]],
                   ocr: Callable[[str], list[dict[str, Any]]] = tesseract_words) -> list[dict[str, str]]:
    """The cut-text findings on one frame (see the module docstring)."""
    rows = ocr(png_path)
    from PIL import Image

    with Image.open(png_path) as im:
        fw, fh = im.size
    found: list[dict[str, str]] = []
    lines: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for w in rows:
        lines.setdefault((w.get("block_num", ""), w.get("par_num", ""), w.get("line_num", "")), []).append(w)
        try:
            conf = float(w["conf"])
            x, y, ww, hh = int(w["left"]), int(w["top"]), int(w["width"]), int(w["height"])
        except (KeyError, TypeError, ValueError):
            continue
        text = w["text"].strip()
        if conf >= EDGE_CONF and sum(c.isalnum() for c in text) >= 2 and (
                x <= EDGE_PX or y <= EDGE_PX or x + ww >= fw - 1 - EDGE_PX or y + hh >= fh - 1 - EDGE_PX):
            found.append({"kind": "EDGE", "text": text})
    for ws in lines.values():
        last = ws[-1]["text"].strip()
        # A real word before the dots ("plus…", "avoidable…"): two letters of OCR noise ("ae...") on
        # a faint glyph is not a length cap (ep2 2eddeaaf f_053).
        if last.endswith(("…", "...", "..")) and sum(c.isalnum() for c in last) >= 3:
            found.append({"kind": "ELLIPSIS", "text": " ".join(w["text"].strip() for w in ws)})
    frame_sq, _ = _squeeze(" ".join(w["text"] for w in rows))
    for block in _blocks(rows):
        cut = _cut_prefix(block, sources, frame_sq)
        if cut:
            src, nxt = cut
            found.append({"kind": "PREFIX", "text": block, "source": src, "next": nxt})
    return found


def check_frames(frames: Sequence[str], doc: dict[str, Any], *, skip: Callable[[int], bool] = lambda _i: False,
                 ocr: Callable[[str], list[dict[str, Any]]] | None = None,
                 workers: int = 4) -> dict[str, Any]:
    """Run :func:`frame_findings` over every frame not ``skip``-ped (index -> True for a photo).
    ``ocr`` defaults to :func:`tesseract_words`, resolved at call time. ``status`` is ``"skipped"``
    when OCR could not run on any frame — never a pass."""
    sources = [(s, *_squeeze(s)) for s in source_lines(doc)]
    todo = [(i, f) for i, f in enumerate(frames) if not skip(i)]
    memo: dict[str, list[dict[str, Any]]] = {}
    read = ocr or tesseract_words

    def ocr(path: str) -> list[dict[str, Any]]:  # each frame is OCR'd once, however many readers
        if path not in memo:
            memo[path] = read(path)
        return memo[path]
    flagged: dict[str, list[dict[str, str]]] = {}
    ran = failed = 0

    def one(item: tuple[int, str]) -> tuple[str, list[dict[str, str]] | None]:
        try:
            return os.path.basename(item[1]), frame_findings(item[1], sources, ocr)
        except Exception:  # noqa: BLE001 — a frame OCR could not read is counted, never passed
            return os.path.basename(item[1]), None

    texts: dict[str, str] = {}

    def one_with_text(item: tuple[int, str]) -> tuple[str, list[dict[str, str]] | None]:
        name, found = one(item)
        if found is not None:
            try:
                texts[name] = _squeeze(" ".join(w["text"] for w in ocr(item[1])))[0]
            except Exception:  # noqa: BLE001 — the text only feeds the reveal filter below
                texts[name] = ""
        return name, found

    names_in_order = [os.path.basename(f) for _, f in todo]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for name, found in pool.map(one_with_text, todo):
            if found is None:
                failed += 1
                continue
            ran += 1
            if found:
                flagged[name] = found
    # A REVEAL IS NOT A CUT. A card that writes its sentence word by word shows an opening of it on
    # one frame and the rest a few seconds later (ep6 c6c180c8 f_179 -> f_180). A cut never shows
    # its continuation: drop a PREFIX whose next words appear in one of the following frames.
    for name in list(flagged):
        i = names_in_order.index(name)
        later = names_in_order[i + 1:i + 1 + REVEAL_LOOKAHEAD]
        kept = [f for f in flagged[name] if f["kind"] != "PREFIX"
                or not any(f.get("next") and f["next"] in texts.get(n, "") for n in later)]
        if kept:
            flagged[name] = kept
        else:
            del flagged[name]
    status = "skipped" if todo and not ran else "ran"
    return {"status": status, "frames_checked": ran, "frames_failed": failed,
            "frames_skipped_photo": len(frames) - len(todo), "flagged": flagged}


def issues(result: dict[str, Any]) -> list[dict[str, str]]:
    """One MAJOR issue per kind seen on at least :data:`MIN_FRAMES` frames, naming the frames."""
    if result.get("status") == "skipped":
        return [{"sev": "MAJOR", "msg": "TEXT-INTEGRITY: OCR unavailable on every frame — cut text "
                                        "was NOT checked (tesseract missing?)"}]
    by_kind: dict[str, list[str]] = {}
    example: dict[str, str] = {}
    for name, found in sorted((result.get("flagged") or {}).items()):
        for f in found:
            if name not in by_kind.setdefault(f["kind"], []):
                by_kind[f["kind"]].append(name)
            example.setdefault(f["kind"], f.get("text", ""))
    what = {"EDGE": "text touching the frame edge (cut by the frame or a camera move)",
            "ELLIPSIS": "a line ending in an ellipsis (a length cap on screen)",
            "PREFIX": "a sentence the job authored, shown only up to a point where it does not end "
                      "(cut by a budget or clipped by its box)"}
    out = []
    for kind in ("PREFIX", "ELLIPSIS", "EDGE"):
        names = by_kind.get(kind) or []
        if len(names) >= MIN_FRAMES:
            out.append({"sev": "MAJOR", "msg": (
                f"TEXT-CUT {kind}: {len(names)}/{result.get('frames_checked', 0)} checked frames show "
                f"{what[kind]} — e.g. {example[kind][:90]!r}; frames {', '.join(names[:12])}"
                + (" …" if len(names) > 12 else ""))})
    return out
