#!/usr/bin/env python3
"""Read the paid-still ledger for one job and say whether pictures were delivered.

Built 2026-08-25 because answering "why did this episode get one picture?" took a
40-minute Cloud Logging window across two jobs. Everything below is now either a
field on the job doc or one log count.

    PYTHONPATH=../kitesforu-workers/src python3 check_paid_still_ledger.py <job_id>

$0 — Firestore + Cloud Logging reads only. Never writes, never generates (Tenet 9).
"""
from __future__ import annotations

import subprocess
import sys

from google.cloud import firestore

PROJECT = "kitesforu-dev"


def _entry_count(job_id: str) -> str:
    """How many times the visuals worker RAN for this job. 12 on job 6691727f,
    before the redelivery/lease fixes (b5fad00d, 33cb5c36, e1d07410) deployed.
    Expected 1."""
    q = (
        'resource.type="cloud_run_revision" AND '
        'resource.labels.service_name="kitesforu-worker-visuals" AND '
        'jsonPayload.message:"run_visuals ENTRY" AND jsonPayload.message:"%s"'
        % job_id[:8]
    )
    try:
        out = subprocess.run(
            ["gcloud", "logging", "read", q, "--project", PROJECT,
             "--limit", "60", "--freshness", "1d", "--format", "value(timestamp)"],
            capture_output=True, text=True, timeout=120,
        ).stdout.strip()
        return str(len([x for x in out.splitlines() if x.strip()]))
    except Exception as exc:  # noqa: BLE001
        return f"(log read failed: {str(exc)[:60]})"


def describe_rollup(rollup) -> list:
    """The ``costs.visuals_images`` lines, read by the stamp's own definition: a ledger stamp
    (workers #3239+, ``meta.assets``) by its kept / discarded / unnamed / free parts, a legacy one as
    a per-clip price. ``[]`` when there is no stamp."""
    if not rollup:
        return []
    meta = rollup.get("meta") if isinstance(rollup.get("meta"), dict) else {}
    assets = meta.get("assets") if isinstance(meta.get("assets"), dict) else None
    out = [f"\n  cost rollup: ${rollup.get('total_cost_usd')}  scenes={meta.get('scenes')}  "
           f"models={meta.get('models')}"]
    if assets is None:
        out.append("      legacy stamp (pre-#3239): priced once per CLIP the plan showed")
        return out

    def _n(key: str) -> tuple:
        per_model = meta.get(key) if isinstance(meta.get(key), dict) else {}
        n = sum(int((d or {}).get("n") or 0) for d in per_model.values() if isinstance(d, dict))
        return n, {m: (d or {}).get("n") for m, d in per_model.items()}

    n_thrown, thrown = _n("discarded")
    n_unnamed, _ = _n("unnamed")
    # The asset map holds a hash per kept render; print its shape, not its contents.
    out.append(f"      kept assets {len(assets)}; discarded renders {n_thrown} {thrown}; "
               f"unnamed {n_unnamed}; free (shown, not paid) {len(meta.get('free') or [])}")
    out.append("      (a DISCARDED render is one the job paid for and threw away — a verify")
    out.append("       REJECT/REGEN or an aptness degrade; a kept asset the clips do not show")
    out.append("       is a pass the plan dropped)")
    return out


#: Workers #3279 (2026-10) changed the UNIT of ``visual.rendered_paid_images`` and of this ledger
#: field under the same names: until then each counted CLIPS carrying a ``model_id``; from the first
#: pass of a revision with #3279 they count distinct PAID PICTURES
#: (``image_cost_ledger.paid_pictures_delivered``). The ledger loop below labels this one; the stamped
#: refund input is labelled where it is printed (:func:`stamp_lines`).
UNIT_CHANGED = ("delivered_billing_counted",)


def _unit_note(ledger) -> str:
    """Which unit a ledger's billing count is in, when the doc can say.

    Main wrote ``billing_undercount`` EXACTLY when its two per-clip counts differed, and #3279 never
    writes it. So: a ledger carrying it is per clip; a ledger whose counts differ WITHOUT it was
    written by #3279 or later and counts pictures; a ledger with ``delivered_billing_counted`` and no
    ``ai_clips`` predates workers #3239 and is per clip. Only equal counts with no marker are
    ambiguous, and only there is the hedge printed (#188 round 2, critic 3)."""
    if not isinstance(ledger, dict):
        return "   (clips before workers #3279, paid pictures since: see the recount)"
    if "billing_undercount" in ledger:
        return "   (per clip: a ledger carrying billing_undercount was written before workers #3279)"
    if "delivered_billing_counted" in ledger and "ai_clips" not in ledger:
        return "   (per clip: a ledger without ai_clips predates workers #3239)"
    if "ai_clips" in ledger and ledger.get("ai_clips") != ledger.get("delivered_billing_counted"):
        return "   (paid pictures: unequal counts with no billing_undercount were written by workers #3279 or later)"
    return "   (clips before workers #3279, paid pictures since: see the recount)"


def _stamped(doc: dict, key: str) -> str:
    """A stamped field as read, telling a MISSING key from a null one, and the string ``'None'``
    from ``None`` (``repr`` for every present value: ``'3'`` is not ``3``)."""
    return "(absent)" if key not in doc else repr(doc[key])


def stamp_lines(visual: dict) -> list:
    """The refund inputs the worker STAMPED, as plain doc reads (no workers import, so they print
    whatever the recount below can do). ``rendered_paid_images_error`` is the reason a count is
    missing: since workers #3279 a count that fails writes no ``rendered_paid_images`` and stamps that
    marker, and the api, reading no count, refunds nothing for the job."""
    out = [f"      stamped rendered_paid_images {_stamped(visual, 'rendered_paid_images')}   "
           f"(the api's refund input){_unit_note(visual.get('paid_still_ledger'))}"]
    if "rendered_paid_images_error" in visual:
        out.append(f"      rendered_paid_images_error   {visual.get('rendered_paid_images_error')}   "
                   "(the picture count failed, so it was not written: the api refunds nothing here)")
    out.append(f"      stamped rendered_motion_clips {_stamped(visual, 'rendered_motion_clips')}")
    return out


