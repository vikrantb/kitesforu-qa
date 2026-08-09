#!/usr/bin/env python3
"""Frame-proof a delivered master: does it ACTUALLY move, per clip window? $0, no LLM, no job.

WHY THIS EXISTS. `visual.motion_not_silently_dead` decides motion from the JOB DOC —
`_clip_moves()` returns True whenever `render_mode` is a video/motion render. Measured
2026-08-08 on two delivered masters, that is exactly backwards: the `video` render mode is the
MOST frozen class in the pipeline, while the "static" Ken Burns path moves far more.

    artifact                       still (Ken Burns)   video (the MOTION path)
    cff04dc8  1080x1920 short            6.116               0.028
    736dbec1  1920x1080 episode          0.455               0.010

So a doc-level check certifies the frozen clips as MOVING. This reads the PIXELS instead — the
"stamped-but-not-rendered" class that `.claude/rules/artifact-verification.md` exists to catch.

METHOD (identical to the one that produced the numbers above, so results stay comparable):
  * decode to 96x171 grayscale at 6fps; take the mean |diff| between adjacent frames;
  * a clip's score is the MEDIAN of its window's diffs (a median ignores the one-frame cut);
  * ~0.5 is the working "perceptible" bar. It is a HEURISTIC, not an established threshold — it
    is useful because 0.010 vs 0.455 is a 45x gap, not a threshold argument. The score is also
    CONTENT-DEPENDENT (a flat diagram on black moves fewer pixels than a textured photo), so
    compare like with like, and prefer the engine's own `kinetic_type` reference (~0.375).

⚠️ WINDOWS COME FROM CONSECUTIVE `start_ms`, NEVER `duration_ms`. `resolve_bounds` extends the
last clip to `span_ms`: on job cff04dc8 the durations summed 49.7s against a 64.3s video, so
using them mis-attributes every measurement after the first drift.

Usage:
    python3 scripts/frame_proof.py <job_id>          # fetches visual.video_url from Firestore
    python3 scripts/frame_proof.py --file out.mp4    # a local master (whole-file only)
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from typing import Any, Optional

_W, _H = 96, 171
_N = _W * _H
_FPS = 6
_BAR = 0.5


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _diffs(path: str, start: float = 0.0, length: Optional[float] = None) -> list[float]:
    """Adjacent-frame mean |diff| over a window, at the fixed sampling above."""
    cmd = ["ffmpeg", "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    if length is not None and length > 0:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-vf", f"fps={_FPS},scale={_W}:{_H},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    frames = [raw[i:i + _N] for i in range(0, len(raw) - _N + 1, _N)]
    return [sum(abs(x - y) for x, y in zip(a, b)) / _N for a, b in zip(frames, frames[1:])]


def _score(diffs: list[float]) -> dict[str, Any]:
    if not diffs:
        return {"frames": 0, "median": None, "p90": None, "dup_pct": None,
                "below_bar": None, "seconds": 0}
    secs = [statistics.mean(diffs[i:i + _FPS]) for i in range(0, len(diffs), _FPS)]
    dup = sum(1 for d in diffs if d == 0)
    return {
        "frames": len(diffs) + 1,
        "median": round(statistics.median(diffs), 4),
        "p90": round(sorted(diffs)[int(len(diffs) * 0.9)], 4),
        "dup_pct": round(dup / len(diffs) * 100, 1),
        "seconds": len(secs),
        "below_bar": sum(1 for s in secs if s < _BAR),
    }


def _windows(clips: list[dict], span: float) -> list[tuple[float, float, str, str]]:
    """(start, length, render_mode, kind) from CONSECUTIVE start_ms — never duration_ms."""
    rows = [c for c in clips if isinstance(c, dict) and c.get("start_ms") is not None]
    rows.sort(key=lambda c: float(c["start_ms"]))
    out: list[tuple[float, float, str, str]] = []
    for i, c in enumerate(rows):
        s = float(c["start_ms"]) / 1000.0
        e = float(rows[i + 1]["start_ms"]) / 1000.0 if i + 1 < len(rows) else span
        out.append((s, max(0.0, e - s), str(c.get("render_mode") or "?"),
                    str((c.get("diagram_debug") or {}).get("kind") or "-")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--project", default="kitesforu-dev")
    args = ap.parse_args()

    path = args.file
    clips: list[dict] = []
    if not path:
        if not args.job_id:
            print("need a job_id or --file", file=sys.stderr)
            return 2
        from google.cloud import firestore  # noqa: PLC0415 — optional dep, only this path needs it

        doc = (firestore.Client(project=args.project)
               .collection("podcast_jobs").document(args.job_id).get())
        job = doc.to_dict() or {}
        vis = job.get("visual") or {}
        url = str(vis.get("video_url") or "")
        if not url.startswith("gs://"):
            print(f"job {args.job_id}: no visual.video_url (got {url!r})", file=sys.stderr)
            return 1
        clips = [c for c in (vis.get("clips") or []) if isinstance(c, dict)]
        path = f"{tempfile.mkdtemp()}/master.mp4"
        subprocess.run(["gsutil", "-q", "cp", url, path], check=True)

    span = _probe_duration(path)
    whole = _score(_diffs(path))
    print(f"master: {path}  duration={span:.2f}s")
    print(f"  WHOLE FILE   median {whole['median']}  p90 {whole['p90']}  "
          f"dup-frames {whole['dup_pct']}%  "
          f"seconds below {_BAR}: {whole['below_bar']}/{whole['seconds']}")

    if not clips:
        return 0

    by_mode: dict[str, list[float]] = {}
    print(f"\n  {'start':>6} {'win':>6} {'mode':14} {'kind':20} {'median':>8}  verdict")
    for s, d, mode, kind in _windows(clips, span):
        if d <= 0.4:
            continue
        sc = _score(_diffs(path, s, d))
        if sc["median"] is None:
            continue
        by_mode.setdefault(mode, []).append(sc["median"])
        flag = "FROZEN" if sc["median"] < _BAR else ""
        print(f"  {s:6.1f} {d:6.1f} {mode:14} {kind:20} {sc['median']:8.3f}  {flag}")

    print("\n  median-of-medians BY RENDER MODE (the comparison that found the defect):")
    for mode, vals in sorted(by_mode.items(), key=lambda kv: -statistics.median(kv[1])):
        frozen = sum(1 for v in vals if v < _BAR)
        print(f"    {mode:14} n={len(vals):3}  {statistics.median(vals):7.3f}   "
              f"below bar {frozen}/{len(vals)}")
    print(json.dumps({"whole_file": whole,
                      "by_mode": {k: statistics.median(v) for k, v in by_mode.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
