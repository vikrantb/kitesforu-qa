#!/usr/bin/env python3
"""The verification job's request, its estimate, its read-back and its wait. One module, four commands.

``create_verification_job.sh`` is the one sanctioned way to POST a verification job
(``.claude/rules/03-money.md``). It owns the arguments, the ACK gate, auth, the POST and the poll.
This module owns the things the script used to do inline, in places that drifted apart:

``plan``           builds the request body, then computes the estimate and the ACK decision FROM IT;
``check-stored``   compares what the api stored with what was requested;
``status-fields``  reads one ``/status`` body through the package's ONE readiness rule
                   (:mod:`kitesforu_qa.visual_readiness`) for the wait loop;
``settle-window``  how long a clip array must hold still before it is counted (``settled_clips``).

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

WHERE THE PRICES COME FROM: the producer's functions, called, never copied (#175 round 5, design D2)
-----------------------------------------------------------------------------------------------------
Round 4 rebuilt ``veo_hero``'s purchase question here through seven private workers names. A copy fails
OPEN on drift: every import still resolves, so the quote stays plausible and wrong. Every number now comes
from a PUBLIC workers function or constant that the pass itself uses (kitesforu-workers PR "a purchased hero
clip is quoted by the code that selects it", branch ``feat/a-purchase-is-quoted-by-the-code-that-selects-it``;
this module refuses to price a purchase from a tree without it):

* **A purchased clip**: ``hero_clip_choice.quote_hero_clip``, the function ``veo_hero`` selects and prices
  with, on the budget ``policy.policy_for_job`` builds from THIS body (``visual_options.motion_clips > 0``
  -> the purchased arm), over the rows ``video.catalog.video_models()`` loads. Four cells: plain or
  anchored, with the fal key bound or not. SELECTION ONLY: no ``submit``, ``poll`` or ``generate``. The fal
  key is a dummy, set inside this process for the "bound" cells. ``MODEL_CATALOG_SHEET_ID`` is cleared first,
  so the loader reads the CSV the way ``kitesforu-worker-visuals`` does (that env is absent there).

  THE SERVING CELL IS THE EXPECTED PRICE (#175 round-5 cost F3). The serving visuals worker carries
  ``FAL_KEY`` (``gcloud run services describe kitesforu-worker-visuals``, read 2026-10-08 by the round-5 cost
  and claims lenses), so a clip is the fal-bound cell, and every non-zero ``costs.visuals_veo`` they read was
  exactly that price per clip. The no-key cells are the FAILOVER range, reachable when fal is unavailable,
  and they set the high end. An ANCHORED cell needs a character plate, and a plate needs paid generation
  (workers ``scene_budget`` passes ``allow_paid=real_images``), so with ``--paid-stills off`` only the plain
  cells are reachable.
* **One plan's paid pictures** (#175 round-5 cost F1, claims D1). Workers book paid images at five dispatch
  sites (``image_cost_ledger``). This bounds the three a non-short purchase reaches, per PLAN:
  * scene stills, ``max_images`` of them: cheapest enabled, unretired ``per image`` row x1 up to the dearest
    x2 (one scene-verify regen per beat). A verify REJECT can add a render on another row; not bounded;
  * relimage bases: ``scene_budget.relimage_cap(max_images, allow_paid)`` of them, at
    ``image_cost_ledger.price_of(RELIMAGE)``, the price the ledger books them at;
  * character reference plates: up to ``character_anchors.ANCHOR_PLATE_BUDGET`` new plates per plan, drawn on
    a reference-capable row (cheapest first), priced at the dearest such row.
  The low end of relimage and plates is $0: both depend on the content. A RE-PLANNED pass can buy again (the
  mux's script-moved refusal, ``mux_gate.REPLAN_CAP``; a character redraw, at most one each), and a born-short
  (``--short``) also reaches ``short_photoreal`` and ``concrete_referent_images``. Those are named in the
  printed line, never silently left out.
* **The visuals authors** (#175 round-5 cost F2): the LLM stages a visuals pass runs (``diagram_author``,
  ``figure_author``, ``geometry_author``, ``visuals_art_director``: workers ``author_cost_rollup``). A
  MEASURED band, not a bound: :data:`_AUTHORS_BAND`. It applies whenever the body lets visuals render
  without the legacy band (``visual_options`` present, or ``--visuals-auto``); the legacy ``--visuals`` band
  already covers it.
* **The audio base** is the measured per-tier band (``COST_CHANGELOG.md`` 2026-09-06 and the T3 measurement).
  Not a catalog fact, and not re-derived here. Its duration basis is recorded where it is known: the low
  tier's $0.025 is a 10 s job, so a longer low run scales its high end linearly (#175 round-5 cost NIT-1).

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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# The package's readiness rule (stdlib only, so any python3 can import it). This tree's `src`, never an
# installed copy: the shared venv carries an editable install of the MAIN checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kitesforu_qa.visual_readiness import (  # noqa: E402
    DEFAULT_STABLE_SECONDS,
    VisualReadiness,
)

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


#: The default duration, 10 s, which the T3 measurement was taken at.
_T3_DURATION_MIN = 0.167

#: Measured audio-pipeline cost per quality tier: ``(low, high, text, basis_min)``. ``basis_min`` is the duration
#: the band was measured at, when that is known. Provenance: ``COST_CHANGELOG.md`` 2026-09-06 (the high-tier
#: story band) and the test-cost ladder (T3 ~ $0.025, a 10 s low job). These are per-JOB measurements, not
#: catalog prices. The medium and high bands record no duration, so they are printed as measured and NOT
#: scaled; a run whose --duration differs says so (#175 round-5 cost NIT-1).
_BASE_BANDS: Dict[str, Tuple[float, float, str, Optional[float]]] = {
    "low": (0.025, 0.025, "~$0.025", _T3_DURATION_MIN),
    "medium": (0.15, 0.15, "~$0.15", None),
    "high": (1.0, 2.25, "~$1.0-1.3 (non-story topic) / ~$1.55-2.25 (story topic)", None),
}
#: The legacy tier-driven visuals band, for ``wants_visuals: true`` with NO ``visual_options``
#: (the ``--visuals`` path, which buys the tier's own stills and, on a paid premium tier, the
#: entitlement hero clip). Unchanged from the script's earlier print. It never applies alongside
#: ``visual_options``: those stills and clips are priced below, and counting both double-booked
#: the stills (#175 round-3 design NIT-2).
_LEGACY_VISUALS_BAND = (0.10, 0.50, "visuals (~$0.10-0.50; a story band already counts veo — don't double-book)")

#: The visuals pass's own LLM author stages, summed per job (workers ``author_cost_rollup``: diagram_author,
#: figure_author, geometry_author, visuals_art_director). A MEASUREMENT, not a bound. Read-only census
#: 2026-10-10, newest 600 ``podcast_jobs`` by ``created_at`` (ids frozen in the round-5 evidence
#: ``census/population.txt``), the 471 jobs with a visual compartment and at least one author stage:
#: median $0.064, p95 $0.126, max $0.152. Command: ``census/r5_census.py`` section 4. The low end is $0 because
#: a run can author nothing (a 10 s ``--visuals-auto`` run authors no blueprint, job 9725a85c).
_AUTHORS_BAND = (0.0, 0.152, "visuals authors ~$0-0.15 (measured: max $0.152, 471 jobs, 2026-10-10)")


def _usd(x: float) -> str:
    if abs(x) < 1e-12:
        return "$0"
    return f"${x:.3f}" if x < 0.1 else f"${x:.2f}"


def _span(lo: float, hi: float) -> str:
    return _usd(lo) if abs(hi - lo) < 1e-9 else f"{_usd(lo)}-{_usd(hi)[1:]}"


@dataclass(frozen=True)
class ClipCell:
    fal_bound: bool
    anchored: bool
    model_id: Optional[str]   # None: nothing fits the budget, so no clip is made (parallax)
    seconds: int
    usd: float
    provider: str = ""        # the catalog row's provider ("fal", "google"), so a reader can tell the cells apart


@dataclass(frozen=True)
class ClipQuote:
    cells: Tuple[ClipCell, ...]
    cap_usd: float
    clip_seconds: int

    def reachable(self, *, anchors_possible: bool) -> Tuple[ClipCell, ...]:
        """The cells this body can reach. An anchored cell needs a plate, and a plate needs paid generation."""
        return tuple(c for c in self.cells if anchors_possible or not c.anchored)

    def serving_low(self, *, anchors_possible: bool) -> float:
        """The serving worker's price: the fal-bound cells (the serving visuals worker carries FAL_KEY)."""
        bound = [c.usd for c in self.reachable(anchors_possible=anchors_possible) if c.fal_bound]
        if not bound:
            raise PricingUnavailable("the quote has no fal-bound cell, so the serving price is unknown")
        return min(bound)

    def high(self, *, anchors_possible: bool) -> float:
        """The failover ceiling: the dearest reachable cell, fal bound or not."""
        return max(c.usd for c in self.reachable(anchors_possible=anchors_possible))

    def detail(self, *, anchors_possible: bool) -> List[str]:
        def cell(c: ClipCell) -> str:
            shape = "anchored" if c.anchored else "plain"
            if c.model_id is None:
                return f"{shape} none fits (no clip)"
            off = "" if (anchors_possible or not c.anchored) else " (unreachable: no paid stills, so no plate)"
            return f"{shape} {c.model_id} {c.seconds}s ${c.usd:.3f}{off}"
        lines = [f"clips: purchased arm (cap ${self.cap_usd:.2f}, {self.clip_seconds}s), "
                 "kitesforu-workers hero_clip_choice.quote_hero_clip (what veo_hero selects and prices), one row per cell:"]
        for bound in (True, False):
            row = [cell(c) for c in self.cells if c.fal_bound is bound]
            label = "fal bound (SERVING)" if bound else "no fal key (failover)"
            lines.append(f"  {label:<22} " + " | ".join(row))
        return lines


