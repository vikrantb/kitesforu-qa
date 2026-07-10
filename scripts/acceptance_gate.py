#!/usr/bin/env python3
"""Acceptance Gate — "Open It Like the Founder" (founder escalation 2026-07-10).

The forcing function for `.claude/rules/acceptance-gate.md`: no user-facing "done" until the REAL
produced artifact is OBSERVED and an independent skeptic fails to refute "ship-ready". This module
is the deterministic + frame-extraction core (PRODUCE → PROBE → OBSERVE); the ADVERSARY step is a
fresh-context vision agent run on the emitted frames + manifest.

Proven: on the real broken job 85ec6fc9 (topic "systematic discrimination", rendered 1080x1920 with
16:9-authored clips) this FAILS on exactly the two defects the founder caught in 30s.

Usage:
  python acceptance_gate.py --job-id <id> [--frames-dir DIR]
Exit 0 = deterministic PASS (still run the vision/adversary step); non-zero = deterministic FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

_EDU_KEYS = ("explain", "educat", "understand", "how ", "what is", "concept",
            "informational", "tutorial", "guide", "why do", "why does")


def _fetch_job(job_id: str) -> dict[str, Any]:
    from google.cloud import firestore  # lazy: keeps the CLI importable without creds
    db = firestore.Client(project=os.environ.get("GCP_PROJECT", "kitesforu-dev"))
    return (db.collection("podcast_jobs").document(job_id).get().to_dict()) or {}


def _probe_dims(path: str) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    parts = out.split(",")
    w = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    h = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    try:
        dur = float(parts[2])
    except (IndexError, ValueError):
        dur = 0.0
    return w, h, dur


def _extract_frames(mp4: str, out_dir: str) -> list[str]:
    """OBSERVE: one frame every ~3s across the FULL duration (never one hero frame)."""
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", mp4, "-vf", "fps=1/3,scale=540:-1",
                    os.path.join(out_dir, "f_%03d.png")],
                   capture_output=True)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".png"))


def run_gate(job_id: str, frames_dir: str | None = None) -> dict[str, Any]:
    d = _fetch_job(job_id)
    topic = d.get("topic") or d.get("title") or ""
    vis = d.get("visual") or {}
    clips = vis.get("clips") or []
    # PRODUCE: the SURFACED url the watch page reads — never an intermediate render path.
    url = vis.get("video_url") or vis.get("video_burned_url")
    issues: list[dict[str, str]] = []
    if not url:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic,
                "issues": [{"sev": "BLOCKER", "msg": "NOT SURFACED: visual.video_url empty"}]}

    tmp = os.path.join(tempfile.gettempdir(), f"ag_{job_id}.mp4")
    subprocess.run(["gsutil", "-q", "cp", url, tmp] if url.startswith("gs://")
                   else ["curl", "-sL", "-o", tmp, url], check=False)
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic,
                "issues": [{"sev": "BLOCKER", "msg": f"artifact not fetchable: {url}"}]}
    vw, vh, dur = _probe_dims(tmp)
    vertical = bool(vw and vh and vh > vw)

    # PROBE — invariant A: authored clip orientation must match the render target.
    clip_aspects = sorted({str(c.get("aspect_ratio")) for c in clips if c.get("aspect_ratio")})
    if vertical and any(a in ("16:9", "16x9", "1.78") for a in clip_aspects):
        issues.append({"sev": "BLOCKER", "msg": (
            f"ORIENTATION MISMATCH (=> CUT): video {vw}x{vh} (vertical) but clips authored "
            f"{clip_aspects} -> 16:9 visuals crop-filled into 9:16, edges CUT")})
    # off-topic Veo on educational content (planner disqualifier).
    ct = str((d.get("audio_config") or {}).get("content_type") or d.get("content_type") or "").lower()
    genre = str(d.get("genre") or "").lower()
    edu = any(k in (ct + genre + topic.lower()) for k in _EDU_KEYS)
    veo = [c for c in clips if c.get("modality") == "video_hero"]
    if edu and veo:
        issues.append({"sev": "MAJOR", "msg": (
            f"OFF-TOPIC RISK: {len(veo)} abstract Veo video_hero clips on EDUCATIONAL content "
            f"('{topic[:48]}') -> prefer meaningful diagrams")})

    # OBSERVE: emit frames for the independent vision/adversary step.
    fdir = frames_dir or os.path.join(tempfile.gettempdir(), f"ag_frames_{job_id}")
    frames = _extract_frames(tmp, fdir)

    verdict = "FAIL" if any(i["sev"] == "BLOCKER" for i in issues) else \
              ("REVIEW" if issues else "PASS_DETERMINISTIC")
    return {"job_id": job_id, "topic": topic, "dims": [vw, vh], "duration": dur,
            "clip_aspects": clip_aspects, "verdict": verdict, "issues": issues,
            "frames_dir": fdir, "num_frames": len(frames),
            "next": "Run the ADVERSARY: a fresh vision agent Reads every frame in frames_dir, told to "
                    "REFUTE 'ship-ready' vs the spec (on-topic? text cut? coherent?). Verdict is final "
                    "only after that + the receipt in .claude/acceptance/."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--frames-dir", default=None)
    a = ap.parse_args()
    for k in ("GRPC_VERBOSITY", "GLOG_minloglevel"):
        os.environ.setdefault(k, "NONE" if "GRPC" in k else "3")
    res = run_gate(a.job_id, a.frames_dir)
    print(json.dumps(res, indent=2))
    return 0 if res["verdict"] in ("PASS_DETERMINISTIC", "REVIEW") else 1


if __name__ == "__main__":
    sys.exit(main())
