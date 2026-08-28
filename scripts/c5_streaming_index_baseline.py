#!/usr/bin/env python3
"""EXECUTED BASELINE: the streaming path gives every same-gender speaker ONE voice.

    python3 scripts/c5_streaming_index_baseline.py --workers-src <path to kitesforu-workers/src>

$0, offline, no Firestore, no generation. Calls the REAL
``voice_selector.select_voices_for_dialogue`` — no mocks, no reimplementation.

WHY THIS EXISTS. "C5" was carried for a day as a CANDIDATE collapse root that
nobody had executed, and it was nearly recorded as fact on a code reading alone.
The reading was also WRONG in its first form: ``_select_from_list`` does
``matching[index % len(matching)]`` and all three callers (voice_selector.py
:738/:793/:856) pass ``speaker_index`` correctly. The function is fine.

The real question is narrower: is ``speaker_index`` 0 for EVERY speaker on the
STREAMING path? It comes from ``enumerate(seen_speakers)`` (voice_selector.py
:552, passed at :568) over the dialogue handed in — and the streaming dispatcher
hands in a SINGLE item (``tts_dispatcher.py:385``,
``dialogue_for_tts = [item.to_dict()]``), so each call sees a ONE-speaker
dialogue and every speaker draws ``matching[0]``.

MEASURED — this script, on kitesforu-workers @ bafebad4..8c5b0c70:

    2 same-gender speakers   BATCH nova/shimmer          STREAM nova/nova
    4 same-gender speakers   BATCH 3 distinct            STREAM 1 distinct
    3 same-gender NAMED      BATCH nova/shimmer/coral    STREAM all nova
    MIXED gender             BATCH 2 distinct            STREAM 2 distinct  (no collision)

The collision is SAME-GENDER ONLY, which is why it went unnoticed: the default
2-host explainer is usually mixed-gender and looks fine. It predicts the
independently-measured 77% same-gender share among shared-voice groups, and it
matches drama being the worst-hit format in voice_identity_census.py (46
collapsed jobs) — a drama cast is several same-gender NAMED characters.

SCOPE — WHAT THIS DOES AND DOES NOT PROVE. It proves the MECHANISM exists and
fires in the real selector on the real streaming shape. It does NOT prove it
causes any particular delivered collapse: a persona / cast-contract override can
replace the selector's pick downstream, which is why fleet collapse is single
digits rather than universal. Pair it with voice_identity_census.py to ask
whether a given job's collapse actually came from here.
"""
from __future__ import annotations

import argparse
import sys


def _item(speaker: str) -> dict:
    return {"speaker": speaker, "text": "Here is the surprising part."}


def run(select, speakers: list[str], gender: str) -> tuple[dict, dict]:
    """Return (batch_result, streaming_result) as {speaker: voice_id}.

    BATCH    — ``stages/audio/worker.py`` passes the WHOLE dialogue once.
    STREAMING— ``tts_dispatcher`` calls the generation entry ONCE PER ITEM, so
               the selector sees a one-speaker dialogue every time.
    """
    gmap = {s: gender for s in speakers}
    batch = select(
        dialogue=[_item(s) for s in speakers], language="en-US",
        speaker_gender_map=gmap,
    )
    stream = {}
    for s in speakers:
        one = select(dialogue=[_item(s)], language="en-US", speaker_gender_map=gmap)
        stream[s] = one[s].voice_id
    return {k: v.voice_id for k, v in batch.items()}, stream


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers-src", required=True,
                    help="path to kitesforu-workers/src (this repo does not vendor it)")
    args = ap.parse_args()
    sys.path.insert(0, args.workers_src)
    try:
        from workers.stages.audio.voice_selector import select_voices_for_dialogue
    except ImportError as exc:
        print(f"could not import the real selector from {args.workers_src}: {exc}")
        return 2

    cases = [
        ("2 same-gender (Host1/Host2)", ["Host1", "Host2"], "FEMALE"),
        ("4 same-gender", ["Host1", "Host2", "Host3", "Host4"], "FEMALE"),
        ("3 same-gender NAMED (a drama cast)", ["Evelyn", "Nadia", "Serena"], "FEMALE"),
    ]
    collided = 0
    for label, speakers, gender in cases:
        batch, stream = run(select_voices_for_dialogue, speakers, gender)
        nb, ns = len(set(batch.values())), len(set(stream.values()))
        flag = "  <-- COLLIDED" if ns < len(speakers) else ""
        collided += ns < len(speakers)
        print(f"{label}")
        print(f"   BATCH  {nb} distinct: {batch}")
        print(f"   STREAM {ns} distinct: {stream}{flag}")

    # THE CONTROL. Without it a reader cannot tell "the streaming path is broken"
    # from "this script always reports a collision".
    batch, stream = run(select_voices_for_dialogue, ["Host1", "Host2"], "MALE")
    mixed_b = select_voices_for_dialogue(
        dialogue=[_item("Host1"), _item("Host2")], language="en-US",
        speaker_gender_map={"Host1": "FEMALE", "Host2": "MALE"})
    mixed_s = {
        s: select_voices_for_dialogue(
            dialogue=[_item(s)], language="en-US",
            speaker_gender_map={"Host1": "FEMALE", "Host2": "MALE"})[s].voice_id
        for s in ("Host1", "Host2")
    }
    print("CONTROL — MIXED gender (must NOT collide)")
    print(f"   BATCH  {len({v.voice_id for v in mixed_b.values()})} distinct")
    print(f"   STREAM {len(set(mixed_s.values()))} distinct: {mixed_s}")
    ok = len(set(mixed_s.values())) == 2
    print(f"\n{collided} of {len(cases)} same-gender cases collided on the streaming path; "
          f"control {'PASSED' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
