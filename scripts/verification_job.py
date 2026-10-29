#!/usr/bin/env python3
"""The verification job's request, its estimate, its read-back and its wait. One module, four commands.

``create_verification_job.sh`` is the one sanctioned way to POST a verification job
(``.claude/rules/03-money.md``). It owns the arguments, the ACK gate, auth, the POST and the poll.
This module owns the things the script used to do inline, in places that drifted apart:

``plan``              builds the request body, then computes the estimate and the ACK decision FROM IT;
``course-plan``       the same for ``--course``: the smallest course ``POST /v1/courses`` accepts, priced and gated
                      as the episodes the api will create for it;
``check-stored``      compares what the api stored with what was requested;
``status-fields``     reads one ``/status`` body through the package's ONE readiness rule
                      (:mod:`kitesforu_qa.visual_readiness`) for the wait loop;
``no-video-reasons``  the producer's by-design no-video reasons, read once before a wait (workers venv);
``settle-window``     how long a clip array must hold still before it is counted (``settled_clips``).

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

WHERE THE PRICES COME FROM: the producer's functions, called (#175 round 5 design D2, round 6)
------------------------------------------------------------------------------------------------
Round 4 rebuilt ``veo_hero``'s purchase question here through seven private workers names. A copy fails
OPEN on drift: every import still resolves, so the quote stays plausible and wrong. Every number now comes
from a PUBLIC workers function or constant that the producer itself reads (kitesforu-workers PR #3280, "a
purchased hero clip is quoted by the code that selects it", branch
``feat/a-purchase-is-quoted-by-the-code-that-selects-it``; this module refuses to price a purchase from a tree
without it). Two image-side bounds are RESTATEMENTS of producer rules, and :class:`ImageQuote` names them.

* **A purchased clip**: ``hero_clip_choice.quote_hero_clip``. It composes the four functions ``veo_hero`` calls
  for each beat, the row (``choose_hero_provider``), the route (``hero_clip_route``), the price
  (``hero_clip_price``) and the spend gates (``hero_clip_gate``), and adds no decision of its own (#3280's fix
  round moved the route there; round 5's text said "the function veo_hero selects and prices with", and veo_hero
  never called it). Asked on the budget AND the arm ``policy.policy_for_job`` builds from THIS body
  (``hero_budget``, ``motion_purchased``), over the rows ``video.catalog.video_models()`` loads. Six cells:
  three beat shapes (:data:`CLIP_SHAPES`: plain, anchored, and the plain beat of an anchored pass) with the fal
  key bound or not. A cell the pass would refuse is $0 and says which gate refused it; a selection that raised
  is a refusal to price. SELECTION ONLY: no ``submit``, ``poll`` or ``generate``. The fal key is a dummy, set
  inside this process for the "bound" cells. ``MODEL_CATALOG_SHEET_ID`` is cleared first, so the loader reads
  the CSV the way ``kitesforu-worker-visuals`` does (that env is absent there). A quote covers each beat's FIRST
  submit: the reference-fallback resubmit, made when Vertex refuses a reference op, is not added (the refused op
  generated nothing, so the beat still has one billable clip).

  THE SERVING CELL IS THE EXPECTED PRICE (#175 round-5 cost F3). The serving visuals worker carries
  ``FAL_KEY`` (``gcloud run services describe kitesforu-worker-visuals``, read 2026-10-08 by the round-5 cost
  and claims lenses), so a clip is the fal-bound cell, and every non-zero ``costs.visuals_veo`` they read was
  exactly that price per clip. The no-key cells are the FAILOVER range, reachable when fal is unavailable,
  and they set the high end. A cell whose PASS is anchored needs a character plate, and a plate needs paid
  generation (workers ``scene_budget`` passes ``allow_paid=real_images``), so with ``--paid-stills off`` only the
  plain cells are reachable.
* **One plan's paid pictures** (#175 round-5 cost F1, claims D1). Workers book paid images at five dispatch
  sites (``image_cost_ledger``). This bounds the three a non-short purchase reaches, per PLAN:
  * scene stills, ``max_images`` of them: cheapest enabled, unretired ``per image`` row x1 up to the dearest
    x(1 + ``verify.regen_policy.SCENE_VERIFY_REGENS_PER_BEAT``). That high end is a DOLLAR ceiling, not a render
    count: a job can book more renders on cheaper rows (round-6 cost C2: 072c32c4 booked 8 on a grant of 3, all
    at $0.039). A verify REJECT can add a render on another row; not bounded. The scene-verify vision judge
    (one check per paid still and clip, well under $0.01 per run, not booked under ``costs.*``) is not added;
  * relimage bases: ``scene_budget.relimage_cap(max_images, allow_paid)`` of them, at
    ``image_cost_ledger.price_of(RELIMAGE)``, the price the ledger books them at. Since workers #3279 a
    relimage draws on the same grant as a scene still, so one pass KEEPS at most ``max_images`` paid
    pictures, relimages included; adding the bases on top of the stills keeps the dearer price of a
    relimage inside the DOLLAR ceiling, and is not a count of what one pass keeps;
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
* **The audio base** is the measured per-tier band (``COST_CHANGELOG.md`` 2026-09-06 and the T3 measurement),
  widened to the census tail (:data:`_AUDIO_TAIL`). Not a catalog fact. Its duration basis is recorded where it is
  known: the low tier's $0.025 is a 10 s job, so a longer low run scales its high end linearly (#175 round-5 cost
  NIT-1). That job ran no blueprint ``architect`` stage and no ``music`` bed, and the census shows low-tier jobs
  that ran both: the costliest low 1-min job's non-visual cost was $0.298, against $0.15 for the scaled band
  (round-6 claims 1). So the high end is the larger of the two.

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
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# The package's readiness rule (stdlib only, so any python3 can import it). This tree's `src`, never an
# installed copy: the shared venv carries an editable install of the MAIN checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kitesforu_qa.visual_readiness import (  # noqa: E402
    DEFAULT_STABLE_SECONDS,
    TERMINAL_PHASES,
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
#: (``visual_refund.maybe_refund_unrendered_visuals``, run from the api's watchdog sweep,
#: ``routes/admin/watchdog.py``, since api #867; ``GET /status`` does not run it, #175 round-6 cost C3).
#: It does NOT refund on ``needs_review`` / ``failed_qa`` (#175 round-3 claims D4). So the count should be
#: one the job can RENDER. What can render is workers ``scene_budget.resolve_ceiling``, executed 2026-09-12 on
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

#: THE MEASURED TAIL THE BANDS LEAVE OUT (#175 round-6 claims 1). The T3 measurement is a 10 s job that ran
#: neither a blueprint ``architect`` stage nor a ``music`` bed, and some low-tier jobs run both: up to
#: ~$0.17 architect and ~$0.12 music in one job. So the band alone under-priced the 1-min visuals run this
#: script is used for. Each entry is the largest non-visual cost of one FINISHED job (``completed``,
#: ``needs_review``, ``failed_qa``): every ``costs.<stage>.total_cost_usd`` summed except ``visuals_images``,
#: ``visuals_veo`` and the four author stages, which are priced separately below.
#:
#: Read-only census 2026-10-10, the frozen ``population.txt`` of the newest 600 ``podcast_jobs`` (it holds 2
#: string-dated 2026-01 docs; see the census script). Command:
#: ``scripts/verification_job_census.py --population <ids> --section audio``.
#: ``(tier, duration_min) -> (max, n)`` for each cell the census measured, and ``(tier, None)`` for the whole
#: tier, which an unmeasured duration falls back to. An observed maximum is not a bound: a future job can
#: exceed it. The audio term's high end is the larger of the band and the tail.
_AUDIO_TAIL: Dict[Tuple[str, Optional[float]], Tuple[float, int]] = {
    ("low", 0.167): (0.165, 194), ("low", 0.5): (0.169, 189), ("low", 1.0): (0.298, 31),
    ("low", 1.5): (0.197, 8), ("low", 2.0): (0.002, 17), ("low", 5.0): (0.279, 7), ("low", None): (0.298, 446),
    ("medium", 0.167): (0.146, 6), ("medium", 1.0): (0.508, 13), ("medium", 1.5): (0.021, 1),
    ("medium", 2.0): (0.345, 19), ("medium", 3.0): (0.181, 1), ("medium", 5.0): (0.389, 9),
    ("medium", 10.0): (0.239, 12), ("medium", None): (0.508, 61),
    ("high", 0.167): (0.471, 1), ("high", 0.5): (0.032, 5), ("high", 1.5): (0.667, 5), ("high", 5.0): (0.025, 1),
    ("high", None): (0.667, 12),
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
#: median $0.064, p95 $0.126, max $0.152. Command: ``scripts/verification_job_census.py --section costs``.
#: The low end is $0 because a run can author nothing (a 10 s ``--visuals-auto`` run usually authors no
#: blueprint: job 9725a85c; 4 of 194 low-tier 10 s jobs in the same census did book an ``architect`` stage).
_AUTHORS_BAND = (0.0, 0.152, "visuals authors ~$0-0.15 (measured: max $0.152, 471 jobs, 2026-10-10)")


def _usd(x: float) -> str:
    if abs(x) < 1e-12:
        return "$0"
    return f"${x:.3f}" if x < 0.1 else f"${x:.2f}"


def _span(lo: float, hi: float) -> str:
    return _usd(lo) if abs(hi - lo) < 1e-9 else f"{_usd(lo)}-{_usd(hi)[1:]}"


#: The three beat shapes a purchased pass can buy, as ``(label, pass_anchored, beat_anchored)``. The workers seam
#: takes the two anchor facts separately because the pass reads them separately (workers #3280 round 1): the
#: PASS selects its row on "some beat has plates", and each BEAT is routed on its own plates. A mixed-anchor
#: pass's establishing beat (no character on screen) is the third shape, the anchored pass's row sent
#: first-frame, and a single ``anchored`` flag priced it on the plain pass's row instead.
CLIP_SHAPES: Tuple[Tuple[str, bool, bool], ...] = (
    ("plain", False, False),
    ("anchored", True, True),
    ("plain-beat-of-anchored-pass", True, False),
)


@dataclass(frozen=True)
class ClipCell:
    fal_bound: bool
    shape: str                # a CLIP_SHAPES label
    pass_anchored: bool       # the pass's row needs a character plate, so paid stills, to be reachable
    model_id: Optional[str]   # None: the pass buys no clip here (nothing fits, or a gate refuses it): $0
    seconds: int
    usd: float
    seed_mode: str = ""       # the route the pass sends it on: first_frame or reference
    skipped: str = ""         # why no clip: the seam's reason (no_row, clip_cap, job_cap, clip_count)
    provider: str = ""        # the catalog row's provider ("fal", "google"), so a reader can tell the cells apart


@dataclass(frozen=True)
class ClipQuote:
    cells: Tuple[ClipCell, ...]
    cap_usd: float
    budget_seconds: int

    def reachable(self, *, anchors_possible: bool) -> Tuple[ClipCell, ...]:
        """The cells this body can reach. An anchored PASS needs a plate, and a plate needs paid generation."""
        return tuple(c for c in self.cells if anchors_possible or not c.pass_anchored)

    def serving_low(self, *, anchors_possible: bool) -> float:
        """The serving worker's price: the fal-bound cells (the serving visuals worker carries FAL_KEY)."""
        bound = [c.usd for c in self.reachable(anchors_possible=anchors_possible) if c.fal_bound]
        if not bound:
            raise PricingUnavailable("the quote has no fal-bound cell, so the serving price is unknown")
        return min(bound)

    def high(self, *, anchors_possible: bool) -> float:
        """The failover ceiling: the dearest reachable cell, fal bound or not."""
        return max(c.usd for c in self.reachable(anchors_possible=anchors_possible))

    def longest_seconds(self, *, anchors_possible: bool) -> int:
        """The longest clip a reachable cell buys. A Veo reference clip is 8 s whatever the budget's own length,
        so the budget's ``max_seconds`` under-counts an anchored cell (workers #3280 round-1 design D6)."""
        return max([c.seconds for c in self.reachable(anchors_possible=anchors_possible)] or [self.budget_seconds])

    def detail(self, *, anchors_possible: bool) -> List[str]:
        def cell(c: ClipCell) -> str:
            off = "" if (anchors_possible or not c.pass_anchored) else " (unreachable: no paid stills, so no plate)"
            if c.model_id is None:
                return f"{c.shape} no clip ({c.skipped or 'nothing fits'}){off}"
            return f"{c.shape} {c.model_id} {c.seed_mode} {c.seconds}s ${c.usd:.3f}{off}"
        lines = [f"clips: purchased arm (cap ${self.cap_usd:.2f}, {self.budget_seconds}s), kitesforu-workers "
                 "hero_clip_choice.quote_hero_clip, which asks the row, route, price and gate functions veo_hero "
                 "calls for each beat; one row per beat shape:"]
        for bound in (True, False):
            row = [cell(c) for c in self.cells if c.fal_bound is bound]
            label = "fal bound (SERVING)" if bound else "no fal key (failover)"
            lines.append(f"  {label:<22} " + " | ".join(row))
        return lines


