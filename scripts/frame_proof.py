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
import os
import statistics
import subprocess
import sys
import tempfile
from typing import Any, Optional

# Reading Firestore and then forking (ffprobe, ffmpeg, git) stalls ~62.7 s while gRPC fork support is
# on and the client was just dropped (measured on the gate by qa #184's round 2). No child makes a
# gRPC call.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from kitesforu_qa.harness.delivered_timeline import DeliveredTimeline, stamp_note  # noqa: E402
from kitesforu_qa.harness.painted_timeline_sidecar import FetchedMaster  # noqa: E402
from kitesforu_qa.integrations.download import DownloadError, download  # noqa: E402

_W, _H = 96, 171
_N = _W * _H
_FPS = 6
_BAR = 0.5


def _probe_duration(path: str) -> float:
    """The VIDEO stream's duration, else the container's. The painted windows describe the video,
    and a video can end short of its master (the producer's ``video_short_of_master``)."""
    for select, entry in ((["-select_streams", "v:0"], "stream=duration"), ([], "format=duration")):
        out = subprocess.run(
            ["ffprobe", "-v", "error", *select, "-show_entries", entry, "-of", "csv=p=0", path],
            capture_output=True, text=True,
        ).stdout.strip()
        try:
            return float(out.split(",")[0])
        except ValueError:
            continue
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


def _timeline_lines(timeline: DeliveredTimeline) -> list[str]:
    """What this tool prints about the timeline it measured against: where it came from, why a stamp
    the job named was not used, and whether a used stamp was held to the master OBJECT the
    downloader fetched (``verified``) or could not be (``unchecked``)."""
    why = f"; {timeline.stamp_rejected}" if timeline.stamp_rejected else ""
    identity = f"; master identity {timeline.master_identity}" if timeline.master_identity else ""
    lines = [f"\n  timeline: {timeline.source} ({timeline.diagnosis}{why}{identity})"]
    note = stamp_note(timeline.stamp_rejected)
    if note:
        lines.append(f"  NOTE: {note}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--project", default="kitesforu-dev")
    args = ap.parse_args()

    if args.file:
        return _prove(args.file, clips=[], job={}, fetched=None)
    if not args.job_id:
        print("need a job_id or --file", file=sys.stderr)
        return 2
    from google.cloud import firestore  # noqa: PLC0415 — optional dep, only this path needs it

    doc = (firestore.Client(project=args.project)
           .collection("podcast_jobs").document(args.job_id).get())
    job = doc.to_dict() or {}
    vis = job.get("visual") or {}
    url = str(vis.get("video_url") or "").strip()
    # `video_url` is surfaced as a PUBLIC https URL on the delivered path, and as `gs://` on
    # some internal ones — accept both. (My first version only took `gs://` and rejected a
    # perfectly good master with "no visual.video_url", printing the URL it had just refused.)
    # An EMPTY value is the real defect shape — "rendered but not surfaced".
    if not url:
        print(f"job {args.job_id}: visual.video_url is EMPTY — rendered but not surfaced "
              f"(video_status={vis.get('video_status')!r}, "
              f"skip_reason={vis.get('video_skip_reason')!r})", file=sys.stderr)
        return 1
    clips = [c for c in (vis.get("clips") or []) if isinstance(c, dict)]
    # The master lives only while it is measured: a directory nobody deletes keeps every master.
    with tempfile.TemporaryDirectory(prefix="kqa_frame_proof_") as tmp:
        path = f"{tmp}/master.mp4"
        # The shared downloader, https and gs:// alike. It reports the object it fetched (its GCS
        # generation and the bytes on disk), so a producer's painted timeline is held to THIS master
        # (``stale_master``); gsutil reported nothing, and this script had the length check only.
        try:
            got = download(url, path)
        except DownloadError as exc:
            print(f"job {args.job_id}: could not fetch {url}: {exc}", file=sys.stderr)
            return 1
        return _prove(path, clips=clips, job=job, fetched=FetchedMaster(got.generation, got.size))


def _prove(path: str, *, clips: list[dict], job: dict, fetched: FetchedMaster | None) -> int:
    """Measure the master at ``path``: the whole file, then each painted window by render mode."""
    span = _probe_duration(path)
    whole = _score(_diffs(path))
    print(f"master: {path}  duration={span:.2f}s")
    print(f"  WHOLE FILE   median {whole['median']}  p90 {whole['p90']}  "
          f"dup-frames {whole['dup_pct']}%  "
          f"seconds below {_BAR}: {whole['below_bar']}/{whole['seconds']}")

    if not clips:
        return 0

    by_mode: dict[str, list[float]] = {}
    # The painted windows come from the delivered-timeline model every reader shares: the
    # producer's sidecar when it reads, else the estimate from the persisted claims (consecutive
    # starts, never `duration_ms`; the last window runs to the master span, as `resolve_bounds`
    # does). The line below says which.
    # The master the downloader fetched is passed, so a stamp that names another object is
    # ``stale_master``. A probe of 0 s is an unknown length, so a stamp with nothing else to tie it
    # to this video is not used (``delivered_timeline.UNTIED``).
    timeline = DeliveredTimeline.from_job(job, master_ms=span * 1000, master=fetched)
    for line in _timeline_lines(timeline):
        print(line)
    print(f"\n  {'start':>6} {'win':>6} {'mode':14} {'kind':20} {'median':>8}  verdict")
    for w in timeline.painted_windows():
        s, d = w.start_ms / 1000.0, (w.end_ms - w.start_ms) / 1000.0
        mode = str(w.fields.get("render_mode") or "?")
        kind = str((w.fields.get("diagram_debug") or {}).get("kind") or "-")
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
