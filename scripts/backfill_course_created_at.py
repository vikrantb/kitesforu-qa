#!/usr/bin/env python3
"""Normalise legacy STRING `courses.created_at` values to Firestore timestamps.

WHY. Firestore orders by TYPE GROUP before value, and string sorts above
timestamp. `courses` holds both shapes (measured 2026-08-22: 133 str, 94
datetime), so `order_by("created_at", DESCENDING).limit(N)` returns the string
rows FIRST and never reaches the datetime ones — the activity feed's course
query returned 25/25 strings whose newest was 2026-03-04, hiding every course
made since. Verified by executing the real query, not by reading the code.

The writer is already fixed: no string-typed course exists after 2026-03-04,
while datetime rows run to 2026-08-21. This is a one-off cleanup of legacy rows,
not a workaround for a live producer.

SAFETY
  * --dry-run is the DEFAULT. Nothing is written without --apply.
  * Only documents whose created_at is a `str` are touched. A datetime row is
    never rewritten, so re-running is idempotent.
  * The parsed instant must round-trip to the same wall-clock reading or the
    row is SKIPPED and reported — a stamp we cannot parse confidently is left
    exactly as it is.
  * Naive stamps are read as UTC: the writer used `datetime.utcnow().isoformat()`,
    so the offset is absent, not local.

USAGE
    python scripts/backfill_course_created_at.py              # dry run
    python scripts/backfill_course_created_at.py --apply      # writes
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from google.cloud import firestore


def parse_legacy(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    # round-trip guard: the instant must still read as the same wall clock
    if parsed.replace(tzinfo=None).isoformat() not in raw:
        return None
    return parsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--project", default="kitesforu-dev")
    args = ap.parse_args()

    db = firestore.Client(project=args.project)
    would, skipped, already = [], [], 0

    for snap in db.collection("courses").stream():
        ca = (snap.to_dict() or {}).get("created_at")
        if isinstance(ca, datetime):
            already += 1
            continue
        if not isinstance(ca, str):
            skipped.append((snap.id, repr(ca), "not a string or datetime"))
            continue
        parsed = parse_legacy(ca)
        if parsed is None:
            skipped.append((snap.id, ca, "unparseable / failed round-trip"))
            continue
        would.append((snap.id, ca, parsed))

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] courses.created_at normalisation")
    print(f"  already datetime (untouched) : {already}")
    print(f"  would convert                : {len(would)}")
    print(f"  SKIPPED (left as-is)         : {len(skipped)}")
    for cid, raw, why in skipped[:10]:
        print(f"     {cid[:10]}  {raw!r}  <- {why}")
    for cid, raw, parsed in would[:8]:
        print(f"     {cid[:10]}  {raw[:26]!r}  ->  {parsed.isoformat()}")
    if len(would) > 8:
        print(f"     ... and {len(would) - 8} more")

    if not args.apply:
        print("\n  Nothing written. Re-run with --apply to commit.")
        return 0

    batch, n = db.batch(), 0
    for cid, _raw, parsed in would:
        batch.update(db.collection("courses").document(cid), {"created_at": parsed})
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = db.batch()
    if n % 400:
        batch.commit()
    print(f"\n  WROTE {n} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