@dataclass(frozen=True)
class ImageQuote:
    """One plan's paid pictures, priced from the producer: stills, relimage bases, character plates.

    Three numbers are the producer's own, imported: ``relimage_cap``, ``plate_budget`` and ``regens``. Two bounds
    are RESTATEMENTS of producer rules, named here because a restatement drifts silently (#175 round-6 design
    D6). :attr:`dearest_plate_row` restates ``character_anchors._anchor_provider``'s row rule
    (reference-capable, else any). :func:`quote_paid_images` restates the IMAGE-row filter (enabled, unretired,
    priced per image). Both are bounds, not selections: drift moves a range, never a choice."""
    rows: Tuple[Tuple[str, float, bool], ...]   # (model_id, usd per image, supports reference images)
    relimage_usd: float                          # image_cost_ledger.price_of(RELIMAGE)
    relimage_cap: Callable[[int, bool], int]     # scene_budget.relimage_cap
    plate_budget: int                            # character_anchors.ANCHOR_PLATE_BUDGET
    regens: int                                  # verify.regen_policy.SCENE_VERIFY_REGENS_PER_BEAT

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
    def high(self) -> float:  # one still's dollar ceiling: the dearest row, times its render and its regen(s)
        return self.dearest[1] * (1 + self.regens)

    def terms(self, n_stills: int) -> List[Tuple[float, float, str]]:
        lo_still, hi_still = n_stills * self.low, n_stills * self.high
        n_rel = int(self.relimage_cap(n_stills, n_stills > 0))
        rel_hi = n_rel * self.relimage_usd
        plate_hi = self.plate_budget * self.dearest_plate_row[1] if n_stills > 0 else 0.0
        out = [(lo_still, hi_still, f"up to {n_stills} paid still(s) {_span(lo_still, hi_still)} (a dollar ceiling)")]
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
            f"x{1 + self.regens} (SCENE_VERIFY_REGENS_PER_BEAT={self.regens}). A DOLLAR ceiling at the dearest row, "
            "not a render count: a job can book more renders on cheaper rows (072c32c4 booked 8 on a grant of "
            "3, all at $0.039), and a verify REJECT can add a render on another row",
            f"relimage: scene_budget.relimage_cap({n_stills}, allow_paid) = {n_rel} at ${self.relimage_usd:.3f} "
            "(image_cost_ledger.price_of(RELIMAGE)); $0 when no beat takes one. A relimage spends the same "
            f"grant as a still since workers #3279, so one pass keeps at most {n_stills} paid picture(s) in all: "
            "this term is a dollar ceiling, not a count",
            f"plates: up to {self.plate_budget} new per plan (character_anchors.ANCHOR_PLATE_BUDGET) at most "
            f"{p_id} ${p:.3f}; $0 with no named character",
            "ONE PLAN: a re-planned pass (mux_gate.REPLAN_CAP) can buy stills, relimage and plates again, and a "
            "changed character can be redrawn once; this estimate does not add those",
            "not added: the scene-verify vision judge (one check per paid still and clip, ~300-500 vision tokens, "
            "well under $0.01 per run, not booked under costs.*)",
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
    if basis is not None and duration_min > basis + 1e-9:
        scale = duration_min / basis
        hi = hi * scale
        text = f"~{_span(lo, hi)} ({text[1:]} measured at 10 s; x{scale:.1f} for {duration_min:g} min)"
    elif basis is None and abs(duration_min - _T3_DURATION_MIN) > 1e-9:
        text = f"{text} (per episode; no duration basis recorded, so NOT scaled to {duration_min:g} min)"
    cell = round(float(duration_min), 3)
    tail = _AUDIO_TAIL.get((tier, cell))
    where = f"{tier} jobs at {duration_min:g} min"
    if tail is None:
        tail = _AUDIO_TAIL.get((tier, None))
        where = f"{tier} jobs of any length (none measured at {duration_min:g} min)"
    if tail is not None and tail[0] > hi:
        hi = tail[0]
        # Only the low tier's tail was read stage by stage (architect, music); the others name no driver.
        driver = ", an architect stage and sometimes a music bed" if tier == "low" else ""
        text = (f"~{_span(lo, hi)} (band {text.lstrip('~')}; the high end is the census tail, the costliest of "
                f"{tail[1]} {where}: ${tail[0]:.3f}{driver})")
    return lo, hi, text


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
            terms.append((0.0, 0.0, "born-short photoreal/referent stills NOT BOUNDED here"))
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
# The course: the smallest one POST /v1/courses accepts, priced and gated as the episodes it creates
# ---------------------------------------------------------------------------------------------

