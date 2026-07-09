#!/usr/bin/env python3
"""quality_matrix.py — the MEASURED QUALITY ENGINE runner.

Scores a SET of jobs (explicit job_ids, a recent-completed Firestore query, or a local offline
docs-dir) via the existing 8-axis SHORT SCORECARD (``kitesforu_qa.scorecard.score_short`` — the SAME
$0/T1 primitive ``scripts/short_scorecard.py`` uses for a single job), then AGGREGATES + RANKS the
results across the whole genre x format matrix so systematic (class-level) weaknesses become visible,
not just one job's one-off issues.

Reuses, never reimplements: ``fetch_job_doc``/``resolve_video`` are imported straight from
``short_scorecard.py`` (this script's sibling), and the aggregation/ranking math lives in
``kitesforu_qa.harness.quality_matrix`` (pure, unit-tested, importable independent of this CLI).

Every source mode defaults to $0 (``ScorecardConfig()`` — judge/VLM axes OFF); pass ``--enable-judge``/
``--vlm`` to escalate specific axes to their T2 ¢ tier, same flags as ``short_scorecard.py``.

Usage:
    # $0 baseline over the last 7 days of completed jobs (the reuse baseline — no new jobs created)
    python3 scripts/quality_matrix.py --query-recent-days 7 --limit 60

    # explicit job_ids
    python3 scripts/quality_matrix.py --job-ids abc123,def456

    # fully offline (a directory of local job-doc JSON files — for testing, $0, no network)
    python3 scripts/quality_matrix.py --docs-dir fixtures/jobs/
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kitesforu_qa.harness.artifact import Artifact  # noqa: E402
from kitesforu_qa.harness.quality_matrix import (  # noqa: E402
    DEFAULT_TARGET_FORMATS,
    DEFAULT_TARGET_GENRES,
    aggregate_all_axes,
    aggregate_by_group,
    instrumentation_gaps,
    parse_datetime,
    propose_matrix_fill,
    proxy_dominant_axes,
    rank_systematic_weaknesses,
    render_backlog_markdown,
)
from kitesforu_qa.scorecard import ScorecardConfig, score_short  # noqa: E402
from kitesforu_qa.scorecard import signals as _signals  # noqa: E402


def _load_short_scorecard_lib():
    """Import ``scripts/short_scorecard.py`` by file path (it's a script, not an installed package —
    same technique ``tests/test_short_scorecard_cli.py`` uses) so we REUSE its ``fetch_job_doc`` /
    ``resolve_video`` rather than re-implementing Firestore-doc-fetch + gs:// video resolution."""
    script_path = REPO_ROOT / "scripts" / "short_scorecard.py"
    spec = importlib.util.spec_from_file_location("short_scorecard_lib", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── job sourcing ───────────────────────────────────────────────────────────────


def query_recent_completed_jobs(project: str, *, days: int, limit: int, overfetch_cap: int = 1500) -> list[dict[str, Any]]:
    """Firestore ``podcast_jobs`` most-recent-first, filtered client-side to ``status == "completed"``
    within the last ``days`` days. Deliberately does NOT add a ``.where(status==...)`` server-side
    filter combined with ``order_by(created_at)`` — that combination needs a composite index that does
    not exist in this project (confirmed live) and would hard-fail the whole run. A single ``order_by``
    needs no index.

    IMPORTANT (confirmed live on kitesforu-dev): ``created_at`` is stored INCONSISTENTLY across
    ``podcast_jobs`` documents — some as a native Firestore Timestamp, some as an ISO8601 string.
    Firestore's ``order_by`` sorts by TYPE first, then value, so a query result is NOT reliably
    monotonic-descending-by-real-time across the type boundary (a handful of ancient string-typed docs
    can sort ahead of thousands of much more recent Timestamp-typed docs). This means we do NOT
    early-break the scan the moment one doc looks "too old" — that would silently miss every genuinely
    recent doc sorted after the stray old ones. Every doc up to ``overfetch_cap`` is checked
    individually instead; ``overfetch_cap`` is the sole bound (still $0 — Firestore reads only, no
    LLM/API spend). This data inconsistency is itself worth a schema-hygiene fix upstream (a
    consistent ``created_at`` write path), independent of this tool."""
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)
    from google.cloud import firestore  # type: ignore[import-not-found]

    db = firestore.Client(project=project)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    docs = db.collection("podcast_jobs").order_by("created_at", direction="DESCENDING").limit(overfetch_cap).stream()
    for d in docs:
        data = d.to_dict() or {}
        data.setdefault("job_id", d.id)
        created = parse_datetime(data.get("created_at"))
        if created is None or created < cutoff:
            continue
        if data.get("status") == "completed":
            out.append(data)
            if len(out) >= limit:
                break
    return out