def billing_recount_lines(clips: list, job: dict) -> list:
    """The refund input recounted from ``clips`` in BOTH units, so the stamp is checked and not
    trusted, whichever revision wrote it:

    * per CLIP carrying a ``model_id`` (the rule before workers #3279);
    * distinct PAID pictures, by the producer's own ``image_cost_ledger.paid_pictures_delivered``
      over the ``costs.visuals_images.meta`` the passes booked (``booked_meta``). The pass's
      receipt is gone offline, but the rollup books its free marks into ``meta.free``, so on a job
      whose rollup wrote this reads what the producer's own count read.

    BOTH READ THE DELIVERED ARRAY, AND THE STAMP DID NOT. The worker counts the rows as its pass
    RENDERED them (``author_uploads.rows_as_rendered``). Where an author's upload covers a paid
    render, the stamp counted the render and this recount sees the upload; and a step that rewrites
    rows after the count (a born-short re-cut) changed this array and not the stamp. So a recount can
    differ from the stamp in EITHER direction. The upload-row count printed beside them says when the
    first applies. A workers tree that predates #3279 has no ``paid_pictures_delivered``; that line
    says so. The producer's count raises rather than guess, and a fault is printed in its place, with
    the per-clip line kept."""
    out = [f"      with a model_id (per clip, the unit before #3279)  "
           f"{sum(1 for c in clips if isinstance(c, dict) and c.get('model_id'))}"]
    try:
        from workers.stages.visuals.image_cost_ledger import booked_meta, paid_pictures_delivered
    except ImportError:
        out.append("      paid pictures (the unit since #3279)  (the workers src on the path predates #3279)")
        return out
    try:
        n = paid_pictures_delivered(clips, None, booked_meta(job))
    except Exception as exc:  # noqa: BLE001 — the producer raises on a real fault; say which count
        out.append(f"      paid pictures (the unit since #3279)  (the producer's count failed: "
                   f"{type(exc).__name__}: {str(exc)[:80]})")
    else:
        out.append(f"      paid pictures (the unit since #3279)               {n}")
    try:
        from workers.stages.visuals.render_contract import is_user_upload
    except ImportError:
        return out
    uploads = sum(1 for c in clips if isinstance(c, dict) and is_user_upload(c))
    out.append(f"      author-upload rows                                 {uploads}"
               "   (each may cover a paid render the stamp counted and these lines do not)")
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    job_id = sys.argv[1]
    doc = firestore.Client(project=PROJECT).collection("podcast_jobs").document(job_id).get()
    if not doc.exists:
        print(f"job {job_id} NOT FOUND")
        return 2
    j = doc.to_dict() or {}
    v = j.get("visual") or {}
    clips = v.get("clips") or []

    print(f"job {job_id}   status={j.get('status')}   visual.status={v.get('status')}")
    print(f"  quality_tier={(j.get('preferences') or {}).get('quality_tier') or j.get('quality_tier')}"
          f"  visual_options={j.get('visual_options')}")

    ledger = v.get("paid_still_ledger")
    if not ledger:
        print("\n  ⚠️  NO paid_still_ledger — either the job predates the stamp, or the")
        print("      stamp did not fire. That is itself the finding; do not read on as")
        print("      though the numbers below are the ledger's.")
    else:
        print("\n  paid_still_ledger:")
        for k in ("requested", "granted", "delivered", "delivered_billing_counted",
                  "billing_undercount", "shortfall"):
            if k in ledger:
                note = (_unit_note(ledger) if k in UNIT_CHANGED else
                        "   (per clip; workers #3279 no longer writes it)" if k == "billing_undercount"
                        else "")
                print(f"      {k:26s} {ledger[k]}{note}")
        extra = {k: x for k, x in ledger.items() if k.startswith("scene_")}
        if extra:
            print(f"      {'budget chain':26s} {extra}")
        if ledger.get("shortfall"):
            print(f"      ⚠️  SHORTFALL {ledger['shortfall']} — granted more than was delivered")

    # The worker's stamps are plain doc reads: printed before the recount, which needs workers.
    print("\n  STAMPED by the worker (the api's refund inputs):")
    for line in stamp_lines(v):
        print(line)

    # Independent recount from the clips, so the ledger is checked and not trusted.
    try:
        sys.path.insert(0, "../kitesforu-workers/src")
        from workers.stages.visuals.renderer import _is_ai_generated_clip

        ai = [c for c in clips if isinstance(c, dict) and _is_ai_generated_clip(c)]
        kinds: dict = {}
        for c in ai:
            k = str((c.get("diagram_debug") or {}).get("kind") or c.get("modality") or "?")
            kinds[k] = kinds.get(k, 0) + 1
        print("\n  RECOUNT from clips (SSOT detector), not the ledger:")
        print(f"      clips                      {len(clips)}")
        print(f"      AI pictures                {len(ai)}   kinds={kinds}")
        for line in billing_recount_lines(clips, j):
            print(line)
    except Exception as exc:  # noqa: BLE001
        print(f"  (recount unavailable: {str(exc)[:80]})")

    for line in describe_rollup((j.get("costs") or {}).get("visuals_images")):
        print(line)

    print(f"\n  run_visuals ENTRY count: {_entry_count(job_id)}   (12 on 6691727f; expect 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
