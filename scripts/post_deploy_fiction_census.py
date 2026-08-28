#!/usr/bin/env python3
"""Post-deploy census: did the 2026-08-27 fiction-visual fixes change NEW jobs?

WHY A DEDICATED SCRIPT AND NOT AN AD-HOC QUERY: this census has three traps that each produced
a WRONG number on 2026-08-27, and they are all baked in here so the next run cannot repeat them.

  TRAP 1 — THE LABEL IS NOT THE ARTIFACT.
      `clips[].modality == "diagram"` counts text CARDS and rule-demoted beats, not figures.
      Measured: a job showed 14 "diagram" labels and ZERO real figures.
  TRAP 2 — `edge_count` PRESENT is not `edge_count` > 0.
      A `maps_sequence` clip carries `edge_count: 0`. Keying on key-presence counted 2 map
      visuals as flowcharts and would have reported a FALSE FAILURE of the deploy.
      A real boxes-and-arrows figure is `kind == "concept_mermaid"` with edges > 0.
  TRAP 4 — A FILTER THAT RUNS BEFORE THE ACCOUNTING MAKES THE DENOMINATOR A LIE.
      `if not clips: continue` sat BEFORE `is_fiction(j)`, so a fiction job that rendered
      nothing was dropped in the same branch as every non-fiction job and never counted as
      fiction at all. Measured 2026-08-28: the script printed "fiction WITH rendered clips: 1"
      while THREE fiction jobs existed in the window; the two missing ones (67d9cb16, 5580f4e8)
      were audio-only. They were not in `skipped` either, so `skipped 0` read as "nothing
      pending" while two fiction jobs had in fact arrived. Fiction is now classified FIRST and
      every fiction job lands in exactly one named bucket, with a balance check that shouts if
      the buckets do not sum to the fiction count.
  TRAP 3 — READ ONLY WHAT IS TERMINAL.
      `status == completed` is NOT terminal for visuals, and neither is `video_status == ready`
      (observed: video ready while the clip array was still being rewritten). One job read
      8 -> 7 -> 14 -> 20 clips. Require a non-empty `clips_settled_at`.

BASELINE, captured 2026-08-27 BEFORE image e4741405 served (fleet-wide, all history):
    fiction jobs with clips            53
      ...with a character bible        29   (55%)
      ...carrying real mermaid figs    12 jobs / 65 figures
    image-cost stamp vs settled        exact 109 · UNDER 104 · over 28

FIXES UNDER TEST (all live on e4741405, 13/13, negative control 0/13):
    #2748  the SSOT could not read a Firestore dict -> #2740/#2742 were INERT on 82% of
           fiction jobs. THIS is the one that arms the rest.
    #2742  fiction beats never author a figure (imagination tree)
    #2745  the director prompt taught `beat`; the schema required {beat, expected, actual}
    #2747  ">=2 present_characters" was a PARTIAL constraint; minItems=1 applies to every beat
    #2746  the <=3 min architect budget (90s) sat below the p90 of its own work (~75s)
    #2749  the image-cost stamp froze an early partial pass (43% undercount)

USAGE:
    python3 scripts/post_deploy_fiction_census.py                # since the deploy
    python3 scripts/post_deploy_fiction_census.py --since 2026-08-28T00:00:00Z
"""
from __future__ import annotations

import argparse
import collections
import datetime
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, "../kitesforu-workers/src")

from google.cloud import firestore  # noqa: E402

from capture_starved_measurements import COLLECTION, PROJECT  # noqa: E402
from workers.common.architect_wiring import (  # noqa: E402
    _FICTION_CONTENT_CATEGORIES as FIC,
)

#: The Cloud Run revision creation time for image e4741405 on worker-visuals — i.e. the moment
#: the fixes began serving. Jobs created before this CANNOT reflect them; including them is how
#: a real improvement gets diluted into "the fix did nothing".
DEPLOY_UTC = "2026-08-27T09:49:57Z"


