#!/usr/bin/env python3
"""Read the paid-still ledger for one job and say whether pictures were delivered.

Built 2026-08-25 because answering "why did this episode get one picture?" took a
40-minute Cloud Logging window across two jobs. Everything below is now either a
field on the job doc or one log count.

    PYTHONPATH=../kitesforu-workers/src python3 check_paid_still_ledger.py <job_id>

$0 — Firestore + Cloud Logging reads only. Never writes, never generates (Tenet 9).
"""
from __future__ import annotations

import subprocess
import sys

from google.cloud import firestore

PROJECT = "kitesforu-dev"


def _entry_count(job_id: str) -> str:
    """How many times the visuals worker RAN for this job. 12 on job 6691727f,
    before the redelivery/lease fixes (b5fad00d, 33cb5c36, e1d07410) deployed.
    Expected 1."""
    q = (
        'resource.type="cloud_run_revision" AND '
        'resource.labels.service_name="kitesforu-worker-visuals" AND '
        'jsonPayload.message:"run_visuals ENTRY" AND jsonPayload.message:"%s"'
        % job_id[:8]
    )
    try:
        out = subprocess.run(
            ["gcloud", "logging", "read", q, "--project", PROJECT,
             "--limit", "60", "--freshness", "1d", "--format", "value(timestamp)"],
            capture_output=True, text=True, timeout=120,
        ).stdout.strip()
        return str(len([x for x in out.splitlines() if x.strip()]))
    except Exception as exc:  # noqa: BLE001
        return f"(log read failed: {str(exc)[:60]})"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    job_id = sys.argv[1]
    doc = firestore.Client(project=PROJECT).collection("podcast_jobs").document(job_id).get()
    if not doc.exists:
        print(f"job {job_id} NOT FOUND")
        return 2
    j = doc.to_dict() or {}
    v = j.get("visual") or {}
    clips = v.get("clips") or []

    print(f"job {job_id}   status={j.get('status')}   visual.status={v.get('status')}")
    print(f"  quality_tier={(j.get('preferences') or {}).get('quality_tier') or j.get('quality_tier')}"
          f"  visual_options={j.get('visual_options')}")

    ledger = v.get("paid_still_ledger")
    if not ledger:
        print("\n  ⚠️  NO paid_still_ledger — either the job predates the stamp, or the")
        print("      stamp did not fire. That is itself the finding; do not read on as")
        print("      though the numbers below are the ledger's.")
    else:
        print("\n  paid_still_ledger:")
        for k in ("requested", "granted", "delivered", "delivered_billing_counted",
                  "billing_undercount", "shortfall"):
            if k in ledger:
                print(f"      {k:26s} {ledger[k]}")
        extra = {k: x for k, x in ledger.items() if k.startswith("scene_")}
        if extra:
            print(f"      {'budget chain':26s} {extra}")
        if ledger.get("shortfall"):
            print(f"      ⚠️  SHORTFALL {ledger['shortfall']} — granted more than was delivered")

    # Independent recount from the clips, so the ledger is checked and not trusted.
    try:
        sys.path.insert(0, "../kitesforu-workers/src")
        from workers.stages.visuals.renderer import _is_ai_generated_clip

        ai = [c for c in clips if isinstance(c, dict) and _is_ai_generated_clip(c)]
        kinds: dict = {}
        for c in ai:
            k = str((c.get("diagram_debug") or {}).get("kind") or c.get("modality") or "?")
            kinds[k] = kinds.get(k, 0) + 1
        print(f"\n  RECOUNT from clips (SSOT detector), not the ledger:")
        print(f"      clips                      {len(clips)}")
        print(f"      AI pictures                {len(ai)}   kinds={kinds}")
        print(f"      with a model_id (billing)  {sum(1 for c in clips if c.get('model_id'))}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (recount unavailable: {str(exc)[:80]})")

    rollup = (j.get("costs") or {}).get("visuals_images")
    if rollup:
        print(f"\n  cost rollup: ${rollup.get('total_cost_usd')}  meta={rollup.get('meta')}")
        print("      (models here that are ABSENT from the clips = paid generations that")
        print("       never reached the artifact — job 681b64ce lost 4 that way)")

    print(f"\n  run_visuals ENTRY count: {_entry_count(job_id)}   (12 on 6691727f; expect 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