#: The fewest episodes the api accepts: ``CreateCourseRequest.total_episodes`` is ``Field(ge=2)`` (kitesforu-schemas
#: 2.85.0, the version the api locks). The workers initiator would take 1 (``limits.COURSE_MODULES_MIN``), but the
#: api answers 1 with a 422 before it writes anything.
COURSE_EPISODES = 2

#: The quality tier each episode of a course created through ``POST /v1/courses`` runs at, whatever the course asked
#: for. ``CreateCourseRequest`` declares ``quality_tier``, but the api's ``create_course`` (kitesforu-api ca510ec,
#: ``services/courses/crud.py``) writes no ``quality_tier`` to the course doc; the orchestrator creates each episode
#: with ``course_state.get("quality_tier")`` (kitesforu-workers 7f384670a, ``stages/course/orchestrator/worker.py``),
#: and ``podcast_services.create_job`` stores that None as ``"medium"``. Smart-create's course executor writes the
#: field; the course door does not. Once it does, an episode's tier is the course's own ``quality_tier``.
COURSE_EPISODE_TIER = "medium"

#: The curriculum call, one per course. Workers ``stages/course/syllabus/worker.py`` sends ``gpt-4o`` (the serving
#: syllabus service sets no ``LLM_MODEL``, and gpt-4o has no ``model_catalog.csv`` row), priced at the workers' own
#: gpt-4o rates (``common/pricing.py``: $2.50 in, $10.00 out per 1M tokens). A MEASUREMENT, not a bound: the
#: ``curriculum_debug.llm_call`` tokens of the 130 courses that record them (232 scanned, read-only, 2026-10-28) cost
#: a median $0.0071 and at most $0.0351; each of the 3 two-episode courses among them cost about $0.007.
_SYLLABUS_BAND = (0.0071, 0.0351, "syllabus ~$0.007-0.035 (one gpt-4o call: median and max of 130 recorded, 2026-10-28)")


