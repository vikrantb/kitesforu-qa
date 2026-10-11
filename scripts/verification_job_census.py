#!/usr/bin/env python3
"""READ-ONLY census behind the numbers in ``create_verification_job.sh`` and ``verification_job.py``. No writes,
except the population file, and only when asked (kitesforu-qa #175 round 6, latency L6-3, claims 4).

The wait budgets, the visuals-authors band and the audio tail cite this script. It used to live outside the repo,
and it overwrote its own "frozen" population every time it ran, so neither the numbers nor the set they came from
could be re-derived. Now:

* ``--population FILE`` names the job ids. When the file EXISTS it is read and never written, so a re-run measures
  the same set. ``--refresh-population`` queries a new set and WRITES the file, and is the only thing that does.
* THE NEWEST N IS ONLY THE TIMESTAMP-TYPED DOCS. ``created_at`` is a Firestore timestamp on new docs and a STRING
  on some 2026-01 docs, and Firestore orders by type group first, so an unfiltered ``order_by(created_at desc)``
  puts the strings first (2 of the round-5 set of 600: 2026-01-15 test-identity docs). ``--refresh-population``
  filters to the timestamp group. The round-5 file, written before this fix, still holds those 2; the census
  says how many string-dated docs a population carries.
* The job ids are real, so the population file is kept OUT of this public repository: pass a path outside it.

Sections (``--section``, default all):
  timing  created -> video (the GCS object time_created of the compartment's video; the job doc stamps no video
          time), for purchased and not-purchased jobs, and created -> completed for audio-only. The wait budgets.
  shapes  the readiness shapes on jobs that asked for visuals, read through the package's ONE rule.
  costs   the four visuals author stages summed per job: the authors band.
  images  booked paid-image dollars on purchased jobs, against the estimate's still bound.
  audio   the non-visual cost per (tier, duration) of finished jobs: the audio tail.

Usage: python3 scripts/verification_job_census.py --population /path/outside/repo/population.txt [--section audio]
       python3 scripts/verification_job_census.py --population /path/outside/repo/population.txt --refresh-population
Needs read access to Firestore and Cloud Storage metadata (``google-cloud-firestore``, ``google-cloud-storage``).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import statistics
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kitesforu_qa.visual_readiness import (  # noqa: E402
    STAMP_SETTLED,
    VisualReadiness,
    is_delivered_hero_clip,
    settle_stamp_state,
)

PROJECT = os.environ.get("GCP_PROJECT", "kitesforu-dev")
COLLECTION = "podcast_jobs"
FINISHED = frozenset({"completed", "needs_review", "failed_qa"})
AUTHORS = ("diagram_author", "figure_author", "geometry_author", "visuals_art_director")
VISUAL_STAGES = frozenset({"visuals_images", "visuals_veo", *AUTHORS})
FIELDS = ["created_at", "completed_at", "status", "user_id", "quality_tier", "visual_options", "wants_visuals",
          "inputs.duration_min", "visual", "costs"]
# The timestamp type group starts above every real date; a filter on it drops the string-typed docs.
_EPOCH = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)


def _ts(v):
    if hasattr(v, "timestamp"):
        return v.timestamp()
    return None


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


def _report(label, xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        print(f"  {label}: n=0")
        return
    print(f"  {label}: n={len(xs)} median={statistics.median(xs):.0f}s p90={_pct(xs, 90):.0f}s "
          f"p95={_pct(xs, 95):.0f}s p99={_pct(xs, 99):.0f}s max={max(xs):.0f}s")


def _blob_of(url):
    """(bucket, blob) for gs://b/x or https://storage.googleapis.com/b/x?sig. None otherwise."""
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("gs://"):
        b, _, name = url[5:].partition("/")
        return (b, name) if b and name else None
    p = urlparse(url)
    if p.netloc == "storage.googleapis.com":
        parts = p.path.lstrip("/").split("/", 1)
        if len(parts) == 2:
            return parts[0], unquote(parts[1])
    return None