def load_docs_dir(path: str) -> list[dict[str, Any]]:
    """Offline job-doc source: every ``*.json`` file in ``path`` is one job doc. $0, no network — the
    path fixtures/tests use. A file that fails to parse is skipped with a stderr warning (fail-open),
    never crashes the whole load."""
    out: list[dict[str, Any]] = []
    for f in sorted(Path(path).glob("*.json")):
        try:
            doc = json.loads(f.read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"warning: failed to parse {f}: {exc}", file=sys.stderr)
            continue
        if isinstance(doc, dict):
            doc.setdefault("job_id", f.stem)
            out.append(doc)
    return out


# ── per-cell scoring ─────────────────────────────────────────────────────────


def score_all(
    job_specs: list[Any],
    *,
    project: str,
    cfg: ScorecardConfig,
    download_video: bool,
    fetch_job_doc: Any,
    resolve_video: Any,
) -> list[dict[str, Any]]:
    """Score every job spec into a "cell": the ``score_short()`` dict + ``genre``/``format``/
    ``quality_tier`` routing keys + ``_scored``. A job spec is either a bare job_id (str, needs a
    Firestore fetch) or an already-fetched doc (dict, from ``--docs-dir``/query mode).

    Bounded (a single pass over a finite list) + fail-open: ANY failure (fetch, video resolve, Artifact
    build, or score_short itself raising) degrades that ONE cell to ``_scored=False`` + ``_error`` —
    it never aborts the run for the other cells."""
    work_dir = tempfile.mkdtemp(prefix="kqa_quality_matrix_")
    cells: list[dict[str, Any]] = []
    for spec in job_specs:
        is_bare_id = isinstance(spec, str)
        job_id = spec if is_bare_id else str(spec.get("job_id") or spec.get("id") or "unknown")
        try:
            doc = fetch_job_doc(project, spec) if is_bare_id else spec
            video_path = resolve_video(None, doc, work_dir) if download_video else None
            art = Artifact.from_doc(doc, video_path=video_path)
            result = score_short(art, cfg)
            result["genre"] = art.genre
            result["format"] = "short" if _signals.is_short(art) else "episode"
            result["quality_tier"] = _signals.job_quality_tier(art)
            result["_scored"] = True
            cells.append(result)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — one bad job must never crash the run
            cells.append(
                {
                    "job_id": job_id,
                    "_scored": False,
                    "_error": f"{type(exc).__name__}: {exc}",
                    "genre": "unknown",
                    "format": "unknown",
                    "quality_tier": "unknown",
                }
            )
    return cells


