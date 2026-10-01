#!/usr/bin/env python3
"""What did each job's visuals passes PAY an image provider for, against what its ledger says?

WHY THIS EXISTS. ``costs.visuals_images`` was read off ``visual.clips``, and a ledger read off the
clips cannot measure spend (workers #3239, round D). Witness e032d06d: the fal queue logged FIVE
paid FLUX renders — three initial, two ``scene_verify ENFORCE REGEN`` — while the clips show one
still, three times. The worker's own success line (``story_visuals rendered ... cache=False``)
saw 1 of the 5, so it is not a provider-side record. This script reads the provider-side one.

THE PROVIDER ARM — one row per image-generation HTTP request the visuals service made, read from
Cloud Logging (httpx logs every request it sends), attributed by ``jsonPayload.job_id``, 200s only:

    fal_flux        POST https://queue.fal.run/fal-ai/flux...        (submit; a queued render)
    vertex_image    POST .../models/<...-image...>:generateContent    (gemini image / nanobanana)
    vertex_imagen   POST .../models/imagen...:predict                 (``predictLongRunning`` = Veo,
                                                                       excluded: settled elsewhere)
    openai_images   POST https://api.openai.com/v1/images/...

A 200 is a request the provider accepted. A fal submit whose render later failed is counted here
and not in the ledger, so the provider arm is an UPPER bound on billed renders; the ledger's
receipt records only dispatches that returned image bytes.

THE RETURNED-BYTES ARM — the vendor clients' own success lines, the closest log record of what the
receipt books (a dispatch that returned image bytes):

    fal FLUX completed ...                              (``fal_flux_client``; before the download)
    story_visuals nanobanana image generated ...        (``nanobanana_client``)
    story_visuals openai image generated ...            (``openai_image_client``)

Imagen logs no success line, so its renders appear only in the provider arm.

ATTRIBUTION IS PARTIAL, and the script says how partial: a request logged without
``jsonPayload.job_id`` is counted under "unattributed" and in no job's row, so every per-job
count is a LOWER bound on that job's requests.

THE LEDGER ARMS, per job, from ``podcast_jobs`` (read-only):

    main_per_clip   clips carrying a paid model, once per CLIP (``_sum_visuals_image_cost``,
                    the per-clip rule every stamp before #3239 was written with)
    clip_assets     distinct paid assets the FINAL clips show (``image_cost_ledger.paid_assets``
                    with no receipt: the head 21bd6de rule)
    stored_scenes   ``costs.visuals_images.meta.scenes`` as written
    booked          paid renders the ledger booked — ``meta.scenes`` on a stamp that carries
                    ``meta.assets`` (kept assets plus ``meta.discarded`` counts), so present only on
                    jobs rolled up by #3239 or later. AFTER DEPLOY, THIS IS THE COLUMN TO CHECK: it
                    should equal the returned-bytes arm, less any render the receipt could not see
                    (the ledger module names them).

USAGE (needs ``WORKERS_SRC`` = a kitesforu-workers ``src`` checkout, for the pricing rules)::

    WORKERS_SRC=../kitesforu-workers/src python scripts/image_spend_provider_census.py \\
        --since 2026-09-01T00:00:00Z

Read-only: Cloud Logging and Firestore reads, no provider call, no write.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT = "kitesforu-dev"
SERVICE = "kitesforu-worker-visuals"

#: The founder's personal accounts, so a census can name them instead of calling them "other".
#: Comma-separated, from the environment; never committed.
_FOUNDER_EMAILS = frozenset(
    e.strip().lower() for e in os.environ.get("KFU_FOUNDER_EMAILS", "").split(",") if e.strip()
)

_QA_ROOT = Path(__file__).resolve().parents[1]
_WORKERS_SRC = os.environ.get("WORKERS_SRC") or str(_QA_ROOT.parent / "kitesforu-workers" / "src")

_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fal_flux", re.compile(r"POST https://queue\.fal\.run/fal-ai/flux")),
    ("vertex_image", re.compile(r"POST https://[^ ]*/models/[^ /:]*-image[^ /:]*:generateContent")),
    ("vertex_imagen", re.compile(r"POST https://[^ ]*/models/imagen[^ /:]*:predict(?!LongRunning)")),
    ("openai_images", re.compile(r"POST https://api\.openai\.com/v1/images/")),
)

_DONE: tuple[tuple[str, str], ...] = (
    ("fal_flux", "fal FLUX completed"),
    ("vertex_image", "story_visuals nanobanana image generated"),
    ("openai_images", "story_visuals openai image generated"),
)

_LOG_FILTER = (
    'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" '
    'AND timestamp>="{since}" AND timestamp<"{until}" AND ('
    'jsonPayload.message:"POST https://queue.fal.run/fal-ai/flux" OR '
    'jsonPayload.message:"-image:generateContent" OR '
    'jsonPayload.message:":predict" OR '
    'jsonPayload.message:"api.openai.com/v1/images" OR '
    'jsonPayload.message:"fal FLUX completed" OR '
    'jsonPayload.message:"nanobanana image generated" OR '
    'jsonPayload.message:"openai image generated")'
)


def classify(message: str) -> str | None:
    """The provider family of one httpx request line, or None when it is not an image render.
    Only accepted requests (``200 OK``) count."""
    if "200 OK" not in message:
        return None
    for family, pattern in _FAMILIES:
        if pattern.search(message):
            return family
    return None


def done_family(message: str) -> str | None:
    """The family of a vendor client's success line (image bytes came back), else None."""
    for family, needle in _DONE:
        if message.startswith(needle) or needle in message[:80]:
            return family
    return None


