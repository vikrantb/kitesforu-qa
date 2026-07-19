#!/usr/bin/env python3
"""Offline content-quality audit — run the RELIABLE harness deterministic content battery over
recent completed jobs and report the real defect signal (the trustworthy replacement for the
content_craft advisory the ship-first ledger currently mines).

Ship-first records a content_craft verdict per job; the 2026-07-19 panel-audit proved content_craft
PASS is only 25%-precision. This tool instead runs the harness's deterministic content checks
(no_duplicate_sentences, no_stray_date_stamp, stays_on_topic, example_has_numbers, …) — reliable,
$0, non-gameable — over recent completed jobs (READ-ONLY: reads the job doc the pipeline already
wrote; no generation, no LLM). It prints how often each real defect fires + which jobs, and compares
to content_craft so the divergence is visible. The subjective axes (insight/turn/citations) are the
refute-panel's job (scripts/panel_batch_wf.js), which is $0-¢ and out-of-band.

Usage: python3 scripts/offline_content_audit.py [--limit 300] [--genre educational]
"""
from __future__ import annotations

import argparse
from collections import Counter


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--genre", default=None, help="only this genre (e.g. educational)")
    ap.add_argument("--min-words", type=int, default=0, help="skip short stubs below N script words (10s test jobs can't have a takeaway)")
    args = ap.parse_args()

    from google.cloud import firestore

    from kitesforu_qa.harness import Artifact, run_dimension

    db = firestore.Client(project="kitesforu-dev")
    q = (
        db.collection("podcast_jobs")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(args.limit)
    )
    scanned = 0
    audited = 0
    defect_counts: Counter = Counter()
    disagree = {"cc_pass_battery_fail": [], "cc_fail_battery_pass": []}
    examples: dict[str, str] = {}

    for snap in q.stream():
        d = snap.to_dict() or {}
        if d.get("status") != "completed":
            continue
        prof = d.get("episode_profile") or {}
        genre = str(prof.get("genre") or "")
        if genre not in ("educational", "explainer"):
            continue
        if args.genre and genre != args.genre:
            continue
        scanned += 1
        try:
            art = Artifact.from_doc(d)
            if args.min_words and len((art.script_text or "").split()) < args.min_words:
                continue
            sr = run_dimension(art, "content")
        except Exception:  # noqa: BLE001 — a job we can't build/audit just gets skipped
            continue
        audited += 1
        battery_pass = sr.passed
        for issue in sr.issues:
            # issue = "[severity] check_id: evidence" — key on the check_id
            cid = issue.split("]", 1)[-1].split(":", 1)[0].strip()
            defect_counts[cid] += 1
            examples.setdefault(cid, snap.id[:8])
        cc = (d.get("stages") or {}).get("content_craft") or {}
        cc_pass = bool(cc.get("passed")) if isinstance(cc, dict) else None
        if cc_pass is True and not battery_pass:
            disagree["cc_pass_battery_fail"].append(snap.id[:8])
        if cc_pass is False and battery_pass:
            disagree["cc_fail_battery_pass"].append(snap.id[:8])

    print("=" * 66)
    print("OFFLINE CONTENT AUDIT — reliable deterministic battery vs content_craft")
    print("=" * 66)
    print(f"completed educational scanned={scanned}  audited={audited}")
    print("\n── deterministic defects (reliable, $0) by frequency ──")
    for cid, n in defect_counts.most_common():
        print(f"  {n:4d}  {cid:42s}  e.g. {examples.get(cid,'')}")
    if not defect_counts:
        print("  (no deterministic content defects in the window)")
    print("\n── content_craft vs battery DISAGREEMENT (the unreliability, made visible) ──")
    print(f"  content_craft PASS but battery FAIL: {len(disagree['cc_pass_battery_fail'])}  {disagree['cc_pass_battery_fail'][:10]}")
    print(f"  content_craft FAIL but battery PASS: {len(disagree['cc_fail_battery_pass'])}  {disagree['cc_fail_battery_pass'][:10]}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
