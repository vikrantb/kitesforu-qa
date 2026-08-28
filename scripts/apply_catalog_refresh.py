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
import argparse, csv, json, sys
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


def apply_to(path: Path, gt: dict, write: bool):
    rows, cols = load_rows(path)
    by_id = {r["model_id"]: r for r in rows}
    read_date = gt["read_date"]
    changes, refusals = [], []

    for col in PROV_COLS:
        if col not in cols:
            cols.append(col)
            for r in rows:
                r.setdefault(col, "")

    for c in gt["price_corrections"]:
        mid = c["model_id"]
        row = by_id.get(mid)
        if row is None:
            refusals.append(f"{mid}: not in {path.parent.parent.name}")
            continue
        cur = row["cost_per_unit"]
        if _close(cur, c["new"]):
            row["price_verified"] = read_date
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
        row["price_verified"] = read_date
        row["price_source_url"] = c["source"]
        changes.append(f"{mid}: {c['old']} -> {c['new']} ({c['unit']})")

    for c in gt.get("confirmed_correct_no_change", []):
        row = by_id.get(c["model_id"])
        if row is not None and _close(row["cost_per_unit"], c["value"]):
            row["price_verified"] = read_date
            row["price_source_url"] = "https://ai.google.dev/gemini-api/docs/pricing"

    for L in gt["lifecycle"]:
        mid = L["model_id"]
        row = by_id.get(mid)
        if row is None:
            continue
        cur = (row.get("eol_date") or "").strip()
        want = L["eol_date"]
        if cur == want:
            continue
        if cur and cur != want:
            changes.append(f"{mid}: eol {cur} -> {want} (announced date differs)")
        else:
            changes.append(f"{mid}: eol unset -> {want}")
        row["eol_date"] = want

    if write and changes:
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    return changes, refusals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if not (a.apply or a.check):
        ap.error("pass --check or --apply")
    gt = json.loads(GROUND_TRUTH.read_text())
    rc = 0
    for path in CATALOGS:
        if not path.exists():
            print(f"SKIP (absent): {path}")
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
