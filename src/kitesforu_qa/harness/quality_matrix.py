"""quality_matrix.py — the MEASURED QUALITY ENGINE aggregator/ranker.

Turns a list of per-job SHORT SCORECARD results (the dict shape ``kitesforu_qa.scorecard.score_short``
returns) into:

  1. a per-axis AGGREGATE (mean/min/max/pass-rate) across the whole set and per (genre, format) group.
  2. a RANKED list of SYSTEMATIC weaknesses — axes that are consistently below their floor across MANY
     cells (a class-level failure), weighted by how many cells are affected AND how far below the bar
     they are — so a one-off low score on a single render never outranks a widespread, deep problem.
  3. an INSTRUMENTATION-GAP report — axes that are consistently ``None`` (unmeasurable), a different
     failure class from "measured but low".
  4. a MATRIX-FILL proposal — which (genre, format) cells the current job set does NOT cover, with a
     bounded $ estimate to fill them (never executed here — a proposal only).
  5. a markdown rendering of all of the above (``render_backlog_markdown``).

Pure, $0, deterministic — no network, no LLM, no Firestore. The CLI (``scripts/quality_matrix.py``)
does the Firestore query + per-job scoring (via the existing ``short_scorecard``/``score_short``
primitives — this module never rescoring, only aggregates results already produced) and hands the
resulting cell list here.

A "cell" is one job's scorecard result: the ``score_short()`` dict (``{job_id, axes, weighted_total,
ship, ...}``) plus three routing keys the caller stamps on it — ``genre``, ``format`` ("short" |
"episode"), and ``quality_tier``. A cell that failed to score entirely (fetch error, corrupt doc,
scorer exception) carries ``_scored=False`` + ``_error`` instead of ``axes`` — fail-open, so one bad
job can never crash the aggregation or the ranking.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..scorecard import signals as _signals
from ..scorecard.rubric import RUBRIC, RUBRIC_BY_NAME
from .battery import run_scorecard, scorecard_summary

# ── datetime parsing ──────────────────────────────────────────────────────────
# Firestore ``created_at`` shows up as a str (ISO8601), a naive/aware ``datetime``, or a
# ``DatetimeWithNanoseconds`` — normalise all three to an aware UTC ``datetime``. Returns ``None``
# (never raises) on anything unparseable so a single bad timestamp can't crash a query/aggregation.


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    # DatetimeWithNanoseconds and similar duck-typed timestamp objects.
    tzinfo = getattr(value, "tzinfo", None)
    if tzinfo is not None:
        return value
    if hasattr(value, "replace"):
        try:
            return value.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001 — unparseable timestamp shape, degrade to None
            return None
    return None


# ── per-axis aggregation ──────────────────────────────────────────────────────


@dataclass
class AxisAggregate:
    axis: str
    floor: int
    n_scored: int          # cells where this axis produced a real (non-None) score
    n_total_cells: int     # cells in the set overall (scored + unscored)
    mean: float | None
    min: float | None
    max: float | None
    n_below_floor: int
    pass_rate: float | None  # (n_scored - n_below_floor) / n_scored
    n_proxy: int = 0        # of the scored cells, how many carry proxy=True (non-authoritative $0
                             # heuristic standing in for a disabled/absent paid judge or a producer-side
                             # provenance contradiction — see rubric.AxisResult.proxy)

    @property
    def proxy_fraction(self) -> float | None:
        return round(self.n_proxy / self.n_scored, 3) if self.n_scored else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "floor": self.floor,
            "n_scored": self.n_scored,
            "n_total_cells": self.n_total_cells,
            "mean": self.mean,
            "min": self.min,
            "max": self.max,
            "n_below_floor": self.n_below_floor,
            "pass_rate": self.pass_rate,
            "n_proxy": self.n_proxy,
            "proxy_fraction": self.proxy_fraction,
        }


def _axis_scores(cells: list[dict[str, Any]], axis_name: str) -> list[float]:
    out: list[float] = []
    for c in cells:
        if not c.get("_scored"):
            continue
        ax = (c.get("axes") or {}).get(axis_name) or {}
        s = ax.get("score")
        if s is not None:
            out.append(float(s))
    return out


def _axis_proxy_count(cells: list[dict[str, Any]], axis_name: str) -> int:
    """How many SCORED cells' ``axis_name`` result is a ``proxy=True`` (non-authoritative) score."""
    n = 0
    for c in cells:
        if not c.get("_scored"):
            continue
        ax = (c.get("axes") or {}).get(axis_name) or {}
        if ax.get("score") is not None and ax.get("proxy"):
            n += 1
    return n


def aggregate_axis(cells: list[dict[str, Any]], axis_name: str) -> AxisAggregate:
    spec = RUBRIC_BY_NAME[axis_name]
    scores = _axis_scores(cells, axis_name)
    n_below = sum(1 for s in scores if s < spec.floor)
    mean = round(sum(scores) / len(scores), 1) if scores else None
    return AxisAggregate(
        axis=axis_name,
        floor=spec.floor,
        n_scored=len(scores),
        n_total_cells=len(cells),
        mean=mean,
        min=(round(min(scores), 1) if scores else None),
        max=(round(max(scores), 1) if scores else None),
        n_below_floor=n_below,
        pass_rate=(round((len(scores) - n_below) / len(scores), 3) if scores else None),
        n_proxy=_axis_proxy_count(cells, axis_name),
    )


def aggregate_all_axes(cells: list[dict[str, Any]]) -> dict[str, AxisAggregate]:
    """{axis_name: AxisAggregate} for all 8 rubric axes, over the whole cell set."""
    return {spec.name: aggregate_axis(cells, spec.name) for spec in RUBRIC}


# ── grouping by (genre, format) ────────────────────────────────────────────────


def group_cells(cells: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in cells:
        key = (str(c.get("genre") or "unknown"), str(c.get("format") or "unknown"))
        groups[key].append(c)
    return dict(groups)


def aggregate_by_group(cells: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, AxisAggregate]]:
    """{(genre, format): {axis_name: AxisAggregate}} — the per-cell-group breakdown."""
    return {key: aggregate_all_axes(group) for key, group in group_cells(cells).items()}


