"""Find jobs whose STATUS contradicts their own DATA. $0 — one Firestore scan, no writes.

Two sibling contradictions, both observed on 2026-08-20:

  A. status=completed WITH error_message set
     -> the retry recovered the job but nothing cleared the field. `common/terminal_error_hygiene`
        fixes this going forward (failure mirrors to last_attempt_error; completion DELETES
        error_message), so a hit here means an OLD doc or a path that bypassed the hygiene.

  B. status=failed WITH a non-empty segments_ready        <-- the one that has no fix yet
     -> the job says "No audio segments generated" while carrying the segments. Seen ONCE in 300
        jobs (520a735b) and never reproduced. The cause is UNRESOLVED: a deploy-window transient
        and an invalidation race were both tested and neither survives — 294 of 300 jobs invalidate
        their script attempt and only that one failed.

Run this after any incident, or on a schedule. A second (B) hit is what turns a rare anomaly into
a diagnosable pattern; until then, do NOT ship a speculative fix for it.

Usage: python3 detect_contradictory_job_state.py [limit]
"""
import sys

from google.cloud import firestore

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 300
db = firestore.Client(project="kitesforu-dev")
rows = db.collection("podcast_jobs").order_by(
    "created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()

a_hits, b_hits, scanned = [], [], 0
for d in rows:
    doc = d.to_dict() or {}
    scanned += 1
    status = doc.get("status")
    err = str(doc.get("error_message") or "").strip()
    segs = doc.get("segments_ready")
    n_segs = len(segs) if isinstance(segs, list) else 0
    if status == "completed" and err:
        a_hits.append((d.id[:8], err[:60]))
    if status == "failed" and n_segs > 0:
        b_hits.append((d.id[:8], n_segs, err[:60]))

print(f"scanned {scanned} jobs\n")
print(f"(A) completed WITH error_message: {len(a_hits)}")
for jid, err in a_hits[:5]:
    print(f"    {jid}  {err}")
print(f"\n(B) failed WITH non-empty segments_ready: {len(b_hits)}   <-- the unresolved one")
for jid, n, err in b_hits[:5]:
    print(f"    {jid}  segments_ready={n}  {err}")
if not b_hits:
    print("    none — the anomaly has not recurred in this window")