def build_course_body(*, topic: str, duration: str, tier: str, style: str, language: str) -> Dict[str, Any]:
    """The ``POST /v1/courses`` body for the smallest course the api accepts. Every key is a declared
    ``CreateCourseRequest`` field: that model drops an undeclared key without an error."""
    return {
        "topic": topic,
        "total_episodes": COURSE_EPISODES,
        "episode_duration_min": float(duration),
        "style": style,
        "language": language,
        "quality_tier": tier,
        # Without it the api HOLDS a course whose topic is under three words, or that names no target_audience, in
        # `clarifying` with questions (routes/courses/crud.py generate_course_clarifier_questions), and nothing runs.
        "skip_clarifier": True,
    }


def course_episode_body(course: Dict[str, Any]) -> Dict[str, Any]:
    """One episode of ``course``, in the keys :func:`estimate` and :func:`ack_decision` read. The orchestrator creates
    each episode through ``POST /v1/podcasts`` with the course's episode duration, the tier the course door leaves it
    (:data:`COURSE_EPISODE_TIER`), and NEITHER visuals key: ``CreateCourseRequest`` has no visuals field, so every
    episode keeps the workers' $0 visuals default and may run its visuals authors."""
    return {"quality_tier": COURSE_EPISODE_TIER, "duration_min": float(course.get("episode_duration_min") or 0)}


