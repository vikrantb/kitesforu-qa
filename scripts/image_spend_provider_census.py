#!/usr/bin/env python3
"""What did each job's visuals passes PAY an image provider for, against what its ledger says?

WHY THIS EXISTS. ``costs.visuals_images`` was read off ``visual.clips``, and a ledger read off the
clips cannot measure spend (workers #3239, round D). Witness e032d06d: the fal queue logged FIVE
paid FLUX renders — three initial, two ``scene_verify ENFORCE REGEN`` — while the clips show one
still, three times. The worker's own success line (``story_visuals rendered ... cache=False``)
saw 1 of the 5, so it is not a provider-side record. This script reads the provider-side one.

THE PROVIDER ARM — one row per image-generation HTTP request the visuals service made, read from
Cloud Logging (httpx logs every request it sends), attributed by ``jsonPayload.job_id``, 200s only,
KEYED BY ENDPOINT AND MODEL, because one family spans an 8x price spread (fal flux/dev $0.025 vs
flux/schnell $0.003; #181 round-2 cost lens):

    fal_flux:<model>       POST https://queue.fal.run/fal-ai/flux/<dev|schnell>  (submit; a queued render)
    vertex_image:<model>   POST .../models/<model>:generateContent  (gemini image / nanobanana)
    vertex_imagen:<model>  POST .../models/imagen...:predict  (``predictLongRunning`` = Veo,
                                                               excluded: settled elsewhere)
    openai_images:<model>  POST https://api.openai.com/v1/images/...  (the model is in the body,
                                                                       not the URL: priced as
                                                                       :data:`OPENAI_ASSUMED_MODEL`)

Each request is priced from the workers catalog (``image_cost_ledger.price_of``), so the census
compares DOLLARS as well as counts: a dispatch booked under the wrong model keeps the count right
and the $ wrong, and only the $ column shows it.

A 200 is a request the provider accepted. A fal submit whose render later failed is counted here
and not in the ledger, so the provider arm is an UPPER bound on billed renders; the ledger's
receipt records only dispatches that returned image bytes.

THE RETURNED-BYTES ARM — the vendor clients' own success lines, the closest log record of what the
receipt books (a dispatch that returned image bytes), keyed by the ``model=`` each line names:

    fal FLUX completed model=...                        (``fal_flux_client``; before the download)
    story_visuals nanobanana image generated model=...  (``nanobanana_client``)
    story_visuals openai image generated model=...      (``openai_image_client``)

IMAGEN LOGS NO SUCCESS LINE (``imagen_client.generate_still`` logs only a skip), so an Imagen render
appears in the provider arm only. The post-deploy comparison therefore reads ``booked`` against
``returned`` PLUS the job's accepted Imagen requests (``returned_or_imagen``); a job with Imagen
requests is the one place that comparison leans on the provider arm's upper bound.

ATTRIBUTION IS PARTIAL ONLY BEFORE REVISION 01200, and the script says how partial: a request
logged without ``jsonPayload.job_id`` is counted under "unattributed" and in no job's row, so a
per-job count can be LOW. That happens only on old revisions. Every unattributed accepted request
in the logs is on ``kitesforu-worker-visuals`` revision 01198 or earlier, at or before
2026-09-08T10:58:13Z. Every attributed one is on revision 01200 or later, at or after
2026-09-08T14:02:51Z. Re-derived 2026-10-01 over ``--since 2026-09-01T00:00:00Z``: 2,711 lines,
865 attributed and 417 unattributed accepted requests, with 0 on the wrong side of the boundary
in either direction (#181 round-3 claims N1). So a window that starts after 2026-09-08T14:02:51Z
has no low bias, and that includes every post-deploy window this census exists for. Before the
boundary, the two biases point opposite ways, and the returned arm carries only the low one.

TRUNCATION IS AN ERROR. ``gcloud logging read --limit N`` returning exactly N rows means the window
held more; every per-job count would silently become a lower bound, so the script stops instead.

THE LEDGER ARMS, per job, from ``podcast_jobs`` (read-only):

    main_per_clip   clips carrying a paid model, once per CLIP (the frozen pre-#3239 rule in
                    ``image_census_rules``, the rule every stamp before #3239 was written with)
    clip_assets     distinct paid assets the FINAL clips show (``image_cost_ledger.paid_assets``
                    with no receipt: the head 21bd6de rule)
    stored_scenes   ``costs.visuals_images.meta.scenes`` as written
    booked          paid renders the ledger booked — ``meta.scenes`` on a stamp that carries
                    ``meta.assets`` (kept assets, ``meta.discarded`` and ``meta.unnamed`` counts),
                    so present only on jobs rolled up by #3239 or later. AFTER DEPLOY, THIS IS THE
                    COLUMN TO CHECK: it should equal ``returned_or_imagen``, less any render the
                    receipt could not see (the ledger module names them)
    booked_usd      ``costs.visuals_images.total_cost_usd`` on those same stamps, against the
                    catalog-priced returned arm

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
from typing import Any, Callable

PROJECT = "kitesforu-dev"
SERVICE = "kitesforu-worker-visuals"

_QA_ROOT = Path(__file__).resolve().parents[1]
_WORKERS_SRC = os.environ.get("WORKERS_SRC") or str(_QA_ROOT.parent / "kitesforu-workers" / "src")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from image_census_rules import legacy_per_clip_count, owner_class  # noqa: E402

#: The OpenAI images endpoint does not name its model in the URL; the provider arm prices it as
#: the cheapest catalog row, the same assumption the cost lens's projection stated.
OPENAI_ASSUMED_MODEL = "gpt-image-1-mini"

_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fal_flux", re.compile(r"POST https://queue\.fal\.run/(fal-ai/flux[^ ?\"]*)")),
    ("vertex_image", re.compile(r"POST https://[^ ]*/models/([^ /:]*-image[^ /:]*):generateContent")),
    ("vertex_imagen", re.compile(r"POST https://[^ ]*/models/(imagen[^ /:]*):predict(?!LongRunning)")),
    ("openai_images", re.compile(r"POST https://api\.openai\.com/v1/images/")),
)

_DONE: tuple[tuple[str, str], ...] = (
    ("fal_flux", "fal FLUX completed"),
    ("vertex_image", "story_visuals nanobanana image generated"),
    ("openai_images", "story_visuals openai image generated"),
)
_DONE_MODEL = re.compile(r"\bmodel=(\S+)")

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


def catalog_model(family: str, raw: str) -> str:
    """The catalog row a request or success line names. fal names an endpoint path
    (``fal-ai/flux/schnell``); the catalog names the model (``flux-schnell``). The path → model
    map is workers' own ``fal_flux_client._MODEL_PATHS``, inverted, not a hand copy (#181 round-3
    design NIT2), so a model workers adds there is priced as that model. A path the map does not
    name stays a raw path, prices at $0 and shows up under its own key. The log filter still reads
    only ``fal-ai/flux*`` request paths."""
    raw = raw.strip().strip('"').rstrip("/")
    if family == "fal_flux":
        from workers.stages.visuals.fal_flux_client import _MODEL_PATHS

        for model, path in _MODEL_PATHS.items():
            if raw in (path, model):
                return model
    return raw


def classify(message: str) -> str | None:
    """``"<family>:<catalog model>"`` for one accepted httpx image request, else None. Only
    accepted requests (``200 OK``) count."""
    if "200 OK" not in message:
        return None
    for family, pattern in _FAMILIES:
        m = pattern.search(message)
        if m:
            raw = m.group(1) if m.groups() else OPENAI_ASSUMED_MODEL
            return f"{family}:{catalog_model(family, raw)}"
    return None


def done_family(message: str) -> str | None:
    """``"<family>:<catalog model>"`` of a vendor client's success line (image bytes came back),
    else None. A line that names no model keys as ``"<family>:?"`` and prices at $0, visibly."""
    for family, needle in _DONE:
        if message.startswith(needle) or needle in message[:80]:
            m = _DONE_MODEL.search(message)
            return f"{family}:{catalog_model(family, m.group(1)) if m else '?'}"
    return None


class TruncatedLogRead(RuntimeError):
    """``gcloud logging read`` returned exactly ``--limit`` rows: the window held more."""


def tally(rows: list[dict], limit: int) -> tuple[dict, dict, dict, dict]:
    """``(requests per job, unattributed requests, completions per job, unattributed
    completions)`` over raw log rows, each a ``Counter`` keyed ``family:model``. Raises
    :class:`TruncatedLogRead` when the read hit its limit."""
    if len(rows) >= limit:
        raise TruncatedLogRead(
            f"gcloud logging read returned {len(rows)} rows at --limit {limit}: the window holds "
            "more, and every per-job count would be a silent lower bound. Narrow --since/--until "
            "or raise --limit.")
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
            (per_job[jid] if jid else unattributed)[fam] += 1
            continue
        dfam = done_family(msg)
        if dfam is not None:
            (done[jid] if jid else undone)[dfam] += 1
    return per_job, dict(unattributed), done, dict(undone)


def provider_counts(since: str, until: str, limit: int) -> tuple[dict, dict, int, dict, dict]:
    """``(requests per job, unattributed requests, lines read, completions per job,
    unattributed completions)``."""
    q = _LOG_FILTER.format(service=SERVICE, since=since, until=until)
    proc = subprocess.run(
        ["gcloud", "logging", "read", q, "--project", PROJECT, "--limit", str(limit),
         "--format", "json"],
        capture_output=True, text=True, check=True,
    )
    rows = json.loads(proc.stdout or "[]")
    per_job, unattributed, done, undone = tally(rows, limit)
    return per_job, unattributed, len(rows), done, undone


def priced(counts: Any, price: Callable[[str], float]) -> float:
    """``sum(n x price(model))`` over a ``family:model`` Counter."""
    return sum(n * price(k.split(":", 1)[1]) for k, n in (counts or {}).items())


#: The only ``podcast_jobs`` fields :func:`job_row` and the owner lookup read. The #181 round-3
#: latency lens measured a whole job doc at p50 299 KB / 425 ms and the masked read at 8.4 KB /
#: 120 ms (7 jobs x 3 reads, from a laptop).
JOB_FIELDS = ("visual.clips", "costs.visuals_images", "user_id")


def read_job(db: Any, jid: str) -> dict:
    """The ``podcast_jobs`` doc of ``jid``, masked to :data:`JOB_FIELDS` (``{}`` when missing)."""
    snap = db.collection("podcast_jobs").document(jid).get(field_paths=list(JOB_FIELDS))
    return (snap.to_dict() or {}) if snap.exists else {}


def job_row(jid: str, doc: dict, fams: Any, done_fams: Any, owner: str,
            price: Callable[[str], float]) -> dict:
    """One job's arms. ``doc`` is the ``podcast_jobs`` dict as :func:`read_job` returns it
    (``{}`` when the doc is missing)."""
    from workers.stages.visuals.image_cost_ledger import paid_assets

    fams = fams or collections.Counter()
    done_fams = done_fams or collections.Counter()
    clips = ((doc.get("visual") or {}).get("clips")) or []
    clips = clips if isinstance(clips, list) else []
    block = (doc.get("costs") or {}).get("visuals_images")
    block = block if isinstance(block, dict) else {}
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    assets, unnamed, _ = paid_assets(clips)
    ledger = isinstance(meta.get("assets"), dict)
    booked = meta.get("scenes") if ledger else None
    imagen = {k: n for k, n in fams.items() if k.startswith("vertex_imagen:")}
    returned = sum(done_fams.values())
    return {
        "job": jid[:8],
        "owner": owner,
        "provider": sum(fams.values()),
        "provider_usd": round(priced(fams, price), 6),
        "returned": returned,
        "returned_usd": round(priced(done_fams, price), 6),
        "returned_or_imagen": returned + sum(imagen.values()),
        "returned_or_imagen_usd": round(priced(done_fams, price) + priced(imagen, price), 6),
        "families": dict(fams),
        "main_per_clip": legacy_per_clip_count(clips),
        "clip_assets": len(assets) + sum(unnamed.values()),
        "stored_scenes": meta.get("scenes"),
        "booked": int(booked) if isinstance(booked, (int, float)) else None,
        "booked_usd": (round(float(block.get("total_cost_usd") or 0.0), 6) if ledger else None),
        "exists": bool(doc),
    }


def summary_lines(table: list[dict]) -> list[str]:
    """The fleet lines, including the post-deploy check on the jobs that carry ``meta.assets``."""
    out = []
    tot = {k: sum(int(r[k] or 0) for r in table)
           for k in ("provider", "returned", "main_per_clip", "clip_assets")}
    out.append(f"  totals: provider={tot['provider']}  returned={tot['returned']}  "
               f"main_per_clip={tot['main_per_clip']}  clip_assets={tot['clip_assets']}")
    out.append(f"  priced: provider ${sum(r['provider_usd'] for r in table):.4f}  "
               f"returned ${sum(r['returned_usd'] for r in table):.4f}")
    for ref in ("provider", "returned"):
        for arm in ("main_per_clip", "clip_assets"):
            above = sum(1 for r in table if r[ref] > r[arm])
            equal = sum(1 for r in table if r[ref] == r[arm])
            below = sum(1 for r in table if r[ref] < r[arm])
            out.append(f"  {ref:8s} vs {arm:13s}: {ref} higher on {above}, equal on {equal}, "
                       f"lower on {below}")
    with_booked = [r for r in table if r["booked"] is not None]
    out.append(f"  jobs carrying meta.assets (rolled up by #3239+): {len(with_booked)}")
    if with_booked:
        n = len(with_booked)
        eq = sum(1 for r in with_booked if r["booked"] == r["returned_or_imagen"])
        eq_usd = sum(1 for r in with_booked
                     if abs(float(r["booked_usd"] or 0.0) - r["returned_or_imagen_usd"]) < 1e-6)
        out.append(f"    booked == returned (+Imagen requests) on {eq} of {n}")
        out.append(f"    booked $ == returned $ (+Imagen requests) on {eq_usd} of {n}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-01T00:00:00Z")
    ap.add_argument("--until", default=datetime.datetime.now(datetime.timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"))
    ap.add_argument("--limit", type=int, default=200000, help="gcloud logging read --limit")
    ap.add_argument("--rows", type=int, default=40, help="per-job rows to print (largest first)")
    args = ap.parse_args()

    sys.path.insert(0, _WORKERS_SRC)
    from google.cloud import firestore  # noqa: E402
    from workers.stages.visuals.image_cost_ledger import price_of  # noqa: E402

    per_job, unattributed, lines, done, undone = provider_counts(args.since, args.until, args.limit)
    db = firestore.Client(project=PROJECT)
    owners: dict[str, str] = {}
    table = []
    for jid in sorted(set(per_job) | set(done)):
        d = read_job(db, jid)
        table.append(job_row(jid, d, per_job.get(jid), done.get(jid),
                             owner_class(db, str(d.get("user_id") or ""), owners), price_of))

    table.sort(key=lambda r: -r["provider"])
    print(f"IMAGE SPEND vs PROVIDER — {SERVICE}, {args.since} .. {args.until}")
    print(f"  log lines read: {lines} (limit {args.limit}); attributed jobs: {len(table)}; "
          f"unattributed accepted requests: {unattributed or 0}; "
          f"unattributed completions: {undone or 0}")
    print(f"  owners: {dict(collections.Counter(r['owner'] for r in table))}")
    print(f"  provider families: {dict(sum((collections.Counter(r['families']) for r in table), collections.Counter()))}")
    for line in summary_lines(table):
        print(line)
    print()
    print(f"  {'job':8s} {'owner':8s} {'provider':>8s} {'prov$':>8s} {'returned':>8s} {'ret$':>8s} "
          f"{'main':>5s} {'clip':>5s} {'stored':>6s} {'booked':>6s} {'book$':>8s}  families")
    for r in table[: args.rows]:
        print(f"  {r['job']:8s} {r['owner']:8s} {r['provider']:>8d} {r['provider_usd']:>8.4f} "
              f"{r['returned']:>8d} {r['returned_usd']:>8.4f} {r['main_per_clip']:>5d} "
              f"{r['clip_assets']:>5d} {str(r['stored_scenes']):>6s} {str(r['booked']):>6s} "
              f"{str(r['booked_usd']):>8s}  "
              f"{r['families']}{'' if r['exists'] else '  (job doc missing)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