def provider_counts(
    since: str, until: str,
) -> tuple[dict[str, collections.Counter], dict[str, int], int, dict[str, collections.Counter], dict[str, int]]:
    """``(requests per job, unattributed requests, lines read, completions per job,
    unattributed completions)``, each per family."""
    q = _LOG_FILTER.format(service=SERVICE, since=since, until=until)
    proc = subprocess.run(
        ["gcloud", "logging", "read", q, "--project", PROJECT, "--limit", "200000",
         "--format", "json"],
        capture_output=True, text=True, check=True,
    )
    rows = json.loads(proc.stdout or "[]")
    per_job: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    done: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    unattributed: collections.Counter = collections.Counter()
    undone: collections.Counter = collections.Counter()
    for r in rows:
        p = r.get("jsonPayload") or {}
        msg = str(p.get("message") or "")
        jid = str(p.get("job_id") or "").strip()
        fam = classify(msg)
        if fam is not None:
            if jid:
                per_job[jid][fam] += 1
            else:
                unattributed[fam] += 1
            continue
        dfam = done_family(msg)
        if dfam is not None:
            if jid:
                done[jid][dfam] += 1
            else:
                undone[dfam] += 1
    return per_job, dict(unattributed), len(rows), done, dict(undone)


def main_per_clip(clips: list[Any]) -> int:
    """The per-clip rule of every pre-#3239 stamp (``worker._sum_visuals_image_cost``, deleted by
    #3239): skip a reused re-cut, count a clip with a model id, count an ai_generated relimage."""
    n = 0
    for c in clips:
        if not isinstance(c, dict):
            continue
        ev = c.get("imagination_event")
        if isinstance(ev, dict) and ev.get("reused") is True:
            continue
        if c.get("rendered_model_id") or c.get("model_id"):
            n += 1
            continue
        dbg = c.get("diagram_debug") or {}
        if c.get("ai_generated") and isinstance(dbg, dict) and dbg.get("kind") == "relimage":
            n += 1
    return n


