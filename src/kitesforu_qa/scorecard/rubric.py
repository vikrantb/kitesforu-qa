"""The SHORT SCORECARD rubric — the 8 axes, their floors/weights, and the SHIP-BAR logic.

This is the single source of truth for "what makes a 9:16 short amazing", promoted from a founder
vibe into a machine-scorable number (Phase 1b of social-shorts-next-level-2026-07-06). Each axis is
scored 0-100 by ``axes.py``; this module owns the axis SPEC (name, floor, weight, tier) and the
verdict math: the weighted total and the SHIP BAR.

SHIP BAR (from the proposal's north_star_rubric): a short may ship only when EVERY axis is scored
and ``>=`` its floor AND the weighted total ``>= 85``. An axis that cannot be scored at $0/¢ (its
pipeline data is not stamped yet — word timestamps, motion provenance, a safety verdict; or a paid
judge is disabled) is emitted with ``score=None`` and listed under ``missing_instrumentation`` — it
NEVER receives a fabricated passing score, and its presence blocks ship (you cannot certify what you
cannot measure). This integrity is the whole point of the scorecard.

Weights are a v0 default: the proposal fixes the FLOORS precisely but not the weights, so these are
chosen to emphasise the hook (the product's most important frame) and the four "never-seen-elsewhere"
differentiators (substance novelty + visual truth + motion density + sync exactness). They are a
tunable constant, documented here so a change is visible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The minimum weighted total a short must clear (on top of every axis clearing its own floor).
SHIP_TOTAL_MIN = 85.0


@dataclass(frozen=True)
class AxisSpec:
    """The immutable definition of one scorecard axis (not its per-render score)."""
    name: str
    floor: int          # the axis's own minimum; ship requires score >= floor
    weight: float       # contribution to the weighted total (weights sum to 1.0)
    tier: str           # cheapest tier that can score it: "T1" ($0) or "T2" (¢ judge/VLM)
    title: str          # human label


# ── THE 8 AXES ────────────────────────────────────────────────────────────────
# floors are FIXED by the proposal; weights are a documented v0 default (sum = 1.00).
RUBRIC: tuple[AxisSpec, ...] = (
    AxisSpec("hook_stop_power",   80, 0.16, "T1", "Hook stop-power"),
    AxisSpec("substance_novelty", 70, 0.14, "T2", "Substance novelty"),
    AxisSpec("visual_truth",      90, 0.14, "T2", "Visual truth"),
    AxisSpec("modality_mix",      70, 0.10, "T1", "Modality mix"),
    AxisSpec("motion_density",    75, 0.12, "T1", "Motion density"),
    AxisSpec("sync_exactness",    85, 0.12, "T1", "Sync exactness"),
    AxisSpec("audio_feel",        70, 0.10, "T1", "Audio feel"),
    AxisSpec("cost_safety",      100, 0.12, "T1", "Cost + safety"),
)

RUBRIC_BY_NAME: dict[str, AxisSpec] = {a.name: a for a in RUBRIC}


@dataclass
class AxisResult:
    """One axis's score for one rendered short. ``score``/``passed`` are ``None`` when the axis
    could not be scored at the current tier (missing instrumentation / a disabled judge) — that is
    an HONEST null, never a fake pass."""
    name: str
    score: float | None                 # 0..100, or None when unmeasurable
    floor: int
    weight: float
    passed: bool | None                 # score >= floor; None when score is None
    how_measured: str
    evidence: str
    tier: str = "T1"
    proxy: bool = False                  # True = a $0 heuristic standing in for a disabled paid judge
    needs: str | None = None            # what would make this axis authoritative (a flag / a stamp)
    sub_signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # Core keys match the proposal's contract exactly: {score, floor, pass, how_measured, evidence}.
        out: dict[str, Any] = {
            "score": self.score,
            "floor": self.floor,
            "pass": self.passed,
            "how_measured": self.how_measured,
            "evidence": self.evidence,
            "tier": self.tier,
            "weight": self.weight,
        }
        if self.proxy:
            out["proxy"] = True
        if self.needs:
            out["needs"] = self.needs
        if self.sub_signals:
            out["sub_signals"] = self.sub_signals
        return out


def make_axis(
    name: str,
    *,
    score: float | None,
    evidence: str,
    how_measured: str,
    proxy: bool = False,
    needs: str | None = None,
    sub_signals: dict[str, Any] | None = None,
) -> AxisResult:
    """Build an ``AxisResult`` from the axis name (pulls floor/weight/tier from the RUBRIC).

    ``score`` may be ``None`` (unmeasurable). A non-None score is clamped to [0, 100] and rounded.
    ``passed`` is derived from the floor (``None`` when the score is ``None``)."""
    spec = RUBRIC_BY_NAME[name]
    if score is not None:
        score = round(max(0.0, min(100.0, float(score))), 1)
        passed: bool | None = score >= spec.floor
    else:
        passed = None
    return AxisResult(
        name=name, score=score, floor=spec.floor, weight=spec.weight, passed=passed,
        how_measured=how_measured, evidence=evidence, tier=spec.tier,
        proxy=proxy, needs=needs, sub_signals=sub_signals or {},
    )


@dataclass
class ShipVerdict:
    """The aggregate SHIP verdict computed from a full set of axis results."""
    ship: bool
    weighted_total: float | None
    weighted_total_basis: str
    reasons: list[str]
    missing_instrumentation: list[dict[str, Any]]
    provisional_axes: list[str]


def evaluate(axes: dict[str, AxisResult]) -> ShipVerdict:
    """Compute the SHIP verdict from the 8 axis results.

    ship == True  ⟺  every axis has a real score, every score >= its floor, AND the weighted total
    (over ALL 8 axes) >= SHIP_TOTAL_MIN. Any ``None``-scored axis (missing instrumentation / disabled
    judge) makes ship False and is surfaced under ``missing_instrumentation`` — never silently passed.

    When some axes are unscorable, ``weighted_total`` is reported over the SCORED axes only, with the
    weights renormalised, and ``weighted_total_basis`` says so — a PARTIAL number, explicitly not
    ship-eligible (so a high partial can never be mistaken for a ship-ready total)."""
    scored = [a for a in axes.values() if a.score is not None]
    missing = [
        {"axis": a.name, "reason": a.evidence, "needs": a.needs or "pipeline instrumentation"}
        for a in axes.values() if a.score is None
    ]
    provisional = sorted(a.name for a in axes.values() if a.proxy)

    if scored:
        wsum = sum(a.weight for a in scored)
        weighted_total = round(sum((a.score or 0.0) * a.weight for a in scored) / wsum, 1) if wsum else None
    else:
        weighted_total = None

    all_scored = not missing
    if all_scored:
        basis = "all 8 axes scored"
    else:
        basis = (f"PARTIAL — {len(scored)}/{len(axes)} axes scored (weights renormalised); "
                 f"{len(missing)} need instrumentation → NOT ship-eligible")

    below = [f"{a.name} {a.score} < floor {a.floor}" for a in scored if a.passed is False]
    reasons: list[str] = []
    if missing:
        reasons.append(f"{len(missing)} axis/axes unmeasurable: {', '.join(m['axis'] for m in missing)}")
    if below:
        reasons.extend(below)
    if weighted_total is not None and all_scored and weighted_total < SHIP_TOTAL_MIN:
        reasons.append(f"weighted total {weighted_total} < ship bar {SHIP_TOTAL_MIN}")
    if provisional:
        reasons.append(f"provisional (proxy) axes pending a judge: {', '.join(provisional)}")

    ship = (
        all_scored
        and not below
        and not provisional            # a proxy is not authoritative → cannot certify ship
        and weighted_total is not None
        and weighted_total >= SHIP_TOTAL_MIN
    )
    if ship:
        reasons = ["all axes >= floor and weighted total >= ship bar"]
    return ShipVerdict(
        ship=ship,
        weighted_total=weighted_total,
        weighted_total_basis=basis,
        reasons=reasons,
        missing_instrumentation=missing,
        provisional_axes=provisional,
    )
