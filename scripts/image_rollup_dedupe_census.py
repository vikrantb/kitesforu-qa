#!/usr/bin/env python3
"""Price each job's FINAL clip array two ways: once per clip, and once per paid asset.

This is the census workers #3239 cited in round D, committed so the PR can cite a command rather
than an output file. It answers ONE question: how much does pricing a still once per SHOWING
(``worker._sum_visuals_image_cost``, the per-clip rule every stamp before #3239 was written with)
differ from pricing it once per ASSET (``image_cost_ledger.paid_assets`` over the clips, no
receipt)?

WHAT IT CANNOT ANSWER: what was paid. Both arms read the final clips, and the provider is paid
for renders no clip carries (a verify REJECT or REGEN, an aptness degrade, a pass the next plan
drops). Witness e032d06d: 5 renders paid, per-clip arm 3, per-asset arm 1. For the provider-side
record use ``image_spend_provider_census.py``. Both arms here are therefore floors on spend of
unknown distance, and neither is "what was paid".

POPULATION: the ``--limit`` newest ``podcast_jobs`` by ``created_at`` at or before ``--until``.
Prices are the catalog's at the ``WORKERS_SRC`` tree (``compute_asset_cost``), so both arms use
the same prices. Owners are labelled by class only (test@ / e2e / founder / other / unknown);
``KFU_FOUNDER_EMAILS`` names the founder's personal accounts and is never committed.

USAGE::

    WORKERS_SRC=../kitesforu-workers/src python scripts/image_rollup_dedupe_census.py \\
        --until 2026-10-01T03:43:00Z --limit 300

Read-only Firestore. No provider call, no write.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import os
import sys
from pathlib import Path
from typing import Any

PROJECT = "kitesforu-dev"
_QA_ROOT = Path(__file__).resolve().parents[1]
_WORKERS_SRC = os.environ.get("WORKERS_SRC") or str(_QA_ROOT.parent / "kitesforu-workers" / "src")
_FOUNDER_EMAILS = frozenset(
    e.strip().lower() for e in os.environ.get("KFU_FOUNDER_EMAILS", "").split(",") if e.strip()
)


def per_clip_usd(clips: list[Any], price: Any) -> float:
    """``_sum_visuals_image_cost`` (deleted by workers #3239), byte-for-byte in its rules: skip a
    reused re-cut, price ``rendered_model_id`` over ``model_id`` once per clip, price an
    ai_generated relimage at the engine estimate."""
    usd = 0.0
    for c in clips:
        if not isinstance(c, dict):
            continue
        ev = c.get("imagination_event")
        if isinstance(ev, dict) and ev.get("reused") is True:
            continue
        mid = c.get("rendered_model_id") or c.get("model_id")
        if mid:
            usd += price(mid)
            continue
        dd = c.get("diagram_debug") or {}
        if c.get("ai_generated") and str(dd.get("kind") or "") == "relimage":
            usd += price("relimage")
    return usd


def _owner(db: Any, uid: str, cache: dict[str, str]) -> str:
    if uid not in cache:
        label = "unknown"
        try:
            u = db.collection("users").document(uid).get()
            email = str(((u.to_dict() or {}) if u.exists else {}).get("email") or "").lower()
            # The E2E harness signs in as the same test@ address under its own user id
            # (``…_e2e``), so the id decides before the address does.
            if "e2e" in uid.lower() or "e2e" in email:
                label = "e2e"
            elif email.startswith("test@"):
                label = "test@"
            elif email and email in _FOUNDER_EMAILS:
                label = "founder"
            elif email:
                label = "other"
        except Exception:  # noqa: BLE001
            pass
        cache[uid] = label
    return cache[uid]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default=datetime.datetime.now(datetime.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"))
    ap.add_argument("--limit", type=int, default=300)
    args = ap.parse_args()

    sys.path.insert(0, _WORKERS_SRC)
    from google.cloud import firestore  # noqa: E402
    from workers.stages.visuals.image_cost_ledger import (  # noqa: E402
        _asset_id,
        _clip_price,
        paid_assets,
        price_of,
    )

    until = datetime.datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    db = firestore.Client(project=PROJECT)
    q = (db.collection("podcast_jobs").where("created_at", "<=", until)
         .order_by("created_at", direction=firestore.Query.DESCENDING).limit(args.limit)
         .select(["visual.clips", "costs.visuals_images", "created_at", "user_id"]))
    owners: dict[str, str] = {}
    scanned = priced = differ = priced_clips = no_identity = 0
    arm_a = arm_b = stored = 0.0
    owner_counts: collections.Counter = collections.Counter()
    newest = oldest = None
    per_job_assets: list[int] = []
    for snap in q.stream():
        d = snap.to_dict() or {}
        scanned += 1
        ca = d.get("created_at")
        newest = newest or ca
        oldest = ca
        clips = ((d.get("visual") or {}).get("clips")) or []
        clips = clips if isinstance(clips, list) else []
        a = per_clip_usd(clips, price_of)
        assets, _unnamed, unnamed_usd = paid_assets(clips)
        b = sum(float(x["usd"]) for x in assets.values()) + unnamed_usd
        if a <= 0 and b <= 0:
            continue
        priced += 1
        per_job_assets.append(len(assets) + sum(_unnamed.values()))
        owner_counts[_owner(db, str(d.get("user_id") or ""), owners)] += 1
        arm_a += a
        arm_b += b
        if round(a, 6) != round(b, 6):
            differ += 1
        for c in clips:
            if _clip_price(c) is not None:
                priced_clips += 1
                if _asset_id(c) is None:
                    no_identity += 1
        block = (d.get("costs") or {}).get("visuals_images")
        if isinstance(block, dict):
            stored += float(block.get("total_cost_usd") or 0.0)

    print(f"IMAGE ROLLUP DEDUPE CENSUS — {args.limit} newest podcast_jobs, created_at <= {args.until}")
    print(f"  scanned {scanned} (newest {newest}, oldest {oldest}); priced > $0: {priced}")
    print(f"  owners of priced jobs: {dict(owner_counts)}")
    print(f"  ARM A per clip (pre-#3239 rule)       : ${arm_a:.4f}")
    print(f"  ARM B per asset in the final clips   : ${arm_b:.4f}   (A/B {arm_a / arm_b:.3f})"
          if arm_b else "  ARM B per asset in the final clips   : $0")
    print(f"  jobs where the arms differ           : {differ} of {priced}")
    print(f"  stored costs.visuals_images          : ${stored:.4f}")
    print(f"  priced clips {priced_clips}; with no identity (no hash, no uri): {no_identity}")
    if per_job_assets:
        xs = sorted(per_job_assets)
        print(f"  distinct paid assets per priced job: p50 {xs[len(xs) // 2]}, "
              f"p90 {xs[int(len(xs) * 0.9)]}, max {xs[-1]}")
    print("  NEITHER ARM IS WHAT WAS PAID — see image_spend_provider_census.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
