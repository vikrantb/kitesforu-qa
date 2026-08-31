#!/usr/bin/env python3
"""frames_vs_captions.py — does the PICTURE match the WORDS SPOKEN AT THAT INSTANT?

$0, read-only, creates no job. Extracts N frames from the artifact the USER OPENS and pairs
each with the caption cue active at that exact timestamp, then writes a manifest a
fresh-context adversary can grade. It renders no verdict itself — deliberately.

WHY THIS EXISTS. The A/V-mismatch backlog item was settled once by hand: "I graded the
delivered witness by extracting 6 frames and reading them against the caption spoken at that
exact timestamp — 4 of 6 are clear mismatches WITH the anchor firing." That method settled in
one look what two offline proxies could not: a strict "on-time" score said 20% and a lenient
rival said 87%, because BOTH ran on re-derived plan text rather than on delivered pixels. The
backlog item therefore bans tuning offline proxies for this question. This makes the method
that DID work repeatable instead of manual.

WHAT IT REUSES, rather than reimplementing:
  * ``harness.checks.video_sync._parse_vtt_cues`` — the VTT parser already used against the
    worker's own ``build_captions_vtt`` output.
  * the job doc's ``captions_vtt`` and the surfaced master URL, so frames come from the exact
    artifact the user opens (rule 02: a bare ``gs://`` field is a FAIL, not a source).

WHAT IT DELIBERATELY DOES NOT DO. It emits no score. A model reading its own frames and
grading them is the echo chamber ``.claude/rules/02-done.md`` warns about — the adversary is a
separate agent given the frames and the brief, never the claim.

Usage:
    python3 scripts/frames_vs_captions.py --job-id <id> [--frames 8] [--out-dir DIR]
    python3 scripts/frames_vs_captions.py --url <mp4-url> --vtt-file <path> [--frames 8]

Exit 0 = a manifest was written. Non-zero = the artifact or its captions could not be read,
which is itself a finding: a video with no captions cannot be graded this way at all.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

from kitesforu_qa.harness.checks.video_sync import _parse_vtt_cues  # noqa: E402


def _fetch_job(job_id: str, project: str) -> dict:
    from google.cloud import firestore  # noqa: PLC0415

    db = firestore.Client(project=project)
    doc = db.collection("podcast_jobs").document(job_id).get()
    if not doc.exists:
        raise SystemExit(f"no such job: {job_id}")
    return doc.to_dict() or {}


def _surfaced_url(job: dict) -> str:
    """The artifact the USER opens — burned captions first, then the plain master.

    A ``gs://`` value is refused rather than rewritten: rule 02 calls an intermediate render
    path a FAIL, and silently converting one would hide exactly the defect that rule exists
    to catch."""
    visual = job.get("visual") or {}
    for key in ("video_burned_url", "video_url"):
        url = visual.get(key) or job.get(key)
        if isinstance(url, str) and url.startswith("http"):
            return url
        if isinstance(url, str) and url.startswith("gs://"):
            raise SystemExit(f"{key} is a bare gs:// path — a surfacing FAIL, not a source")
    raise SystemExit("no surfaced master URL on this job (visuals may not have completed)")


def _duration_ms(path: str) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    try:
        return int(float(out) * 1000)
    except ValueError as exc:
        raise SystemExit(f"ffprobe could not read a duration from {path}") from exc


def _cue_at(cues: list, ms: int) -> str:
    """The caption ACTIVE at ``ms``; if none is (a gap between cues), the nearest one, MARKED.

    A gap is reported rather than hidden — "nothing was being said" is a legitimate reading of
    a frame, and silently borrowing a neighbouring cue would manufacture a mismatch."""
    for c in cues:
        if c["start_ms"] <= ms <= c["end_ms"]:
            return c["text"]
    if not cues:
        return ""
    nearest = min(cues, key=lambda c: min(abs(c["start_ms"] - ms), abs(c["end_ms"] - ms)))
    return f"[GAP - nearest cue] {nearest['text']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job-id")
    ap.add_argument("--url", help="an mp4 URL directly (skips Firestore)")
    ap.add_argument("--vtt-file", help="a local .vtt, when --url is used")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--project", default="kitesforu-dev")
    args = ap.parse_args()

    if not args.job_id and not args.url:
        ap.error("one of --job-id or --url is required")

    if args.job_id:
        job = _fetch_job(args.job_id, args.project)
        url = _surfaced_url(job)
        vtt = job.get("captions_vtt") or (job.get("visual") or {}).get("captions_vtt") or ""
        label = args.job_id
    else:
        url, label = args.url, "url"
        vtt = pathlib.Path(args.vtt_file).read_text() if args.vtt_file else ""

    if not vtt.strip():
        print("NO CAPTIONS: this artifact carries no captions_vtt, so it cannot be graded")
        print("against its own words. That absence IS the finding — report it, do not")
        print("substitute a proxy.")
        return 3

    cues = _parse_vtt_cues(vtt)
    if not cues:
        print(f"CAPTIONS UNPARSEABLE: {len(vtt)} chars present but ZERO cues parsed —")
        print("parser or caption-format drift. Do not treat this as 'no mismatches'.")
        return 3

    base = args.out_dir or os.environ.get("KFU_ARTIFACTS_DIR") or "."
    out_dir = pathlib.Path(base) / f"frames_{label[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    local = out_dir / "master.mp4"
    if not local.exists():
        print(f"fetching {url[:96]} ...")
        urllib.request.urlretrieve(url, local)  # noqa: S310 — our own job doc's https URL

    total = _duration_ms(str(local))
    # Interior samples only: t=0 is a title card and t=end an outro on most artifacts, and
    # neither is the content-relevance question being asked.
    stamps = [int(total * (i + 1) / (args.frames + 1)) for i in range(args.frames)]

    rows = []
    for ms in stamps:
        frame = out_dir / f"t{ms // 1000:04d}s.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ms / 1000:.3f}",
             "-i", str(local), "-frames:v", "1", "-q:v", "3", str(frame)],
            check=False,
        )
        rows.append({"t_ms": ms, "t": f"{ms // 60000}:{(ms // 1000) % 60:02d}",
                     "spoken": _cue_at(cues, ms), "frame": str(frame)})

    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps(
        {"job_id": args.job_id, "url": url, "duration_ms": total,
         "cues": len(cues), "pairs": rows}, indent=2))

    print(f"\nartifact  : {url[:100]}")
    print(f"duration  : {total / 1000:.1f}s   cues: {len(cues)}   frames: {len(rows)}")
    print(f"manifest  : {manifest}\n")
    for r in rows:
        print(f"  t={r['t']:>6}  said: {r['spoken'][:88]!r}")
        print(f"           frame: {r['frame']}")
    print("\nNext: hand these frames + spoken lines to a FRESH-CONTEXT adversary told to")
    print("refute 'every frame supports what is being said'. Do not grade them here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