# ── ranked systematic weaknesses ──────────────────────────────────────────────
# A hand-authored (not LLM-generated — $0) hypothesis + suggested fix area per axis, grounded in how
# each axis is actually computed (axes.py). Shown alongside the REAL aggregate numbers in the backlog
# markdown, never presented as a substitute for them.
AXIS_HYPOTHESES: dict[str, dict[str, str]] = {
    "hook_stop_power": {
        "hypothesis": (
            "First beat isn't photo/high-contrast, the hook line is too long (>12 words) or opens with "
            "a banned phrase, or the first spoken word lands after 2.5s — check beat0 modality "
            "selection and the hook-line generator against narration_rules.yaml's banned-opener list."
        ),
        "fix_area": "visuals: beat0 modality selection + script: hook-line generation",
    },
    "substance_novelty": {
        "hypothesis": (
            "This axis is a $0 PROXY (research-grounding + angle-brief richness) unless --enable-judge "
            "is wired — a low mean usually means the research route resolver is choosing 'none'/'skip' "
            "too often, or the angle brief is thin (<500 chars, 0 angles)."
        ),
        "fix_area": "research: route resolver + angle-research brief generation",
    },
    "visual_truth": {
        "hypothesis": (
            "Either the VLM axis is disabled at $0 baseline (an honest null needing --vlm to escalate, "
            "not a defect) or photoreal-labeled beats are genuinely failing the photo-vs-illustration "
            "check (an image provider producing illustration output mislabeled ai_photo/scene_image)."
        ),
        "fix_area": "visuals: image-generation provider fidelity vs modality label",
    },
    "modality_mix": {
        "hypothesis": (
            "Low Shannon entropy across {ai_photo, diagram, kinetic_text, scene} beat buckets — the "
            "reel leans on one modality (commonly all kinetic_text or all photo) instead of a balanced "
            "mix. Check the Visual Director's per-beat modality allocation."
        ),
        "fix_area": "visuals: Visual Director modality allocation",
    },
    "motion_density": {
        "hypothesis": (
            "Too few beats carry engine motion provenance (render_mode in parallax/kenburns/video) "
            "relative to static stills — the render engine is defaulting to still cards. Check "
            "motion_preset selection in the render pipeline."
        ),
        "fix_area": "visuals: render engine motion_preset selection",
    },
    "sync_exactness": {
        "hypothesis": (
            "Very likely an INSTRUMENTATION GAP, not a quality regression: Phase 1a (a persisted "
            "ground-truth per-word timestamp track) has not landed on these jobs, so the axis cannot be "
            "scored at all — check missing_instrumentation coverage before treating this as a low score."
        ),
        "fix_area": "instrumentation: Phase 1a word-timestamp track",
    },
    "audio_feel": {
        "hypothesis": (
            "The spectral judge is detecting drone/hum instead of real music, or a planned music cue "
            "wasn't detected by listening-QA — check music-bed selection and the TTS/mastering final "
            "seam for drone artifacts."
        ),
        "fix_area": "audio: music-bed selection + mastering final seam",
    },
    "cost_safety": {
        "hypothesis": (
            "A hard gate: either the per-job cost rollup exceeded the per-short cap, or a safety/"
            "moderation verdict is failing/missing. ANY low score here is a priority fix — it means a "
            "cost overrun or an unmoderated render is reaching ship."
        ),
        "fix_area": "cost telemetry + safety/moderation pipeline",
    },
}


@dataclass
class SystematicWeakness:
    axis: str
    floor: int
    mean_score: float | None
    n_scored: int
    n_below_floor: int
    affected_fraction: float
    mean_gap_below_floor: float
    severity: float
    affected_job_ids: list[str]
    hypothesis: str
    fix_area: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "floor": self.floor,
            "mean_score": self.mean_score,
            "n_scored": self.n_scored,
            "n_below_floor": self.n_below_floor,
            "affected_fraction": self.affected_fraction,
            "mean_gap_below_floor": self.mean_gap_below_floor,
            "severity": self.severity,
            "affected_job_ids": self.affected_job_ids,
            "hypothesis": self.hypothesis,
            "fix_area": self.fix_area,
        }


# An axis where >= this fraction of its scored cells are proxy=True (a non-authoritative $0
# heuristic standing in for a disabled judge, e.g. substance_novelty's research-grounding proxy when
# --enable-judge is off, or a producer-side provenance contradiction) is EXCLUDED from the ranked
# "systematic weaknesses" list — see ``proxy_dominant_axes`` below. Ranking a self-declared
# non-authoritative proxy alongside fully-measured axes (identical severity math, no caveat) is
# exactly the aggregation bug the 2026-07 baseline validation found: substance_novelty's mean
# 1.2/70 ranked as the #1 severity "systematic weakness" even though ``rubric.evaluate()`` already
# treats a proxy as "provisional — cannot certify ship", never an equal-confidence measured failure.
PROXY_FRACTION_FLOOR = 0.5