def owner_class(db: Any, user_id: str, cache: dict[str, str]) -> str:
    """test@ / e2e / founder / other / unknown — never the address itself."""
    if user_id in cache:
        return cache[user_id]
    label = "unknown"
    try:
        u = db.collection("users").document(user_id).get()
        email = str(((u.to_dict() or {}) if u.exists else {}).get("email") or "").lower()
        # The E2E harness signs in as the same test@ address under its own user id (``…_e2e``),
        # so the id decides before the address does.
        if "e2e" in user_id.lower() or "e2e" in email:
            label = "e2e"
        elif email.startswith("test@"):
            label = "test@"
        elif email and email in _FOUNDER_EMAILS:
            label = "founder"
        elif email:
            label = "other"
    except Exception:  # noqa: BLE001 — a label lookup must not stop the census
        label = "unknown"
    cache[user_id] = label
    return label


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-01T00:00:00Z")
    ap.add_argument("--until", default=datetime.datetime.now(datetime.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"))
    ap.add_argument("--rows", type=int, default=40, help="per-job rows to print (largest first)")
    args = ap.parse_args()

    sys.path.insert(0, _WORKERS_SRC)
    from google.cloud import firestore  # noqa: E402
    from workers.stages.visuals.image_cost_ledger import paid_assets  # noqa: E402

    per_job, unattributed, lines, done, undone = provider_counts(args.since, args.until)
    db = firestore.Client(project=PROJECT)
    owners: dict[str, str] = {}
    table = []
    for jid in sorted(set(per_job) | set(done)):
        fams = per_job.get(jid) or collections.Counter()
        snap = db.collection("podcast_jobs").document(jid).get()
        d = snap.to_dict() if snap.exists else {}
        clips = ((d.get("visual") or {}).get("clips")) or []
        clips = clips if isinstance(clips, list) else []
        block = (d.get("costs") or {}).get("visuals_images")
        meta = (block or {}).get("meta") if isinstance(block, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        assets, unnamed, _ = paid_assets(clips)
        booked = meta.get("scenes") if isinstance(meta.get("assets"), dict) else None
        table.append({
            "job": jid[:8],
            "owner": owner_class(db, str(d.get("user_id") or ""), owners),
            "provider": sum(fams.values()),
            "returned": sum((done.get(jid) or collections.Counter()).values()),
            "families": dict(fams),
            "main_per_clip": main_per_clip(clips),
            "clip_assets": len(assets) + sum(unnamed.values()),
            "stored_scenes": meta.get("scenes"),
            "booked": int(booked) if isinstance(booked, (int, float)) else None,
            "exists": snap.exists,
        })

    table.sort(key=lambda r: -r["provider"])
    print(f"IMAGE SPEND vs PROVIDER — {SERVICE}, {args.since} .. {args.until}")
    print(f"  log lines read: {lines}; attributed jobs: {len(table)}; "
          f"unattributed accepted requests: {unattributed or 0}; "
          f"unattributed completions: {undone or 0}")
    print(f"  owners: {dict(collections.Counter(r['owner'] for r in table))}")
    print(f"  provider families: {dict(sum((collections.Counter(r['families']) for r in table), collections.Counter()))}")
    tot = {k: sum(int(r[k] or 0) for r in table)
           for k in ("provider", "returned", "main_per_clip", "clip_assets")}
    print(f"  totals: provider={tot['provider']}  returned={tot['returned']}  "
          f"main_per_clip={tot['main_per_clip']}  clip_assets={tot['clip_assets']}")
    for ref in ("provider", "returned"):
        for arm in ("main_per_clip", "clip_assets"):
            above = sum(1 for r in table if r[ref] > r[arm])
            equal = sum(1 for r in table if r[ref] == r[arm])
            below = sum(1 for r in table if r[ref] < r[arm])
            print(f"  {ref:8s} vs {arm:13s}: {ref} higher on {above}, equal on {equal}, "
                  f"lower on {below}")
    with_booked = [r for r in table if r["booked"] is not None]
    print(f"  jobs carrying meta.assets (rolled up by #3239+): {len(with_booked)}")
    if with_booked:
        for ref in ("provider", "returned"):
            eq = sum(1 for r in with_booked if r["booked"] == r[ref])
            print(f"    booked == {ref} on {eq} of {len(with_booked)}")
    print()
    print(f"  {'job':8s} {'owner':8s} {'provider':>8s} {'returned':>8s} {'main':>5s} {'clip':>5s} "
          f"{'stored':>6s} {'booked':>6s}  families")
    for r in table[: args.rows]:
        print(f"  {r['job']:8s} {r['owner']:8s} {r['provider']:>8d} {r['returned']:>8d} "
              f"{r['main_per_clip']:>5d} "
              f"{r['clip_assets']:>5d} {str(r['stored_scenes']):>6s} {str(r['booked']):>6s}  "
              f"{r['families']}{'' if r['exists'] else '  (job doc missing)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