def course_estimate(course: Dict[str, Any]) -> Estimate:
    """The curriculum call plus every episode's own :func:`estimate`."""
    episode = estimate(course_episode_body(course))
    n = int(course.get("total_episodes") or 0)
    each = f"{n} episode(s) at {COURSE_EPISODE_TIER}, each {episode.text}"
    if episode.low is None or episode.high is None:
        return Estimate(text=f"unknown = {_SYLLABUS_BAND[2]} + {each}", low=None, high=None)
    lo = _SYLLABUS_BAND[0] + n * episode.low
    hi = _SYLLABUS_BAND[1] + n * episode.high
    return Estimate(text=f"~{_span(lo, hi)} = {_SYLLABUS_BAND[2]} + {each}", low=lo, high=hi)


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
#: The workers module that names the reasons no video is coming (workers #3280's fix round). A tree without it
#: predates the move, and the wait then ends on no skip reason at all.
_SKIP_REASONS_MODULE = "workers.stages.visuals.video_skip_reasons"


def _producer_on_path(workers_src: str, module: str, *, label: str) -> None:
    """Put ``workers_src`` first on ``sys.path`` for an import of ``module``. Fails closed with the reason.

    The live visuals worker routes from the CSV (MODEL_CATALOG_SHEET_ID is absent on kitesforu-worker-visuals).
    With it set here the loader would reach Google Sheets, a network call, and could read a different catalog
    than the one that serves, so it is cleared."""
    if not os.path.isdir(os.path.join(workers_src, "workers")):
        raise PricingUnavailable(f"no `workers` package under {workers_src}")
    os.environ.pop("MODEL_CATALOG_SHEET_ID", None)
    if workers_src not in sys.path:
        sys.path.insert(0, workers_src)
    if not os.path.isfile(os.path.join(workers_src, *module.split(".")) + ".py"):
        raise PricingUnavailable(f"the workers tree at {workers_src} predates {label} ({module}). {_NEEDS_3280}")