def rank_systematic_weaknesses(
    cells: list[dict[str, Any]],
    agg_by_axis: dict[str, AxisAggregate] | None = None,
    *,
    proxy_fraction_floor: float = PROXY_FRACTION_FLOOR,
) -> list[SystematicWeakness]:
    """Rank axes by SEVERITY = (# cells below floor) x (mean point-gap below floor).

    An axis that is both WIDESPREAD (many affected cells) AND DEEP (far below its floor on average)
    ranks above a one-off single low score on an otherwise-fine axis — operationalising "the class-level
    failures, not one-offs". Axes with zero cells below floor (or zero scored cells) are omitted
    entirely — this ranks WEAKNESSES, not the whole rubric. An axis whose scored cells are MOSTLY
    ``proxy=True`` (>= ``proxy_fraction_floor``, default 50%) is ALSO omitted here — a proxy score is
    a non-authoritative $0 heuristic standing in for a disabled paid judge (or a producer-side
    provenance contradiction), so a low proxy mean cannot be presented as a validated, class-level
    PIPELINE failure with the same confidence as a fully-measured axis. See ``proxy_dominant_axes``
    for where these are surfaced instead (a distinct failure class, same pattern as
    ``instrumentation_gaps``).
    """
    agg_by_axis = agg_by_axis or aggregate_all_axes(cells)
    out: list[SystematicWeakness] = []
    for spec in RUBRIC:
        agg = agg_by_axis.get(spec.name)
        if agg is None or agg.n_scored == 0 or agg.n_below_floor == 0:
            continue
        pf = agg.proxy_fraction
        if pf is not None and pf >= proxy_fraction_floor:
            continue
        gaps: list[float] = []
        job_ids: list[str] = []
        for c in cells:
            if not c.get("_scored"):
                continue
            ax = (c.get("axes") or {}).get(spec.name) or {}
            s = ax.get("score")
            if s is None or s >= spec.floor:
                continue
            gaps.append(spec.floor - float(s))
            job_ids.append(str(c.get("job_id") or "unknown"))
        if not gaps:
            continue
        mean_gap = round(sum(gaps) / len(gaps), 1)
        affected_fraction = round(len(job_ids) / agg.n_scored, 3)
        severity = round(len(job_ids) * mean_gap, 1)
        hyp = AXIS_HYPOTHESES.get(spec.name, {})
        out.append(
            SystematicWeakness(
                axis=spec.name,
                floor=spec.floor,
                mean_score=agg.mean,
                n_scored=agg.n_scored,
                n_below_floor=len(job_ids),
                affected_fraction=affected_fraction,
                mean_gap_below_floor=mean_gap,
                severity=severity,
                affected_job_ids=job_ids,
                hypothesis=hyp.get("hypothesis", "no hypothesis authored for this axis yet"),
                fix_area=hyp.get("fix_area", "unassigned"),
            )
        )
    out.sort(key=lambda w: (-w.severity, -w.n_below_floor, -w.mean_gap_below_floor))
    return out


# ── instrumentation gaps (axes that are NULL, not low — a different failure class) ────────


@dataclass
class InstrumentationGap:
    axis: str
    n_measured: int
    n_total_scored_cells: int
    coverage: float  # n_measured / n_total_scored_cells

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "n_measured": self.n_measured,
            "n_total_scored_cells": self.n_total_scored_cells,
            "coverage": self.coverage,
        }


def instrumentation_gaps(
    cells: list[dict[str, Any]],
    agg_by_axis: dict[str, AxisAggregate] | None = None,
    *,
    coverage_floor: float = 0.5,
) -> list[InstrumentationGap]:
    """Axes whose measurement COVERAGE (fraction of scored cells with a real, non-None score) is below
    ``coverage_floor`` — e.g. sync_exactness at 0% because Phase 1a hasn't landed. Sorted worst-coverage
    first. Cells that failed to score entirely (``_scored=False``) are excluded from the denominator —
    this reports on axes within jobs that DID score, not on total job-fetch failures."""
    agg_by_axis = agg_by_axis or aggregate_all_axes(cells)
    n_scored_cells = sum(1 for c in cells if c.get("_scored"))
    out: list[InstrumentationGap] = []
    for spec in RUBRIC:
        agg = agg_by_axis.get(spec.name)
        if agg is None or n_scored_cells == 0:
            continue
        coverage = round(agg.n_scored / n_scored_cells, 3)
        if coverage < coverage_floor:
            out.append(
                InstrumentationGap(
                    axis=spec.name,
                    n_measured=agg.n_scored,
                    n_total_scored_cells=n_scored_cells,
                    coverage=coverage,
                )
            )
    out.sort(key=lambda g: g.coverage)
    return out


# ── proxy-dominant axes (score is a non-authoritative $0 heuristic, not a validated failure) ──


@dataclass
class ProxyAxis:
    axis: str
    mean_score: float | None
    n_proxy: int
    n_scored: int
    proxy_fraction: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "mean_score": self.mean_score,
            "n_proxy": self.n_proxy,
            "n_scored": self.n_scored,
            "proxy_fraction": self.proxy_fraction,
        }


def proxy_dominant_axes(
    cells: list[dict[str, Any]],
    agg_by_axis: dict[str, AxisAggregate] | None = None,
    *,
    proxy_fraction_floor: float = PROXY_FRACTION_FLOOR,
) -> list[ProxyAxis]:
    """Axes where >= ``proxy_fraction_floor`` (default 50%) of scored cells are ``proxy=True`` — a
    DIFFERENT failure class from both "measured and failing" (``rank_systematic_weaknesses``) and
    "unmeasurable" (``instrumentation_gaps``): the axis DID produce a number, but that number is a
    non-authoritative $0 heuristic (a disabled paid judge's proxy, or a producer-side provenance
    contradiction) — ``rubric.evaluate()`` already refuses to certify SHIP on a proxy for exactly
    this reason. Sorted worst-mean-score first so the most alarming-looking (but least trustworthy)
    proxy leads the section."""
    agg_by_axis = agg_by_axis or aggregate_all_axes(cells)
    out: list[ProxyAxis] = []
    for spec in RUBRIC:
        agg = agg_by_axis.get(spec.name)
        if agg is None or agg.n_scored == 0:
            continue
        pf = agg.proxy_fraction
        if pf is None or pf < proxy_fraction_floor:
            continue
        out.append(
            ProxyAxis(
                axis=spec.name,
                mean_score=agg.mean,
                n_proxy=agg.n_proxy,
                n_scored=agg.n_scored,
                proxy_fraction=pf,
            )
        )
    out.sort(key=lambda p: (p.mean_score if p.mean_score is not None else 0.0))
    return out


# ── proposed matrix fill (genre x format gaps — NEVER executes anything) ──────

