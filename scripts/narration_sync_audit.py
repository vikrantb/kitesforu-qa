#!/usr/bin/env python3
"""narration_sync_audit.py — does the PICTURE follow the WORDS? ($0, read-only, no jobs created.)

Runs the four ``kitesforu_qa.harness.narration_alignment`` metrics over a set of EXISTING jobs and
aggregates them, so the fleet-level state of the "visuals and audio don't match" defect is one
command away. Creates nothing, generates nothing, costs nothing — it reads Firestore job docs and
does timing math (reuse-first per the test-cost ladder: T1, never T3).

Reuses, never reimplements: the metrics live in ``kitesforu_qa.harness.narration_alignment`` (pure,
unit-tested in ``tests/test_narration_alignment.py``) and the VTT parser is the one already used by
``harness/checks/video_sync.py``.

Usage:
    # the fleet baseline quoted in narration_alignment.py's docstring
    python3 scripts/narration_sync_audit.py --recent 40

    # a specific witness, with the worst offenders listed
    python3 scripts/narration_sync_audit.py --job-ids f6709ffc-1be9-4fb4-923e-1fd0bf0dbeb8 --verbose

    # fully offline (a local job-doc JSON), for testing
    python3 scripts/narration_sync_audit.py --doc-file /tmp/job.json --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from statistics import median
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kitesforu_qa.harness.checks.video_sync import _parse_vtt_cues  # noqa: E402
from kitesforu_qa.harness.narration_alignment import (  # noqa: E402
    Cue,
    boundary_alignment,
    card_provenance_lag,
    hold_across_sentences,
    shown_words_lag,
    starved_clips,
)

_PROJECT = os.environ.get("KITESFORU_PROJECT", "kitesforu-dev")
_COLLECTION = "podcast_jobs"


def _cues(doc: dict[str, Any]) -> list[Cue]:
    visual = doc.get("visual") or {}
    vtt = visual.get("captions_vtt") or doc.get("captions_vtt")
    if not vtt:
        return []
    return [Cue(c["start_ms"], c["end_ms"], c.get("text") or "") for c in _parse_vtt_cues(vtt)]


_TERMINAL = {"completed", "failed_qa", "failed"}


def _cards(doc: dict[str, Any], clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clips whose beat puts a line of script text on screen."""
    enrichment = doc.get("_art_director_enrichment") or {}
    out = []
    for clip in clips:
        entry = enrichment.get(str(clip.get("beat_index"))) or {}
        text = entry.get("on_screen_text") if isinstance(entry, dict) else None
        if text:
            out.append({**clip, "text": text})
    return out


def _spoken_segments(doc: dict[str, Any]) -> list[tuple[int, int, str]]:
    """``(start_ms, end_ms, text)`` per narration segment, on the REAL master timeline.

    ``master_segment_timeline`` is a LIST of ``{index,start_ms,end_ms}``, NOT a dict. Reading it
    as a dict silently degrades to gapless cumulative offsets and produces a plausible, wrong
    answer — that exact mistake produced a 260ms median that had to be retracted.
    """
    text_by_index = {
        int(s["index"]): str(s.get("text_full") or s.get("text_preview") or "")
        for s in (doc.get("segments_ready") or [])
        if isinstance(s, dict) and s.get("index") is not None
    }
    rows: list[tuple[int, int, str]] = []
    for r in doc.get("master_segment_timeline") or []:
        if not isinstance(r, dict) or "index" not in r:
            continue
        try:
            rows.append((int(r["start_ms"]), int(r["end_ms"]), text_by_index.get(int(r["index"]), "")))
        except (TypeError, ValueError):
            continue
    return rows


