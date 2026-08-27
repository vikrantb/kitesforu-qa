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



#: Formats that deliver ONE voice BY DESIGN. A job in one of these cannot
#: "under-deliver" — comparing it against a multi-entry cast contract is
#: meaningless. Mirrors the SSOT in kitesforu-workers
#: stages/script/cast_voice_sizing.select_base_script_template, which maps
#: NARRATION/MONOLOGUE/SHORT -> speaker_count 1 (the born-short single-voice
#: invariant) and DRAMA/MULTI_VOICE -> 3, default -> 2.
SINGLE_VOICE_FORMATS = frozenset({"short", "narration", "monologue"})


def is_single_voice_format(job: dict) -> bool:
    """True when the job's format delivers one voice by design."""
    fmt = dig(job, "audio_config", "audio_format")
    return isinstance(fmt, str) and fmt.strip().lower() in SINGLE_VOICE_FORMATS


def cast_size(job: dict) -> tuple[int | None, str]:
    """How many voices was this job CAST with? Returns (n, which_source).

    Two fields claim to answer this and they DISAGREE on 106 of 420 jobs, in
    BOTH directions. Neither is authoritative, so the rule is deliberately
    CONSERVATIVE: a job counts as under-delivering only when delivery is below
    BOTH candidates.

    MEASURED 2026-08-27 over the 57 disagreeing jobs that have a measurable
    delivery (positive control: 643 jobs carry >=1 quality_gate attempt):

        19  delivered == contract      < speaker_count   (speaker_count over-plans)
        11  delivered == speaker_count < contract        (contract over-lists)
        16  delivered <  both                            (a real loss either way)
         6  delivered >= both                            (no loss either way)
         5  delivered between the two

    Taking the MAX would score the first, second and fifth rows as losses --
    30 false positives out of 57. Taking the MIN scores all five rows the way
    the delivered audio actually reads. An inflated census is not harmless: on
    2026-08-27 a contract-preferring denominator made born-short 8f1c4416 the
    ONLY "under-delivering" job in the post-deploy window, i.e. the instrument
    manufactured the single data point it would have reported.

    MIN also reproduces the worker-log cross-check recorded in qa#138: the 9
    jobs with speaker_count=3, real cast=2, delivered=2 score as no-loss.
    """
    # Formats that deliver one voice BY DESIGN can never under-deliver.
    if is_single_voice_format(job):
        return 1, "single-voice-format"
    vm = dig(job, "voice_cast", "contract", "voice_map")
    sc = dig(job, "audio_config", "speaker_count")
    have_vm = isinstance(vm, dict) and bool(vm)
    have_sc = isinstance(sc, int) and bool(sc)
    if have_vm and have_sc and len(vm) != sc:
        return min(len(vm), sc), "disagree/min"
    if have_vm:
        return len(vm), "contract"
    return (sc, "speaker_count") if have_sc else (None, "none")


def cast_disagreement(job: dict) -> tuple[int, int] | None:
    """(contract, speaker_count) when the two sources disagree, else None.

    Reported as its own line rather than folded into the under-delivery total:
    a cast contract that collapsed below the planned speaker_count is an
    UPSTREAM defect, and conflating it with a delivery loss is what made the
    headline number untrustworthy.
    """
    vm = dig(job, "voice_cast", "contract", "voice_map")
    sc = dig(job, "audio_config", "speaker_count")
    if isinstance(vm, dict) and vm and isinstance(sc, int) and sc and len(vm) != sc:
        return len(vm), sc
    return None


def classify(job: dict) -> str | None:
    """Which stage lost the voices, or None when nothing was lost."""
    cast, _src = cast_size(job)
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
    scanned = eligible = under = disagreed = collapsed_out = 0
    causes: Counter[str] = Counter()
    denom: Counter[str] = Counter()
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

        cast, cast_src = cast_size(job)
        qg = dig(job, "stages", "quality_gate") or {}
        measurable = any(isinstance(qg.get(a), dict) for a in _ATTEMPTS)
        disagree = cast_disagreement(job)
        # A disagreement that drives the cast below 2 makes the job INELIGIBLE,
        # so it silently leaves the census. Count it rather than lose it: that
        # is the upstream cast-collapse defect, not an absence of one.
        if disagree and measurable and (not cast or cast < 2):
            collapsed_out += 1
        if not cast or cast < 2 or not measurable:
            continue
        eligible += 1
        if disagree:
            disagreed += 1
        denom[cast_src] += 1
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
    # A denominator you cannot attribute is a number you cannot defend.
    print("  denominator used — NEITHER field is authoritative; on a "
          "disagreement the SMALLER wins,")
    print("  so a job counts as under-delivering only when it is below BOTH:")
    for src, n in denom.most_common():
        label = {
            "contract": "voice_cast.contract.voice_map",
            "speaker_count": "audio_config.speaker_count",
            "single-voice-format": "format delivers 1 voice BY DESIGN "
                                   "(short/narration/monologue) — never a loss",
            "disagree/min": "the two DISAGREED — took the smaller",
        }.get(src, src)
        print(f"      {n:4d}  {label}")
    print()
    print("  CAUSES of under-delivery:")
    for cause, n in causes.most_common():
        print(f"      {n:4d}  {cause}")
    if disagreed or collapsed_out:
        print()
        print("  ⚠ UPSTREAM — the two cast fields DISAGREED. Reported separately, never")
        print("    folded into the total above: a contract that collapsed below the plan")
        print("    is an upstream defect, and conflating it with a delivery loss is what")
        print("    made this headline number untrustworthy before 2026-08-27.")
        print(f"      {disagreed:4d}  of the {eligible} ELIGIBLE jobs (these took the smaller)")
        print(f"      {collapsed_out:4d}  measurable job(s) the disagreement drove BELOW a cast of 2,")
        print("            so they left the census entirely — the cast-collapse class")


if __name__ == "__main__":
    main()