def _as_utc_dt(value):
    """Parse a Firestore timestamp OR an ISO string into an aware UTC datetime.

    NEVER COMPARE THESE AS STRINGS. Firestore returns a DatetimeWithNanoseconds, whose str()
    uses a SPACE separator ("2026-08-27 10:15:00+00:00") while an ISO cutoff uses "T". Since
    " " (0x20) < "T" (0x54), a lexical compare marks EVERY job as older than the cutoff and the
    census reports a FALSE ZERO. That is exactly what happened on the first run of this script.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    txt = str(value).strip().replace(" ", "T")
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(txt)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def is_fiction(job: dict) -> bool:
    """Mirrors `_is_fiction_job`'s ordering. NOTE both sources are DICTS off the job doc —
    passing a hand-built object here is exactly the mistake that hid an inert fix for a day."""
    p = job.get("preferences") or {}
    ac = job.get("audio_config") or {}
    cc = (
        p.get("_content_category")
        or p.get("content_category")
        or ((p.get("_story_engine") or {}).get("primary_engine"))
        or ac.get("content_type")
        or ""
    )
    return str(cc).strip().lower() in FIC


def real_flowcharts(clips: list) -> int:
    """TRAP 2. A real boxes-and-arrows figure only — not a 0-edge map, not a text card."""
    n = 0
    for c in clips:
        dbg = c.get("diagram_debug")
        if not isinstance(dbg, dict):
            continue
        if int(dbg.get("edge_count") or 0) > 0:
            n += 1
    return n


def countable_paid(clips: list) -> int:
    """Mirrors `_sum_visuals_image_cost`'s inclusion rules — skip reused re-cuts, count
    model_id clips PLUS ai_generated relimage clips (which carry no model_id). Using a
    different definition than the producer inflates disagreement in BOTH directions."""
    n = 0
    for c in clips:
        ev = c.get("imagination_event")
        if isinstance(ev, dict) and ev.get("reused") is True:
            continue
        if c.get("model_id"):
            n += 1
            continue
        dbg = c.get("diagram_debug") or {}
        if c.get("ai_generated") and isinstance(dbg, dict) and dbg.get("kind") == "relimage":
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEPLOY_UTC,
                    help=f"ISO8601 UTC; default = the deploy moment ({DEPLOY_UTC})")
    args = ap.parse_args()
    since = args.since

    since_dt = _as_utc_dt(since)
    if since_dt is None:
        print(f"unparseable --since: {since!r}", file=sys.stderr)
        return 1

    db = firestore.Client(project=PROJECT)

    scanned = fiction = settled = 0
    # TRAP 4 — every fiction job is ACCOUNTED FOR, in a bucket, by name. The original loop ran
    # `if not clips: continue` BEFORE `is_fiction`, so a fiction job that rendered nothing was
    # dropped in the same branch as every non-fiction job and never counted as fiction at all.
    # Observed 2026-08-28: two new fiction jobs (67d9cb16, 5580f4e8) were invisible, and they
    # were not in `skipped` either — so `skipped 0` read as "nothing pending" while two fiction
    # jobs had in fact arrived. A denominator that silently loses rows is a claim about the
    # filter, not about the population.
    no_visual_stage = []   # `visual` absent/empty -> visuals never ran for this job
    visual_no_clips = []   # visuals ran but produced no clips -> a real signal, not a non-event
    unsettled = []         # clips present, no settle stamp -> still being rewritten
    with_bible = 0
    fig_jobs = 0
    fig_total = 0
    fiction_beat_jobs = 0
    tree_mermaid_jobs = 0
    cost_exact = cost_under = cost_over = 0
    kinds: collections.Counter = collections.Counter()

    for d in db.collection(COLLECTION).stream():
        j = d.to_dict() or {}
        created = _as_utc_dt(j.get("created_at"))
        if created is None or created < since_dt:
            continue
        scanned += 1
        # Classify FICTION FIRST. Whether a job rendered anything is a property of the job we
        # are reporting on, never a reason to stop counting it.
        if not is_fiction(j):
            continue
        fiction += 1
        v = j.get("visual") or {}
        clips = [c for c in (v.get("clips") or []) if isinstance(c, dict)]

        if not v:
            no_visual_stage.append(d.id[:8])
            continue
        if not clips:
            visual_no_clips.append(d.id[:8])
            continue

        # TRAP 3 — only a non-empty settle stamp means the array is the delivered one.
        stamp = v.get("clips_settled_at")
        if not (isinstance(stamp, str) and stamp):
            unsettled.append(d.id[:8])
            continue
        settled += 1

        wb = v.get("world_bible") or {}
        if (wb.get("characters") if isinstance(wb, dict) else None):
            with_bible += 1

        rf = real_flowcharts(clips)
        if rf:
            fig_jobs += 1
            fig_total += rf
        for c in clips:
            dbg = c.get("diagram_debug")
            if isinstance(dbg, dict) and int(dbg.get("edge_count") or 0) > 0:
                kinds[str(dbg.get("kind"))] += 1

        reasons = [str(r) for c in clips for r in (c.get("modality_reasons") or [])]
        if any("fiction_beat" in r for r in reasons):
            fiction_beat_jobs += 1
        if any("imagination_tree:depict" in r and "mermaid" in r for r in reasons):
            tree_mermaid_jobs += 1

        st = (j.get("costs") or {}).get("visuals_images")
        sc = (st.get("meta") or {}).get("scenes") if isinstance(st, dict) else None
        if sc is not None:
            exp = countable_paid(clips)
            if exp:
                if int(sc) < exp:
                    cost_under += 1
                elif int(sc) > exp:
                    cost_over += 1
                else:
                    cost_exact += 1

    print(f"POST-DEPLOY FICTION CENSUS — jobs created since {since}")
    print(f"  (deploy of e4741405 on worker-visuals: {DEPLOY_UTC})\n")
    def _ids(bucket: list) -> str:
        if not bucket:
            return ""
        shown = ", ".join(bucket[:6])
        return f"   [{shown}{', +%d more' % (len(bucket) - 6) if len(bucket) > 6 else ''}]"

    print(f"  jobs created in window        : {scanned}")
    print(f"  ...FICTION jobs               : {fiction}")
    print(f"       visuals never ran        : {len(no_visual_stage)}{_ids(no_visual_stage)}")
    print(f"       visuals ran, 0 clips     : {len(visual_no_clips)}{_ids(visual_no_clips)}")
    print(f"       clips present, unsettled : {len(unsettled)}{_ids(unsettled)}")
    print(f"       SETTLED (usable, n)      : {settled}")
    accounted = len(no_visual_stage) + len(visual_no_clips) + len(unsettled) + settled
    if accounted != fiction:  # the whole point of the funnel — it must balance
        print(f"  !! ACCOUNTING GAP: {fiction} fiction jobs but {accounted} bucketed — a row is "
              f"being dropped silently. Do not trust the numbers below.")
    if not settled:
        print("\n  NO SETTLED FICTION JOBS YET — this is not evidence either way.")
        if no_visual_stage or visual_no_clips:
            print(f"  Note: {len(no_visual_stage)} fiction job(s) never ran visuals and "
                  f"{len(visual_no_clips)} ran but produced no clips. Those cannot answer a "
                  f"visuals question — they are not 'pending', and they are not a failure "
                  f"signal for the fixes either.")
        print("  Re-run when a fiction job with visuals has SETTLED.")
        return 0
    print()
    print(f"  1) REAL mermaid flowcharts (edges>0) : {fig_total} figures across {fig_jobs} job(s)")
    print(f"       kinds: {dict(kinds) or '{}'}")
    print(f"       BEFORE (all history): 65 figures across 12 jobs")
    print(f"  2) jobs with imagination_tree->mermaid: {tree_mermaid_jobs}   [expect 0]")
    print(f"     jobs where 'fiction_beat' fired    : {fiction_beat_jobs}   [positive evidence]")
    print(f"  3) character bible present           : {with_bible}/{settled} "
          f"({100*with_bible/settled:.0f}%)   BEFORE: 55%")
    print(f"  4) image-cost stamp vs settled       : exact {cost_exact} · UNDER {cost_under} "
          f"· over {cost_over}")
    print(f"       BEFORE (all history): exact 109 · UNDER 104 · over 28")
    print()
    print("  READ HONESTLY: small n is not a trend. Report the DENOMINATOR with every number,")
    print("  and remember overcounts (stamp > settled) are RE-RENDERS, a separate filed item —")
    print("  they are not expected to go to zero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
