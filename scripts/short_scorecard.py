#!/usr/bin/env python3
"""short_scorecard.py — CLI entry point for the SHORT SCORECARD (Phase 1b, $0/¢ machine-scorable
rubric for 9:16 shorts).

Scores a rendered short's job doc (``podcast_jobs/<job_id>``) + its rendered MP4 on the 8 axes
defined in ``kitesforu_qa.scorecard`` and prints the resulting scorecard as JSON: per-axis
{score, floor, pass, how_measured, evidence}, the weighted total, the SHIP verdict, and the honest
list of axes that need instrumentation this render couldn't answer.

Every LLM/VLM axis (substance-novelty judge, visual-truth VLM) is OFF by default via
``ScorecardConfig`` — a bare invocation is strictly $0/T1 (Firestore read + GCS download + local
ffmpeg/ffprobe only). Pass ``--enable-judge``/``--vlm`` to escalate those two axes to their
authoritative T2 tier: ``--enable-judge`` needs a judge_fn wired in externally (unwired = an honest
null); ``--vlm`` wires the REAL provider-agnostic photo-vs-illustration VLM
(``kitesforu_qa.scorecard.vlm.photo_vs_illustration_vlm_fn`` — ~$0.001/beat, T2 ¢, see
COST_CHANGELOG.md) automatically, so passing it alone is enough to get an authoritative axis-3 score.

Usage:
    python3 scripts/short_scorecard.py --job-id <job_id> [--project kitesforu-dev] [--out result.json]
    python3 scripts/short_scorecard.py --job-id <job_id> --vlm   # escalate axis 3 to the real VLM (¢)
    python3 scripts/short_scorecard.py --doc-file <job.json> --video <local.mp4>   # fully offline
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitesforu_qa.harness.artifact import Artifact  # noqa: E402
from kitesforu_qa.scorecard import ScorecardConfig, score_short  # noqa: E402


def fetch_job_doc(project: str, job_id: str) -> dict[str, Any]:
    """Fetch ``podcast_jobs/<job_id>`` from Firestore. Raises ``SystemExit`` if not found — this
    is a hard precondition, not a gracefully-degradable signal (there is nothing to score)."""
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)
    from google.cloud import firestore  # type: ignore[import-not-found]

    db = firestore.Client(project=project)
    snap = db.collection("podcast_jobs").document(job_id).get()
    if not snap.exists:
        raise SystemExit(f"job {job_id!r} not found in podcast_jobs (project={project!r})")
    doc = snap.to_dict() or {}
    doc.setdefault("job_id", job_id)
    return doc


def resolve_video(video: str | None, doc: dict[str, Any], work_dir: str) -> str | None:
    """Return a LOCAL filesystem path to the rendered MP4, downloading a ``gs://`` URI via
    ``gsutil`` if needed. Falls back to the doc's own ``visual.video_burned_url``/``video_url``
    when ``--video`` isn't passed. Returns ``None`` (never raises) when no video can be resolved —
    the video-dependent axes then degrade honestly rather than crash the whole run."""
    uri = video or (doc.get("visual") or {}).get("video_burned_url") or (doc.get("visual") or {}).get("video_url")
    if not uri:
        return None
    uri = str(uri)
    if not uri.startswith("gs://"):
        return uri if os.path.exists(uri) else None
    dest = os.path.join(work_dir, os.path.basename(uri) or "episode_video.mp4")
    try:
        subprocess.run(["gsutil", "-q", "cp", uri, dest], check=True, timeout=180)
        return dest
    except Exception as exc:  # noqa: BLE001 — degrade, don't crash the whole scorecard run
        print(f"warning: gsutil cp failed for {uri}: {exc}", file=sys.stderr)
        return None


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Score a rendered 9:16 short on the 8-axis SHORT SCORECARD.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--job-id", help="Firestore podcast_jobs/<job_id> to fetch")
    src.add_argument("--doc-file", help="local job-doc JSON (offline; skips Firestore entirely)")
    p.add_argument("--video", help="local video path or gs:// URI (default: the doc's video_burned_url/video_url)")
    p.add_argument("--audio", help="local audio path (default: none; audio_feel falls back to --video)")
    p.add_argument("--project", default=os.environ.get("GCP_PROJECT", "kitesforu-dev"))
    p.add_argument(
        "--enable-judge", action="store_true",
        help="turn ON the substance-novelty LLM judge axis (¢ spend; needs a wired judge_fn — unwired stays an honest null)",
    )
    p.add_argument(
        "--vlm", action="store_true",
        help=(
            "turn ON the visual-truth VLM axis and wire the REAL provider-agnostic photo-vs-illustration "
            "VLM (T2 ¢ spend, ~$0.001/beat — see COST_CHANGELOG.md); default OFF stays the honest "
            "'needs-VLM' null"
        ),
    )
    p.add_argument("--out", help="also write the JSON scorecard to this path")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    doc = json.loads(Path(args.doc_file).read_text()) if args.doc_file else fetch_job_doc(args.project, args.job_id)

    work_dir = tempfile.mkdtemp(prefix="kqa_short_scorecard_")
    video_path = resolve_video(args.video, doc, work_dir)
    if video_path is None:
        print(
            "warning: no local video resolved — motion_density/audio_feel will report missing_instrumentation",
            file=sys.stderr,
        )

    art = Artifact.from_doc(doc, audio_path=args.audio, video_path=video_path)
    vlm_fn = None
    if args.vlm:
        from kitesforu_qa.scorecard.vlm import photo_vs_illustration_vlm_fn  # noqa: E402

        vlm_fn = photo_vs_illustration_vlm_fn
    cfg = ScorecardConfig(enable_judge=args.enable_judge, enable_vlm=args.vlm, vlm_fn=vlm_fn)
    result = score_short(art, cfg)

    out_json = json.dumps(result, indent=2, default=str)
    print(out_json)
    if args.out:
        Path(args.out).write_text(out_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
