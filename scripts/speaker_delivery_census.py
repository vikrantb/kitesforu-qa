#!/usr/bin/env python3
"""Does a multi-voice cast actually reach the listener? $0, read-only.

    python3 scripts/speaker_delivery_census.py [--since 2026-08-27T00:30:00]

Answers one question: of the jobs CAST with >=2 voices, how many DELIVERED
fewer than they were cast with — and which stage lost them.

WHY THIS EXISTS. On 2026-08-26 the founder's "I always hear the 2 voices" was
traced to four independent causes, and the only way to know which fixes worked
is the SAME command on both arms. Baseline that day (n=398 datetime-dated jobs,
2026-07-19..2026-08-26, of 4145 docs):

    166 jobs cast >=2 voices with a measurable final script
     55 delivered FEWER than cast  (33%)
        40  the sub-capsule trim collapsed it   -> fixed 941376db (#2732)
         9  the regen traded a host for length  -> fixed a1d4481c (#2733)
         4  script single-speaker from attempt 1 -> #2734 (held)
         2  cast never reached (audio_config self-contradiction)

METHOD — two traps this script exists to avoid:

 1. UNORDERED FULL SCAN. `podcast_jobs.created_at` is MIXED-TYPE (measured
    2026-08-26: 1198 datetime / 1 str / 1 None per 1200), so
    `order_by("created_at").limit(N)` TYPE-CLIPS — the string-dated rows eat
    slots in the ordered window. On that date it cost 2 of 400 slots and missed
    ZERO real jobs, but the margin is not guaranteed. Scan everything and filter
    in Python.

 2. THE FINAL ATTEMPT IS NOT attempt_1. Read the HIGHEST attempt_N_metrics
    present; comparing the cast against attempt 1 measures the script the
    pipeline threw away.

Use --since to isolate post-deploy traffic. Same command, both arms.
"""
from __future__ import annotations

import argparse
import datetime
import json
from collections import Counter

from google.cloud import firestore

_ATTEMPTS = ("attempt_3_metrics", "attempt_2_metrics", "attempt_1_metrics")


def dig(d, *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _speakers(qg, attempt):
    dist = dig(qg, attempt, "speaker_balance", "distribution") or {}
    return sorted(dist) if isinstance(dist, dict) else []


def classify(job: dict) -> str | None:
    """Which stage lost the voices, or None when nothing was lost."""
    cast = dig(job, "audio_config", "speaker_count")
    qg = dig(job, "stages", "quality_gate") or {}
    present = [a for a in _ATTEMPTS if isinstance(qg.get(a), dict)]
    if not cast or cast < 2 or not present:
        return None
    final = _speakers(qg, present[0])
    if not final or len(final) >= cast:
        return None

    blob = json.dumps(job, default=str)
    # The trim records its own verdict; prefer the stamp over inference.
    if '"sub_capsule_trim"' in blob and '"applied": true' in blob.lower():
        return "trim collapsed it"
    if '"regen_deleted_a_speaker": true' in blob.lower():
        return "regen traded a host (caught by the new guard)"
    first = _speakers(qg, "attempt_1_metrics")
    if len(first) == 1:
        return "script single-speaker from attempt 1"
    if len(final) < len(first):
        return "regen traded a host"
    return "cast never reached by any attempt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO8601; only jobs created at/after this")
    ap.add_argument("--project", default="kitesforu-dev")
    args = ap.parse_args()

    since = None
    if args.since:
        since = datetime.datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=datetime.timezone.utc)

    db = firestore.Client(project=args.project)
    scanned = eligible = under = 0
    causes: Counter[str] = Counter()
    oldest = newest = None

    for doc in db.collection("podcast_jobs").stream():  # UNORDERED — see METHOD
        job = doc.to_dict() or {}
        scanned += 1
        created = job.get("created_at")
        if not isinstance(created, datetime.datetime):
            continue  # the mixed-type rows; never silently counted
        if since and created < since:
            continue
        oldest = created if oldest is None or created < oldest else oldest
        newest = created if newest is None or created > newest else newest

        cast = dig(job, "audio_config", "speaker_count")
        qg = dig(job, "stages", "quality_gate") or {}
        if not cast or cast < 2 or not any(isinstance(qg.get(a), dict) for a in _ATTEMPTS):
            continue
        eligible += 1
        cause = classify(job)
        if cause:
            under += 1
            causes[cause] += 1

    print(f"scanned {scanned} docs" + (f" since {args.since}" if since else ""))
    if not eligible:
        print("  NO eligible jobs in this window — nothing to report.")
        print("  (a cast>=2 job with a measurable final script is required)")
        return
    print(f"  window: {oldest} .. {newest}")
    print(f"  jobs CAST with >=2 voices and a measurable final script: {eligible}")
    pct = 100 * under / eligible
    print(f"  delivered FEWER voices than cast: {under}  ({pct:.0f}%)")
    for cause, n in causes.most_common():
        print(f"      {n:4d}  {cause}")


if __name__ == "__main__":
    main()
