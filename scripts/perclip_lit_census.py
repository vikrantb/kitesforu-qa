#!/usr/bin/env python3
"""Per-clip lit-area census — the motion-magic occlusion instrument, rebuilt durable.

The original (`scratchpad/perclip_occ.py`, 2026-08-24) died with its session scratchpad;
its headline comparisons (35.4% etc.) are NOT comparable to this rebuild unless the lit
definition matched — treat every number this script prints as a FRESH baseline, never as
that session's series (motion-magic PROPOSAL.md §"corrected numbers" retracts the older
stitched-master figures outright).

DEFINITION (declared, since the measurement carries its command):
  * per clip, from the clip's OWN `asset_uri` (engine label and pixels from the same
    record — the stitched-master time-sampling credited mermaid pixels to htmlfig);
  * stills: the image itself; motion mp4s: the middle frame (ffmpeg);
  * grayscale at 128x72; dark floor = p10 of the frame's own pixels;
  * lit fraction = share of pixels > floor + 16 (16/255 ≈ a just-visible step above
    the background; declared constant, tunable only WITH a re-baseline).

Usage:
  GCP_PROJECT_ID=kitesforu-dev python3 scripts/perclip_lit_census.py <job_id> [--limit N]
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections import defaultdict

LIT_STEP = 16
W, H = 128, 72


def _gray_frame(path: str, is_video: bool) -> bytes | None:
    cmd = ["ffmpeg", "-v", "error"]
    if is_video:
        # MIDDLE frame, never frame 0: kinetic/motion clips open dark before
        # their content animates in (the same frame-0 trap the card thumbnails
        # shipped with) — measuring t=0 reads "black", not "occluded".
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path], capture_output=True, timeout=30,
            ).stdout.strip() or 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            dur = 0.0
        if dur > 0:
            cmd += ["-ss", f"{dur / 2:.2f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", f"scale={W}:{H}", "-pix_fmt", "gray",
            "-f", "rawvideo", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out[: W * H] if len(out) >= W * H else None


def _lit_fraction(frame: bytes) -> float:
    vals = sorted(frame)
    p10 = vals[len(vals) // 10]
    thr = min(255, p10 + LIT_STEP)
    return sum(1 for v in frame if v > thr) / len(frame)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    job_id = sys.argv[1]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 200

    from google.cloud import firestore, storage

    db = firestore.Client()
    gcs = storage.Client()
    doc = db.collection("podcast_jobs").document(job_id).get().to_dict() or {}
    clips = (doc.get("visual") or {}).get("clips") or []
    rows, by_engine = [], defaultdict(list)
    measured = skipped = 0
    for c in clips[:limit]:
        if not isinstance(c, dict):
            continue
        uri = c.get("asset_uri")
        if not isinstance(uri, str) or not uri.startswith("gs://"):
            skipped += 1
            continue
        is_video = uri.lower().endswith((".mp4", ".webm", ".mov"))
        bucket_name, _, blob = uri[5:].partition("/")
        suffix = ".mp4" if is_video else ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tf:
            try:
                gcs.bucket(bucket_name).blob(blob).download_to_filename(tf.name)
            except Exception as exc:  # noqa: BLE001 — one bad asset must not kill the census
                print(f"  fetch-fail {uri[-30:]}: {str(exc)[:50]}")
                skipped += 1
                continue
            frame = _gray_frame(tf.name, is_video)
        if not frame:
            skipped += 1
            continue
        lit = _lit_fraction(frame)
        dbg = c.get("diagram_debug") or {}
        engine = f"{dbg.get('kind', c.get('modality'))}/{str(dbg.get('disposition',''))[:12]}"
        rows.append((lit, engine, uri[-28:]))
        by_engine[engine].append(lit)
        measured += 1

    rows.sort()
    print(f"job {job_id}: measured={measured} skipped={skipped} (definition: p10+{LIT_STEP}, {W}x{H})")
    for lit, engine, tail in rows[:5]:
        print(f"  darkest {lit:5.1%}  {engine:<28} {tail}")
    if rows:
        import statistics
        lits = [r[0] for r in rows]
        print(f"  median lit {statistics.median(lits):.1%} · p10 {sorted(lits)[len(lits)//10]:.1%} · max {max(lits):.1%}")
    for engine, ls in sorted(by_engine.items()):
        import statistics
        print(f"  {engine:<30} n={len(ls):<3} median={statistics.median(ls):5.1%}")


if __name__ == "__main__":
    main()