DEFAULT_TARGET_GENRES: tuple[str, ...] = (
    "explainer", "storytelling", "educational", "horror",
    "interview", "documentary", "comedy", "romance",
)
DEFAULT_TARGET_FORMATS: tuple[str, ...] = ("short", "episode")
T3_COST_PER_CELL_USD = 0.025           # 10s quality_tier=low cell (create_verification_job.sh default)
T4_REPRESENTATIVE_RANGE_USD = (0.15, 1.30)  # a 30-60s medium/high-tier representative cell


@dataclass
class MatrixCell:
    genre: str
    format: str
    covered: bool
    n_cells: int
    tiers_covered: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "genre": self.genre,
            "format": self.format,
            "covered": self.covered,
            "n_cells": self.n_cells,
            "tiers_covered": self.tiers_covered,
        }


def propose_matrix_fill(
    cells: list[dict[str, Any]],
    *,
    target_genres: tuple[str, ...] = DEFAULT_TARGET_GENRES,
    target_formats: tuple[str, ...] = DEFAULT_TARGET_FORMATS,
) -> dict[str, Any]:
    """Cross the TARGET genre x format grid against what the current cell set actually covers.
    Returns the full grid (so callers can see both hits and gaps) + a bounded T3/T4 cost estimate to
    fill the gaps. Computes numbers ONLY — never creates a job (that is the founder's ack'd next step,
    via ``scripts/create_verification_job.sh``)."""
    groups = group_cells([c for c in cells if c.get("_scored")])
    grid: list[MatrixCell] = []
    for genre in target_genres:
        for fmt in target_formats:
            group = groups.get((genre, fmt), [])
            tiers = sorted({str(c.get("quality_tier") or "unknown") for c in group})
            grid.append(MatrixCell(genre=genre, format=fmt, covered=bool(group), n_cells=len(group), tiers_covered=tiers))
    missing = [g for g in grid if not g.covered]
    t3_estimate = round(len(missing) * T3_COST_PER_CELL_USD, 3)
    return {
        "target_genres": list(target_genres),
        "target_formats": list(target_formats),
        "grid": [g.to_dict() for g in grid],
        "target_cells": len(grid),
        "covered_cells": len(grid) - len(missing),
        "missing_cells": [{"genre": g.genre, "format": g.format} for g in missing],
        "t3_fill_estimate_usd": t3_estimate,
        "t4_representative_range_usd": list(T4_REPRESENTATIVE_RANGE_USD),
        "note": (
            "T3 = a 10s quality_tier=low cell per gap (~$0.025/ea, no founder ack needed) via "
            "scripts/create_verification_job.sh. T4 = one representative 30-60s medium/high-tier cell "
            "per gap for premium-only behaviors (founder-ack gated, touch .claude/FOUNDER_SPEND_ACK). "
            "This function only COMPUTES the estimate — it never creates a job."
        ),
    }


# ── markdown rendering ─────────────────────────────────────────────────────────