def _population(db, path: Path, *, refresh: bool, n: int):
    from google.cloud import firestore

    if path.exists() and not refresh:
        return [line.strip() for line in path.read_text().splitlines() if line.strip()], "read"
    q = (db.collection(COLLECTION)
         .where(filter=firestore.FieldFilter("created_at", ">=", _EPOCH))
         .order_by("created_at", direction=firestore.Query.DESCENDING).select(["created_at"]).limit(n))
    ids = [s.id for s in q.stream()]
    path.write_text("".join(f"{i}\n" for i in ids))
    return ids, "written"


def _rows(db, ids):
    out = []
    for i in range(0, len(ids), 100):
        refs = [db.collection(COLLECTION).document(j) for j in ids[i:i + 100]]
        for s in db.get_all(refs, field_paths=FIELDS):
            d = s.to_dict() or {}
            vo = d.get("visual_options") if isinstance(d.get("visual_options"), dict) else {}
            vis = d.get("visual") if isinstance(d.get("visual"), dict) else {}
            costs = d.get("costs") if isinstance(d.get("costs"), dict) else {}
            stage = {k: float(v["total_cost_usd"]) for k, v in costs.items()
                     if isinstance(v, dict) and isinstance(v.get("total_cost_usd"), (int, float))}
            dur = (d.get("inputs") or {}).get("duration_min") if isinstance(d.get("inputs"), dict) else None
            clips = [c for c in (vis.get("clips") or []) if isinstance(c, dict)] if isinstance(vis, dict) else []
            out.append(dict(
                id=s.id, created=_ts(d.get("created_at")), created_raw=d.get("created_at"),
                completed=_ts(d.get("completed_at")), status=d.get("status"), tier=d.get("quality_tier"),
                duration=dur, purchased=int(vo.get("motion_clips") or 0), asked=d.get("wants_visuals") is True or bool(vo),
                visual=vis, video_url=vis.get("video_burned_url") or vis.get("video_url"), stage=stage,
                hero=sum(1 for c in clips if is_delivered_hero_clip(c)),
            ))
    return out


def timing(rows, gcs):
    print("\n== timing: created -> video object time_created (GCS metadata); created -> completed_at")
    cache = {}

    def video_t(url):
        key = _blob_of(url)
        if key is None:
            return None
        if key not in cache:
            try:
                blob = gcs.bucket(key[0]).get_blob(key[1])
                cache[key] = blob.time_created.timestamp() if blob is not None else None
            except Exception:  # noqa: BLE001 - an unreadable object is reported as missing, never guessed
                cache[key] = None
        return cache[key]

    vids = [(r, video_t(r["video_url"])) for r in rows if r["video_url"] and r["created"]]
    print(f"  video urls: {len(vids)}, object read: {sum(1 for _, t in vids if t)}")
    for label, pick in (("purchased", lambda r: r["purchased"] > 0), ("not purchased", lambda r: r["purchased"] == 0)):
        ts = [t - r["created"] for r, t in vids if t and pick(r)]
        _report(f"{label}: created -> video", ts)
        for bound in (1800, 3600, 5400, 7200, 10800):
            print(f"    {label}: video after {bound}s: {sum(1 for x in ts if x > bound)} of {len(ts)}")
    audio_only = [r for r in rows if not r["asked"] and r["completed"] and r["created"]]
    _report("audio-only: created -> completed_at", [r["completed"] - r["created"] for r in audio_only])


