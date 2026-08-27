#!/usr/bin/env python3
"""Does ONE speaker label map to ONE voice — and does one voice serve only one
label? $0, read-only, single unordered scan.

    python3 scripts/voice_identity_census.py [--project kitesforu-dev] [--since 2026-08-01]

WHY THIS EXISTS. ``speaker_delivery_census.py`` counts speaker LABELS in the
script. Labels are not voices. ``tts_segment_logs`` carries BOTH ``speaker`` and
``voice_id`` per rendered segment, so the founder's standing complaint — "I
always hear the 2 voices" — can be answered against the audio that actually
shipped rather than against the script that asked for it. This script measures
the LABEL -> VOICE mapping in both directions:

  COLLAPSE   several labels rendered through ONE voice  (a cast on paper only)
  FRACTURE   one label rendered through SEVERAL voices  (a speaker changes person)

THREE METHOD TRAPS THIS SCRIPT EXISTS TO AVOID
──────────────────────────────────────────────
1. IDENTITY IS THE ``(provider, voice_id)`` PAIR, NEVER ``voice_id`` ALONE.
   Measured 2026-08-27 over 56,377 segment rows: 14,376 of them (25.5%, across
   900 of 2,896 jobs) carry a voice_id from a DIFFERENT provider's namespace —
   overwhelmingly an ElevenLabs id logged under ``provider='google'``
   (``pNInz6obpgDQGcFmaJgB`` / ``21m00Tcm4TlvDq8ikWAM``, which are the top two
   ids on provider='elevenlabs'). 100% of those rows are ``fallback_used=True``:
   on an EL failover the log records the REQUESTED EL id, not the Google voice
   the listener actually heard. Keying on voice_id alone silently merges two
   different renders. ``--namespace`` prints this control.

2. ``index`` IS NOT AN ORDERING KEY. 817 of 2,896 jobs have ``index`` all-equal
   or None (the historically-broken all-zero rows); the Firestore array order is
   not chronological either. ``timestamp`` is present on 100% of rows and is the
   only usable ordering key — which matters because MID-EPISODE provider
   switches are a different defect from a job that used one provider throughout.

3. ``created_at`` IS MIXED-TYPE, so ``order_by(...).limit(N)`` type-clips. The
   scan is unordered and filtered in Python.

REPORTED SEPARATELY, ON PURPOSE. A 2-hander that delivers 2 voices is CORRECT,
not a defect — so "delivered exactly 2 voices" is never counted as a loss. The
founder's sentence is about REPETITION ACROSS EPISODES, which is the voice-SET
section: how often different episodes get the same pair.
"""
from __future__ import annotations

import argparse
import datetime
import re
from collections import Counter, defaultdict

from google.cloud import firestore

# Namespace shapes, derived from the observed vocabulary per provider — not
# assumed. See trap 1 above.
_EL_ID = re.compile(r"^[A-Za-z0-9]{20}$")
_GOOGLE_ID = re.compile(r"^[a-z]{2}-[A-Z]{2}-")
_OPENAI_IDS = frozenset(
    {"alloy", "echo", "fable", "onyx", "nova", "shimmer",
     "sage", "ballad", "coral", "ash", "verse"}
)


def dig(d, *path):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def norm_label(x) -> str:
    """Speaker labels drift in punctuation between stages (``Prof_ James`` vs
    ``Prof. James``); compare on the letters."""
    return re.sub(r"[^a-z0-9]+", "", str(x).lower())


def id_namespace(voice_id) -> str:
    v = str(voice_id)
    if _GOOGLE_ID.match(v):
        return "google"
    if v in _OPENAI_IDS:
        return "openai"
    if _EL_ID.match(v):
        return "elevenlabs"
    if re.match(r"^[A-Z][a-z]+$", v):
        return "inworld"
    return "other"


def usable_segments(job: dict) -> list:
    """Rendered rows carrying both a speaker and a voice, in TIME order."""
    segs = [
        s for s in (job.get("tts_segment_logs") or [])
        if isinstance(s, dict) and s.get("speaker") and s.get("voice_id")
    ]
    segs.sort(key=lambda s: str(s.get("timestamp") or ""))
    return segs