# ── CLI ────────────────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Score a set of jobs on the 8-axis SHORT SCORECARD and rank systematic weaknesses.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--job-ids", help="comma-separated Firestore podcast_jobs job_ids to score")
    src.add_argument("--query-recent-days", type=int, help="score COMPLETED jobs from the last N days (the $0 reuse baseline)")
    src.add_argument("--docs-dir", help="score local job-doc JSON files in this directory (offline, $0, no network)")

    p.add_argument("--project", default=os.environ.get("GCP_PROJECT", "kitesforu-dev"))
    p.add_argument("--limit", type=int, default=60, help="max jobs to score in query mode (bounded; default 60)")
    p.add_argument("--no-video", action="store_true", help="skip gs:// video download (faster; video-dependent axes degrade honestly)")
    p.add_argument("--enable-judge", action="store_true", help="turn ON the substance-novelty LLM judge axis (¢; unwired stays an honest null)")
    p.add_argument("--vlm", action="store_true", help="turn ON the visual-truth VLM axis (¢, ~$0.001/beat — wires the real photo-vs-illustration VLM)")
    p.add_argument("--top-n", type=int, default=5, help="how many ranked systematic weaknesses to show in the markdown backlog (default 5)")
    p.add_argument("--target-genres", default=",".join(DEFAULT_TARGET_GENRES), help="comma-separated genre list for the PROPOSED MATRIX FILL section")
    p.add_argument("--target-formats", default=",".join(DEFAULT_TARGET_FORMATS), help="comma-separated format list (short,episode) for the PROPOSED MATRIX FILL section")
    p.add_argument("--out-json", default=str(REPO_ROOT / "reports" / "quality_matrix_result.json"))
    p.add_argument("--out-backlog", default=str(REPO_ROOT / "QUALITY_BACKLOG.md"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.job_ids:
        job_specs: list[Any] = [j.strip() for j in args.job_ids.split(",") if j.strip()]
        mode = "job-ids"
    elif args.query_recent_days is not None:
        job_specs = query_recent_completed_jobs(args.project, days=args.query_recent_days, limit=args.limit)
        mode = f"query-recent-{args.query_recent_days}d"
    else:
        job_specs = load_docs_dir(args.docs_dir)
        mode = "docs-dir"

    sc_lib = _load_short_scorecard_lib()

    vlm_fn = None
    if args.vlm:
        from kitesforu_qa.scorecard.vlm import photo_vs_illustration_vlm_fn  # noqa: E402

        vlm_fn = photo_vs_illustration_vlm_fn
    cfg = ScorecardConfig(enable_judge=args.enable_judge, enable_vlm=args.vlm, vlm_fn=vlm_fn)

    cells = score_all(
        job_specs,
        project=args.project,
        cfg=cfg,
        download_video=not args.no_video,
        fetch_job_doc=sc_lib.fetch_job_doc,
        resolve_video=sc_lib.resolve_video,
    )

    agg_by_axis = aggregate_all_axes(cells)
    weaknesses = rank_systematic_weaknesses(cells, agg_by_axis)
    gaps = instrumentation_gaps(cells, agg_by_axis)
    proxies = proxy_dominant_axes(cells, agg_by_axis)
    target_genres = tuple(g.strip() for g in args.target_genres.split(",") if g.strip())
    target_formats = tuple(f.strip() for f in args.target_formats.split(",") if f.strip())
    matrix_fill = propose_matrix_fill(cells, target_genres=target_genres, target_formats=target_formats)

    generated_at = datetime.now(timezone.utc).isoformat()
    n_scored = sum(1 for c in cells if c.get("_scored"))

    result = {
        "generated_at": generated_at,
        "project": args.project,
        "mode": mode,
        "n_cells_total": len(cells),
        "n_cells_scored": n_scored,
        "n_cells_unscored": len(cells) - n_scored,
        "cells": cells,
        "per_axis_aggregate": {name: agg.to_dict() for name, agg in agg_by_axis.items()},
        "per_group_aggregate": {
            f"{genre}|{fmt}": {n: a.to_dict() for n, a in axes.items()}
            for (genre, fmt), axes in aggregate_by_group(cells).items()
        },
        "ranked_systematic_weaknesses": [w.to_dict() for w in weaknesses],
        "instrumentation_gaps": [g.to_dict() for g in gaps],
        "proxy_dominant_axes": [p.to_dict() for p in proxies],
        "proposed_matrix_fill": matrix_fill,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2, default=str))

    backlog_md = render_backlog_markdown(
        cells,
        generated_at=generated_at,
        project=args.project,
        mode=mode,
        top_n=args.top_n,
        target_genres=target_genres,
        target_formats=target_formats,
    )
    out_backlog = Path(args.out_backlog)
    out_backlog.parent.mkdir(parents=True, exist_ok=True)
    out_backlog.write_text(backlog_md)

    print(f"scored {n_scored}/{len(cells)} cells (mode={mode}, project={args.project})")
    print(f"wrote {out_json}")
    print(f"wrote {out_backlog}")
    print()
    print("Top ranked systematic weaknesses:")
    for i, w in enumerate(weaknesses[: args.top_n], start=1):
        print(f"  {i}. {w.axis}: severity={w.severity:.1f} mean={w.mean_score} n_below_floor={w.n_below_floor}/{w.n_scored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
