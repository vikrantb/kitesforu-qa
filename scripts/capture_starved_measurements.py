#!/usr/bin/env python3
"""When ONE post-deploy job lands, capture what EVERY traffic-starved item needs.

WHY THIS EXISTS. On 2026-08-20 the backlog held ~10 separate `[~]` items whose
blocker was not a decision and not code — it was the SAME drought. The fleet
produced 0 jobs after the 18:55:09Z deploy, and each lane was watching for its
own witness independently. That arrangement wastes the drought's end: the first
job to land gets observed by whichever lane happens to be awake, the other nine
miss the window, and the next job is hours away.

So this captures ALL of it from ONE job doc, in one pass.

TENET 9 — this is debug/observability tooling and therefore strictly read-only
and out-of-band. It READS a job document that the pipeline already wrote. It
creates no job, makes no provider call, writes nothing to Firestore, and cannot
influence what any user receives. Running it costs $0.

USAGE
    python3 capture_starved_measurements.py <job_id> [--out DIR]
    python3 capture_starved_measurements.py --latest-after 2026-08-20T18:55:09Z

Each probe reports its own POSITIVE CONTROL (what it looked at) so a zero can be
told apart from a probe that never ran — the failure mode that produced five
false zeros in one session on 2026-08-03/04.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore

PROJECT = "kitesforu-dev"
COLLECTION = "podcast_jobs"


def _as_utc(value: Any) -> Optional[datetime]:
    """`created_at` is USUALLY a timestamp and SOMETIMES an ISO string.

    Measured 2026-08-20 on the newest 60 jobs: 58 DatetimeWithNanoseconds + 2
    str. Firestore's total ordering sorts strings AFTER timestamps, so a DESC
    query returns the string rows FIRST — a probe that skips what it cannot
    parse silently loses the top of its own window.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    if getattr(value, "tzinfo", None) is None:
        try:
            return value.replace(tzinfo=timezone.utc)
        except (TypeError, AttributeError):
            return None
    return value


# --------------------------------------------------------------------------
# One probe per starved backlog item. Each returns a dict that ALWAYS carries
# `_control` — what it actually examined — so an empty result is readable.
# --------------------------------------------------------------------------

def probe_character_consistency(job: Dict[str, Any]) -> Dict[str, Any]:
    """Do the four cast visual-identity fields survive to the shot prompt?

    Chain: bible_author.py:82 -> prompt_assembler.build_shot_prompt:249-263.

    PATH VERIFIED, NOT GUESSED. The cast lives at `visual.world_bible.characters`.
    My first version read `story_blueprint.cast`, which does not exist on the
    job doc at all — it returned "0 cast members" and a red verdict for every
    job on earth. A zero from a guessed path measures the guess.

    NO CAST IS NOT A FAILURE. An educational explainer legitimately has zero
    characters; witness b61a52f9 (18 clips, educational) has an empty list by
    design. Only a job that HAS characters can fail this probe, so the verdict
    is three-valued and never collapses "nothing to check" into "broken".
    """
    visual = job.get("visual") or {}
    clips = visual.get("clips") or []
    characters = ((visual.get("world_bible") or {}).get("characters")) or []
    fields = ("appearance", "wardrobe", "ethnicity", "age_range")
    per_member = [
        {"name": m.get("name"), **{f: bool(m.get(f)) for f in fields}}
        for m in characters if isinstance(m, dict)
    ]
    degraded = sum(
        1 for c in clips if isinstance(c, dict) and c.get("consistency_degraded")
    )
    if not per_member:
        verdict = "N/A — this job has no characters (not a defect)"
    elif all(all(m[f] for f in fields) for m in per_member):
        verdict = "ALL FOUR FIELDS PRESENT on every character"
    else:
        verdict = "INCOMPLETE — at least one character is missing a field"
    return {
        "_control": f"{len(clips)} clips, {len(characters)} characters at "
                    f"visual.world_bible.characters",
        "cast_fields_present": per_member,
        "verdict": verdict,
        "clips_flagged_consistency_degraded": degraded,
        "clip_asset_uris": [c.get("asset_uri") for c in clips if isinstance(c, dict)][:20],
    }


def probe_semantic_sync(job: Dict[str, Any]) -> Dict[str, Any]:
    """#2572 — did the join key finally reach the instrument (`checked > 0`)?"""
    ss = (job.get("visual") or {}).get("semantic_sync") or {}
    checked = ss.get("checked")
    return {
        "_control": f"semantic_sync {'PRESENT' if ss else 'ABSENT'} on this job",
        "present": bool(ss),
        "checked": checked,
        "verdict": ("INSTRUMENT LIVE" if isinstance(checked, int) and checked > 0
                    else "still 0/absent — join key did NOT arrive"),
    }


