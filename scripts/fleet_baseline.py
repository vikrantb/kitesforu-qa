#!/usr/bin/env python3
"""Capture the fleet's PRE-CHANGE baseline so tonight's deploys are attributable. $0, read-only.

WHY NOW. The census adversary's sharpest process note: "a baseline recorded AFTER the changes ship
is not a baseline... when the fleet moves, nobody can say which of ~20 deletions moved it." Tonight
shipped #2476 (fact contract), #2477 (format matrix), #2478 (audio dynamics) and the visual gate
predicate. NO JOB HAS RUN AGAINST THEM YET (0 job-bearing worker log lines), so every completed job
in Firestore is still a clean "before" — but only until traffic resumes.

METHOD. Full UNORDERED scan. `podcast_jobs.created_at` is MIXED-TYPE, so
`order_by("created_at").limit(N)` TYPE-CLIPS to an arbitrary slice rather than "the N most recent" —
every ordered figure quoted in tonight's census is suspect for exactly this reason.

Run again after traffic resumes and diff. Same command both arms.
"""
from __future__ import annotations
import json, sys
from collections import Counter
from google.cloud import firestore

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
db = firestore.Client(project="kitesforu-dev")

n = completed = 0
clip_kinds = Counter()
adv = Counter()
consensus_reasons = Counter()
have_distinctness = 0
pictorial = []
text_share = []
clips_per_job = []
unlabelled = 0
clips_total = 0

for d in db.collection("podcast_jobs").limit(LIMIT).stream():
    n += 1
    j = d.to_dict() or {}
    if j.get("status") != "completed":
        continue
    completed += 1
    vis = j.get("visual") or {}
    clips = vis.get("clips") or []
    if clips:
        clips_per_job.append(len(clips))
        for c in clips:
            if not isinstance(c, dict):
                continue
            clips_total += 1
            k = c.get("kind") or c.get("modality") or c.get("source")
            if k:
                clip_kinds[str(k)] += 1
            else:
                unlabelled += 1
    dist = vis.get("distinctness") or {}
    if dist:
        have_distinctness += 1
        for key, sink in (("pictorial_share", pictorial), ("text_share", text_share)):
            v = dist.get(key)
            if isinstance(v, (int, float)):
                sink.append(float(v))
    qg = (j.get("stages") or {}).get("quality_gate") or {}
    blob = " ".join(str(qg.get(k) or "") for k in
                    ("content_advisory", "listening_advisory", "artifact_advisory"))
    for tok in ("stage5_content_quality", "content_craft", "story_judge_narrative_craft"):
        if tok in blob:
            adv[tok] += 1
    jc = (j.get("stages") or {}).get("judge_consensus") or {}
    rs = jc.get("reasons") or []
    if isinstance(rs, str):
        rs = [rs]
    if rs:
        consensus_reasons[",".join(sorted(str(r) for r in rs))] += 1

def med(v):
    v = sorted(v)
    return round(v[len(v) // 2], 4) if v else None

out = {
    "scanned": n, "completed": completed,
    "clips_total": clips_total,
    "clip_kinds": dict(clip_kinds.most_common(8)),
    "unlabelled_clips": unlabelled,
    "unlabelled_pct": round(100.0 * unlabelled / max(clips_total, 1), 1),
    # `clips_per_job` only collects jobs that HAVE clips (the `if clips:` above), so this
    # median is NOT over `completed`. Name its denominator, exactly as
    # `jobs_with_distinctness` already does for the two medians below it.
    "jobs_with_clips": len(clips_per_job),
    "median_clips_per_job": med(clips_per_job),
    "jobs_with_distinctness": have_distinctness,
    "median_pictorial_share": med(pictorial),
    "median_text_share": med(text_share),
    "advisory_tokens": dict(adv),
    "consensus_reasons": dict(consensus_reasons.most_common(8)),
}
print(json.dumps(out, indent=2))