def shapes(rows):
    print("\n== shapes: readiness on jobs that asked for visuals, by the package's ONE rule (no producer reasons)")
    asked = [r for r in rows if r["asked"]]
    phase = collections.Counter(VisualReadiness.from_visual(r["visual"]).video_phase(expected=True)[0] for r in asked)
    stamp = collections.Counter(settle_stamp_state({"visual": r["visual"]}) for r in asked)
    print(f"  n={len(asked)} phases={dict(phase)} settle={dict(stamp)}")
    print("  skip reasons:", dict(collections.Counter(r["visual"].get("video_skip_reason") for r in asked
                                                       if r["visual"].get("video_skip_reason"))))
    settled = [r for r in asked if r["purchased"] and r["video_url"] and settle_stamp_state({"visual": r["visual"]})
               == STAMP_SETTLED]
    print(f"  purchased, a video and a settled stamp: {len(settled)}; every purchased clip delivered: "
          f"{sum(1 for r in settled if r['hero'] >= r['purchased'])}")


def costs(rows):
    print("\n== costs: the four visuals author stages, summed per job, on jobs with a visual compartment")
    sums = [sum(r["stage"].get(k, 0.0) for k in AUTHORS) for r in rows
            if r["visual"] and any(k in r["stage"] for k in AUTHORS)]
    if sums:
        print(f"  n={len(sums)} median={statistics.median(sums):.4f} p95={_pct(sums, 95):.4f} max={max(sums):.4f}")


def images(rows):
    print("\n== images: booked costs.visuals_images on purchased jobs")
    paid = [r for r in rows if r["purchased"] and "visuals_images" in r["stage"]]
    vals = sorted(r["stage"]["visuals_images"] for r in paid)
    print(f"  n={len(paid)} max={max(vals) if vals else 0:.3f}; over $1.608: {sum(1 for v in vals if v > 1.608)}; "
          f"over $2.30: {sum(1 for v in vals if v > 2.30)}")


def audio(rows):
    print("\n== audio: non-visual cost of FINISHED jobs (every stage except visuals_* and the author stages)")
    by = collections.defaultdict(list)
    for r in rows:
        if r["status"] in FINISHED and isinstance(r["duration"], (int, float)) and r["stage"]:
            by[(str(r["tier"]), round(float(r["duration"]), 3))].append(
                sum(v for k, v in r["stage"].items() if k not in VISUAL_STAGES))
    tiers = collections.defaultdict(list)
    for (tier, dur), xs in sorted(by.items()):
        tiers[tier] += xs
        print(f"  {tier:<7} {dur:>6} min: n={len(xs):>3} median={statistics.median(xs):.4f} max={max(xs):.4f}")
    for tier, xs in sorted(tiers.items()):
        print(f"  {tier:<7} any length: n={len(xs):>3} max={max(xs):.4f}")


SECTIONS = {"timing": timing, "shapes": shapes, "costs": costs, "images": images, "audio": audio}


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--population", required=True, type=Path,
                    help="the job-id file: read when it exists, written only with --refresh-population")
    ap.add_argument("--refresh-population", action="store_true", help="query a new population and write it")
    ap.add_argument("-n", type=int, default=600, help="population size for --refresh-population")
    ap.add_argument("--section", choices=sorted(SECTIONS), action="append")
    args = ap.parse_args()
    from google.cloud import firestore, storage

    db = firestore.Client(project=PROJECT)
    ids, how = _population(db, args.population, refresh=args.refresh_population, n=args.n)
    rows = _rows(db, ids)
    stringly = sum(1 for r in rows if isinstance(r["created_raw"], str))
    cs = [r["created"] for r in rows if r["created"]]
    print(f"population: {len(ids)} ids {how} ({args.population.name}); docs read: {len(rows)}; "
          f"string-dated created_at: {stringly}")
    if cs:
        print("created_at window (timestamp-typed docs):",
              dt.datetime.fromtimestamp(min(cs), dt.timezone.utc).isoformat()[:16], "..",
              dt.datetime.fromtimestamp(max(cs), dt.timezone.utc).isoformat()[:16])
    for name in args.section or sorted(SECTIONS):
        fn = SECTIONS[name]
        fn(rows, storage.Client(project=PROJECT)) if name == "timing" else fn(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
