#!/usr/bin/env python3
"""Measure the DELIVERED visual clips of a job — occupancy, ink, and real motion. $0, read-only.

WHY THIS EXISTS. On 2026-08-11 the same three questions were asked of five different jobs, and the
probe was hand-written each time. Three of those hand-written probes returned a confident, dramatic
number that was WRONG:

  * a static-fraction threshold of 0.006 (taken from window-level readings and applied to per-frame
    deltas) reported EVERY motion clip as frozen. Extracted frames showed chips revealing one by
    one. The engine was fine; the instrument was off by ~30x.
  * a census of "motion assets" pulled 12 assets that all came from ONE job, and reported the
    result as a fleet number.
  * `tail -10` on a directory listing hid the alphabetically-first entries and made vendored GSAP
    plugins look missing.

So this is the one instrument, with the calibration written next to the numbers that produced it.
Re-deriving it by hand is how the above happened.

CALIBRATION (measured on 14 delivered clips from one render, 2026-08-11):

    GSAP motion clips     p90 per-frame delta 0.0004-0.0009; ZERO frames with delta == 0
    Ken Burns still       p90 0.0054 (a full-frame zoom moves every pixel)
    a genuinely dead clip max 0.00008

USAGE
    python scripts/measure_delivered_clips.py <job_id> [--project kitesforu-dev] [--limit N]

Reads Firestore + GCS; writes nothing.
"""
from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

#: Per-frame mean-abs delta at or below this counts the frame as unchanged. Sits above a dead
#: clip's 0.00008 and an order of magnitude below animating content.
STATIC_FRAME_EPS = 0.0002

#: The occupancy floor the founder's reference videos clear (99.9-100% span) and our failing frames
#: do not (21% x 9%).
MIN_SPAN = 0.90


def _frame_deltas(path: str, max_frames: int = 400) -> List[float]:
    """Mean-abs greyscale delta between CONSECUTIVE decoded frames.

    Sequential decode, never seeking: seeking can land on the same keyframe repeatedly and
    manufacture zeros. Downscaled to 160x90 first — the question is "did the picture change", not
    "which pixel changed".
    """
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    cap = cv2.VideoCapture(path)
    prev, out = None, []
    try:
        while len(out) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype("float32") / 255.0
            g = cv2.resize(g, (160, 90))
            if prev is not None:
                out.append(float(np.mean(np.abs(g - prev))))
            prev = g
    finally:
        cap.release()
    return out


def _last_frame_png(path: str) -> Optional[bytes]:
    """The final frame, where a staged reveal has everything on screen."""
    png = path + ".last.png"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.3", "-i", path,
             "-frames:v", "1", png],
            check=True, timeout=60,
        )
        with open(png, "rb") as fh:
            return fh.read()
    except Exception:  # noqa: BLE001
        return None
    finally:
        if os.path.exists(png):
            os.remove(png)


def measure_clip(local_path: str) -> Dict[str, Any]:
    """One clip's motion + occupancy numbers. Missing values stay None, never 0."""
    deltas = _frame_deltas(local_path)
    row: Dict[str, Any] = {
        "frames": len(deltas) + 1 if deltas else 0,
        "static_frac": None, "p90_delta": None, "dead_frames": None,
        "span_w": None, "span_h": None, "ink": None,
    }
    if deltas:
        row["static_frac"] = sum(1 for d in deltas if d <= STATIC_FRAME_EPS) / len(deltas)
        row["p90_delta"] = (
            statistics.quantiles(deltas, n=10)[-1] if len(deltas) > 1 else deltas[0]
        )
        row["dead_frames"] = sum(1 for d in deltas if d == 0.0)
    png = _last_frame_png(local_path)
    if png:
        try:
            from workers.stages.visuals.frame_occupancy import measure_span

            span = measure_span(png)
            if span:
                row["span_w"], row["span_h"], row["ink"] = span
        except Exception:  # noqa: BLE001
            pass
    return row


def _fmt(value: Optional[float], spec: str) -> str:
    return format(value, spec) if value is not None else "—"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--project", default="kitesforu-dev")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    sys.path.insert(
        0,
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "kitesforu-workers", "src")
        ),
    )
    from google.cloud import firestore, storage

    db = firestore.Client(project=args.project)
    sc = storage.Client(project=args.project)
    doc = db.collection("podcast_jobs").document(args.job_id).get().to_dict() or {}
    visual = doc.get("visual") or {}
    clips = [c for c in (visual.get("clips") or []) if isinstance(c, dict)]
    if not clips:
        print(f"job {args.job_id[:8]}: NO CLIPS (visual.clips empty) — nothing delivered to measure")
        return 1

    print(f"job {args.job_id[:8]} — {len(clips)} clips; measuring up to {args.limit}")
    header = (f"{'asset':<30}{'kind':<9}{'frames':>7}{'static':>8}"
              f"{'p90d':>10}{'dead':>6}{'span':>14}{'ink':>8}")
    print(header)
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for clip in clips[: args.limit]:
        uri = str(clip.get("asset_uri") or "")
        if not uri.endswith(".mp4"):
            continue
        kind = "motion" if uri.endswith(("_motion.mp4", "_stage.mp4")) else "still"
        bucket, _, blob = uri.replace("gs://", "").partition("/")
        local = os.path.join(tempfile.gettempdir(), os.path.basename(blob))
        try:
            sc.bucket(bucket).blob(blob).download_to_filename(local)
        except Exception as exc:  # noqa: BLE001
            print(f"  {os.path.basename(blob)[:28]:<30}download failed: {str(exc)[:40]}")
            continue
        row = measure_clip(local)
        rows.append((kind, row))
        span = (f"{row['span_w']:.2f}x{row['span_h']:.2f}"
                if row["span_w"] is not None else "—")
        dead = row["dead_frames"] if row["dead_frames"] is not None else "—"
        print(f"{os.path.basename(blob)[:28]:<30}{kind:<9}{row['frames']:>7}"
              f"{_fmt(row['static_frac'], '.2f'):>8}{_fmt(row['p90_delta'], '.5f'):>10}"
              f"{dead:>6}{span:>14}{_fmt(row['ink'], '.3f'):>8}")
        os.remove(local)

    if not rows:
        print("no .mp4 clips found")
        return 1

    motion = [r for k, r in rows if k == "motion" and r["span_w"] is not None]
    if motion:
        spans = sorted(r["span_w"] for r in motion)
        inks = sorted(r["ink"] for r in motion)
        passing = sum(1 for s in spans if s >= MIN_SPAN)
        alive = sum(1 for r in motion if (r["dead_frames"] or 0) == 0)
        print(f"\nMOTION clips: n={len(motion)}  median span_w {statistics.median(spans):.3f}  "
              f"median ink {statistics.median(inks):.3f}")
        print(f"  clearing the {MIN_SPAN} occupancy floor : {passing}/{len(motion)}")
        print(f"  changing on EVERY frame               : {alive}/{len(motion)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
