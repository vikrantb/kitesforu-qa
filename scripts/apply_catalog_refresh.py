#!/usr/bin/env python3
"""Apply provider-verified prices and lifecycle dates to model_catalog.csv.

WHY THIS IS A SCRIPT AND NOT HAND-EDITS
---------------------------------------
The catalog is mirrored across two repos. A hand-edit applies to one, drifts from
the other, and leaves no record of WHERE a number came from. This reads a cited
ground-truth file, applies it to every mirror, and stamps each touched row with
the source URL and the date it was read.

THE GUARD THAT MATTERS
----------------------
Every correction declares the value it EXPECTS to find. If the catalog does not
hold that value, the row is REFUSED, not overwritten -- the catalog changed since
the research was done, so the research is stale for that row. A refusal is a
non-zero exit, never a silent skip.

    python3 apply_catalog_refresh.py --check    # report only, no writes
    python3 apply_catalog_refresh.py --apply
"""
from __future__ import annotations
import argparse, csv, io, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GROUND_TRUTH = HERE.parent / "data" / "model_pricing_ground_truth.json"
WS = HERE.parent.parent
# ONLY the authoritative catalog. kitesforu-workers/config/model_catalog.csv is
# the single source of truth; model_catalog_for_sheets.csv and the course-workers
# copy are BYTE MIRRORS regenerated mechanically by
# kitesforu-workers/scripts/sync_catalog_mirrors.py. Writing a mirror directly
# makes it drift from its generator -- run the sync script instead.
CATALOGS = [
    WS / "kitesforu-workers" / "config" / "model_catalog.csv",
]
PROV_COLS = ["price_verified", "price_source_url"]


def _close(a: str, b: float) -> bool:
    try:
        return abs(float(a) - b) < 1e-9
    except (TypeError, ValueError):
        return False


def load_rows(path: Path):
    with path.open() as fh:
        r = csv.DictReader(fh)
        return list(r), list(r.fieldnames or [])


def _write_changed_records(path: Path, text: str, cols: list[str], by_id: dict, touched: set) -> None:
    """Rewrite ONLY the records whose values changed; every other byte stays as it was.

    A DictWriter rewrite padded each ``#`` comment line with a run of trailing commas and
    re-serialised all 149 rows, so one price change was a 34-line diff a reviewer had to read
    line by line (2026-09-30). Spans come from the csv reader's own line count, so a quoted
    field holding a newline is still one record.
    """
    lines = text.splitlines(keepends=True)
    reader = csv.reader(lines)
    out, prev = [], 0
    for rec in reader:
        span, prev = lines[prev:reader.line_num], reader.line_num
        mid = rec[0] if rec else ""
        if mid in touched:
            buf = io.StringIO()
            csv.writer(buf, lineterminator="").writerow([by_id[mid].get(c, "") for c in cols])
            tail = span[-1]
            out.append(buf.getvalue() + tail[len(tail.rstrip("\r\n")):])
        else:
            out.extend(span)
    with path.open("w", newline="") as fh:
        fh.write("".join(out))