@dataclass(frozen=True)
class ImageQuote:
    """One plan's paid pictures, priced from the producer: stills, relimage bases, character plates."""
    rows: Tuple[Tuple[str, float, bool], ...]   # (model_id, usd per image, supports reference images)
    relimage_usd: float                          # image_cost_ledger.price_of(RELIMAGE)
    relimage_cap: Callable[[int, bool], int]     # scene_budget.relimage_cap
    plate_budget: int                            # character_anchors.ANCHOR_PLATE_BUDGET
    regens: int = 1                              # scene-verify regens allowed per beat (renderer.py)

    @property
    def cheapest(self) -> Tuple[str, float]:
        return min(((m, u) for m, u, _ in self.rows), key=lambda r: (r[1], r[0]))

    @property
    def dearest(self) -> Tuple[str, float]:
        return max(((m, u) for m, u, _ in self.rows), key=lambda r: (r[1], r[0]))

    @property
    def dearest_plate_row(self) -> Tuple[str, float]:
        """A plate is drawn on a reference-capable row (``character_anchors._anchor_provider``), and on any row
        when none is reference-capable. The dearest such row bounds one plate."""
        refs = [(m, u) for m, u, r in self.rows if r]
        return max(refs or [(m, u) for m, u, _ in self.rows], key=lambda r: (r[1], r[0]))

    @property
    def low(self) -> float:   # one still, kept for the still-row tests
        return self.cheapest[1]

    @property
    def high(self) -> float:  # one still at the dearest row with its regen
        return self.dearest[1] * (1 + self.regens)

    def terms(self, n_stills: int) -> List[Tuple[float, float, str]]:
        lo_still, hi_still = n_stills * self.low, n_stills * self.high
        n_rel = int(self.relimage_cap(n_stills, n_stills > 0))
        rel_hi = n_rel * self.relimage_usd
        plate_hi = self.plate_budget * self.dearest_plate_row[1] if n_stills > 0 else 0.0
        out = [(lo_still, hi_still, f"up to {n_stills} paid still(s) {_span(lo_still, hi_still)}")]
        if n_rel:
            out.append((0.0, rel_hi, f"up to {n_rel} relimage base(s) {_span(0.0, rel_hi)}"))
        if plate_hi:
            out.append((0.0, plate_hi, f"up to {self.plate_budget} character plate(s) {_span(0.0, plate_hi)}"))
        return out

    def detail(self, n_stills: int) -> List[str]:
        lo_id, lo = self.cheapest
        hi_id, hi = self.dearest
        p_id, p = self.dearest_plate_row
        n_rel = int(self.relimage_cap(n_stills, n_stills > 0))
        return [
            f"stills: {len(self.rows)} enabled IMAGE rows, {lo_id} ${lo:.3f} x1 .. {hi_id} ${hi:.3f} "
            f"x{1 + self.regens} (one scene-verify regen); a verify REJECT can add a render on another row",
            f"relimage: scene_budget.relimage_cap({n_stills}, allow_paid) = {n_rel} at ${self.relimage_usd:.3f} "
            "(image_cost_ledger.price_of(RELIMAGE)); $0 when no beat takes one",
            f"plates: up to {self.plate_budget} new per plan (character_anchors.ANCHOR_PLATE_BUDGET) at most "
            f"{p_id} ${p:.3f}; $0 with no named character",
            "ONE PLAN: a re-planned pass (mux_gate.REPLAN_CAP) can buy stills, relimage and plates again, and a "
            "changed character can be redrawn once; this estimate does not add those",
        ]


