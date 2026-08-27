#!/usr/bin/env python3
"""What a job COSTS us versus what the rate card CHARGES.

WHY THIS EXISTS
    `kitesforu-workers/src/workers/common/job_pricing.py` implements a 3x display
    multiplier the founder asked for on 2026-08-14 -- and nothing calls it. Before
    deciding whether to wire or delete it, the useful question is what margin the
    SHIPPED path already delivers, because the shipped path is a different pricing
    philosophy:

        RATE CARD (live):  duration x quality x priority x intelligence -> credits
                           credits x credit_efficiency ($/credit) -> the USD shown
                           (kitesforu-api/src/api/routes/users.py:155)
        COST-PLUS (unwired): observed internal cost x 3

    They never meet, which is why job_pricing has no callers.

RESULT 2026-08-27 (real-sized jobs, >5 credits, n=30, internal cost p50 $0.1159):
    Enthusiast 6.90x · Creator 5.34x · Pro 4.49x · Studio 3.61x · PAYG 2.72x
    => the 3x target is already exceeded on 3 of 5 tiers. The exposure is the CHEAP
       tiers on EXPENSIVE jobs: PAYG p25 2.15x, Studio p25 2.86x.

HONEST LIMITS -- state these with any number this prints:
    * 96% of the raw completed-with-cost population is `test_user_e2e` (verification
      traffic), which is why the >5-credit cut matters and why n is small.
    * GROSS margin on direct generation cost only. Excludes Cloud Run, storage,
      egress and support. Net is lower than anything printed here.
    * credits_charged is absent on free/trial jobs; those are dropped, not zero-filled.

USAGE
    cd kitesforu-qa/scripts && python3 margin_census.py
    python3 margin_census.py --min-credits 5
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys

sys.path.insert(0, ".")

from capture_starved_measurements import COLLECTION, PROJECT  # noqa: E402
from google.cloud import firestore  # noqa: E402

# $ per credit, mirrored from kitesforu-api/src/api/services/tiers/configs.py.
# A mirror drifts: re-read that file before quoting these.
RATE = {
    "Enthusiast": 0.1267,
    "Creator": 0.0980,
    "Pro": 0.0825,
    "Studio": 0.0663,
    "PAYG": 0.0500,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-credits", type=float, default=5.0,
                    help="exclude jobs at or below this many credits (tiny test runs)")
    args = ap.parse_args()

    db = firestore.Client(project=PROJECT)
    rows: list[tuple[float, float, str]] = []
    drop: collections.Counter = collections.Counter()
    scanned = 0

    for doc in db.collection(COLLECTION).stream():
        scanned += 1
        d = doc.to_dict() or {}
        if d.get("status") != "completed":
            drop["not completed"] += 1
            continue
        costs = d.get("costs")
        internal = costs.get("total_usd_estimate") if isinstance(costs, dict) else None
        credits = d.get("credits_charged")
        if not isinstance(internal, (int, float)) or internal <= 0:
            drop["no internal cost"] += 1
            continue
        if not isinstance(credits, (int, float)) or credits <= 0:
            drop["no credits_charged (free/trial)"] += 1
            continue
        rows.append((float(internal), float(credits), str(d.get("user_id") or "?")))

    print(f"scanned {scanned} job docs · usable {len(rows)}")
    for k, v in drop.most_common():
        print(f"   dropped: {k}: {v}")
    if not rows:
        print("NO USABLE ROWS — do not quote a number")
        return 1

    users = collections.Counter(r[2] for r in rows)
    top_user, top_n = users.most_common(1)[0]
    print(f"\npopulation is {top_n * 100 // len(rows)}% `{top_user}` "
          f"— report this with any figure below")

    big = [(i, c) for i, c, _ in rows if c > args.min_credits]
    print(f"\nreal-sized jobs (> {args.min_credits:g} credits): n={len(big)}")
    if len(big) < 8:
        print("   TOO FEW to quote a median — reporting n only")
        return 0

    inter = sorted(i for i, _ in big)
    med = statistics.median(inter)
    print(f"   internal cost  p50 ${med:.4f}  p25 ${inter[len(inter)//4]:.4f}  "
          f"p75 ${inter[3*len(inter)//4]:.4f}")
    print("\n   REVENUE / INTERNAL COST (median of the per-job ratio):")
    for tier, rate in RATE.items():
        ratios = sorted((c * rate) / i for i, c in big)
        print(f"     {tier:<12} ${rate:.4f}/cr  median {statistics.median(ratios):5.2f}x"
              f"   p25 {ratios[len(ratios)//4]:5.2f}x")
    print(f"\n   a 3x COST-PLUS price on the p50 job = ${med * 3:.4f}")
    print("   GROSS margin only — excludes Cloud Run, storage, egress, support.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