def apply_to(path: Path, gt: dict, write: bool):
    with path.open(newline="") as fh:  # keep the file's own line endings (the catalog is CRLF)
        text = fh.read()
    rows, cols = load_rows(path)
    header_before = list(cols)
    by_id = {r["model_id"]: r for r in rows}
    before = {mid: dict(r) for mid, r in by_id.items()}
    read_date = gt["read_date"]
    changes, refusals = [], []

    for col in PROV_COLS:
        if col not in cols:
            cols.append(col)
            for r in rows:
                r.setdefault(col, "")

    # A model can be read more than once. Only its NEWEST read applies; the older reads are the
    # history of how the row got to its price, skipped rather than refused. Without this a chain
    # (91.65 -> 50 read 2026-08-27, then 50 -> 40 read 2026-09-30) refuses its own earlier
    # entry on the re-run after the second is applied. Each entry dates itself; the top-level
    # read_date is the default for an entry that does not.
    newest: dict = {}
    for c in gt["price_corrections"]:
        when = c.get("read_date", read_date)
        if c["model_id"] not in newest or when >= newest[c["model_id"]][0]:
            newest[c["model_id"]] = (when, c)

    for when, c in newest.values():
        mid = c["model_id"]
        row = by_id.get(mid)
        if row is None:
            refusals.append(f"{mid}: not in {path.parent.parent.name}")
            continue
        cur = row["cost_per_unit"]
        if _close(cur, c["new"]):
            row["price_verified"] = when
            row["price_source_url"] = c["source"]
            continue  # already applied -- idempotent
        if not _close(cur, c["old"]):
            refusals.append(
                f"{mid}: expected {c['old']}, catalog holds {cur} -- research is stale for this row"
            )
            continue
        if row["unit_description"].strip() != c["unit"]:
            refusals.append(
                f"{mid}: unit mismatch -- catalog '{row['unit_description']}' vs research '{c['unit']}'"
            )
            continue
        row["cost_per_unit"] = f"{c['new']:.2f}"
        row["price_verified"] = when
        row["price_source_url"] = c["source"]
        # The row's prose must not keep quoting the price it no longer holds.
        row["notes"] = (
            f"{row.get('notes') or ''} || {when} PRICE CORRECTED {c['old']} -> {c['new']} "
            f"({c['unit']}): {c.get('why_we_were_wrong', '').strip()} Source: {c['source']}"
        ).lstrip(" |")
        changes.append(f"{mid}: {c['old']} -> {c['new']} ({c['unit']})")

    # Each confirmation names the page it was read from: a hardcoded Google URL was stamped on
    # every confirmed row whatever its provider.
    for c in gt.get("confirmed_correct_no_change", []):
        row = by_id.get(c["model_id"])
        if row is not None and _close(row["cost_per_unit"], c["value"]):
            row["price_verified"] = c.get("read_date", read_date)
            row["price_source_url"] = c["source"]

    # A date moves only from the value the research EXPECTED (``old_eol``; absent = unset), the
    # same staleness guard prices have. Without it the applier reverted two deliberate
    # 2026-09-23 corrections made after the 2026-08-27 read: gemini-2.5-flash-image back to the
    # Gemini API's "earliest possible" 2026-10-02 (it is served through Vertex, which retires
    # it 2027-03-15), and gemini-3.1-flash-lite's cleared availability floor back to 2027-05-07.
    # eol_date is an active switch -- a past date drops the row -- so a stale date is an outage.
    for L in gt["lifecycle"]:
        mid = L["model_id"]
        row = by_id.get(mid)
        if row is None:
            continue
        cur = (row.get("eol_date") or "").strip()
        want = L["eol_date"]
        if cur == want:
            continue
        expected = (L.get("old_eol") or "").strip()
        if cur != expected:
            refusals.append(
                f"{mid}: eol expected {expected or 'unset'}, catalog holds {cur or 'unset'} "
                f"-- research is stale for this row"
            )
            continue
        changes.append(f"{mid}: eol {cur or 'unset'} -> {want}")
        row["eol_date"] = want

    if write and changes:
        if cols != header_before:
            # A new provenance column changes every row, so the whole table is rewritten.
            with path.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
        else:
            touched = {mid for mid, r in by_id.items() if r != before[mid]}
            _write_changed_records(path, text, cols, by_id, touched)
    return changes, refusals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument(
        "--catalog", action="append", type=Path,
        help="the catalog to check/apply (repeatable); default: the workspace's workers catalog. "
             "Name it when this script runs from a worktree, whose parent is not the workspace.",
    )
    a = ap.parse_args(argv)
    if not (a.apply or a.check):
        ap.error("pass --check or --apply")
    gt = json.loads(GROUND_TRUTH.read_text())
    rc = 0
    for path in a.catalog or CATALOGS:
        if not path.exists():
            # An absent catalog is a failure, not a skip: from a worktree the default path
            # resolves outside the workspace, and "SKIP ... exit 0" read as a clean check of
            # a catalog nobody looked at (2026-09-30).
            print(f"ABSENT: {path} -- nothing was checked")
            rc = 1
            continue
        ch, ref = apply_to(path, gt, write=a.apply)
        print(f"\n=== {path.parent.parent.name} ===")
        for c in ch:
            print(f"  {'APPLIED' if a.apply else 'WOULD APPLY'}: {c}")
        for r in ref:
            print(f"  REFUSED: {r}")
        if not ch and not ref:
            print("  no changes (already current)")
        if ref:
            rc = 1
    if rc:
        print("\nRefusals above: the catalog disagrees with the research. Re-verify before forcing.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