@dataclass
class Estimate:
    text: str
    low: Optional[float]
    high: Optional[float]
    detail: List[str] = field(default_factory=list)


def _audio_term(tier: str, duration_min: float) -> Tuple[Optional[float], Optional[float], str]:
    band = _BASE_BANDS.get(tier)
    if band is None:
        return None, None, "unknown"
    lo, hi, text, basis = band
    if basis is not None:
        if duration_min <= basis + 1e-9:
            return lo, hi, text
        scale = duration_min / basis
        hi = hi * scale
        return lo, hi, f"~{_span(lo, hi)} ({text[1:]} measured at 10 s; x{scale:.1f} for {duration_min:g} min is the high end)"
    if abs(duration_min - _T3_DURATION_MIN) < 1e-9:
        return lo, hi, text
    return lo, hi, f"{text} (per episode; no duration basis recorded, so NOT scaled to {duration_min:g} min)"


def _visuals_may_render(body: Dict[str, Any]) -> bool:
    """The body lets the visuals pass run: it bought something, or it left the $0 default on (``--visuals-auto``
    sends neither key)."""
    return bool(_visual_options(body)) or ("wants_visuals" not in body and "visuals_opt_out" not in body)


def estimate(body: Dict[str, Any], *, clips: Optional[ClipQuote] = None,
             images: Optional[ImageQuote] = None, short: bool = False) -> Estimate:
    """The estimate for exactly this body. Raises :class:`PricingUnavailable` when the body buys
    clips or stills and the matching quote is absent. It refuses rather than print a number that
    leaves out what is bought."""
    tier = str(body.get("quality_tier"))
    duration = float(body.get("duration_min") or 0)
    terms: List[Tuple[Optional[float], Optional[float], str]] = [_audio_term(tier, duration)]
    detail: List[str] = []
    if body.get("wants_visuals") is True and not _visual_options(body):
        terms.append((_LEGACY_VISUALS_BAND[0], _LEGACY_VISUALS_BAND[1], _LEGACY_VISUALS_BAND[2]))
    elif _visuals_may_render(body):
        terms.append(_AUTHORS_BAND)
    n_stills = ordered_stills(body)
    n_clips = ordered_clips(body)
    if n_clips > 0:
        if clips is None:
            raise PricingUnavailable(f"the body buys {n_clips} clip(s) and no clip quote was given")
        anchors = n_stills > 0
        lo, hi = n_clips * clips.serving_low(anchors_possible=anchors), n_clips * clips.high(anchors_possible=anchors)
        terms.append((lo, hi, f"{n_clips} paid clip(s) {_span(lo, hi)}"))
        detail += clips.detail(anchors_possible=anchors)
    if n_stills > 0:
        if images is None:
            raise PricingUnavailable(f"the body buys up to {n_stills} still(s) and no still quote was given")
        terms += images.terms(n_stills)
        detail += images.detail(n_stills)
        if short:
            terms.append((0.0, 0.0, "+ born-short photoreal/referent stills NOT BOUNDED here"))
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
# Quotes from the producer: a kitesforu-workers tree, through its PUBLIC functions
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