def voice_identity(seg: dict) -> tuple:
    """The pair, never the bare id — see trap 1."""
    return (seg.get("provider"), str(seg.get("voice_id")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="kitesforu-dev")
    ap.add_argument("--since", help="ISO8601; only jobs created at/after this")
    ap.add_argument("--namespace", action="store_true",
                    help="print the provider/voice_id namespace control (trap 1)")
    args = ap.parse_args()

    since = None
    if args.since:
        since = datetime.datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=datetime.timezone.utc)

    db = firestore.Client(project=args.project)

    scanned = with_logs = 0
    rows = []
    ns_mismatch = Counter()
    ns_total = 0
    seg_by_voice = Counter()

    for doc in db.collection("podcast_jobs").stream():  # UNORDERED — see trap 3
        job = doc.to_dict() or {}
        scanned += 1
        segs = usable_segments(job)
        if not segs:
            continue
        with_logs += 1
        created = job.get("created_at")
        if since:
            if not isinstance(created, datetime.datetime) or created < since:
                continue

        lab2voice = defaultdict(set)
        voice2lab = defaultdict(set)
        for s in segs:
            vid = voice_identity(s)
            lab2voice[norm_label(s["speaker"])].add(vid)
            voice2lab[vid].add(norm_label(s["speaker"]))
            seg_by_voice[vid] += 1
            ns_total += 1
            n = id_namespace(s.get("voice_id"))
            if n != "other" and n != s.get("provider"):
                ns_mismatch[(s.get("provider"), n, bool(s.get("fallback_used")))] += 1

        providers = [s.get("provider") for s in segs]
        fractured = {l: v for l, v in lab2voice.items() if len(v) > 1}
        rows.append(dict(
            job=doc.id,
            month=created.isoformat()[:7] if isinstance(created, datetime.datetime) else "?",
            fmt=(dig(job, "audio_config", "audio_format") or "?"),
            tier=job.get("quality_tier") or job.get("subscription_tier") or "?",
            labels=set(lab2voice), voices=set(voice2lab),
            fractured=fractured,
            collapsed={v: l for v, l in voice2lab.items() if len(l) > 1},
            # A MID-EPISODE switch is a provider CHANGE along the timeline, which
            # is a different defect from a job that used one provider throughout.
            switches=sum(1 for a, b in zip(providers, providers[1:]) if a != b),
            any_fallback=any(s.get("fallback_used") for s in segs),
            providers=sorted(set(providers)),
            # The CAST contract's own voice_id per label — so "did the cast
            # reach the listener" is answerable without a second scan.
            contract={
                norm_label(k): str(v["voice_id"])
                for k, v in (dig(job, "voice_cast", "contract", "voice_map") or {}).items()
                if isinstance(v, dict) and v.get("voice_id")
            },
            delivered_by_label={l: {vid for _p, vid in vs} for l, vs in lab2voice.items()},
        ))

    print(f"scanned {scanned} podcast_jobs docs; {with_logs} carry tts_segment_logs")
    print(f"POPULATION: {len(rows)} jobs" + (f" created >= {args.since}" if since else ""))
    if not rows:
        print("  nothing in this window.")
        return

    if args.namespace:
        print("\n" + "=" * 72)
        print("CONTROL — does voice_id belong to the logged provider? (trap 1)")
        tot = sum(ns_mismatch.values())
        print(f"  {tot} of {ns_total} rows ({100*tot/ns_total:.1f}%) are MISMATCHED")
        for (prov, ns, fb), c in ns_mismatch.most_common(8):
            print(f"     {c:7d}  provider={prov:<11s} id-namespace={ns:<11s} fallback_used={fb}")
        print("  A mismatched row's voice_id is the REQUESTED voice, not the one heard.")

    multi = [r for r in rows if len(r["labels"]) >= 2]
    coll = [r for r in multi if r["collapsed"]]
    print("\n" + "=" * 72)
    print("DIRECTION 1 — COLLAPSE (several LABELS share ONE voice)")
    print("=" * 72)
    print(f"  jobs delivering >=2 distinct labels:            {len(multi)}")
    if multi:
        print(f"  >=1 voice serving MORE THAN ONE label:         {len(coll)}"
              f"  ({100*len(coll)/len(multi):.0f}%)")
        print(f"  ALL labels collapsed onto a SINGLE voice:      "
              f"{sum(1 for r in multi if len(r['voices']) == 1)}")
        print(f"    of the collapsed, WITHOUT any fallback row:  "
              f"{sum(1 for r in coll if not r['any_fallback'])}"
              "   <- nothing failed; the cast was never distinct")
        for f, c in Counter(r["fmt"] for r in coll).most_common(8):
            print(f"       {c:4d}  {f}")

    frac = [r for r in rows if r["fractured"]]
    print("\n" + "=" * 72)
    print("DIRECTION 2 — FRACTURE (ONE label rendered by SEVERAL voices)")
    print("=" * 72)
    print(f"  jobs with >=1 label on >1 (provider,voice_id):  {len(frac)}"
          f"  of {len(rows)}  ({100*len(frac)/len(rows):.0f}%)")
    intra = sum(
        1 for r in frac
        if any(len({p for p, _ in v}) == 1 for v in r["fractured"].values())
    )
    print(f"     {intra:4d}  >=1 fractured label stayed on ONE provider (not a failover)")
    print(f"     {sum(1 for r in frac if r['switches']):4d}  had a MID-EPISODE provider switch")
    print(f"     {sum(1 for r in frac if r['any_fallback']):4d}  had >=1 fallback_used row")
    print(f"     {sum(1 for r in frac if not r['switches'] and not r['any_fallback']):4d}"
          "  had NEITHER  <- nothing failed; the selector simply decided differently")
    for f, c in Counter(r["fmt"] for r in frac).most_common(8):
        print(f"       {c:4d}  {f}")

    print("\n" + "=" * 72)
    print("THE '2 VOICES' CLAIM — repetition ACROSS episodes, not within one")
    print("=" * 72)
    print("  (a 2-hander delivering 2 voices is CORRECT — never counted as a loss)")
    sets = Counter(frozenset(r["voices"]) for r in multi)
    for vs, c in sets.most_common(5):
        shown = ", ".join(f"{p}:{v}" for p, v in sorted(vs))
        print(f"     {c:5d}  {100*c/len(multi):5.1f}%  {{{shown}}}")
    print(f"     distinct voice SETS across {len(multi)} multi-label jobs: {len(sets)}")

    print("\n" + "=" * 72)
    print("THE CAST CONTRACT vs WHAT SHIPPED")
    print("=" * 72)
    print("  voice_cast.contract.voice_map[label].voice_id  vs  tts_segment_logs")
    withc = [r for r in rows if r["contract"]
             and any(l in r["delivered_by_label"] for l in r["contract"])]
    if not withc:
        print("  no job in this window carries both a cast contract and delivered segments.")
    else:
        bad = []
        for r in withc:
            common = [l for l in r["contract"] if l in r["delivered_by_label"]]
            if any(r["delivered_by_label"][l] != {r["contract"][l]} for l in common):
                bad.append(r)
        print(f"  jobs with BOTH a contract (with voice_ids) and delivered segments: {len(withc)}")
        print(f"  >=1 label delivered a voice the contract did NOT name:  {len(bad)}"
              f"  ({100*len(bad)/len(withc):.0f}%)")
        bym = defaultdict(lambda: [0, 0])
        for r in withc:
            bym[r["month"]][0] += 1
        for r in bad:
            bym[r["month"]][1] += 1
        for m in sorted(bym):
            n, b = bym[m]
            print(f"     {m}  {b:4d}/{n:<5d}  ({100*b/n:.0f}%)")
        for r in bad[:3]:
            common = [l for l in r["contract"] if l in r["delivered_by_label"]]
            for l in common:
                if r["delivered_by_label"][l] != {r["contract"][l]}:
                    print(f"     {r['job'][:8]} {r['fmt']}: label {l!r} contracted "
                          f"{r['contract'][l]!r}, delivered {sorted(r['delivered_by_label'][l])}")
                    break

    print("\n" + "=" * 72)
    print("BY MONTH — so a fix that landed mid-window cannot hide in an average")
    print("=" * 72)
    by = defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        b = by[r["month"]]
        b[0] += 1
        if len(r["labels"]) >= 2:
            b[1] += 1
            if r["collapsed"]:
                b[2] += 1
        if r["fractured"]:
            b[3] += 1
    print(f"  {'month':9s}{'jobs':>6s}{'multi':>7s}{'COLLAPSE':>10s}{'FRACTURE':>10s}")
    for m in sorted(by):
        n, mu, co, fr = by[m]
        cs = f"{co} ({100*co/mu:.0f}%)" if mu else "-"
        print(f"  {m:9s}{n:6d}{mu:7d}{cs:>10s}{f'{fr} ({100*fr/n:.0f}%)':>10s}")


if __name__ == "__main__":
    main()
