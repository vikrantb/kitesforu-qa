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
from pathlib import Path
from collections import Counter
from google.cloud import firestore

# The opt-in question is decided by PRODUCTION's predicate, never a copy of it here.
# `wants_visuals` is only one of three opt-in keys (`visual_scenes`,
# `visual_scenes_requested`) across three containers (top-level, `inputs`, `preferences`),
# and the api persists it TRUTHY-ONLY — so "absent" does not mean "off". A local
# reimplementation agreed with `job_opted_in` on all 2158 completed jobs on 2026-08-28 and
# would still have silently diverged the first time a job used one of the other keys.
# If workers/src is not importable the opt-in keys are OMITTED and say so: a missing number
# is honest, a lookalike from a second implementation is not.
import os
_WORKERS_SRC = os.environ.get("WORKERS_SRC") or str(
    Path(__file__).resolve().parents[2] / "kitesforu-workers" / "src")
job_opted_in = None
visuals_disabled = None
_OPTIN_IMPORT_ERR = ""
try:
    if _WORKERS_SRC not in sys.path:
        sys.path.insert(0, _WORKERS_SRC)
    from workers.stages.visuals.flags import job_opted_in, _visuals_disabled as visuals_disabled
except Exception as _e:  # noqa: BLE001
    _OPTIN_IMPORT_ERR = f"{type(_e).__name__}: {_e}"

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
clips_per_completed = []
clips_per_requested = []   # wants_visuals is True — 0 clips here is a DEFECT
n_declined = 0             # wants_visuals is False — 0 clips here is CORRECT
n_unspecified = 0          # field ABSENT — intent unknown, never folded into a rate
unspecified_with_clips = 0
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
    # TWO different questions, so two medians (see the out dict):
    #   clips_per_job       — only jobs that HAVE clips (the 2026-08-17 baseline's arm)
    #   clips_per_completed — every completed job; no clips counts as 0
    clips_per_completed.append(len(clips))
    # wants_visuals has THREE states and they are NOT two. Censused 2026-08-28 over 2158
    # completed jobs: True=223 (95% got clips), False=3 (0% — correct), ABSENT=1932 of which
    # 374 got clips ANYWAY. So absent does NOT mean 'declined'; treating it as such would
    # exclude 374 jobs that demonstrably received visuals. The absent rows carry their own
    # count instead of being folded into either rate. (Absent-with-clips is also era-bounded:
    # 2026-06-22..2026-08-21, so it is not even one population.)
    if job_opted_in is None:
        pass  # canonical predicate unavailable — the opt-in keys are omitted below, loudly
    elif job_opted_in(j):
        clips_per_requested.append(len(clips))
    elif visuals_disabled is not None and visuals_disabled(j):
        n_declined += 1
    else:
        n_unspecified += 1
        if clips:
            unspecified_with_clips += 1
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
    # `median_clips_per_job` stays over jobs that HAVE clips: FLEET-BASELINE-2026-08-17.json
    # stores 14 from this same arm and this script exists to be diffed against it ("same
    # command both arms"). Redefining it in place would make that diff read 14 -> ~0 as a
    # collapse that never happened. `median_clips_per_completed_job` is the fleet-wide answer.
    # `clips_per_job` only collects jobs that HAVE clips (the `if clips:` above), so this
    # median is NOT over `completed`. Name its denominator, exactly as
    # `jobs_with_distinctness` already does for the two medians below it.
    "jobs_with_clips": len(clips_per_job),
    "median_clips_per_job": med(clips_per_job),
    "median_clips_per_completed_job": med(clips_per_completed),
    # The denominator that separates "wanted visuals and got none" from "never asked".
    # requested + declined + unspecified == completed, by construction.
    **({
        "visuals_requested": len(clips_per_requested),
        "median_clips_per_requested_job": med(clips_per_requested),
        "requested_but_zero_clips": sum(1 for c in clips_per_requested if c == 0),
        "visuals_declined": n_declined,
        "visuals_unspecified": n_unspecified,
        "unspecified_with_clips": unspecified_with_clips,
    } if job_opted_in is not None else {
        "visuals_optin_breakdown": f"UNAVAILABLE — {_OPTIN_IMPORT_ERR}. "
                                   f"Set WORKERS_SRC to kitesforu-workers/src.",
    }),
    "jobs_with_distinctness": have_distinctness,
    "median_pictorial_share": med(pictorial),
    "median_text_share": med(text_share),
    "advisory_tokens": dict(adv),
    "consensus_reasons": dict(consensus_reasons.most_common(8)),
}
print(json.dumps(out, indent=2))