#: The workers module that makes a quote ask what the pass asks. A tree without it predates the seam.
_SEAM_MODULE = "workers.stages.visuals.hero_clip_choice"


def _workers(workers_src: str):
    """Import the producer's public selection and pricing functions from ``workers_src``. Fails closed with
    the reason, and names the interpreter, because the import needs the workers dependencies (#175 round-5
    design D3: the shell runs this under kitesforu-workers/.venv/bin/python)."""
    if not os.path.isdir(os.path.join(workers_src, "workers")):
        raise PricingUnavailable(f"no `workers` package under {workers_src}")
    # The live visuals worker routes from the CSV (MODEL_CATALOG_SHEET_ID is absent on
    # kitesforu-worker-visuals). With it set here the loader would reach Google Sheets, a network
    # call, and could read a different catalog than the one that serves.
    os.environ.pop("MODEL_CATALOG_SHEET_ID", None)
    if workers_src not in sys.path:
        sys.path.insert(0, workers_src)
    seam = os.path.join(workers_src, *_SEAM_MODULE.split(".")) + ".py"
    if not os.path.isfile(seam):
        raise PricingUnavailable(
            f"the workers tree at {workers_src} predates the quote seam ({_SEAM_MODULE}). kitesforu-qa #175 "
            "depends on the kitesforu-workers PR that adds it (branch "
            "feat/a-purchase-is-quoted-by-the-code-that-selects-it): merge it, or set WORKERS_SRC to a tree that has it")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from workers.common.model_eol import is_past_eol
            from workers.common.pricing import iter_model_rows
            from workers.stages.visuals import image_cost_ledger
            from workers.stages.visuals.character_anchors import ANCHOR_PLATE_BUDGET
            from workers.stages.visuals.hero_clip_choice import quote_hero_clip
            from workers.stages.visuals.policy import policy_for_job
            from workers.stages.visuals.scene_budget import relimage_cap
            from workers.stages.visuals.video.catalog import _truthy, video_models
    except Exception as exc:  # noqa: BLE001: any import failure means the producer moved
        raise PricingUnavailable(f"the workers selector did not import from {workers_src} under "
                                 f"{sys.executable}: {type(exc).__name__}: {exc}") from exc
    return dict(is_past_eol=is_past_eol, iter_model_rows=iter_model_rows, policy_for_job=policy_for_job,
                quote_hero_clip=quote_hero_clip, truthy=_truthy, video_models=video_models,
                relimage_cap=relimage_cap, plate_budget=ANCHOR_PLATE_BUDGET,
                relimage_usd=image_cost_ledger.price_of(image_cost_ledger.RELIMAGE))