_NEEDS_3280 = ("kitesforu-qa #175 depends on the kitesforu-workers PR that adds it (branch "
               "feat/a-purchase-is-quoted-by-the-code-that-selects-it): merge it, or set WORKERS_SRC to a tree "
               "that has it")


def _workers(workers_src: str):
    """Import the producer's public selection and pricing functions from ``workers_src``. Fails closed with the reason,
    and names the interpreter, because the import needs the workers dependencies (#175 round-5 design D3: the
    shell runs this under kitesforu-workers/.venv/bin/python)."""
    _producer_on_path(workers_src, _SEAM_MODULE, label="the quote seam")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from workers.common.model_eol import is_past_eol
            from workers.common.pricing import iter_model_rows
            from workers.stages.visuals import image_cost_ledger
            from workers.stages.visuals.character_anchors import ANCHOR_PLATE_BUDGET
            from workers.stages.visuals.hero_clip_choice import SKIP_SELECT_FAILED, quote_hero_clip
            from workers.stages.visuals.policy import policy_for_job
            from workers.stages.visuals.scene_budget import relimage_cap
            from workers.stages.visuals.verify.regen_policy import SCENE_VERIFY_REGENS_PER_BEAT
            from workers.stages.visuals.video.catalog import _truthy, video_models
    except Exception as exc:  # noqa: BLE001: any import failure means the producer moved
        raise PricingUnavailable(f"the workers selector did not import from {workers_src} under "
                                 f"{sys.executable}: {type(exc).__name__}: {exc}") from exc
    return dict(is_past_eol=is_past_eol, iter_model_rows=iter_model_rows, policy_for_job=policy_for_job,
                quote_hero_clip=quote_hero_clip, select_failed=SKIP_SELECT_FAILED, truthy=_truthy,
                video_models=video_models, relimage_cap=relimage_cap, plate_budget=ANCHOR_PLATE_BUDGET,
                regens=SCENE_VERIFY_REGENS_PER_BEAT,
                relimage_usd=image_cost_ledger.price_of(image_cost_ledger.RELIMAGE))


def producer_no_video_reasons(workers_src: str) -> List[str]:
    """The producer's own by-design no-video reasons (``video_skip_reasons.BY_DESIGN_NO_VIDEO_REASONS``), the ONLY
    skip reasons that end the wait beside a non-``ready`` ladder (#175 round 6: design D1, latency L6-1, critic
    F2). Imported, never copied: a copy fails open on drift."""
    _producer_on_path(workers_src, _SKIP_REASONS_MODULE, label="the no-video reasons")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from workers.stages.visuals.video_skip_reasons import BY_DESIGN_NO_VIDEO_REASONS
    except Exception as exc:  # noqa: BLE001
        raise PricingUnavailable(f"the producer's no-video reasons did not import from {workers_src} under "
                                 f"{sys.executable}: {type(exc).__name__}: {exc}") from exc
    reasons = sorted(str(r) for r in BY_DESIGN_NO_VIDEO_REASONS)
    if not reasons:
        raise PricingUnavailable("the producer's BY_DESIGN_NO_VIDEO_REASONS is empty")
    return reasons


def _row_provider(w: Dict[str, Any], model_id: str) -> str:
    row = (w["iter_model_rows"]() or {}).get(model_id) or {}
    return str(row.get("provider") or "").rsplit(".", 1)[-1].lower()