def _fmt_score(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def _axis_summary_table(agg_by_axis: dict[str, AxisAggregate]) -> str:
    lines = [
        "| Axis | Floor | Mean | Min | Max | Scored/Total | Pass rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for spec in RUBRIC:
        a = agg_by_axis[spec.name]
        pass_rate = "—" if a.pass_rate is None else f"{a.pass_rate * 100:.0f}%"
        lines.append(
            f"| {spec.title} (`{a.axis}`) | {a.floor} | {_fmt_score(a.mean)} | {_fmt_score(a.min)} | "
            f"{_fmt_score(a.max)} | {a.n_scored}/{a.n_total_cells} | {pass_rate} |"
        )
    return "\n".join(lines)


def _group_table(groups: dict[tuple[str, str], dict[str, AxisAggregate]]) -> str:
    if not groups:
        return "_no cells to group._"
    lines = ["| Genre | Format | " + " | ".join(spec.title for spec in RUBRIC) + " | n |", "|---|---|" + "---|" * (len(RUBRIC) + 1)]
    for (genre, fmt), agg in sorted(groups.items()):
        n = next(iter(agg.values())).n_total_cells if agg else 0
        cells_str = " | ".join(_fmt_score(agg[spec.name].mean) for spec in RUBRIC)
        lines.append(f"| {genre} | {fmt} | {cells_str} | {n} |")
    return "\n".join(lines)


def _weakness_section(weaknesses: list[SystematicWeakness], top_n: int) -> str:
    if not weaknesses:
        return "_no axis has any cell below its floor — nothing systematic to rank._"
    out = []
    for i, w in enumerate(weaknesses[:top_n], start=1):
        sample_ids = ", ".join(w.affected_job_ids[:8])
        more = f" (+{len(w.affected_job_ids) - 8} more)" if len(w.affected_job_ids) > 8 else ""
        out.append(
            f"### {i}. `{w.axis}` — severity {w.severity:.1f}\n\n"
            f"- **Mean score:** {_fmt_score(w.mean_score)} (floor {w.floor})\n"
            f"- **Affected:** {w.n_below_floor}/{w.n_scored} scored cells ({w.affected_fraction * 100:.0f}%), "
            f"mean gap below floor: {w.mean_gap_below_floor:.1f} pts\n"
            f"- **Hypothesis:** {w.hypothesis}\n"
            f"- **Suggested fix area:** {w.fix_area}\n"
            f"- **Affected job_ids:** {sample_ids}{more}\n"
        )
    return "\n".join(out)


def _instrumentation_section(gaps: list[InstrumentationGap]) -> str:
    if not gaps:
        return "_every axis has >= 50% measurement coverage across scored cells._"
    lines = ["| Axis | Measured | Coverage |", "|---|---|---|"]
    for g in gaps:
        lines.append(f"| `{g.axis}` | {g.n_measured}/{g.n_total_scored_cells} | {g.coverage * 100:.0f}% |")
    return "\n".join(lines)


def _proxy_section(proxies: list[ProxyAxis]) -> str:
    if not proxies:
        return "_no axis is majority-proxy — every ranked weakness above is a fully-measured score._"
    lines = [
        "| Axis | Mean score | Proxy cells | Proxy fraction |",
        "|---|---|---|---|",
    ]
    for p in proxies:
        lines.append(
            f"| `{p.axis}` | {_fmt_score(p.mean_score)} | {p.n_proxy}/{p.n_scored} | "
            f"{p.proxy_fraction * 100:.0f}% |"
        )
    lines.append("")
    lines.append(
        "A **proxy** score is a non-authoritative $0 heuristic standing in for a disabled paid "
        "judge/VLM (e.g. substance_novelty's research-grounding proxy when `--enable-judge` is "
        "off) or a producer-side provenance contradiction the axis detected in its own inputs "
        "(e.g. motion_density when the renderer's own telemetry disagrees with the render_mode "
        "signal). These axes are deliberately EXCLUDED from Top Ranked Systematic Weaknesses above "
        "— a low mean here may be a REAL pipeline gap or may just reflect the disabled judge; "
        "escalate the relevant flag (`--enable-judge` / `--vlm`) before treating it as a validated "
        "class-level failure. `rubric.evaluate()` already refuses to certify SHIP on any proxy axis "
        "for the same reason."
    )
    return "\n".join(lines)


def _unscored_section(cells: list[dict[str, Any]]) -> str:
    unscored = [c for c in cells if not c.get("_scored")]
    if not unscored:
        return "_none — every job in this run produced a scorecard._"
    lines = ["| job_id | error |", "|---|---|"]
    for c in unscored[:30]:
        lines.append(f"| {c.get('job_id', 'unknown')} | {c.get('_error', 'unknown')} |")
    more = f"\n\n_(+{len(unscored) - 30} more unscored jobs, truncated)_" if len(unscored) > 30 else ""
    return "\n".join(lines) + more


def _matrix_fill_section(matrix_fill: dict[str, Any]) -> str:
    missing = matrix_fill["missing_cells"]
    lines = [
        f"Target grid: {len(matrix_fill['target_genres'])} genres x {len(matrix_fill['target_formats'])} "
        f"formats = {matrix_fill['target_cells']} cells. Covered by this run's cell set: "
        f"**{matrix_fill['covered_cells']}/{matrix_fill['target_cells']}**.",
        "",
        matrix_fill["note"],
        "",
    ]
    if missing:
        lines.append("**Missing cells (no scored job in the current set):**")
        lines.append("")
        lines.append("| Genre | Format |")
        lines.append("|---|---|")
        for m in missing:
            lines.append(f"| {m['genre']} | {m['format']} |")
        lines.append("")
        lines.append(
            f"**Estimated cost to fill all {len(missing)} gaps at T3 (10s, quality_tier=low, no ack "
            f"needed):** ~${matrix_fill['t3_fill_estimate_usd']:.3f}. "
            f"**T4 representative-cell validation** (premium-tier/longer-form behaviors only, "
            f"founder-ack gated): ~${matrix_fill['t4_representative_range_usd'][0]:.2f}–"
            f"${matrix_fill['t4_representative_range_usd'][1]:.2f} per gap."
        )
        lines.append("")
        lines.append(
            "**This section is a proposal only — no job has been created.** Filling these gaps is the "
            "founder's ack'd next step (`touch .claude/FOUNDER_SPEND_ACK` then "
            "`kitesforu-qa/scripts/create_verification_job.sh`)."
        )
    else:
        lines.append("Every target (genre, format) cell is covered by this run's cell set.")
    return "\n".join(lines)


def render_backlog_markdown(
    cells: list[dict[str, Any]],
    *,
    generated_at: str,
    project: str,
    mode: str,
    top_n: int = 5,
    target_genres: tuple[str, ...] = DEFAULT_TARGET_GENRES,
    target_formats: tuple[str, ...] = DEFAULT_TARGET_FORMATS,
) -> str:
    """Render the full QUALITY_BACKLOG.md content from a scored cell set. Deterministic given the same
    ``cells`` input (idempotent) — safe to overwrite on every run."""
    agg_by_axis = aggregate_all_axes(cells)
    weaknesses = rank_systematic_weaknesses(cells, agg_by_axis)
    gaps = instrumentation_gaps(cells, agg_by_axis)
    proxies = proxy_dominant_axes(cells, agg_by_axis)
    groups = aggregate_by_group(cells)
    matrix_fill = propose_matrix_fill(cells, target_genres=target_genres, target_formats=target_formats)

    n_total = len(cells)
    n_scored = sum(1 for c in cells if c.get("_scored"))

    return f"""# QUALITY_BACKLOG — Measured Quality Engine

Generated: {generated_at} · Project: `{project}` · Mode: `{mode}`
Cells: {n_scored}/{n_total} scored (the rest failed to score — see Unscored Jobs below; fail-open, not
a crash).

This backlog is produced by `scripts/quality_matrix.py` — a $0/T1 aggregation of the existing
8-axis SHORT SCORECARD (`kitesforu_qa.scorecard.score_short`) over a set of real completed jobs.
No new jobs were generated to produce this baseline.

## Baseline Summary (per axis, aggregated across all scored cells)

{_axis_summary_table(agg_by_axis)}

## Per (Genre x Format) Breakdown — mean score per axis

{_group_table(groups)}

## Top Ranked Systematic Weaknesses

Ranked by **severity = (# cells below floor) x (mean point-gap below floor)** — widespread AND deep
problems rank first; a single one-off low score on an otherwise-fine axis will not appear here.

{_weakness_section(weaknesses, top_n)}

## Non-Authoritative Proxy Axes (score is a $0 heuristic, not a validated failure)

{_proxy_section(proxies)}

## Instrumentation Gaps (axis is UNMEASURABLE, not low — a different failure class)

{_instrumentation_section(gaps)}

## Unscored Jobs (fetch/score failures — fail-open, never crashed the run)

{_unscored_section(cells)}

## PROPOSED MATRIX FILL (needs founder ack)

{_matrix_fill_section(matrix_fill)}
"""


# ══════════════════════════════════════════════════════════════════════════════════════════════
# EPISODES + COURSES — extends the Measured Quality Engine beyond 9:16 shorts (2026-07-09).
# ══════════════════════════════════════════════════════════════════════════════════════════════
#
# The 8-axis SHORT SCORECARD above is deliberately 9:16-specific: modality_mix/motion_density/
# visual_truth are shot-language axes for a scrolling short, and hook_stop_power/sync_exactness
# assume a single-beat-per-second vertical reel. None of that is the right yardstick for a
# 16:9/audio EPISODE (podcasts) or a COURSE module (corporate training — "every lesson IS a podcast
# job", see kitesforu-docs' course-revamp memory) — those are audio-first, longer-form, and often
# carry NO 9:16 visual beats at all.
#
# So this extension does NOT invent a parallel fixed rubric for episodes/courses. It reuses the
# harness's own GENERAL battery (``battery.run_scorecard``), which already runs the RIGHT
# measurement per genre: ``structure`` (is this a real completed render), ``content`` (per-genre
# script quality — hook/substance/examples/attribution/dread/etc, genre-gated via ``checks_for``),
# ``audio-mix`` (clipping/loudness/silence/truncation/channel-balance — the real audio_feel),
# ``cost-correctness`` (the TTS-cost reconciliation invariant), and — only when the episode/course
# opted into visuals — ``visual-images``/``video-sync``/``music-sfx``. A pure audio-only podcast
# simply skip()s the visual dimensions (not a failure, not fabricated) and is scored on
# structure+content+audio-mix+cost-correctness alone.
#
# The granular unit here is the individual CHECK (138 registered today, vs the short scorecard's
# fixed 8 axes) — ranking "systematic weaknesses" at the check_id level is the natural granularity
# for a battery this rich, mirroring how the short engine ranks by axis: SEVERITY = how WIDESPREAD
# (how many jobs fail it) x how SEVERE (the check's own authored severity: critical/high/medium/
# low), so a widespread + severe class-level bug ranks above a single job's one-off low mark.

CONTENT_CLASS_SHORT = "short"
CONTENT_CLASS_EPISODE = "episode"
CONTENT_CLASS_COURSE = "course"


def detect_content_class(art: Any) -> str:
    """course | episode | short — the routing key for the episodes/courses extension.

    'course' wins over 'short': a course episode is a regular ``podcast_jobs`` doc stamped
    ``parent_type=='course'`` by the course pipeline's job-creation client ("a course episode IS a
    podcast job" — kitesforu-course-workers' ``podcast_api_client.py`` / the live course pipeline in
    kitesforu-workers). In practice a course module is never also short-classified, so ordering only
    matters defensively. Everything else non-short is a regular 'episode' (this also covers
    ``parent_type=='class'`` children — they are not shorts and not courses, so they fall into the
    general 'episode' bucket rather than a third dedicated class)."""
    parent_type = str(art.doc.get("parent_type") or "").strip().lower()
    if parent_type == "course":
        return CONTENT_CLASS_COURSE
    if _signals.is_short(art):
        return CONTENT_CLASS_SHORT
    return CONTENT_CLASS_EPISODE


def score_episode_or_course(art: Any) -> dict[str, Any]:
    """Score one EPISODE or COURSE artifact via the harness's GENERAL battery (``run_scorecard``) —
    REUSED, never reimplemented. $0/T1: every check is deterministic Python over the job doc (+ any
    locally-resolved audio/video) — nothing here calls an LLM/VLM/judge.

    Returns a "cell" analogous to ``score_short()``'s ``{job_id, axes, ...}`` shape, but keyed by
    check_id rather than a fixed axis rubric: ``{job_id, genre, content_class, quality_tier,
    overall_passed, dimensions, checks, n_checks_total, n_checks_failed, _scored}``. ``checks`` is
    the FLAT list of every NON-skipped ``CheckResult`` dict this artifact's genre applies — a skip
    means "not applicable" (e.g. no audio on this doc), not a measurement, so it must never enter a
    fail-rate denominator downstream."""
    scorecard = run_scorecard(art)
    summary = scorecard_summary(scorecard)
    checks_flat: list[dict[str, Any]] = [
        c for stage in scorecard.values() for c in stage.data.get("checks", []) if not c.get("skipped")
    ]
    return {
        "job_id": art.job_id,
        "genre": art.genre,
        "content_class": detect_content_class(art),
        "quality_tier": _signals.job_quality_tier(art),
        "overall_passed": summary["passed"],
        "dimensions": summary["dimensions"],
        "checks": checks_flat,
        "n_checks_total": summary["total_checks"],
        "n_checks_failed": summary["total_failed"],
        "_scored": True,
    }


def group_cells_by_content_class(cells: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """{content_class: [cell, ...]} — the episode/course analogue of ``group_cells``."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cells:
        groups[str(c.get("content_class") or "unknown")].append(c)
    return dict(groups)


# ── per-check aggregation (the check-level analogue of AxisAggregate) ────────────────────────────

_SEVERITY_WEIGHT: dict[str, float] = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}


@dataclass
class CheckAggregate:
    check_id: str
    dimension: str
    severity: str
    genre: str | None
    n_applicable: int          # non-skipped instances across scored cells (this check actually ran)
    n_failed: int
    fail_rate: float | None    # n_failed / n_applicable
    mean_score: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id, "dimension": self.dimension, "severity": self.severity,
            "genre": self.genre, "n_applicable": self.n_applicable, "n_failed": self.n_failed,
            "fail_rate": self.fail_rate, "mean_score": self.mean_score,
        }


def _check_instances(cells: list[dict[str, Any]], check_id: str) -> list[dict[str, Any]]:
    """Every non-skipped CheckResult dict for ``check_id`` across scored cells (``score_episode_or_
    course`` already drops skipped instances, so every entry here is a real applicable measurement)."""
    return [
        chk for c in cells if c.get("_scored")
        for chk in c.get("checks", []) if chk.get("check_id") == check_id
    ]


def aggregate_check(cells: list[dict[str, Any]], check_id: str) -> CheckAggregate:
    instances = _check_instances(cells, check_id)
    n = len(instances)
    n_failed = sum(1 for i in instances if not i.get("passed"))
    scores = [float(i["score"]) for i in instances if i.get("score") is not None]
    return CheckAggregate(
        check_id=check_id,
        dimension=str(instances[0].get("dimension")) if instances else "unknown",
        severity=str(instances[0].get("severity")) if instances else "medium",
        genre=(instances[0].get("genre") if instances else None),
        n_applicable=n,
        n_failed=n_failed,
        fail_rate=(round(n_failed / n, 3) if n else None),
        mean_score=(round(sum(scores) / len(scores), 3) if scores else None),
    )


def aggregate_all_checks(cells: list[dict[str, Any]]) -> dict[str, CheckAggregate]:
    """{check_id: CheckAggregate} over every check_id that appears in >= 1 scored cell — NOT the
    full 138-check registry (the applicable set differs per genre; a check that never ran on this
    cell set has no aggregate, exactly like an axis that was never scored has no meaningful mean)."""
    ids = sorted({chk["check_id"] for c in cells if c.get("_scored") for chk in c.get("checks", [])})
    return {cid: aggregate_check(cells, cid) for cid in ids}


@dataclass
class SystematicCheckFailure:
    check_id: str
    dimension: str
    severity: str
    n_failed: int
    n_applicable: int
    fail_rate: float
    severity_weighted: float
    affected_job_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id, "dimension": self.dimension, "severity": self.severity,
            "n_failed": self.n_failed, "n_applicable": self.n_applicable, "fail_rate": self.fail_rate,
            "severity_weighted": self.severity_weighted, "affected_job_ids": self.affected_job_ids,
        }


def rank_systematic_check_failures(
    cells: list[dict[str, Any]],
    agg_by_check: dict[str, CheckAggregate] | None = None,
    *,
    min_applicable: int = 2,
) -> list[SystematicCheckFailure]:
    """Rank check_ids by SEVERITY = (# jobs failing) x (the check's own authored severity weight:
    critical=4/high=3/medium=2/low=1) — a widespread AND severe class-level bug outranks a single
    job's one-off low mark on an otherwise-fine check, mirroring ``rank_systematic_weaknesses``'s
    "widespread AND deep" principle at check granularity. ``min_applicable`` guards against ranking
    a check that only ever ran once (100% fail rate on n=1 is not evidence of a class-level bug)."""
    agg_by_check = agg_by_check or aggregate_all_checks(cells)
    out: list[SystematicCheckFailure] = []
    for cid, agg in agg_by_check.items():
        if agg.n_applicable < min_applicable or agg.n_failed == 0:
            continue
        weight = _SEVERITY_WEIGHT.get(agg.severity, 1.0)
        severity_weighted = round(agg.n_failed * weight, 1)
        job_ids = sorted({
            c["job_id"] for c in cells if c.get("_scored")
            for chk in c.get("checks", [])
            if chk.get("check_id") == cid and not chk.get("passed")
        })
        out.append(
            SystematicCheckFailure(
                check_id=cid, dimension=agg.dimension, severity=agg.severity,
                n_failed=agg.n_failed, n_applicable=agg.n_applicable,
                fail_rate=agg.fail_rate or 0.0, severity_weighted=severity_weighted,
                affected_job_ids=job_ids,
            )
        )
    out.sort(key=lambda w: (-w.severity_weighted, -w.n_failed, -w.fail_rate))
    return out


# ── per-dimension aggregation (the dimension-level rollup — content/structure/audio-mix/...) ─────


@dataclass
class DimensionAggregate:
    dimension: str
    n_cells: int            # scored cells where >= 1 check in this dimension applied (non-skipped)
    n_total_cells: int       # all scored cells in the set
    mean_score: float | None   # mean CheckResult.score over every applicable check in this dimension
    pass_rate: float | None    # fraction of n_cells whose StageResult.passed was True
    n_checks_total: int
    n_checks_failed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension, "n_cells": self.n_cells, "n_total_cells": self.n_total_cells,
            "mean_score": self.mean_score, "pass_rate": self.pass_rate,
            "n_checks_total": self.n_checks_total, "n_checks_failed": self.n_checks_failed,
        }


def aggregate_dimension(cells: list[dict[str, Any]], dimension: str) -> DimensionAggregate:
    scored = [c for c in cells if c.get("_scored")]
    cells_with_dim = [c for c in scored if any(chk.get("dimension") == dimension for chk in c.get("checks", []))]
    dim_checks = [chk for c in scored for chk in c.get("checks", []) if chk.get("dimension") == dimension]
    scores = [float(chk["score"]) for chk in dim_checks if chk.get("score") is not None]
    n_pass = sum(
        1 for c in cells_with_dim if bool((c.get("dimensions") or {}).get(dimension, {}).get("passed"))
    )
    return DimensionAggregate(
        dimension=dimension,
        n_cells=len(cells_with_dim),
        n_total_cells=len(scored),
        mean_score=(round(sum(scores) / len(scores), 3) if scores else None),
        pass_rate=(round(n_pass / len(cells_with_dim), 3) if cells_with_dim else None),
        n_checks_total=len(dim_checks),
        n_checks_failed=sum(1 for chk in dim_checks if not chk.get("passed")),
    )


def aggregate_all_dimensions(cells: list[dict[str, Any]]) -> dict[str, DimensionAggregate]:
    """{dimension: DimensionAggregate} over every dimension that produced >= 1 applicable check."""
    dims = sorted({chk["dimension"] for c in cells if c.get("_scored") for chk in c.get("checks", [])})
    return {d: aggregate_dimension(cells, d) for d in dims}


# ── markdown rendering (episodes/courses section — inserted, never overwrites the short baseline) ──


def _dimension_table(agg_by_dim: dict[str, DimensionAggregate]) -> str:
    if not agg_by_dim:
        return "_no dimension produced any applicable check for this content-class (nothing scored yet)._"
    lines = [
        "| Dimension | Mean score | Pass rate | Checks (failed/total) | Cells with checks |",
        "|---|---|---|---|---|",
    ]
    for dim in sorted(agg_by_dim):
        a = agg_by_dim[dim]
        mean = "—" if a.mean_score is None else f"{a.mean_score:.2f}"
        pr = "—" if a.pass_rate is None else f"{a.pass_rate * 100:.0f}%"
        lines.append(
            f"| `{dim}` | {mean} | {pr} | {a.n_checks_failed}/{a.n_checks_total} | "
            f"{a.n_cells}/{a.n_total_cells} |"
        )
    return "\n".join(lines)


def _check_failure_section(weaknesses: list[SystematicCheckFailure], top_n: int) -> str:
    if not weaknesses:
        return "_no check has any failing instance — nothing systematic to rank (or too few scored jobs)._"
    out = []
    for i, w in enumerate(weaknesses[:top_n], start=1):
        sample_ids = ", ".join(w.affected_job_ids[:8])
        more = f" (+{len(w.affected_job_ids) - 8} more)" if len(w.affected_job_ids) > 8 else ""
        out.append(
            f"### {i}. `{w.check_id}` ({w.dimension}, severity={w.severity}) — weighted "
            f"{w.severity_weighted:.1f}\n\n"
            f"- **Failing:** {w.n_failed}/{w.n_applicable} applicable cells "
            f"({w.fail_rate * 100:.0f}%)\n"
            f"- **Affected job_ids:** {sample_ids}{more}\n"
        )
    return "\n".join(out)


def _content_class_unscored_section(cells: list[dict[str, Any]]) -> str:
    unscored = [c for c in cells if not c.get("_scored")]
    if not unscored:
        return ""
    lines = ["", "### Unscored jobs (fetch/score failures — fail-open)", "", "| job_id | error |", "|---|---|"]
    for c in unscored[:20]:
        lines.append(f"| {c.get('job_id', 'unknown')} | {c.get('_error', 'unknown')} |")
    more = f"\n\n_(+{len(unscored) - 20} more unscored, truncated)_" if len(unscored) > 20 else ""
    return "\n".join(lines) + more


def _content_class_section(cells: list[dict[str, Any]], *, label: str, top_n: int) -> str:
    scored = [c for c in cells if c.get("_scored")]
    return "\n".join(
        [
            f"## {label.upper()} — {len(scored)}/{len(cells)} scored",
            "",
            "### Dimension baseline",
            "",
            _dimension_table(aggregate_all_dimensions(cells)),
            "",
            f"### Top {top_n} ranked systematic check failures",
            "",
            _check_failure_section(rank_systematic_check_failures(cells), top_n),
            _content_class_unscored_section(cells),
        ]
    )


def render_episode_course_section(
    cells: list[dict[str, Any]],
    *,
    generated_at: str,
    project: str,
    mode: str,
    top_n: int = 5,
) -> str:
    """The EPISODES + COURSES section BODY (no upsert markers — the CLI wraps this with
    ``upsert_markdown_section``). Deterministic given the same ``cells`` input."""
    by_class = group_cells_by_content_class(cells)
    episodes = by_class.get(CONTENT_CLASS_EPISODE, [])
    courses = by_class.get(CONTENT_CLASS_COURSE, [])
    n_total = len(cells)
    n_scored = sum(1 for c in cells if c.get("_scored"))

    return f"""# Measured Quality Engine — EPISODES + COURSES

Generated: {generated_at} · Project: `{project}` · Mode: `{mode}`
Cells: {n_scored}/{n_total} scored.

This section is produced by `scripts/quality_matrix.py --content-class episodes` — a $0/T1
aggregation of the harness's GENERAL check battery (`kitesforu_qa.harness.battery.run_scorecard`,
138 checks across structure/content/audio-mix/cost-correctness/visual-images/video-sync/music-sfx)
over real completed EPISODE + COURSE jobs. Course jobs are `podcast_jobs` docs stamped
`parent_type=='course'` — "a course episode IS a podcast job". Unlike the 9:16 SHORT SCORECARD
above, there is no fixed 8-axis rubric here: the granular unit is the individual CHECK, ranked by
how widespread + severe its failures are across the scored set. No new jobs were generated to
produce this baseline.

{_content_class_section(episodes, label="episodes", top_n=top_n)}

{_content_class_section(courses, label="courses", top_n=top_n)}
"""


EPISODES_COURSES_SECTION_START = (
    "<!-- MEASURED_QUALITY_ENGINE:EPISODES_COURSES:START "
    "(auto-generated by scripts/quality_matrix.py --content-class episodes; do not hand-edit) -->"
)
EPISODES_COURSES_SECTION_END = "<!-- MEASURED_QUALITY_ENGINE:EPISODES_COURSES:END -->"


def upsert_markdown_section(
    existing_text: str,
    section_body: str,
    *,
    start_marker: str,
    end_marker: str,
) -> str:
    """Insert/replace a MARKED block inside an existing markdown document without touching anything
    outside the markers — how the episodes/courses baseline coexists with the short baseline in the
    SAME QUALITY_BACKLOG.md without clobbering it (each is idempotent + deterministic on its own:
    the short engine overwrites the whole file when run in ``--content-class short`` mode; this
    upsert only ever replaces its own marked block within whatever the short engine last wrote).
    A file with no existing markers gets the block appended; re-running replaces only the
    previously-inserted block, never anything else."""
    block = f"{start_marker}\n\n{section_body}\n\n{end_marker}\n"
    if start_marker in existing_text and end_marker in existing_text:
        pre, _, rest = existing_text.partition(start_marker)
        _, _, post = rest.partition(end_marker)
        pre = pre.rstrip("\n")
        sep = "\n\n" if pre else ""
        post = post.lstrip("\n")
        return f"{pre}{sep}{block}{post}"
    if not existing_text.strip():
        return block
    sep = "" if existing_text.endswith("\n\n") else ("\n" if existing_text.endswith("\n") else "\n\n")
    return f"{existing_text}{sep}{block}"