def _row_provider(w: Dict[str, Any], model_id: str) -> str:
    row = (w["iter_model_rows"]() or {}).get(model_id) or {}
    return str(row.get("provider") or "").rsplit(".", 1)[-1].lower()


def quote_purchased_clips(w: Dict[str, Any], body: Dict[str, Any], *, aspect: str) -> ClipQuote:
    """The four cells, each the clip ``veo_hero`` would buy, on the budget the producer builds from THIS body."""
    budget = w["policy_for_job"](dict(body)).hero_budget
    cells: List[ClipCell] = []
    for fal_bound in (True, False):
        with _fal_key(fal_bound):
            # The catalog must have LOADED. `select_provider`'s registry falls soft to one hardcoded Veo
            # provider on an empty catalog, which would price a catalog that failed to load as if it had.
            if not w["video_models"]():
                raise PricingUnavailable("the workers catalog yielded no usable video row")
            for anchored in (False, True):
                q = w["quote_hero_clip"](budget, anchored=anchored, paid_opt_in=True, aspect_ratio=aspect)
                cells.append(ClipCell(fal_bound, anchored, q.model_id, int(q.seconds), float(q.usd),
                                      _row_provider(w, q.model_id) if q.model_id else ""))
    return ClipQuote(cells=tuple(cells), cap_usd=float(budget.max_clip_usd),
                     clip_seconds=int(budget.max_seconds))