def quote_purchased_clips(w: Dict[str, Any], body: Dict[str, Any], *, aspect: str) -> ClipQuote:
    """Six cells, each the FIRST clip ``veo_hero`` would buy for one beat shape (:data:`CLIP_SHAPES`), with the fal
    key bound and not, on the budget AND the arm the producer builds from THIS body: ``policy_for_job(body)``'s
    ``hero_budget`` and ``motion_purchased``. The arm used to be a literal ``paid_opt_in=True`` beside the policy
    object that carries it (#175 round-6 design D5)."""
    policy = w["policy_for_job"](dict(body))
    budget = policy.hero_budget
    cells: List[ClipCell] = []
    for fal_bound in (True, False):
        with _fal_key(fal_bound):
            # The catalog must have LOADED. `select_provider`'s registry falls soft to one hardcoded Veo
            # provider on an empty catalog, which would price a catalog that failed to load as if it had.
            if not w["video_models"]():
                raise PricingUnavailable("the workers catalog yielded no usable video row")
            for shape, pass_anchored, beat_anchored in CLIP_SHAPES:
                q = w["quote_hero_clip"](budget, pass_anchored=pass_anchored, beat_anchored=beat_anchored,
                                         paid_opt_in=bool(policy.motion_purchased), aspect_ratio=aspect)
                # The seam fails open where the pass does: a selection that RAISED is "no clip" there. Here it is
                # an unknown, and an approval must never price an unknown as $0.
                if q.skipped == w["select_failed"]:
                    raise PricingUnavailable(f"the workers selector raised for the {shape} cell: {q.detail}")
                cells.append(ClipCell(fal_bound, shape, pass_anchored, q.model_id, int(q.seconds), float(q.usd),
                                      seed_mode=str(q.seed_mode or ""), skipped=str(q.skipped or ""),
                                      provider=_row_provider(w, q.model_id) if q.model_id else ""))
    return ClipQuote(cells=tuple(cells), cap_usd=float(budget.max_clip_usd), budget_seconds=int(budget.max_seconds))


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
                      relimage_cap=w["relimage_cap"], plate_budget=int(w["plate_budget"]), regens=int(w["regens"]))


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
    cheapest wrong invocation (#175 round-2 design D7, latency NIT). ``clip_seconds`` is the longest clip a
    reachable quoted cell buys (``ClipQuote.longest_seconds``), not the budget's own length: a Veo reference
    clip is 8 s on a 6 s budget (workers #3280 round-1 design D6)."""
    clips = ordered_clips(body)
    episode_s = float(body.get("duration_min") or 0) * 60
    video_s = clips * clip_seconds
    if clips <= 0 or video_s * 2 <= episode_s:
        return []
    return [
        f"⚠️  {clips} clip(s) is ~{video_s}s of video for a {episode_s:.0f}s episode: more than half of it.",
        "    A short episode has too few beats for the hero ranker to choose among, and a 10 s run usually",
        "    authors no blueprint (job 9725a85c; 4 of 194 low 10 s jobs in the 2026-10-10 census did).",
        "    A paid-video T4 wants ~1-2 min: raise --duration.",
    ]


def status_fields(snapshot: Dict[str, Any], *, expect_video: bool, no_video_reasons: Sequence[str] = (),
                  now_ms: Optional[int] = None) -> List[str]:
    """One ``/status`` body, read through the package's ONE readiness rule, as strings for the shell:

        status | video_expected | phase | reason | settle | fingerprint | hero_clips | terminal | trustworthy

    Joined with ``|`` by the CLI (a tab is IFS whitespace, so an empty field would collapse and shift every
    field after it); a ``|`` inside the reason is replaced.

    ``video_expected``: the script's own request (``expect_video``: ``--visuals``, ``--visuals-auto``, or a
    purchase), OR the doc's ``wants_visuals``. ``--visuals-auto`` sends neither key and the workers' $0
    non-fiction default never writes ``wants_visuals``, so ``/status`` alone called that run audio-only, and
    the wait printed "Grade it" while the visuals it was run to exercise were still rendering (#175 round-5
    code critic F1).

    ``phase``/``reason``: :meth:`VisualReadiness.video_phase`, given the producer's by-design reasons
    (``no_video_reasons``, read once by ``no-video-reasons``) and this read's clock (``now_ms``, for the
    leases). ``terminal``: the phase is in ``TERMINAL_PHASES`` (``failed``, ``no_video``); the wait exits 5 on
    it, naming the reason, once two reads a poll apart agree, instead of waiting out its budget for a video
    that is not coming (latency L1). ``trustworthy``: :attr:`VisualReadiness.clip_count_trustworthy`, the one
    read's half of the clip-count rule. The shell branches on these two verdicts; it used to re-derive them
    from ``phase`` and ``settle`` (#175 round-6 design D3, critic F3).

    ``settle``/``fingerprint``/``hero_clips``: a clip count is read only from a settled read (the ladder at
    ``ready``, the ``clips_settled_at`` stamp not cleared, and the same fingerprint for
    ``settled_clips.DEFAULT_STABLE_SECONDS``, which the wait checks across reads). ``hero_clips`` counts by the
    producer's predicate, ``modality == video_hero`` AND ``render_mode == video`` (critic F2)."""
    r = VisualReadiness.from_visual(snapshot.get("visual"))
    expected = bool(expect_video or snapshot.get("wants_visuals") is True)
    phase, reason = r.video_phase(expected=expected, no_video_reasons=frozenset(no_video_reasons), now_ms=now_ms)
    return [
        str(snapshot.get("status") or ""),
        "yes" if expected else "no",
        phase,
        reason.replace("|", "/"),
        r.settle_stamp,
        r.clips_fingerprint,
        str(r.hero_clips),
        "yes" if phase in TERMINAL_PHASES else "no",
        "yes" if r.clip_count_trustworthy else "no",
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
    warn = (clip_coverage_warning(sent, clip_quote.longest_seconds(anchors_possible=ordered_stills(sent) > 0))
            if clip_quote else [])
    return {"payload": payload, "est": est.text, "est_low": est.low, "est_high": est.high,
            "est_detail": detail, "needs_ack": needs_ack, "ack_reason": reason, "warnings": warn}


def _course_plan(args: argparse.Namespace) -> Dict[str, Any]:
    """:func:`_plan` for ``--course``, in the same shape. The ACK decision is :func:`ack_decision` of ONE EPISODE,
    because a course orders episode jobs, and each is gated as the job the api will create for it."""
    payload = json.dumps(build_course_body(topic=args.topic, duration=args.duration, tier=args.tier,
                                           style=args.style, language=args.language))
    sent = json.loads(payload)   # as in _plan: the estimate and the ACK decision read the bytes curl sends
    episode = course_episode_body(sent)
    needs_ack, reason = ack_decision(episode)
    est = course_estimate(sent)
    warn: List[str] = []
    if sent["quality_tier"] != episode["quality_tier"]:
        warn.append(f"⚠️  the course asks for quality_tier={sent['quality_tier']}, which the api's course door does not "
                    f"store: each episode runs at {episode['quality_tier']}, and is priced and gated here at "
                    f"{episode['quality_tier']}.")
    return {"payload": payload, "est": est.text, "est_low": est.low, "est_high": est.high,
            "est_detail": list(est.detail), "needs_ack": needs_ack, "ack_reason": reason, "warnings": warn,
            "course_episodes": sent["total_episodes"], "episode_tier": episode["quality_tier"]}


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

    cp = sub.add_parser("course-plan", help="--course: the smallest course's body + estimate + ACK decision, as one "
                                            "JSON object")
    for name in ("--topic", "--duration", "--tier", "--style", "--language"):
        cp.add_argument(name, required=True)

    c = sub.add_parser("check-stored", help="fields the api stored lower than requested, one per line")
    c.add_argument("--requested", required=True, help="the exact JSON body that was POSTed")
    c.add_argument("--snapshot", required=True, help="the GET /status JSON, or - for stdin")

    s = sub.add_parser("status-fields",
                       help="status|video_expected|phase|reason|settle|fingerprint|hero_clips|terminal|trustworthy")
    s.add_argument("--snapshot", required=True, help="the GET /status JSON, or - for stdin")
    s.add_argument("--expect-video", default="no", choices=("yes", "no"),
                   help="the script asked for visuals itself (--visuals, --visuals-auto or a purchase)")
    s.add_argument("--no-video-reasons", default="",
                   help="comma-separated: the producer's by-design reasons (from `no-video-reasons`); empty ends "
                        "the wait on no skip reason")

    n = sub.add_parser("no-video-reasons",
                       help="the producer's BY_DESIGN_NO_VIDEO_REASONS, comma-separated (run under the workers python)")
    n.add_argument("--workers-src", required=True)

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
    if args.cmd == "course-plan":
        try:
            out = _course_plan(args)
        except ValueError as exc:
            print(f"cannot plan this course: {exc}", file=sys.stderr)
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
    if args.cmd == "no-video-reasons":
        noise = io.StringIO()
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                reasons = producer_no_video_reasons(args.workers_src)
        except PricingUnavailable as exc:
            print(f"cannot read the producer's no-video reasons: {exc}", file=sys.stderr)
            return 1
        print(",".join(reasons))
        return 0
    snapshot = _read_snapshot(args.snapshot)
    print("|".join(status_fields(snapshot if isinstance(snapshot, dict) else {},
                                 expect_video=args.expect_video == "yes",
                                 no_video_reasons=[r for r in args.no_video_reasons.split(",") if r],
                                 now_ms=int(time.time() * 1000))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
