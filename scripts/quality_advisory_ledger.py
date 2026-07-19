#!/usr/bin/env python3
"""Offline quality-advisory ledger — the "learn OFFLINE" half of ship-first QA.

Ship-first QA (workers `cd7c7d22`, 2026-07-19) stopped withholding + regenerating
episodes on subjective/heuristic gate verdicts. Instead each such verdict is now
SHIPPED and recorded as an advisory on the job doc:

    stages.quality_gate.content_advisory    e.g. "content_quality_advisory: story_judge_narrative_craft"
    stages.quality_gate.listening_advisory   e.g. "listening_qa_advisory: listening_qa_voice_collapse"
    stages.quality_gate.artifact_advisory    e.g. "artifact_qa_advisory: artifact_close_close_inaudible"

That capture is only worth anything if something MINES it. This script is that
miner: it reads recent completed jobs (READ-ONLY, $0 — the job doc the pipeline
already wrote; no LLM, no generation, Tenet-9 out-of-band), parses the advisory
strings, and ranks the recurring failure signatures by {gate × axis} and
{gate × axis × genre}. The top signatures ARE the upfront-generation backlog:
"voice_collapse clusters on 2-speaker drama" → fix the cast manifest; "close
truncation clusters on medium-tier" → raise the close max_tokens. Feed the ranked
output into kitesforu-docs/proposals/upfront-quality-prevention-2026-07-19/.

Usage:
    python3 scripts/quality_advisory_ledger.py [--limit 500] [--days N] [--json]

Nothing here writes to Firestore or triggers any generation. Safe to run anytime.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

PROJECT_ID = "kitesforu-dev"
COLLECTION = "podcast_jobs"

# The three advisory fields ship-first QA writes (gate label → job-doc key).
ADVISORY_FIELDS = {
    "content": "content_advisory",
    "listening": "listening_advisory",
    "artifact": "artifact_advisory",
}


def _parse_advisory(raw: Any) -> list[str]:
    """"listening_qa_advisory: axis_a,axis_b" -> ["axis_a", "axis_b"]. Permissive:
    a missing/blank/malformed value yields []."""
    if not isinstance(raw, str) or ":" not in raw:
        return []
    _, _, axes_part = raw.partition(":")
    return [a.strip() for a in axes_part.split(",") if a.strip()]


def _genre_of(doc: dict[str, Any]) -> str:
    """Best-effort genre for clustering — permissive across the shapes the
    pipeline uses (episode_profile.genre is the discovered one; preferences.genre
    the requested one). Falls back to content_type, then 'unknown'."""
    prof = doc.get("episode_profile") or {}
    prefs = doc.get("preferences") or {}
    return str(
        (prof.get("genre") if isinstance(prof, dict) else None)
        or (prefs.get("genre") if isinstance(prefs, dict) else None)
        or doc.get("content_type")
        or "unknown"
    )


def _iter_recent_jobs(db: Any, limit: int, days: int | None):
    """Recent podcast_jobs, newest first. Single-field order_by is auto-indexed;
    status/date are filtered in Python so no composite index is required."""
    from google.cloud import firestore

    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = (
        db.collection(COLLECTION)
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    for snap in q.stream():
        doc = snap.to_dict() or {}
        if cutoff is not None:
            ts = doc.get("updated_at")
            # Firestore timestamps are tz-aware datetimes; skip if older.
            if isinstance(ts, datetime) and ts < cutoff:
                continue
        yield snap.id, doc


def collect(db: Any, limit: int, days: int | None) -> dict[str, Any]:
    """Scan recent jobs; tally advisory signatures. Pure aggregation, no writes."""
    scanned = 0
    completed = 0
    jobs_with_advisory = 0
    by_gate_axis: Counter = Counter()
    by_gate_axis_genre: Counter = Counter()
    by_gate: Counter = Counter()
    examples: dict[str, str] = {}

    for job_id, doc in _iter_recent_jobs(db, limit, days):
        scanned += 1
        if doc.get("status") != "completed":
            continue
        completed += 1
        qg = (doc.get("stages") or {}).get("quality_gate") or {}
        if not isinstance(qg, dict):
            continue
        genre = _genre_of(doc)
        had = False
        for gate, key in ADVISORY_FIELDS.items():
            axes = _parse_advisory(qg.get(key))
            for axis in axes:
                had = True
                by_gate[gate] += 1
                by_gate_axis[(gate, axis)] += 1
                by_gate_axis_genre[(gate, axis, genre)] += 1
                examples.setdefault(f"{gate}:{axis}", job_id)
        if had:
            jobs_with_advisory += 1

    return {
        "scanned": scanned,
        "completed": completed,
        "jobs_with_advisory": jobs_with_advisory,
        "by_gate": by_gate,
        "by_gate_axis": by_gate_axis,
        "by_gate_axis_genre": by_gate_axis_genre,
        "examples": examples,
    }


def render_report(agg: dict[str, Any]) -> str:
    completed = agg["completed"]
    with_adv = agg["jobs_with_advisory"]
    pct = (100.0 * with_adv / completed) if completed else 0.0
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("QUALITY-ADVISORY LEDGER — offline (ship-first QA, learn-offline half)")
    lines.append("=" * 72)
    lines.append(
        f"scanned={agg['scanned']}  completed={completed}  "
        f"with_advisory={with_adv} ({pct:.1f}% of completed shipped WITH a flagged signal)"
    )
    lines.append("")
    lines.append("── advisory volume by GATE (which subjective gate fires most) ──")
    for gate, n in agg["by_gate"].most_common():
        lines.append(f"  {n:5d}  {gate}")
    lines.append("")
    lines.append("── TOP recurring signatures {gate · axis} — the UPFRONT-fix backlog ──")
    for (gate, axis), n in agg["by_gate_axis"].most_common(20):
        ex = agg["examples"].get(f"{gate}:{axis}", "")
        lines.append(f"  {n:5d}  {gate:9s}  {axis:38s}  e.g. {ex}")
    lines.append("")
    lines.append("── clustered by GENRE (where to target the upfront fix) ──")
    for (gate, axis, genre), n in agg["by_gate_axis_genre"].most_common(20):
        lines.append(f"  {n:5d}  {gate:9s}  {genre:16s}  {axis}")
    if not agg["by_gate_axis"]:
        lines.append("")
        lines.append("  (no advisories recorded in the scanned window — either the")
        lines.append("   fixes already landed upstream, or ship-first hasn't shipped")
        lines.append("   flagged jobs yet. Re-run with a larger --limit / --days.)")
    lines.append("=" * 72)
    return "\n".join(lines)


def _to_jsonable(agg: dict[str, Any]) -> dict[str, Any]:
    return {
        "scanned": agg["scanned"],
        "completed": agg["completed"],
        "jobs_with_advisory": agg["jobs_with_advisory"],
        "by_gate": dict(agg["by_gate"]),
        "by_gate_axis": {f"{g}:{a}": n for (g, a), n in agg["by_gate_axis"].items()},
        "by_gate_axis_genre": {
            f"{g}:{a}:{ge}": n for (g, a, ge), n in agg["by_gate_axis_genre"].items()
        },
        "examples": agg["examples"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=500, help="most-recent jobs to scan")
    ap.add_argument("--days", type=int, default=None, help="only jobs updated within N days")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    from google.cloud import firestore

    db = firestore.Client(project=PROJECT_ID)
    agg = collect(db, limit=args.limit, days=args.days)
    if args.json:
        print(json.dumps(_to_jsonable(agg), indent=2, default=str))
    else:
        print(render_report(agg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
