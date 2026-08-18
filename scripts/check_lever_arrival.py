#!/usr/bin/env python3
"""Did a job receive the user's typed lever answers? Read-only, $0, one job.

SCOPE — deliberately small. This answers ONE question: for a job that already exists, did the
Creation Canvas's typed answers reach `preferences`? It does NOT create a job and does NOT drive
the UI.

WHY IT IS THIS SHAPE. The carrier half — "a real signed-in user tapping a real chip produces an
execute request carrying `canvas_session_id`" — is pinned at $0 by the browser, in
`kitetest tests/staging/canvas-lever-answers-reach-the-job.spec.ts` (#130); it inspects the POST
body and ABORTS the request, so no job is created. The last link — the worker actually receiving
`preferences['_sub_mode_sliders']` — is the half a browser cannot see, and this is that half.

I first wrote a script that tried to do BOTH: drive gated intake, refine, batch intake and execute,
then read the doc. It never produced a verdict, and every failure was mine — SSE parsed as JSON, a
python.org interpreter with no CA bundle, one refine hop when lever questions only appear in round
2+, a batch intake needing its own question loop. That orchestration is deleted rather than left
half-working: a broken instrument is worse than none, because it returns something that LOOKS like
a verdict.

USAGE:  check_lever_arrival.py <job_id>          # one job
        check_lever_arrival.py --recent 200      # scan for ANY job that carries them
"""
from __future__ import annotations
import sys
from google.cloud import firestore

KEYS = ("_sub_mode_sliders", "_lever_answers", "_charge_level", "_content_rating", "_sub_mode")
db = firestore.Client(project="kitesforu-dev")


def report(job_id: str, prefs: dict) -> bool:
    under = sorted(k for k in prefs if k.startswith("_"))
    print(f"  job {job_id[:8]}  underscore prefs: {under or '<none>'}")
    hit = False
    for k in KEYS:
        if k in prefs:
            print(f"    {k}: {prefs[k]!r}")
            hit = True
    return hit


if len(sys.argv) >= 3 and sys.argv[1] == "--recent":
    n = int(sys.argv[2])
    # FULL UNORDERED scan: podcast_jobs.created_at is MIXED-TYPE, so order_by().limit() TYPE-CLIPS
    # to an arbitrary slice rather than "the N most recent".
    scanned = carried = 0
    for d in db.collection("podcast_jobs").limit(n).stream():
        j = d.to_dict() or {}
        if j.get("status") != "completed":
            continue
        scanned += 1
        prefs = j.get("preferences") or {}
        if any(k in prefs for k in KEYS):
            carried += 1
            report(d.id, prefs)
    print(f"\n  {carried} of {scanned} completed jobs carry ANY lever preference key.")
    print("  0 is the expected result until lever questions are actually asked — see the")
    print("  'catalog offers 13, generator asks zero' item in .claude/BACKLOG.md.")
    sys.exit(0)

if len(sys.argv) < 2:
    sys.exit(__doc__)

jid = sys.argv[1]
doc = db.collection("podcast_jobs").document(jid).get()
if not doc.exists:
    sys.exit(f"  job {jid} not found")
prefs = (doc.to_dict() or {}).get("preferences") or {}
if report(jid, prefs):
    print("\n  ✅ ARRIVED — a typed answer reached this job's preferences.")
else:
    print("\n  ❌ NOT PRESENT on this job. Before calling the wiring broken, check whether a lever")
    print("     question was ever ASKED for it — with zero asked, nothing could arrive.")
