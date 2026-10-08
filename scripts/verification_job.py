#!/usr/bin/env python3
"""The verification job's request, its estimate and its read-back. One module, three commands.

``create_verification_job.sh`` is the one sanctioned way to POST a verification job
(``.claude/rules/03-money.md``). It owns the arguments, the ACK gate, auth, the POST and the poll.
This module owns the three things the script used to do inline, in places that drifted apart:

``plan``           builds the request body, then computes the estimate and the ACK decision FROM IT;
``check-stored``   compares what the api stored with what was requested;
``status-fields``  extracts the few ``/status`` fields the wait loop decides on.

THE ESTIMATE AND THE PAYLOAD COME FROM ONE EXPRESSION (kitesforu-qa #175 rounds 2-3)
-------------------------------------------------------------------------------------
The estimate shown to the spend approver and the body sent to the api used to be built about 100
lines apart, and they shared nothing. Round 2 turned on paid stills in the body, and the printed
estimate stayed byte-identical (round-2 cost D1/D2). Round 3 then priced those stills from a
provider family the catalog had retired (round-3 cost FIX-1, claims D3), and counted them twice on
``--visuals --motion-clips`` (round-3 design NIT-2). Correcting the constants would not have fixed
the shape. :func:`plan` serializes the body ONCE. The estimate and the ACK decision are then
computed by parsing THOSE BYTES, the same string curl sends, so they cannot describe a different
request.

WHERE THE PRICES COME FROM: the producer's code, not a copy of it
-------------------------------------------------------------------
* **A purchased clip** is priced by kitesforu-workers' own ``select_provider``. It ranks over the
  rows ``video_models()`` loads from the tree's ``config/model_catalog.csv``, on the budget
  ``policy_for_job`` builds when ``visual_options.motion_clips > 0``
  (``HeroBudget(max_clip_usd=_MOTION_CLIP_USD_CAP, max_seconds=_MOTION_CLIP_SECONDS)``,
  ``policy.py``). The cells are the four that ``veo_hero`` can ask on that arm:
  * plain, purpose ``_plain_purpose(True)`` (``video_motion``), or anchored, purpose
    ``video_character``;
  * fal bound or not.
  It is SELECTION ONLY: no ``submit``, ``poll`` or ``generate``. The fal key is a dummy, set
  inside this process for the "bound" cells. ``MODEL_CATALOG_SHEET_ID`` is cleared first, so the
  loader reads the CSV the same way ``kitesforu-worker-visuals`` does (that env is absent there).
* **A paid still** is priced from the same loader's rows: every enabled, unretired ``IMAGE`` row
  priced ``per image``. The range runs from the cheapest row x1 to the dearest x2, because the
  renderer allows ONE scene-verify regen per beat. A verify REJECT can add a render on another
  row; this range does not bound that, and the printed line says so.
* **The audio base** is the measured per-tier band from ``COST_CHANGELOG.md`` 2026-09-06. It is
  not a catalog fact, and it is not re-derived here.

A purchased clip lands on the PURCHASED arm only. ``policy_for_job`` replaces the hero budget when
``vo_motion > 0`` and sizes the entitlement allowance only when ``vo_motion == 0``. So a job that
buys clips never also draws entitlement clips ($0.45/4 s: veo-3.1-lite $0.12 or
minimax/h3/reference-to-video $0.30, the arm workers #3273 discusses).

Offline and $0. ``plan`` reads a local tree, and ``check-stored`` and ``status-fields`` read text
handed to them. No command here makes a network call.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------------------------
# The request body
# ---------------------------------------------------------------------------------------------

#: How many paid stills ride along with purchased clips. This is decided ONCE, here, beside the
#: body that sends it.
#:
#: The api charges 1 credit per ``max_images`` UPFRONT (``option_pricing.py`` CREDITS_PER_IMAGE).
#: On a ``completed`` job it refunds the stills charged but never rendered
#: (``visual_refund.maybe_refund_unrendered_visuals``, run by ``GET /status``). It does NOT refund
#: on ``needs_review`` / ``failed_qa`` (#175 round-3 claims D4). So the count should be one the job
#: can RENDER. What can render is workers ``scene_budget.resolve_ceiling``, executed 2026-09-12 on
#: workers bdc3b0d2 with ``vo_present=True, real_images=True, max_images > 0`` (#175 round-3,
#: three lenses reproduced it):
#:   * quality ``low``: 3 for every subscription (``is_cheapest_mode`` clamps to
#:     ``_SCENE_CAP["free"]``);
#:   * quality medium/high on a paid subscription: ``max_images`` itself.
#: A FREE subscription never reaches that function with these options. The api sets
#: ``visual_options_resolved = None`` for a non-paid tier (``podcast_services.py``, the free-tier
#: clamp), which is why the script reads the stored request back after creating the job. So the
#: count is 3 at ``low`` (all it can render) and 4 at any other tier. 4 is the api's own
#: ``VisualOptions`` default and a paying user's untouched shape (api ``models.py``
#: ``max_images: Field(default=4)``; frontend ``DEFAULT_VISUAL_OPTIONS``).
_STILLS_AT_LOW = 3
_STILLS_ABOVE_LOW = 4


def paid_stills_count(tier: str, motion_clips: int, paid_stills: str) -> int:
    if motion_clips <= 0 or paid_stills != "on":
        return 0
    return _STILLS_AT_LOW if tier == "low" else _STILLS_ABOVE_LOW


def build_body(*, topic: str, duration: str, tier: str, style: str, visuals: str, fmt: str,
               content_rating: str, source_writeup: str, language: str, motion_clips: int,
               paid_stills: str) -> Dict[str, Any]:
    """The POST /v1/podcasts body. Key ORDER is the order this script has always sent, so a default
    run serializes to the same bytes as before this module existed."""
    body: Dict[str, Any] = {
        "topic": topic,
        "duration_min": float(duration),
        "style": style,
        "quality_tier": tier,
        "economy_mode": tier == "low",
        "intro_enabled": False,
        "allow_premium": tier in ("high", "ultra"),
        "skip_clarifier": True,
        "language": language,
    }
    # "auto" sends NEITHER key, so the worker's own non-fiction $0 visual default decides.
    # Sending visuals_opt_out=true (the old else-branch) killed visuals outright, so the
    # visual-planning path could never be exercised at T3.
    if visuals != "auto":
        body["wants_visuals"] = visuals == "true"
        body["visuals_opt_out"] = visuals != "true"
    if fmt:
        body["format"] = fmt
    if content_rating:
        body["content_rating"] = content_rating
    if motion_clips > 0:
        # Declared on the api's own CreateJobRequest (``visual_options: Optional[VisualOptions]``),
        # so the strict schemas model does not drop it.
        #
        # `real_images` AND `max_images` ARE SENT EXPLICITLY. A bare ``{"motion_clips": N}`` is
        # normalised by the api to ``{real_images: False, max_images: 0, motion_clips: N}``
        # (``option_pricing.normalize_visual_options`` defaults a missing `real_images` to False
        # and then FORCES `max_images` to 0). The workers policy then turns that into
        # ``user_paid_cap = 0``, and every paid still is demoted to a $0 card. A paid-video
        # verification job whose stills are all cards measures the wrong population: #3116's
        # mechanism turns on picture-vs-figure, and a card IS a figure. ``--paid-stills off``
        # keeps that clips-only shape reachable on purpose. It is the shape the frontend's
        # clips-only builders send (#175 round-2 cost D3).
        stills = paid_stills_count(tier, motion_clips, paid_stills)
        body["visual_options"] = {
            "real_images": stills > 0,
            "max_images": stills,
            "motion_clips": motion_clips,
        }
    if source_writeup:
        # Declared on CreateJobRequest (schemas 2.60.0) so the strict model keeps it; the
        # direct create path stamps it top-level onto the job doc (api #734).
        body["source_writeup_id"] = source_writeup
    # NOTE: short_video is a QUERY PARAM (?short_video=true), NOT a body field. The strict schemas
    # CreateJobRequest drops body extras, so a body short_video is silently ignored (verified live
    # 2026-07-06: body-only rendered a normal episode). The script appends it to the POST URL.
    return body


def _visual_options(body: Dict[str, Any]) -> Dict[str, Any]:
    vo = body.get("visual_options")
    return vo if isinstance(vo, dict) else {}


def ordered_clips(body: Dict[str, Any]) -> int:
    return int(_visual_options(body).get("motion_clips") or 0)


def ordered_stills(body: Dict[str, Any]) -> int:
    vo = _visual_options(body)
    return int(vo.get("max_images") or 0) if vo.get("real_images") else 0


# ---------------------------------------------------------------------------------------------
# The ACK decision: keyed on what the body ORDERS
# ---------------------------------------------------------------------------------------------

def ack_decision(body: Dict[str, Any]) -> Tuple[bool, str]:
    """``(needs_ack, reason)``. Every condition reads the body, so the gate keys on what is ORDERED.

    Paid stills now require the ACK themselves. They used to be named in the reason without
    setting ``needs_ack``, and they stayed behind the gate only because two other sites agreed
    (#175 round-3 design NIT-1). The reason keeps its trailing-space format so the printed line is
    unchanged for every existing condition."""
    reasons: List[str] = []
    tier = body.get("quality_tier")
    if tier != "low":
        reasons.append(f"tier={tier}")
    if body.get("wants_visuals") is True:
        reasons.append("visuals=on")
    duration = float(body.get("duration_min") or 0)
    if duration > 0.5:
        reasons.append(f"duration={duration}min")
    clips = ordered_clips(body)
    if clips > 0:
        reasons.append(f"motion_clips={clips}")
    stills = ordered_stills(body)
    if stills > 0:
        reasons.append(f"paid_stills={stills}")
    return bool(reasons), "".join(f"{r} " for r in reasons)


# ---------------------------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------------------------

class PricingUnavailable(RuntimeError):
    """The body buys something this module cannot price from the producer. Refuse; never guess."""


#: Measured audio-pipeline cost per quality tier: the band this script has always printed.
#: Derivation and provenance: ``COST_CHANGELOG.md`` 2026-09-06 (the high-tier story band) and the
#: test-cost ladder (T3 ~ $0.025). These are per-JOB measurements, not catalog prices.
_BASE_BANDS: Dict[str, Tuple[float, float, str]] = {
    "low": (0.025, 0.025, "~$0.025"),
    "medium": (0.15, 0.15, "~$0.15"),
    "high": (1.0, 2.25, "~$1.0-1.3 (non-story topic) / ~$1.55-2.25 (story topic)"),
}
#: The legacy tier-driven visuals band, for ``wants_visuals: true`` with NO ``visual_options``
#: (the ``--visuals`` path, which buys the tier's own stills and, on a paid premium tier, the
#: entitlement hero clip). Unchanged from the script's earlier print. It never applies alongside
#: ``visual_options``: those stills and clips are priced below, and counting both double-booked
#: the stills (#175 round-3 design NIT-2).
_LEGACY_VISUALS_BAND = (0.10, 0.50, "visuals (~$0.10-0.50; a story band already counts veo — don't double-book)")


@dataclass(frozen=True)
class ClipCell:
    fal_bound: bool
    anchored: bool
    model_id: Optional[str]   # None: nothing fits the budget, so no clip is made (parallax)
    seconds: int
    usd: float


@dataclass(frozen=True)
class ClipQuote:
    cells: Tuple[ClipCell, ...]
    cap_usd: float
    clip_seconds: int

    @property
    def low(self) -> float:
        return min(c.usd for c in self.cells)

    @property
    def high(self) -> float:
        return max(c.usd for c in self.cells)

    def detail(self) -> List[str]:
        def cell(c: ClipCell) -> str:
            shape = "anchored" if c.anchored else "plain"
            if c.model_id is None:
                return f"{shape} none fits (no clip)"
            return f"{shape} {c.model_id} {c.seconds}s ${c.usd:.3f}"
        lines = [f"clips: purchased arm (cap ${self.cap_usd:.2f}, {self.clip_seconds}s), "
                 "kitesforu-workers select_provider, one row per cell:"]
        for bound in (True, False):
            row = [cell(c) for c in self.cells if c.fal_bound is bound]
            lines.append(f"  {'fal bound  ' if bound else 'no fal key '} " + " | ".join(row))
        return lines


@dataclass(frozen=True)
class StillQuote:
    rows: Tuple[Tuple[str, float], ...]   # (model_id, usd per image), enabled + unretired
    regens: int = 1                        # scene-verify regens allowed per beat (renderer.py)

    @property
    def cheapest(self) -> Tuple[str, float]:
        return min(self.rows, key=lambda r: (r[1], r[0]))

    @property
    def dearest(self) -> Tuple[str, float]:
        return max(self.rows, key=lambda r: (r[1], r[0]))

    @property
    def low(self) -> float:
        return self.cheapest[1]

    @property
    def high(self) -> float:
        return self.dearest[1] * (1 + self.regens)

    def detail(self) -> List[str]:
        lo_id, lo = self.cheapest
        hi_id, hi = self.dearest
        return [f"stills: {len(self.rows)} enabled IMAGE rows, {lo_id} ${lo:.3f} x1 .. {hi_id} ${hi:.3f} "
                f"x{1 + self.regens} (one scene-verify regen); a verify REJECT can add a render on another row"]


@dataclass
class Estimate:
    text: str
    low: Optional[float]
    high: Optional[float]
    detail: List[str] = field(default_factory=list)


def _usd(x: float) -> str:
    return f"${x:.3f}" if x < 0.1 else f"${x:.2f}"


def _span(lo: float, hi: float) -> str:
    return _usd(lo) if abs(hi - lo) < 1e-9 else f"{_usd(lo)}-{_usd(hi)[1:]}"


def estimate(body: Dict[str, Any], *, clips: Optional[ClipQuote] = None,
             stills: Optional[StillQuote] = None) -> Estimate:
    """The estimate for exactly this body. Raises :class:`PricingUnavailable` when the body buys
    clips or stills and the matching quote is absent. It refuses rather than print a number that
    leaves out what is bought."""
    tier = str(body.get("quality_tier"))
    base = _BASE_BANDS.get(tier)
    terms: List[Tuple[Optional[float], Optional[float], str]] = [
        (base[0], base[1], base[2]) if base else (None, None, "unknown")
    ]
    detail: List[str] = []
    if body.get("wants_visuals") is True and not _visual_options(body):
        terms.append((_LEGACY_VISUALS_BAND[0], _LEGACY_VISUALS_BAND[1], _LEGACY_VISUALS_BAND[2]))
    n_clips = ordered_clips(body)
    if n_clips > 0:
        if clips is None:
            raise PricingUnavailable(f"the body buys {n_clips} clip(s) and no clip quote was given")
        lo, hi = n_clips * clips.low, n_clips * clips.high
        terms.append((lo, hi, f"{n_clips} paid clip(s) {_span(lo, hi)}"))
        detail += clips.detail()
    n_stills = ordered_stills(body)
    if n_stills > 0:
        if stills is None:
            raise PricingUnavailable(f"the body buys up to {n_stills} still(s) and no still quote was given")
        lo, hi = n_stills * stills.low, n_stills * stills.high
        terms.append((lo, hi, f"up to {n_stills} paid still(s) {_span(lo, hi)}"))
        detail += stills.detail()
    if len(terms) == 1:
        return Estimate(text=terms[0][2], low=terms[0][0], high=terms[0][1], detail=detail)
    parts = [f"audio {terms[0][2]}"] + [t[2] for t in terms[1:]]
    lows = [t[0] for t in terms if t[0] is not None]
    highs = [t[1] for t in terms if t[1] is not None]
    if len(lows) != len(terms) or len(highs) != len(terms):
        return Estimate(text="unknown = " + " + ".join(parts), low=None, high=None, detail=detail)
    total_lo, total_hi = sum(lows), sum(highs)
    return Estimate(text=f"~{_span(total_lo, total_hi)} = " + " + ".join(parts),
                    low=total_lo, high=total_hi, detail=detail)


# ---------------------------------------------------------------------------------------------
# Quotes from the producer: a kitesforu-workers tree
# ---------------------------------------------------------------------------------------------

@contextlib.contextmanager
def _fal_key(bound: bool):
    """Set a DUMMY fal key for the "bound" cells, inside this process only. fal availability is
    ``bool(FAL_KEY) and spec_for(model)``; nothing here submits, so the value is never sent."""
    old = os.environ.pop("FAL_KEY", None)
    if bound:
        os.environ["FAL_KEY"] = "selection-only-never-sent"
    try:
        yield
    finally:
        os.environ.pop("FAL_KEY", None)
        if old is not None:
            os.environ["FAL_KEY"] = old


def _workers(workers_src: str):
    """Import the producer's selection code from ``workers_src``. Fails closed with the reason."""
    if not os.path.isdir(os.path.join(workers_src, "workers")):
        raise PricingUnavailable(f"no `workers` package under {workers_src}")
    # The live visuals worker routes from the CSV (MODEL_CATALOG_SHEET_ID is absent on
    # kitesforu-worker-visuals). With it set here the loader would reach Google Sheets, a network
    # call, and could read a different catalog than the one that serves.
    os.environ.pop("MODEL_CATALOG_SHEET_ID", None)
    if workers_src not in sys.path:
        sys.path.insert(0, workers_src)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from workers.common.model_eol import is_past_eol
            from workers.common.pricing import iter_model_rows
            from workers.stages.visuals import policy
            from workers.stages.visuals.veo_hero import _plain_purpose
            from workers.stages.visuals.video import factory
            from workers.stages.visuals.video.catalog import _truthy, video_models
    except Exception as exc:  # noqa: BLE001: any import failure means the producer moved
        raise PricingUnavailable(f"the workers selector did not import from {workers_src}: "
                                 f"{type(exc).__name__}: {exc}") from exc
    return dict(is_past_eol=is_past_eol, iter_model_rows=iter_model_rows, policy=policy,
                plain_purpose=_plain_purpose, factory=factory, truthy=_truthy,
                video_models=video_models)


def quote_purchased_clips(w: Dict[str, Any], *, aspect: str) -> ClipQuote:
    factory, policy = w["factory"], w["policy"]
    budget = factory.HeroBudget(max_clip_usd=policy._MOTION_CLIP_USD_CAP,
                                max_seconds=policy._MOTION_CLIP_SECONDS)
    cells: List[ClipCell] = []
    for fal_bound in (True, False):
        with _fal_key(fal_bound):
            # The registry is built from the rows `video_models()` loads, exactly as
            # `factory.build_registry` does. `build_registry` itself is not called: on an empty
            # catalog it FALLS SOFT to one hardcoded Veo provider, which would price a catalog that
            # failed to load as if it had loaded.
            models = w["video_models"]()
            if not models:
                raise PricingUnavailable("the workers catalog yielded no usable video row")
            registry = [
                factory._TRANSPORTS[m.provider](m) if m.provider in factory._TRANSPORTS
                else factory._NoTransportProvider(m)
                for m in models
            ]
            for anchored in (False, True):
                purpose = "video_character" if anchored else w["plain_purpose"](True)
                p = factory.select_provider(budget, registry, needs_reference_images=anchored,
                                            needs_first_frame=True, purpose=purpose,
                                            aspect_ratio=aspect)
                if p is None:
                    cells.append(ClipCell(fal_bound, anchored, None, 0, 0.0))
                    continue
                with_refs = anchored and factory._provider_supports_references(p)
                secs = factory.clip_seconds(p, budget.max_seconds, with_references=with_refs)
                cells.append(ClipCell(fal_bound, anchored, p.model_id(), secs,
                                      float(factory.clip_cost(p, secs))))
    return ClipQuote(cells=tuple(cells), cap_usd=float(budget.max_clip_usd),
                     clip_seconds=int(budget.max_seconds))


def quote_paid_stills(w: Dict[str, Any]) -> StillQuote:
    rows: List[Tuple[str, float]] = []
    for mid, row in (w["iter_model_rows"]() or {}).items():
        if "IMAGE" not in str(row.get("task_types", "")).upper():
            continue
        if not w["truthy"](row.get("enabled", True)) or w["is_past_eol"](row):
            continue
        if str(row.get("unit_description") or "").strip().lower() != "per image":
            continue
        try:
            usd = float(row.get("cost_per_unit") or 0)
        except (TypeError, ValueError):
            continue
        if usd > 0:
            rows.append((str(row.get("model_id") or mid), usd))
    if not rows:
        raise PricingUnavailable("the workers catalog has no enabled IMAGE row priced per image")
    return StillQuote(rows=tuple(rows))


# ---------------------------------------------------------------------------------------------
# The read-back: what the api stored against what was requested
# ---------------------------------------------------------------------------------------------

def check_stored(requested: Dict[str, Any], snapshot: Dict[str, Any]) -> List[str]:
    """Every field the api stored LOWER than requested. ``[]`` means it stored what was asked.

    The api clamps paid options for a free subscription without an error.
    ``podcast_services.create_job`` sets ``visual_options_resolved = None`` when
    ``not tier_is_paid(user.tier)``, and ``clamp_quality_tier_for_free_tier`` lowers every quality
    rung above ``medium`` to ``medium``. The job is then created and charged for less than the
    script printed, and nothing in the 200 response says so. ``GET /status`` echoes the stored
    request as ``inputs`` (``original_request.retry_inputs``, top level first, then ``inputs``),
    and ``quality_tier`` top-level."""
    raw_inputs = snapshot.get("inputs")
    inputs: Dict[str, Any] = raw_inputs if isinstance(raw_inputs, dict) else {}
    raw_vo = inputs.get("visual_options")
    stored_vo: Dict[str, Any] = raw_vo if isinstance(raw_vo, dict) else {}
    req_vo = _visual_options(requested)
    short: List[str] = []
    for key in ("motion_clips", "max_images"):
        want = int(req_vo.get(key) or 0)
        got = int(stored_vo.get(key) or 0)
        if got < want:
            short.append(f"visual_options.{key}: requested {want}, stored {got}")
    if req_vo.get("real_images") and not stored_vo.get("real_images"):
        short.append("visual_options.real_images: requested true, stored "
                     f"{json.dumps(stored_vo.get('real_images'))}")
    want_tier = requested.get("quality_tier")
    got_tier = snapshot.get("quality_tier") or inputs.get("quality_tier")
    if want_tier and got_tier != want_tier:
        short.append(f"quality_tier: requested {want_tier}, stored {json.dumps(got_tier)}")
    return short


# ---------------------------------------------------------------------------------------------
# /status fields for the wait loop
# ---------------------------------------------------------------------------------------------

def clip_coverage_warning(body: Dict[str, Any], clip_seconds: int) -> List[str]:
    """Warn when the purchased clips would cover more than HALF the episode.

    The old predicate fired only when the clips were LONGER than the episode, so
    ``--motion-clips 1`` at the 10 s default (6 s of video in a 10 s episode) said nothing. The
    advice it would have printed ("a paid-video T4 wants at least ~2.0 min") never reached the
    cheapest wrong invocation (#175 round-2 design D7, latency NIT). The clip length is the
    producer's ``_MOTION_CLIP_SECONDS``, not a copy of it."""
    clips = ordered_clips(body)
    episode_s = float(body.get("duration_min") or 0) * 60
    video_s = clips * clip_seconds
    if clips <= 0 or video_s * 2 <= episode_s:
        return []
    return [
        f"⚠️  {clips} clip(s) is ~{video_s}s of video for a {episode_s:.0f}s episode: more than half of it.",
        "    A short episode has too few beats for the hero ranker to choose among, and a 10 s run",
        "    authors no blueprint (job 9725a85c, 2026-08-18). A paid-video T4 wants ~1-2 min: raise --duration.",
    ]


def status_fields(snapshot: Dict[str, Any]) -> List[str]:
    """``[status, wants_visuals, video, visual_status, hero_clips]`` as strings, for the shell.
    Joined with ``|`` by the CLI. A tab is IFS whitespace, so an empty field would collapse and
    shift every field after it.

    ``video`` is "yes" once a composed MP4 exists. Assembly is ONCE-ONLY and waits for any Veo op
    still rendering (workers ``visuals/worker.py``: ``veo_pending``, "assembly waits for it (so the
    hero clip is IN the video)"; ``motion_pending`` skips the same way). So a video URL means the
    delivered video, hero clips included, is final. ``clips_settled_at`` does not mean that: it is
    re-stamped by every terminal persist, including the stills pass that runs before the Veo ops
    land. ``hero_clips`` counts clips whose ``modality`` is ``video_hero``, the stamp
    ``veo_hero`` puts on an applied clip."""
    raw_visual = snapshot.get("visual")
    visual: Dict[str, Any] = raw_visual if isinstance(raw_visual, dict) else {}
    raw_clips = visual.get("clips")
    clips: List[Any] = raw_clips if isinstance(raw_clips, list) else []
    hero = sum(1 for c in clips if isinstance(c, dict) and c.get("modality") == "video_hero")
    video = bool(snapshot.get("video_url") or visual.get("video_url"))
    return [
        str(snapshot.get("status") or ""),
        "yes" if snapshot.get("wants_visuals") is True else "no",
        "yes" if video else "no",
        str(visual.get("status") or ""),
        str(hero),
    ]


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def _plan(args: argparse.Namespace) -> Dict[str, Any]:
    body = build_body(topic=args.topic, duration=args.duration, tier=args.tier, style=args.style,
                      visuals=args.visuals, fmt=args.format, content_rating=args.content_rating,
                      source_writeup=args.source_writeup, language=args.language,
                      motion_clips=args.motion_clips, paid_stills=args.paid_stills)
    payload = json.dumps(body)
    # FROM HERE ON, ONLY THE BYTES. The estimate and the ACK decision read the serialized payload,
    # never `body`, so neither can describe a request that differs from what curl sends.
    sent = json.loads(payload)
    needs_ack, reason = ack_decision(sent)
    clip_quote = still_quote = None
    if ordered_clips(sent) > 0 or ordered_stills(sent) > 0:
        if not args.workers_src:
            raise PricingUnavailable("this body buys clips or stills, and no --workers-src was given to price them")
        noise = io.StringIO()
        # The producer's loaders log to stdout/stderr; stdout must carry only this JSON. What they
        # said is kept, and shown when pricing fails, because that is when it explains something.
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                w = _workers(args.workers_src)
                if ordered_clips(sent) > 0:
                    clip_quote = quote_purchased_clips(w, aspect="9:16" if args.short == "true" else "16:9")
                if ordered_stills(sent) > 0:
                    still_quote = quote_paid_stills(w)
        except Exception as exc:
            tail = "\n".join(noise.getvalue().strip().splitlines()[-15:])
            if tail:
                print(f"(workers output before the failure)\n{tail}", file=sys.stderr)
            if isinstance(exc, PricingUnavailable):
                raise
            raise PricingUnavailable(f"the workers selector raised {type(exc).__name__}: {exc}") from exc
    est = estimate(sent, clips=clip_quote, stills=still_quote)
    detail = list(est.detail)
    if detail and args.catalog_label:
        detail.insert(0, f"priced from {args.catalog_label}")
    warn = clip_coverage_warning(sent, clip_quote.clip_seconds) if clip_quote else []
    return {"payload": payload, "est": est.text, "est_low": est.low, "est_high": est.high,
            "est_detail": detail, "needs_ack": needs_ack, "ack_reason": reason, "warnings": warn}


def _read_snapshot(arg: str) -> Any:
    """``-`` reads stdin. A /status body with a full visual compartment can be large, and argv
    has a size limit."""
    text = sys.stdin.read() if arg == "-" else arg
    try:
        return json.loads(text)
    except ValueError:
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="body + estimate + ACK decision, as one JSON object")
    p.add_argument("--topic", required=True)
    p.add_argument("--duration", required=True)
    p.add_argument("--tier", required=True)
    p.add_argument("--style", required=True)
    p.add_argument("--visuals", required=True, choices=("true", "false", "auto"))
    p.add_argument("--format", default="")
    p.add_argument("--content-rating", default="")
    p.add_argument("--source-writeup", default="")
    p.add_argument("--language", required=True)
    p.add_argument("--motion-clips", type=int, default=0, choices=(0, 1, 2, 3))
    p.add_argument("--paid-stills", default="on", choices=("on", "off"))
    p.add_argument("--short", default="false", choices=("true", "false"))
    p.add_argument("--workers-src", default="")
    p.add_argument("--catalog-label", default="")

    c = sub.add_parser("check-stored", help="fields the api stored lower than requested, one per line")
    c.add_argument("--requested", required=True, help="the exact JSON body that was POSTed")
    c.add_argument("--snapshot", required=True, help="the GET /status JSON, or - for stdin")

    s = sub.add_parser("status-fields", help="status|wants_visuals|video|visual status|hero clips")
    s.add_argument("--snapshot", required=True, help="the GET /status JSON, or - for stdin")

    args = ap.parse_args(argv)
    if args.cmd == "plan":
        try:
            out = _plan(args)
        except (PricingUnavailable, ValueError) as exc:
            print(f"cannot plan this request: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(out))
        return 0
    if args.cmd == "check-stored":
        snapshot = _read_snapshot(args.snapshot)
        # A body with no status is not a read: an error page, an auth failure, an empty
        # transport. It must not pass as "stored nothing less", so it is exit 2 and not [].
        if not isinstance(snapshot, dict) or not snapshot.get("status"):
            print("the /status body is unreadable or carries no status", file=sys.stderr)
            return 2
        for line in check_stored(json.loads(args.requested), snapshot):
            print(line)
        return 0
    snapshot = _read_snapshot(args.snapshot)
    print("|".join(status_fields(snapshot if isinstance(snapshot, dict) else {})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