def quote_paid_images(w: Dict[str, Any]) -> ImageQuote:
    rows: List[Tuple[str, float, bool]] = []
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
            rows.append((str(row.get("model_id") or mid), usd,
                         bool(w["truthy"](row.get("supports_reference_images", False)))))
    if not rows:
        raise PricingUnavailable("the workers catalog has no enabled IMAGE row priced per image")
    return ImageQuote(rows=tuple(rows), relimage_usd=float(w["relimage_usd"]),
                      relimage_cap=w["relimage_cap"], plate_budget=int(w["plate_budget"]))


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


def status_fields(snapshot: Dict[str, Any], *, expect_video: bool) -> List[str]:
    """One ``/status`` body, read through the package's ONE readiness rule, as strings for the shell:

        status | video_expected | phase | reason | settle | fingerprint | hero_clips

    Joined with ``|`` by the CLI (a tab is IFS whitespace, so an empty field would collapse and shift every
    field after it); a ``|`` inside the reason is replaced.

    ``video_expected``: the script's own request (``expect_video``: ``--visuals``, ``--visuals-auto``, or a
    purchase), OR the doc's ``wants_visuals``. ``--visuals-auto`` sends neither key and the workers' $0
    non-fiction default never writes ``wants_visuals``, so ``/status`` alone called that run audio-only, and
    the wait printed "Grade it" while the visuals it was run to exercise were still rendering (#175 round-5
    code critic F1).

    ``phase``/``reason``: :meth:`VisualReadiness.video_phase`. ``failed`` and ``no_video`` are terminal; the
    wait exits 5 on them, naming the reason, instead of waiting out its budget for a video that is not coming
    (latency L1: ``failed_assembly`` and ``partial`` + ``audio_only_no_clips`` both waited 90 minutes).

    ``settle``/``fingerprint``/``hero_clips``: a clip count is read only from a settled read (the ladder at
    ``ready``, the ``clips_settled_at`` stamp not cleared, and the same fingerprint for
    ``settled_clips.DEFAULT_STABLE_SECONDS``, which the wait checks across reads). ``hero_clips`` counts by the
    producer's predicate, ``modality == video_hero`` AND ``render_mode == video`` (critic F2)."""
    r = VisualReadiness.from_visual(snapshot.get("visual"), top_level_video_url=snapshot.get("video_url"))
    expected = bool(expect_video or snapshot.get("wants_visuals") is True)
    phase, reason = r.video_phase(expected=expected)
    return [
        str(snapshot.get("status") or ""),
        "yes" if expected else "no",
        phase,
        reason.replace("|", "/"),
        r.settle_stamp,
        r.clips_fingerprint,
        str(r.hero_clips),
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
    clip_quote = image_quote = None
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
                    clip_quote = quote_purchased_clips(w, sent, aspect="9:16" if args.short == "true" else "16:9")
                if ordered_stills(sent) > 0:
                    image_quote = quote_paid_images(w)
        except Exception as exc:
            tail = "\n".join(noise.getvalue().strip().splitlines()[-15:])
            if tail:
                print(f"(workers output before the failure)\n{tail}", file=sys.stderr)
            if isinstance(exc, PricingUnavailable):
                raise
            raise PricingUnavailable(f"the workers selector raised {type(exc).__name__}: {exc}") from exc
    est = estimate(sent, clips=clip_quote, images=image_quote, short=args.short == "true")
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

    s = sub.add_parser("status-fields",
                       help="status|video_expected|phase|reason|settle|fingerprint|hero_clips")
    s.add_argument("--snapshot", required=True, help="the GET /status JSON, or - for stdin")
    s.add_argument("--expect-video", default="no", choices=("yes", "no"),
                   help="the script asked for visuals itself (--visuals, --visuals-auto or a purchase)")

    sub.add_parser("settle-window", help="seconds a clip array must hold still before it is counted")

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
    if args.cmd == "settle-window":
        print(int(DEFAULT_STABLE_SECONDS))
        return 0
    snapshot = _read_snapshot(args.snapshot)
    print("|".join(status_fields(snapshot if isinstance(snapshot, dict) else {},
                                 expect_video=args.expect_video == "yes")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