def audit(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Every metric for one job doc. None when the job has no measurable timing spine.

    MID-FLIGHT DOCS ARE SKIPPED, not scored. A visuals re-run REPLACES the clip array rather
    than appending to it (13 -> 19 -> 3 observed on one job; 17 -> 6 observed on 1ab08626 while
    this metric was being built), so a snapshot read reports a number that never existed.
    """
    visual = doc.get("visual") or {}
    clips = visual.get("clips") or doc.get("visual_clips") or []
    cues = _cues(doc)
    if not clips or not cues:
        return None
    if str(doc.get("status") or "") not in _TERMINAL or not visual.get("video_url"):
        return None

    hold = hold_across_sentences(clips, cues)
    bound = boundary_alignment(clips, cues)
    starved = starved_clips(clips)
    shown = shown_words_lag(_cards(doc, clips), cues)
    # EXACT provenance — scored only where the rendered sentence is spoken verbatim, so it can
    # never invent an anchor the way thresholded keyword scoring did (workers PR #2153).
    prov = card_provenance_lag(clips, _spoken_segments(doc))
    return {
        "provenance": prov,
        "job_id": doc.get("job_id") or doc.get("id") or "?",
        "clips": len(clips),
        "cues": len(cues),
        "hold": hold,
        "boundary": bound,
        "starved": starved,
        "shown": shown,
    }


def _fetch(job_ids: list[str] | None, recent: int | None) -> list[dict[str, Any]]:
    from google.cloud import firestore

    db = firestore.Client(project=_PROJECT)
    out: list[dict[str, Any]] = []
    if job_ids:
        for jid in job_ids:
            snap: Any = db.collection(_COLLECTION).document(jid).get()
            if snap.exists:
                out.append({**(snap.to_dict() or {}), "job_id": snap.id})
        return out
    query = (
        db.collection(_COLLECTION)
        .where(filter=firestore.FieldFilter("status", "==", "completed"))
        .order_by("completed_at", direction=firestore.Query.DESCENDING)
        .limit(recent or 40)
    )
    for snap in query.stream():
        out.append({**(snap.to_dict() or {}), "job_id": snap.id})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--recent", type=int, help="scan the N most recent completed podcast_jobs")
    src.add_argument("--job-ids", help="comma-separated job ids")
    src.add_argument("--doc-file", help="a local job-doc JSON (fully offline)")
    ap.add_argument("--verbose", action="store_true", help="list the worst offenders per job")
    args = ap.parse_args()

    if args.doc_file:
        docs = [json.load(open(args.doc_file))]
    else:
        docs = _fetch(args.job_ids.split(",") if args.job_ids else None, args.recent)

    results = [r for r in (audit(d) for d in docs) if r]
    skipped = len(docs) - len(results)
    print(f"scanned={len(results)} skipped(no clips/captions)={skipped}  project={_PROJECT}\n")

    header = f"{'job':10} {'clips':>5} {'sent/pic':>8} {'on-bound':>8} {'shown-lag':>9} {'starved':>7}"
    print(header)
    print("-" * len(header))
    for r in results:
        lag = f"{r['shown'].median_lag_ms:.0f}ms" if r["shown"].traceable else "-"
        print(
            f"{str(r['job_id'])[:8]:10} {r['clips']:>5} {r['hold'].median_sentences:>8.1f} "
            f"{r['boundary'].aligned_frac:>7.0%} {lag:>9} {r['starved'].starved:>7}"
        )
        if args.verbose:
            for o in r["shown"].offenders[:3]:
                print(f"    +{o['lag_ms']}ms  ON SCREEN {o['on_screen']!r}")
                print(f"             HEARD     {o['heard']!r}")
            for o in r["hold"].offenders[:3]:
                print(f"    picture held across {o['sentences']} sentences ({o['start_ms']}-{o['end_ms']}ms)")

    if results:
        lags = [r["shown"].median_lag_ms for r in results if r["shown"].traceable]
        print(f"\n=== FLEET (n={len(results)} jobs) ===")
        print(f" median sentences one picture is held across : {median([r['hold'].median_sentences for r in results]):.1f}")
        print(f" median cuts landing on a speech boundary    : {median([r['boundary'].aligned_frac for r in results]):.0%}")
        print(f" median cut offset from nearest boundary     : {median([r['boundary'].median_offset_ms for r in results]):.0f}ms")
        if lags:
            print(f" median shown-vs-spoken lag on text cards    : {median(lags):.0f}ms")
        print(f" jobs with >= 1 zero-duration clip           : {sum(1 for r in results if r['starved'].starved)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
