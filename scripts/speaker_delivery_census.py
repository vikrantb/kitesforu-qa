#!/usr/bin/env python3
"""Does a multi-voice cast actually reach the listener? $0, read-only.

    python3 scripts/speaker_delivery_census.py [--since 2026-08-27T00:30:00]

Answers two questions, kept separate on purpose: of the jobs CAST with >=2
voices, how many DELIVERED fewer than they were cast with and which stage lost
them -- and, independently, how many delivered MORE.

WHY THIS EXISTS. On 2026-08-26 the founder's "I always hear the 2 voices" was
traced to four independent causes, and the only way to know which fixes worked
is the SAME command on both arms.

CURRENT BASELINE (2026-08-27, full unordered scan of 4155 docs):

    283 jobs cast >=2 voices with a measurable delivered script
     71 delivered FEWER than cast  (25%)
        30  script single-speaker from attempt 1  -> #2734 (open decision)
        20  the sub-capsule trim collapsed it     -> fixed 941376db (#2732)
        15  the regen traded a host for length    -> fixed a1d4481c (#2733)
         6  cast never reached by any attempt     -> #2735
    130 delivered MORE voices than cast
     75  of those render several LABELS through FEWER voice_ids

The 2026-08-26 baseline this file used to carry (166 eligible / 55 under / 33%,
with "script single-speaker" at 4) came from an instrument with SIX defects and
must not be quoted. It is preserved in
.claude/handoffs/2026-08-26-voices-under-delivery.md for provenance only.

METHOD — four traps this script exists to avoid:

 1. UNORDERED FULL SCAN. `podcast_jobs.created_at` is MIXED-TYPE (measured
    2026-08-26: 1198 datetime / 1 str / 1 None per 1200), so
    `order_by("created_at").limit(N)` TYPE-CLIPS -- the string-dated rows eat
    slots in the ordered window. Scan everything and filter in Python.

 2. READ THE TAKE THAT SHIPPED, NOT THE LATEST ONE.
    ** This entry previously said the OPPOSITE -- "read the HIGHEST
    attempt_N_metrics present" -- and that instruction WAS the bug. **
    When the regen cap bails to attempt 1, the gate still stamps attempt 2's
    metrics, so the highest attempt describes the take that was THROWN AWAY.
    MEASURED 2026-08-27 (positive control: 2896 jobs carry tts_segment_logs):
    149 jobs stamped `regen_cap_bailed_to_attempt_1` have delivered segments;
    on 26 the attempts name different speakers, and on all 26 the highest
    attempt was the discarded one. Job 38507678 shipped ['Host1','Host2'] while
    attempt 2 recorded ['Host1'].
    Delivery therefore comes from `tts_segment_logs` -- the real TTS input, so
    the shipped take by construction -- falling back to the gate only when no
    log exists. See delivered_speakers().

 3. NEITHER CAST FIELD IS AUTHORITATIVE. `audio_config.speaker_count` and
    `voice_cast.contract.voice_map` disagree on 106 of 420 jobs, in BOTH
    directions, so a disagreement takes the SMALLER and a job counts as
    under-delivering only when it is below both. And the FORMAT is only the
    BASE: `_apply_cast_voice_floor` raises it for a planned multi-character
    cast. See cast_size().

 4. ATTRIBUTE ON REAL FIELDS, UPSTREAM FIRST. The cause test was once a
    substring match over `json.dumps(job)`, so any unrelated `applied` field
    attributed the loss to the trim. Read the field; and test the script before
    the trim, because the trim runs on the regen. See classify().

LABELS ARE NOT VOICES. Everything above counts speaker LABELS. Several labels
can render through one voice_id, in which case the script has a cast and the
audio does not -- which is what the founder's sentence is actually about. That
number is reported separately via delivered_voices(); never fold it in.

Use --since to isolate post-deploy traffic. Same command, both arms. At a ~25%
base rate a window under ~50 eligible jobs cannot decide anything -- say the n.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os as _os
import re
import sys
from collections import Counter
from pathlib import Path

_WORKERS_SRC = _os.environ.get("WORKERS_SRC") or str(
    Path(__file__).resolve().parents[2] / "kitesforu-workers" / "src")
if _WORKERS_SRC not in sys.path:
    sys.path.insert(0, _WORKERS_SRC)
try:
    from workers.stages.script.cast_voice_sizing import (
        _cast_size_from_preferences as _prod_cast_size,
    )
except Exception as _exc:  # noqa: BLE001
    raise SystemExit(
        f"speaker_delivery_census: cannot import production's cast sizing ({type(_exc).__name__}: "
        f"{_exc}).\nSet WORKERS_SRC to kitesforu-workers/src. Refusing to run: the cast floor is "
        f"this census's denominator, and a local re-implementation of it drifted LOW on 43 of "
        f"4160 jobs before it was deleted."
    ) from _exc

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


def _prefs_blobs(job: dict) -> list:
    """Every dict the SSOT might receive as ``preferences``.

    The cast sketch is written to BOTH ``preferences`` and
    ``episode_profile.user_preferences``, and only one of them may carry
    ``cast_size`` (job 33cdaa8d has cast_size=3 under episode_profile and only
    ``characters`` under preferences), so both are consulted.
    """
    out = []
    for path in (("preferences",), ("episode_profile", "user_preferences")):
        v = dig(job, *path)
        if isinstance(v, dict):
            out.append(v)
    return out


def cast_floor(job: dict) -> int:
    """Planned cast size — from PRODUCTION's own function, not a copy of it.

    This used to be a hand-written mirror of `_cast_size_from_preferences`, and it had drifted.
    Measured 2026-08-28 over the full collection (n=4160): the mirror was **LOWER on 43 jobs
    (1.03%) and higher on none** — one-directional under-counting, so switching to the real
    function loses nothing.

    It read only `_cast_sketch` and `_persona_voice_map`. Production also consults the
    AudioDramaConfig cast, and then max'es in `_canonical_cast_floor` — the blueprint canonical
    cast, which exists precisely because "a STALE api `_cast_sketch` (cast_size=1) used to shadow
    a richer architect cast" (job 81ad9db9: sketch said one narrator, the covenant put 3 named
    characters in the script prompt). The mirror was blind to exactly that case.

    Why it matters HERE: an under-counted floor understates under-delivery. A job whose real
    floor is 4, measured as 2, delivering 2 voices looks compliant and is not.

    The earlier mirror had already been widened once for the same class of blindness — its own
    note recorded that "mirroring only the format map made this census blind to exactly the
    collapse it exists to catch" (job 33cdaa8d). Widening a copy twice is the argument for not
    keeping a copy.
    """
    return max([_prod_cast_size(b) for b in _prefs_blobs(job)] or [0])


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
    # Formats that deliver one voice BY DESIGN can never under-deliver --
    # UNLESS a planned multi-character cast raised the count, which is what
    # _apply_cast_voice_floor does in the SSOT. Format is the base, not the answer.
    if is_single_voice_format(job):
        floor = cast_floor(job)
        return (floor, "single-voice-format/cast-floor") if floor > 2 else (1, "single-voice-format")
    vm = dig(job, "voice_cast", "contract", "voice_map")
    sc = dig(job, "audio_config", "speaker_count")
    have_vm = isinstance(vm, dict) and bool(vm)
    have_sc = isinstance(sc, int) and bool(sc)
    if have_vm and have_sc and len(vm) != sc:
        return min(len(vm), sc), "disagree/min"
    if have_vm:
        base = len(vm)
        floor = cast_floor(job)
        return (floor, "cast-floor") if floor > 2 and floor > base else (base, "contract")
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


def _norm(name) -> str:
    """Speaker labels drift in punctuation between stages: the gate records
    ``Prof_ James Okafor`` where the TTS log records ``Prof. James Okafor``.

    Compare on letters AND DIGITS. The digit is LOAD-BEARING: it is the only
    thing separating ``Host1`` from ``Host2``. A letters-only normaliser merges
    every two-host cast into a single speaker, and MEASURED 2026-08-27 that is
    1889 of the 2896 jobs carrying ``tts_segment_logs`` (65%), 1870 of them the
    plain ``['Host1','Host2']`` pair. The census would then report the entire
    two-host corpus as a total voice collapse -- silently, with no error, and
    looking exactly like a dramatic real finding.

    ``test_the_digit_in_host1_is_load_bearing`` fails if this is ever narrowed.
    """
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def delivered_speakers(job: dict) -> tuple[set, str]:
    """Distinct speaker labels in the audio that ACTUALLY SHIPPED.

    ``tts_segment_logs`` is the real TTS input, so it is the shipped take by
    construction. The gate's ``attempt_N_metrics`` are NOT: when the regen cap
    bails to attempt 1, the gate still stamps the metrics of attempt 2, and the
    census's "latest attempt" then describes the take that was THROWN AWAY.

    MEASURED 2026-08-27 (positive control: 2896 jobs carry tts_segment_logs):
    149 jobs are stamped ``regen_cap_bailed_to_attempt_1`` and have delivered
    segments; on 26 of them attempt 1 and attempt 2 name different speakers,
    and on all 26 the census read attempt 2 while the audio was attempt 1.
    Job 38507678 shipped ['Host1','Host2'] while attempt 2 recorded ['Host1'] --
    scored as an under-delivery that never happened.
    """
    segs = job.get("tts_segment_logs")
    if isinstance(segs, list) and segs:
        names = {_norm(s.get("speaker")) for s in segs
                 if isinstance(s, dict) and s.get("speaker")}
        names.discard("")
        if names:
            return names, "tts_segment_logs"
    qg = dig(job, "stages", "quality_gate") or {}
    present = [a for a in _ATTEMPTS if isinstance(qg.get(a), dict)]
    if present:
        return {_norm(x) for x in _speakers(qg, present[0])}, "gate/latest-attempt"
    return set(), "none"


def delivered_voices(job: dict) -> set:
    """Distinct voice_ids actually rendered -- what a listener HEARS.

    Not the same question as the labels. Several cast labels can render
    through ONE voice, in which case the script has a cast and the audio does
    not. "I always hear the 2 voices" is a claim about THIS number, so it is
    reported alongside the labels rather than folded into them.

    IDENTITY IS THE (provider, voice_id) PAIR, NEVER voice_id ALONE.
    MEASURED 2026-08-27 over 56,377 segment rows: 14,376 (25.5%, across 900 of
    the 2896 jobs with delivery) carry a voice_id from a DIFFERENT provider's
    namespace -- almost all an ElevenLabs id logged under provider='google',
    and 100% of those rows are fallback_used=True. On an ElevenLabs failover
    the log records the REQUESTED id, not the Google voice actually heard, so a
    bare-voice_id key merges two genuinely different renders.

    HONEST SCOPE: this does NOT move the within-job counts this census reports
    -- measured, the collapse count is 75 under both keys, because inside one
    job an id is consistently logged under one provider. It matters for
    CROSS-JOB aggregation ("which voices dominate the fleet"), which is exactly
    the question the founder's sentence asks, so the pair is used here rather
    than left as a trap for the next reader.
    """
    segs = job.get("tts_segment_logs")
    if not isinstance(segs, list):
        return set()
    return {(str(s.get("provider")), str(s.get("voice_id"))) for s in segs
            if isinstance(s, dict) and s.get("voice_id")}


def classify(job: dict) -> str | None:
    """Which stage lost the voices, or None when nothing was lost.

    ATTRIBUTION IS UPSTREAM-FIRST AND READS REAL FIELDS.

    Both properties were wrong until 2026-08-27 and they compounded:

    1. The trim test was a SUBSTRING match on the whole document --
       ``'"applied": true' in json.dumps(job)`` -- so any unrelated ``applied``
       field anywhere in the job attributed the loss to the trim. Job
       0c5d1055 carries ``sub_capsule_trim.applied = false``
       (``reason: within_band``: the trim never ran) and was still labelled
       "trim collapsed it".

    2. The trim was tested BEFORE the script, so a job whose attempt 1 was
       already single-speaker was labelled with the trim that ran afterwards.
       MEASURED over the 121 jobs eligible since 2026-08-01: the census
       reported ZERO "script single-speaker from attempt 1", while 11 of the
       37 under-delivering jobs had attempt 1 deliver exactly one speaker.
       Every one was labelled "trim collapsed it".

    The trim runs on the regen (``branch: regen_pre_tts``), so an attempt 1
    that already speaks with one voice was authored that way -- the script is
    the more upstream cause and is now tested first.
    """
    cast, _src = cast_size(job)
    qg = dig(job, "stages", "quality_gate") or {}
    present = [a for a in _ATTEMPTS if isinstance(qg.get(a), dict)]
    if not cast or cast < 2:
        return None
    final, _how = delivered_speakers(job)
    if not final or len(final) >= cast:
        return None
    if not present:
        return "cast never reached by any attempt"

    # UPSTREAM FIRST: a single-voice attempt 1 predates every later stage.
    if len(_speakers(qg, "attempt_1_metrics")) == 1:
        return "script single-speaker from attempt 1"
    # Real field reads -- never a substring of the serialised document.
    if dig(qg, "sub_capsule_trim", "applied") is True:
        return "trim collapsed it"
    if dig(qg, "regen_deleted_a_speaker") is True:
        return "regen traded a host (caught by the new guard)"
    if len(final) < len(_speakers(qg, "attempt_1_metrics")):
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
    over = over_one_voice = 0
    over_by_fmt: Counter[str] = Counter()
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
        # OVER-delivery: measured for EVERY job, including the cast<2 ones the
        # under-delivery census skips. Distinct voice_ids are tracked
        # separately because several labels can share one voice -- a script
        # with a cast and audio without one.
        shipped, _how = delivered_speakers(job)
        if cast and shipped and len(shipped) > cast:
            over += 1
            over_by_fmt[(dig(job, "audio_config", "audio_format") or "?")] += 1
            voices = delivered_voices(job)
            if voices and len(voices) < len(shipped):
                over_one_voice += 1
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
        # OVER-delivery is a different defect and was previously INVISIBLE:
        # the loop skipped cast < 2, so a one-voice format that shipped three
        # voices could never be seen. A born-short is exactly that shape.
        # (checked below against every job, not just cast >= 2 ones)
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
    if over:
        print()
        print(f"  DELIVERED MORE VOICES THAN CAST: {over}")
        print("    A DIFFERENT defect, and invisible before 2026-08-27: the loop")
        print("    skipped cast < 2, so a one-voice format that shipped several")
        print("    voices could never be counted. Born-short 8f1c4416 shipped")
        print("    Narrator + Nadia + Theo against speaker_count=1.")
        for f, n in over_by_fmt.most_common(8):
            print(f"      {n:4d}  {f}")
        if over_one_voice:
            print(f"    of these, {over_one_voice} render several LABELS through FEWER")
            print("      voice_ids — the script has a cast, the audio does not.")
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