def probe_visual_register(job: Dict[str, Any]) -> Dict[str, Any]:
    """The three-way split: has register / never had a kind / suppressed-with-reason."""
    clips = (job.get("visual") or {}).get("clips") or []
    has_reg = no_kind = suppressed = unexplained = 0
    for c in clips:
        if not isinstance(c, dict):
            continue
        if c.get("visual_register"):
            has_reg += 1
        elif not c.get("kind"):
            no_kind += 1
        elif c.get("register_suppressed"):
            suppressed += 1
        else:
            unexplained += 1
    return {
        "_control": f"{len(clips)} clips examined",
        "has_register": has_reg,
        "never_had_a_kind": no_kind,
        "suppressed_with_reason": suppressed,
        "GENUINELY_UNEXPLAINED": unexplained,
    }


def probe_render_waste(job: Dict[str, Any]) -> Dict[str, Any]:
    """#2585 — assets rendered for beats that are never shown, and zero-width beats.

    FIELD NAMES VERIFIED AGAINST A REAL CLIP, NOT ASSUMED. Clips carry
    `start_ms` + `duration_ms`. My first version read `start_s`/`end_s`, which
    do not exist, so nothing ever landed in `shown` and the probe reported
    "18 of 18 assets never shown" — a perfect 100% that was pure artefact.
    A result that extreme is a probe bug until proven otherwise.
    """
    clips = (job.get("visual") or {}).get("clips") or []
    shown, zero_width, untimed = set(), 0, 0
    all_assets = set()
    for c in clips:
        if not isinstance(c, dict):
            continue
        uri = c.get("asset_uri")
        if uri:
            all_assets.add(uri)
        dur = c.get("duration_ms")
        if not isinstance(dur, (int, float)):
            untimed += 1
            continue
        if dur <= 0:
            zero_width += 1
        elif uri:
            shown.add(uri)
    return {
        "_control": f"{len(clips)} clips examined via start_ms/duration_ms "
                    f"({untimed} carried no usable duration)",
        "zero_width_clips": zero_width,
        "unique_assets": len(all_assets),
        "assets_never_shown": len(all_assets - shown),
        "note": ("assets_never_shown == unique_assets means the probe found no "
                 "timing at all — suspect the field names before the pipeline"),
    }


def probe_visual_style_consumer(job: Dict[str, Any]) -> Dict[str, Any]:
    """workers PR #2612 — once merged, does a user style answer reach the directive?"""
    directive = ((job.get("scenario_tailoring") or {}).get("visual_directive")) or {}
    prefs = job.get("preferences") or {}
    return {
        "_control": f"visual_directive {'PRESENT' if directive else 'ABSENT'}",
        "photographic_level": directive.get("photographic_level"),
        "allow_photoreal": directive.get("allow_photoreal"),
        "reason": directive.get("reason"),
        "user_supplied__visual_style": prefs.get("_visual_style"),
        "note": ("reason containing 'user_style=' proves a user answer won; "
                 "'genre=' means the genre policy decided"),
    }


PROBES = {
    "character_consistency": probe_character_consistency,
    "semantic_sync_join_key": probe_semantic_sync,
    "visual_register_split": probe_visual_register,
    "render_waste": probe_render_waste,
    "visual_style_consumer": probe_visual_style_consumer,
}


def capture(job_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "job_id": job_id,
        "status": job.get("status"),
        "created_at": str(job.get("created_at")),
        "clip_count": len((job.get("visual") or {}).get("clips") or []),
        "probes": {},
    }
    for name, fn in PROBES.items():
        try:
            out["probes"][name] = fn(job)
        except Exception as exc:  # a broken probe must not hide the others
            out["probes"][name] = {"_control": "PROBE RAISED", "error": f"{type(exc).__name__}: {exc}"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?")
    ap.add_argument("--latest-after", help="ISO8601; pick the newest job created after this")
    ap.add_argument("--out", default="/tmp")
    args = ap.parse_args()

    db = firestore.Client(project=PROJECT)

    if args.job_id:
        snap = db.collection(COLLECTION).document(args.job_id).get()
        if not snap.exists:
            print(f"job {args.job_id} not found", file=sys.stderr)
            return 1
        job_id, job = args.job_id, snap.to_dict() or {}
    elif args.latest_after:
        cutoff = _as_utc(args.latest_after)
        docs = list(db.collection(COLLECTION)
                      .order_by("created_at", direction=firestore.Query.DESCENDING)
                      .limit(60).stream())
        print(f"POSITIVE CONTROL: fetched {len(docs)} docs", file=sys.stderr)
        cands = [(d.id, d.to_dict() or {}) for d in docs
                 if (_as_utc((d.to_dict() or {}).get("created_at")) or cutoff) > cutoff]
        if not cands:
            print(f"no job created after {cutoff.isoformat()} — drought continues", file=sys.stderr)
            return 2
        job_id, job = cands[0]
    else:
        ap.error("give a job_id or --latest-after")

    result = capture(job_id, job)
    path = f"{args.out}/starved_capture_{job_id[:8]}.json"
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str))
    print(f"\nwritten: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
