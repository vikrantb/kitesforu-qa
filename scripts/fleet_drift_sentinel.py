#!/usr/bin/env python3
"""fleet_drift_sentinel.py — standing DARK-FEATURE / fleet-drift detector ($0, deterministic).

WHY THIS EXISTS (2026-07 motion incident): since ~07-23 ZERO jobs surfaced parallax/kenburns
motion and nothing alerted — a founder complaint was the only alarm. Worse, the short visual
gate hard-failed shorts for the exact monotony the pipeline itself manufactured: a gate failing
~100% of gated jobs is itself an unread alarm. This sentinel makes both classes of silent drift
mechanically visible:

* FEATURE COLLAPSE  — something the fleet used to produce (motion clips, music beds, video
  surfacing, figures on writeups) silently stops appearing ("dark feature").
* FEATURE SPIKE     — a failure reason / retry counter / cost that used to be rare explodes.
* GATE-META         — a QA gate axis failing >= 80% of gated jobs in the trailing window: the
  gate is punishing a symptom the pipeline manufactures ("gate-punishing-manufactured-symptom").
* TRANSITION DATE   — for each flagged metric, daily buckets are bisected to the first collapsed
  day and correlated against Cloud Run worker deploy timestamps → 2-3 suspect revisions.

SEVERITY IS DIRECTIONALITY-AWARE. For BAD-SIGNAL families (failure_reason:* except "none",
status:failed*, retried:*, gate:*, clips_all_same_hash) a COLLAPSE is an IMPROVEMENT →
severity INFO, never exit 1 (stripping a prevalent failure must not gate deploys for a week);
their SPIKES are CRITICAL (a failure family exploding IS the incident). Good-signal collapses
stay CRITICAL. Cost spikes (cost_usd / credits_used mean or p95 >= 2.5x with volume) are
CRITICAL too — a fleet-wide burn must gate the round, not rot in an unread report.

COST DILUTION LIMIT (honest): ONE burned job among ~70 moves neither the window mean nor the
p95 (p95 needs roughly n/20 affected jobs to shift). The max-vs-prior-p95 OUTLIER channel
(trailing max >= 10x prior p95 → WARNING, non-gating) exists for exactly that single-job case;
per-job spend caps remain the pipeline cost guardrails' job, not this fleet-level sentinel's.

EXPECTED CHANGES ACK: deliberate flips (feature removals, flag changes, QA campaigns) are muted
via scratch/reports/drift/ack.json — {"acks": [{"metric": "motion:parallax", "until":
"2026-08-01", "segment": "short", "note": "flag flip PR #123"}]}. Metric supports fnmatch
globs; segment/note optional. A matching, unexpired ack demotes the finding to INFO (still
reported + marked acked, never exit 1). Entries expire automatically after their date.

APPLICABILITY: output-shaped features only count where they CAN occur — motion:* and
video_url_present are computed only over clip-bearing jobs (clips_present is the denominator),
so an audio-only/QA-campaign mix shift shows as ONE mix-level clips_present finding instead of
a false motion collapse fan-out. podcast_all findings that duplicate an identical short/episode
finding are deduped (podcast_all only surfaces what per-segment volume guards would hide).

TENET-9 / COST: strictly read-only + $0 — Firestore field PROJECTIONS (`.select`, never full
1MiB docs) over data the pipeline already wrote, plus (optional) `gcloud run revisions list`.
No LLM calls, no generation, no job mutation. Fully deterministic.

WHEN TO RUN: once per deploy round (it exits 1 on any CRITICAL finding, so it can gate the
round), and it is cron/loop-schedulable for a standing watch (scheduling infra is a founder
follow-up). Cold start: `--once-baseline` writes the current trailing battery as the baseline
so the very first comparison window has a prior.

Usage:
    python3 scripts/fleet_drift_sentinel.py                      # 7d vs prior 7d, report + exit code
    python3 scripts/fleet_drift_sentinel.py --window-days 3      # tighter windows
    python3 scripts/fleet_drift_sentinel.py --once-baseline      # cold-start: persist current battery
    python3 scripts/fleet_drift_sentinel.py --quiet              # cron mode (files + exit code only)
    python3 scripts/fleet_drift_sentinel.py --skip-revisions     # no gcloud (offline / CI)

Reports land in scratch/reports/drift/YYYY-MM-DD.{md,json}. Exit 0 = no CRITICAL; 1 = CRITICAL
drift found; the JSON is the machine-readable twin of the markdown.

All detection logic is pure functions over plain dicts (unit-tested with synthetic docs in
tests/test_fleet_drift_sentinel.py — no Firestore in tests); the Firestore/gcloud readers are
thin injected adapters at the bottom of this file.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "scratch" / "reports" / "drift"
BASELINE_FILENAME = "baseline.json"
ACK_FILENAME = "ack.json"

# Cost-family numerics: a SPIKE here (mean or p95) is CRITICAL — fleet-wide burn gates deploys.
COST_METRICS = ("cost_usd", "credits_used")

_SEVERITY_RANK = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}

# Cloud Run services whose deploy timestamps are correlated against transition dates. The 12
# podcast workers share ONE image (one deploy round updates all), so a representative subset
# is enough; writeups run on the course-worker image family.
DEFAULT_SUSPECT_SERVICES = (
    "kitesforu-worker-audio",
    "kitesforu-worker-script",
    "kitesforu-worker-visuals",
    "kitesforu-worker-writeup-generate",
)

# Firestore projections — NEVER load full docs (1MiB job docs exist). `visual.clips` cannot be
# subfield-projected (Firestore has no array-element projection), so the whole array is fetched
# and immediately reduced to a histogram + distinct-hash count by extract_job_features().
PODCAST_JOB_PROJECTION = [
    "created_at",
    "status",
    "format",
    "quality_tier",
    "tier",
    "failure_reason",
    "qa_auto_retry_count",
    "script_attempt_invalidated_count",
    "visual_gate_retry_count",
    "costs.total_usd_estimate",
    "inputs.duration_min",
    "visual.clips",
    "visual.video_url",
    "stages.quality_gate.passed",
    "stages.judge_consensus.final_passed",
    "stages.judge_consensus.outcome",
    "stages.content_craft.passed",
    "stages.`job-audio`.qa",
    "qa.music_dropped",
]

# Writeup format keys are a known enum (kitesforu_schemas.enums.WriteupFormat) — projections
# reach into formats_generated.<fmt>.{figures,seo,sources} per key because Firestore cannot
# wildcard map keys. A future unknown format degrades gracefully (its subfields simply are not
# projected; extraction reads whatever is present).
WRITEUP_FORMAT_KEYS = (
    "blog_post", "newsletter", "linkedin_post", "twitter_thread", "medium_article",
    "executive_summary", "show_notes", "cheat_sheet", "interview_qa_pack",
    "star_story_bank", "flashcard_deck",
)
WRITEUP_PROJECTION = ["created_at", "status", "credits_used", "formats_requested"] + [
    f"formats_generated.{fmt}.{sub}"
    for fmt in WRITEUP_FORMAT_KEYS
    for sub in ("figures", "seo", "sources")
]

MOTION_RENDER_MODES = ("parallax", "kenburns", "video")


@dataclass(frozen=True)
class DriftConfig:
    """All detection thresholds in one place (tested; override via CLI where exposed)."""

    window_days: int = 7
    min_jobs_per_window: int = 8      # volume guard: a window/segment below this is skipped
    collapse_min_prior: float = 0.30  # collapse only meaningful if the feature WAS prevalent
    collapse_rel_drop: float = 0.60   # relative drop that counts as a collapse
    spike_ratio: float = 2.5          # trailing/prior prevalence ratio that counts as a spike
    spike_min_positive: int = 3       # a spike needs at least this many trailing positives
    new_signal_min_prev: float = 0.25  # prior==0: trailing prevalence to call a new signal
    gate_fail_rate: float = 0.80      # gate-meta: axis failing >= this share of gated jobs
    gate_min_gated: int = 8           # gate-meta volume guard
    min_daily_volume: int = 3         # daily buckets below this are ignored by the bisect
    numeric_prior_floor: float = 1e-9  # guard against div-by-~0 on numeric means
    cost_outlier_ratio: float = 10.0  # trailing max >= this x prior p95 → single-job burn


# ────────────────────────────────────────────────────────────────────────────
# 1. EXTRACTION — one projected/full Firestore doc → one small plain-dict row.
#    Works identically on projected docs and full docs (projection rebuilds the
#    same nested shape), so tests use plain synthetic dicts.
# ────────────────────────────────────────────────────────────────────────────

def parse_ts(value: Any) -> datetime | None:
    """Firestore timestamp (datetime), ISO-8601 string, or None → aware UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _get(doc: dict, *keys: str) -> Any:
    cur: Any = doc
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _float_or_none(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _job_segment(doc: dict) -> str:
    """"short" (born 9:16) vs "episode" — same signals scorecard.signals.is_short trusts."""
    if str(doc.get("format") or "").lower() == "short_video":
        return "short"
    dm = _float_or_none(_get(doc, "inputs", "duration_min"))
    if dm is not None and 0 < dm <= 1.5:
        return "short"
    return "episode"


def _clips(doc: dict) -> list[dict]:
    raw = _get(doc, "visual", "clips")
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _music_dropped(doc: dict) -> bool | None:
    """qa.music_dropped, wherever the pipeline wrote it. None = unknown (not gated)."""
    for blob in (_get(doc, "stages", "job-audio", "qa"), doc.get("qa")):
        if isinstance(blob, dict) and "music_dropped" in blob:
            v = blob.get("music_dropped")
            if v is not None:
                return bool(v)
    return None


def _gate_axes(doc: dict, segment: str, status: str, failure_reason: str) -> dict[str, bool]:
    """axis → FAILED?; an axis appears only when the gate actually produced a verdict."""
    axes: dict[str, bool] = {}
    qg = _get(doc, "stages", "quality_gate", "passed")
    if isinstance(qg, bool):
        axes["quality_gate"] = not qg
    jc_final = _get(doc, "stages", "judge_consensus", "final_passed")
    jc_outcome = _get(doc, "stages", "judge_consensus", "outcome")
    if isinstance(jc_final, bool):
        axes["judge_consensus"] = not jc_final
    elif isinstance(jc_outcome, str) and jc_outcome:
        axes["judge_consensus"] = jc_outcome == "failed"
    cc = _get(doc, "stages", "content_craft", "passed")
    if isinstance(cc, bool):
        axes["content_craft"] = not cc
    dropped = _music_dropped(doc)
    if dropped is not None:
        axes["music_delivery"] = dropped
    if segment == "short":
        # The visual gate runs on every short when enabled; a fail leaves visible markers
        # (retry counter / failure_reason). Denominator = all shorts, so a healthy fleet
        # sits near 0% and only a punishing gate approaches 100% (the motion case).
        retry = _float_or_none(doc.get("visual_gate_retry_count")) or 0
        failed = (
            retry > 0
            or "short_visual_gate" in failure_reason
            or (status == "failed_qa" and "visual" in failure_reason)
        )
        axes["short_visual_gate"] = bool(failed)
    return axes


def extract_job_features(doc: dict) -> dict | None:
    """One podcast_jobs doc (projected or full) → a small drift row. None = no created_at."""
    created = parse_ts(doc.get("created_at"))
    if created is None:
        return None
    status = str(doc.get("status") or "unknown")
    failure_reason = str(doc.get("failure_reason") or "").lower()
    segment = _job_segment(doc)
    completed = status == "completed"

    categoricals = {
        "status": status,
        "format": str(doc.get("format") or "unknown"),
        "quality_tier": str(doc.get("quality_tier") or doc.get("tier") or "unknown"),
        "failure_reason": failure_reason or "none",
    }

    flags: dict[str, bool] = {"completed": completed}
    for name in ("qa_auto_retry_count", "script_attempt_invalidated_count",
                 "visual_gate_retry_count"):
        v = _float_or_none(doc.get(name))
        if v is not None or completed:  # explicit counter, or a completed job that never retried
            flags[f"retried:{name}"] = bool(v and v > 0)

    numerics: dict[str, float] = {}
    cost = _float_or_none(_get(doc, "costs", "total_usd_estimate"))
    if cost is not None:
        numerics["cost_usd"] = cost

    # Output-shaped features are only APPLICABLE on completed jobs (a failed job carrying no
    # clips is not a dark feature) — applicability = key presence in `flags`. Within completed
    # jobs, motion:*/video_url_present/clip metrics are applicable ONLY on CLIP-BEARING jobs:
    # an audio-only job (wants_visuals=false — routine on QA-campaign weeks) can never have
    # motion, so it must not dilute those denominators. clips_present is the single MIX-LEVEL
    # signal for "the fleet stopped producing clips at all".
    if completed:
        clips = _clips(doc)
        flags["clips_present"] = bool(clips)
        if clips:
            modes = [str(c.get("render_mode") or "").strip().lower() for c in clips]
            for mode in MOTION_RENDER_MODES:
                flags[f"motion:{mode}"] = mode in modes
            flags["motion:any"] = any(m in MOTION_RENDER_MODES for m in modes)
            hashes = {str(c.get("content_hash") or "").strip() for c in clips}
            hashes.discard("")
            numerics["clip_count"] = float(len(clips))
            numerics["distinct_content_hashes"] = float(len(hashes))
            flags["clips_all_same_hash"] = len(clips) >= 3 and len(hashes) == 1
            video_url = _get(doc, "visual", "video_url")
            flags["video_url_present"] = bool(isinstance(video_url, str) and video_url.strip())
        dropped = _music_dropped(doc)
        if dropped is not None:
            flags["music_delivered"] = not dropped
        flags["cost_recorded"] = cost is not None

    return {
        "created_at": created,
        "segment": segment,
        "categoricals": categoricals,
        "flags": flags,
        "numerics": numerics,
        "gate_axes": _gate_axes(doc, segment, status, failure_reason),
    }


def extract_writeup_features(doc: dict) -> dict | None:
    """One writeups doc (projected or full) → a drift row. None = no created_at."""
    created = parse_ts(doc.get("created_at"))
    if created is None:
        return None
    status = str(doc.get("status") or "unknown")
    completed = status == "completed"
    generated = doc.get("formats_generated")
    formats = [v for v in generated.values() if isinstance(v, dict)] \
        if isinstance(generated, dict) else []

    flags: dict[str, bool] = {"completed": completed}
    numerics: dict[str, float] = {}
    if completed:
        figures_total = 0
        for f in formats:
            figs = f.get("figures")
            if isinstance(figs, list):
                figures_total += len(figs)
        flags["figures_present"] = figures_total > 0
        flags["seo_present"] = any(f.get("seo") for f in formats)
        flags["sources_present"] = any(f.get("sources") for f in formats)
        numerics["figures_count"] = float(figures_total)
        credits = _float_or_none(doc.get("credits_used"))
        flags["credits_recorded"] = bool(credits and credits > 0)
        if credits is not None:
            numerics["credits_used"] = credits

    return {
        "created_at": created,
        "segment": "writeup",
        "categoricals": {"status": status},
        "flags": flags,
        "numerics": numerics,
        "gate_axes": {},
    }


# ────────────────────────────────────────────────────────────────────────────
# 2. WINDOWING + BATTERY STATS (pure)
# ────────────────────────────────────────────────────────────────────────────

def assign_windows(rows: Iterable[dict], now: datetime,
                   window_days: int) -> tuple[list[dict], list[dict]]:
    """(trailing, prior): [now-w, now) and [now-2w, now-w). Rows outside both are dropped."""
    t_start = now - timedelta(days=window_days)
    p_start = now - timedelta(days=2 * window_days)
    trailing, prior = [], []
    for r in rows:
        ts = r["created_at"]
        if t_start <= ts < now:
            trailing.append(r)
        elif p_start <= ts < t_start:
            prior.append(r)
    return trailing, prior


def window_feature_stats(rows: list[dict]) -> dict[str, dict[str, float]]:
    """{feature: {p, n, pos}} — prevalence with APPLICABILITY-aware denominators.

    * flags: denominator = rows that carry the flag key (extraction wrote an explicit bool).
    * categoricals (one-hot "field:value"): denominator = rows that carry the field.
    """
    pos: dict[str, int] = {}
    n: dict[str, int] = {}
    cat_n: dict[str, int] = {}
    for r in rows:
        for name, v in r["flags"].items():
            n[name] = n.get(name, 0) + 1
            if v:
                pos[name] = pos.get(name, 0) + 1
        for fld, val in r["categoricals"].items():
            cat_n[fld] = cat_n.get(fld, 0) + 1
            key = f"{fld}:{val}"
            pos[key] = pos.get(key, 0) + 1
    stats: dict[str, dict[str, float]] = {}
    for name, count in n.items():
        stats[name] = {"p": pos.get(name, 0) / count, "n": count, "pos": pos.get(name, 0)}
    for name, count in pos.items():
        if ":" in name and name.split(":", 1)[0] in cat_n:
            denom = cat_n[name.split(":", 1)[0]]
            stats[name] = {"p": count / denom, "n": denom, "pos": count}
    # a categorical value absent from this window still needs an explicit 0 when the field
    # has volume — detect_drift fills that from the union (n comes from cat_n via _cat_n).
    # the field-level denominators ride along under a reserved key (read by _stat_for)
    stats["_cat_n"] = {fld: float(cnt) for fld, cnt in cat_n.items()}
    return stats


def _p95(values: list[float]) -> float:
    """Nearest-rank p95 (deterministic, no numpy). NOTE the dilution limit: one outlier among
    ~70 values does NOT move p95 — it takes roughly n/20 affected values to shift it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def window_numeric_stats(rows: list[dict]) -> dict[str, dict[str, float]]:
    """{metric: {mean, n, p95, max}} over rows where the metric is present."""
    vals: dict[str, list[float]] = {}
    for r in rows:
        for name, v in r["numerics"].items():
            vals.setdefault(name, []).append(v)
    return {
        name: {"mean": sum(v) / len(v), "n": len(v), "p95": _p95(v), "max": max(v)}
        for name, v in vals.items()
    }


def _stat_for(stats: dict, name: str) -> dict[str, float] | None:
    """Feature stat, synthesizing an explicit-zero for a categorical value missing from this
    window when its parent field has volume (a vanished failure_reason is prevalence 0)."""
    if name in stats:
        return dict(stats[name])
    cat_n = stats.get("_cat_n") or {}
    fld = name.split(":", 1)[0] if ":" in name else None
    if fld and fld in cat_n:
        return {"p": 0.0, "n": cat_n[fld], "pos": 0}
    return None


# ────────────────────────────────────────────────────────────────────────────
# 3. DETECTION (pure)
# ────────────────────────────────────────────────────────────────────────────

def is_bad_signal_metric(name: str) -> bool:
    """True when HIGHER prevalence of this metric is WORSE (failure/retry/gate-fail families).

    Directionality contract: a bad signal COLLAPSING is an improvement (a fix landed, a failure
    got stripped) → INFO, never a deploy-gating CRITICAL; a bad signal SPIKING is the incident
    itself → CRITICAL. `failure_reason:none` and `status:completed` are GOOD complements and
    stay on the good-signal path (their collapse = more failures = CRITICAL)."""
    if name.startswith("failure_reason:"):
        return name != "failure_reason:none"
    if name.startswith("status:"):
        return name.split(":", 1)[1].startswith("failed")
    if name == "clips_all_same_hash":  # monotony marker: collapsing = variety returned
        return True
    return name.startswith(("retried:", "gate:"))


def _finding(kind: str, severity: str, segment: str, metric: str, metric_kind: str,
             prior_value: float, trailing_value: float, prior_n: float, trailing_n: float,
             change: float | None, prior_source: str, detail: str) -> dict:
    return {
        "kind": kind, "severity": severity, "segment": segment, "metric": metric,
        "metric_kind": metric_kind, "prior_value": round(prior_value, 4),
        "trailing_value": round(trailing_value, 4), "prior_n": int(prior_n),
        "trailing_n": int(trailing_n),
        "change": round(change, 4) if change is not None else None,  # None = ratio vs 0 prior
        "prior_source": prior_source, "detail": detail,
        "transition_date": None, "suspect_revisions": [],
    }


def detect_prevalence_drift(trailing: dict, prior: dict, cfg: DriftConfig, segment: str,
                            prior_source: str = "window") -> list[dict]:
    findings: list[dict] = []
    names = (set(trailing) | set(prior)) - {"_cat_n"}
    for name in sorted(names):
        t = _stat_for(trailing, name)
        p = _stat_for(prior, name)
        if t is None or p is None:
            continue  # not observable in one window (no applicable rows) — volume guard below
        if t["n"] < cfg.min_jobs_per_window or p["n"] < cfg.min_jobs_per_window:
            continue
        pp, tp = p["p"], t["p"]
        bad = is_bad_signal_metric(name)
        if pp >= cfg.collapse_min_prior and pp > 0 and (pp - tp) / pp >= cfg.collapse_rel_drop:
            # Directionality: a BAD signal collapsing is an IMPROVEMENT — report it (INFO,
            # never exit-1), don't gate deploys for a week because a failure got fixed.
            findings.append(_finding(
                "collapse", "INFO" if bad else "CRITICAL", segment, name, "prevalence",
                pp, tp, p["n"], t["n"], (pp - tp) / pp, prior_source,
                f"prevalence {pp:.0%} → {tp:.0%} ({(pp - tp) / pp:.0%} relative drop)"
                + (" — bad-signal collapse = improvement" if bad else "")))
        elif pp > 0 and tp / pp >= cfg.spike_ratio and t["pos"] >= cfg.spike_min_positive:
            # Directionality: a BAD signal spiking IS the incident → CRITICAL (exit 1).
            findings.append(_finding(
                "spike", "CRITICAL" if bad else "WARNING", segment, name, "prevalence",
                pp, tp, p["n"], t["n"], tp / pp, prior_source,
                f"prevalence {pp:.0%} → {tp:.0%} ({tp / pp:.1f}x)"))
        elif pp == 0 and tp >= cfg.new_signal_min_prev and t["pos"] >= cfg.spike_min_positive:
            findings.append(_finding(
                "new_signal", "WARNING", segment, name, "prevalence", pp, tp, p["n"], t["n"],
                None, prior_source,
                f"0% prior → {tp:.0%} trailing ({int(t['pos'])} jobs)"))
    return findings


def detect_numeric_drift(trailing: dict, prior: dict, cfg: DriftConfig, segment: str,
                         prior_source: str = "window") -> list[dict]:
    """Mean collapse/spike per metric, plus TWO cost-only channels the mean dilutes away:

    * p95 spike (CRITICAL) — a burned COHORT (~n/20+ jobs) the mean averages down.
    * max-vs-prior-p95 outlier (WARNING, non-gating) — a SINGLE burned job among ~70, which
      moves neither the mean nor the p95 (the honest dilution limit of both aggregates).
    Cost-family MEAN spikes are CRITICAL: fleet-wide burn must gate the deploy round."""
    findings: list[dict] = []
    for name in sorted(set(trailing) | set(prior)):
        t, p = trailing.get(name), prior.get(name)
        if not t or not p:
            continue
        if t["n"] < cfg.min_jobs_per_window or p["n"] < cfg.min_jobs_per_window:
            continue
        is_cost = name in COST_METRICS
        pm, tm = p["mean"], t["mean"]
        if pm <= cfg.numeric_prior_floor:
            if tm > cfg.numeric_prior_floor and t["n"] >= cfg.min_jobs_per_window:
                findings.append(_finding(
                    "new_signal", "WARNING", segment, name, "mean", pm, tm, p["n"], t["n"],
                    None, prior_source, f"mean {pm:.4g} → {tm:.4g} (was ~0)"))
            continue
        mean_spiked = False
        if (pm - tm) / pm >= cfg.collapse_rel_drop:
            findings.append(_finding(
                "collapse", "CRITICAL", segment, name, "mean", pm, tm, p["n"], t["n"],
                (pm - tm) / pm, prior_source,
                f"mean {pm:.4g} → {tm:.4g} ({(pm - tm) / pm:.0%} relative drop)"))
        elif tm / pm >= cfg.spike_ratio:
            mean_spiked = True
            findings.append(_finding(
                "spike", "CRITICAL" if is_cost else "WARNING", segment, name, "mean",
                pm, tm, p["n"], t["n"], tm / pm, prior_source,
                f"mean {pm:.4g} → {tm:.4g} ({tm / pm:.1f}x)"
                + (" — fleet-wide cost burn" if is_cost else "")))
        if not is_cost:
            continue
        # p95 channel: a burned cohort the mean dilutes (baseline priors may lack p95/max).
        t95, p95 = t.get("p95"), p.get("p95")
        if (not mean_spiked and t95 and p95 and p95 > cfg.numeric_prior_floor
                and t95 / p95 >= cfg.spike_ratio):
            findings.append(_finding(
                "spike", "CRITICAL", segment, name, "p95", p95, t95, p["n"], t["n"],
                t95 / p95, prior_source,
                f"p95 {p95:.4g} → {t95:.4g} ({t95 / p95:.1f}x) — burned cohort the "
                f"mean dilutes"))
        # max-outlier channel: ONE burned job (invisible to mean AND p95 at fleet volume).
        tmax = t.get("max")
        if (tmax and p95 and p95 > cfg.numeric_prior_floor
                and tmax >= cfg.cost_outlier_ratio * p95):
            findings.append(_finding(
                "outlier", "WARNING", segment, name, "max", p95, tmax, p["n"], t["n"],
                tmax / p95, prior_source,
                f"single-job burn: trailing max {tmax:.4g} >= "
                f"{cfg.cost_outlier_ratio:.0f}x prior p95 {p95:.4g} — mean/p95 would "
                f"dilute this"))
    return findings


def detect_gate_meta(trailing_rows: list[dict], cfg: DriftConfig, segment: str) -> list[dict]:
    """A gate axis failing >= gate_fail_rate of gated jobs = the gate is punishing a symptom
    the pipeline manufactures (the motion case: 100%-failing visual gate, nobody reading it)."""
    gated: dict[str, int] = {}
    failed: dict[str, int] = {}
    for r in trailing_rows:
        for axis, is_failed in r["gate_axes"].items():
            gated[axis] = gated.get(axis, 0) + 1
            if is_failed:
                failed[axis] = failed.get(axis, 0) + 1
    findings = []
    for axis in sorted(gated):
        n, f = gated[axis], failed.get(axis, 0)
        if n < cfg.gate_min_gated:
            continue
        rate = f / n
        if rate >= cfg.gate_fail_rate:
            findings.append(_finding(
                "gate_meta", "CRITICAL", segment, f"gate:{axis}", "fail_rate", 0.0, rate,
                0, n, rate,
                "window", f"gate-punishing-manufactured-symptom: {axis} failed {f}/{n} "
                          f"({rate:.0%}) of gated jobs — the gate is an unread alarm"))
    return findings


# ────────────────────────────────────────────────────────────────────────────
# 4. TRANSITION-DATE BISECTION + DEPLOY CORRELATION (pure)
# ────────────────────────────────────────────────────────────────────────────

def daily_series(rows: list[dict], metric: str, metric_kind: str,
                 min_daily_volume: int) -> list[tuple[date, float, int]]:
    """Chronological [(day, value, n)] for one metric; days under min volume are dropped."""
    buckets: dict[date, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r["created_at"].date(), []).append(r)
    series: list[tuple[date, float, int]] = []
    for day in sorted(buckets):
        day_rows = buckets[day]
        if metric_kind == "mean":
            vals = [r["numerics"][metric] for r in day_rows if metric in r["numerics"]]
            if len(vals) >= min_daily_volume:
                series.append((day, sum(vals) / len(vals), len(vals)))
            continue
        stat = _stat_for(window_feature_stats(day_rows), metric)
        if stat is not None and stat["n"] >= min_daily_volume:
            series.append((day, stat["p"], int(stat["n"])))
    return series


def bisect_transition_day(series: list[tuple[date, float, int]],
                          predicate: Callable[[float], bool]) -> date | None:
    """First day of the PERSISTENT bad suffix (bad on day d and every qualifying day after).
    The series is a (noisy) step function good→bad, so this is a suffix bisect: one isolated
    bad day mid-good is not a transition."""
    if not series:
        return None
    last_good = -1
    for i, (_, value, _) in enumerate(series):
        if not predicate(value):
            last_good = i
    first_bad = last_good + 1
    return series[first_bad][0] if first_bad < len(series) else None


def collapse_predicate(prior_value: float, cfg: DriftConfig) -> Callable[[float], bool]:
    threshold = prior_value * (1 - cfg.collapse_rel_drop)
    return lambda v: v <= threshold


def spike_predicate(prior_value: float, cfg: DriftConfig) -> Callable[[float], bool]:
    threshold = prior_value * cfg.spike_ratio
    return lambda v: v >= threshold


def nearest_revisions(transition_day: date, revisions: list[dict], k: int = 3,
                      max_days: float = 5.0) -> list[dict]:
    """2-3 deploy suspects nearest the transition day (|deployed - transition| <= max_days)."""
    t = datetime(transition_day.year, transition_day.month, transition_day.day,
                 tzinfo=timezone.utc)
    scored = []
    for rev in revisions:
        deployed = rev.get("deployed_at")
        if not isinstance(deployed, datetime):
            continue
        delta_h = (deployed - t).total_seconds() / 3600.0
        if abs(delta_h) <= max_days * 24:
            scored.append((abs(delta_h), delta_h, rev))
    scored.sort(key=lambda x: x[0])
    return [
        {"service": rev.get("service"), "revision": rev.get("revision"),
         "deployed_at": rev["deployed_at"].isoformat(), "delta_hours": round(delta_h, 1)}
        for _, delta_h, rev in scored[:k]
    ]


def attach_transitions(findings: list[dict], rows_by_segment: dict[str, list[dict]],
                       revisions: list[dict], cfg: DriftConfig) -> None:
    """Mutates flagged collapse/spike findings with transition_date + suspect_revisions."""
    for f in findings:
        if f["kind"] not in ("collapse", "spike"):
            continue
        rows = rows_by_segment.get(f["segment"]) or []
        series = daily_series(rows, f["metric"], f["metric_kind"], cfg.min_daily_volume)
        pred = (collapse_predicate if f["kind"] == "collapse" else spike_predicate)(
            f["prior_value"], cfg)
        day = bisect_transition_day(series, pred)
        if day is None:
            continue
        f["transition_date"] = day.isoformat()
        f["suspect_revisions"] = nearest_revisions(day, revisions)


# ────────────────────────────────────────────────────────────────────────────
# 5. BASELINE (cold-start prior) + THE SENTINEL RUN (pure orchestration)
# ────────────────────────────────────────────────────────────────────────────

def battery_snapshot(rows_by_segment: dict[str, list[dict]]) -> dict:
    """The current battery, JSON-able — written by --once-baseline, read as a cold-start
    prior when the real prior window is under-volume."""
    snap: dict[str, Any] = {}
    for segment, rows in rows_by_segment.items():
        feats = {k: v for k, v in window_feature_stats(rows).items() if k != "_cat_n"}
        snap[segment] = {
            "n_jobs": len(rows),
            "features": feats,
            "numerics": window_numeric_stats(rows),
        }
    return snap


def group_by_segment(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """short / episode / writeup, plus podcast_all (short+episode) for fleet-wide collapses
    that per-segment volume guards would otherwise hide."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["segment"], []).append(r)
        if r["segment"] in ("short", "episode"):
            by.setdefault("podcast_all", []).append(r)
    return by


def apply_acks(findings: list[dict], acks: list[dict], now: datetime) -> int:
    """Mute DELIBERATE changes (feature removal, flag flip, QA campaign): a finding whose
    metric fnmatch-es an unexpired ack entry (optionally segment-scoped) is demoted to INFO —
    still reported + marked acked, but never exit-1. Returns the number muted. Malformed or
    expired entries are skipped (fail-open: a broken ack must never mute OR crash)."""
    today = now.date()
    muted = 0
    for f in findings:
        for a in acks:
            if not isinstance(a, dict):
                continue
            until = parse_ts(a.get("until"))  # date-only ISO ok; inclusive through that day
            if until is None or today > until.date():
                continue
            if a.get("segment") and a["segment"] != f["segment"]:
                continue
            pattern = str(a.get("metric") or "")
            if not pattern or not fnmatch.fnmatchcase(f["metric"], pattern):
                continue
            f["severity"] = "INFO"
            f["acked"] = True
            note = str(a.get("note") or "").strip()
            f["detail"] += f" [ACKED until {a['until']}" + (f": {note}]" if note else "]")
            muted += 1
            break
    return muted


def dedupe_podcast_all(findings: list[dict]) -> list[dict]:
    """Drop podcast_all findings that duplicate an identical short/episode finding —
    podcast_all exists only to surface fleet-wide drift that per-segment volume guards hide."""
    covered = {(f["kind"], f["metric"], f["metric_kind"])
               for f in findings if f["segment"] in ("short", "episode")}
    return [f for f in findings
            if not (f["segment"] == "podcast_all"
                    and (f["kind"], f["metric"], f["metric_kind"]) in covered)]


def run_sentinel(rows: list[dict], now: datetime, cfg: DriftConfig,
                 revisions: list[dict] | None = None,
                 baseline: dict | None = None,
                 acks: list[dict] | None = None) -> dict:
    """The whole detection pass over pre-extracted rows. Pure: no I/O, fully synthetic-testable."""
    revisions = revisions or []
    findings: list[dict] = []
    segments_meta: dict[str, Any] = {}
    rows_by_segment = group_by_segment(rows)

    for segment, seg_rows in sorted(rows_by_segment.items()):
        trailing, prior = assign_windows(seg_rows, now, cfg.window_days)
        meta: dict[str, Any] = {"trailing_n": len(trailing), "prior_n": len(prior)}
        segments_meta[segment] = meta

        if len(trailing) < cfg.min_jobs_per_window:
            meta["skipped"] = "insufficient trailing volume"
            continue

        t_feats, t_nums = window_feature_stats(trailing), window_numeric_stats(trailing)
        prior_source = "window"
        if len(prior) >= cfg.min_jobs_per_window:
            p_feats, p_nums = window_feature_stats(prior), window_numeric_stats(prior)
        elif baseline and segment in baseline:
            p_feats = dict(baseline[segment].get("features") or {})
            p_nums = dict(baseline[segment].get("numerics") or {})
            prior_source = "baseline"
            meta["prior_source"] = "baseline"
        else:
            meta["skipped"] = "insufficient prior volume (no baseline)"
            findings.extend(detect_gate_meta(trailing, cfg, segment))
            continue

        findings.extend(detect_prevalence_drift(t_feats, p_feats, cfg, segment, prior_source))
        findings.extend(detect_numeric_drift(t_nums, p_nums, cfg, segment, prior_source))
        findings.extend(detect_gate_meta(trailing, cfg, segment))

    findings = dedupe_podcast_all(findings)
    acked_count = apply_acks(findings, acks or [], now)
    attach_transitions(findings, rows_by_segment, revisions, cfg)
    findings.sort(key=lambda f: (_SEVERITY_RANK.get(f["severity"], 3),
                                 f["segment"], f["metric"]))
    return {
        "generated_at": now.isoformat(),
        "window_days": cfg.window_days,
        "segments": segments_meta,
        "findings": findings,
        "critical_count": sum(1 for f in findings if f["severity"] == "CRITICAL"),
        "acked_count": acked_count,
    }


def has_critical(result: dict) -> bool:
    return bool(result.get("critical_count"))


# ────────────────────────────────────────────────────────────────────────────
# 6. REPORT RENDERING + FILE OUTPUT
# ────────────────────────────────────────────────────────────────────────────

def render_markdown(result: dict) -> str:
    lines = [
        f"# Fleet Drift Sentinel — {result['generated_at'][:10]}",
        "",
        f"Trailing {result['window_days']}d vs prior {result['window_days']}d "
        f"(generated {result['generated_at']}, $0 deterministic, read-only).",
        "",
        "## Window volumes",
        "",
        "| segment | trailing jobs | prior jobs | note |",
        "|---|---|---|---|",
    ]
    for seg, meta in sorted(result["segments"].items()):
        note = meta.get("skipped") or meta.get("prior_source", "")
        lines.append(f"| {seg} | {meta['trailing_n']} | {meta['prior_n']} | {note} |")
    findings = result["findings"]
    lines += ["", f"## Findings ({len(findings)} — {result['critical_count']} CRITICAL)", ""]
    if not findings:
        lines.append("No drift detected above thresholds.")
    else:
        lines += [
            "| severity | kind | segment | metric | prior | trailing | detail | transition | suspects |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for f in findings:
            suspects = "; ".join(
                f"{s['service']}@{s['revision']} ({s['delta_hours']:+.0f}h)"
                for s in f["suspect_revisions"]) or "-"
            lines.append(
                f"| {f['severity']} | {f['kind']} | {f['segment']} | `{f['metric']}` "
                f"| {f['prior_value']} (n={f['prior_n']}, {f['prior_source']}) "
                f"| {f['trailing_value']} (n={f['trailing_n']}) "
                f"| {f['detail']} | {f['transition_date'] or '-'} | {suspects} |")
    lines += [
        "",
        "## Reading this report",
        "",
        "* **collapse of a GOOD signal** (CRITICAL) — a feature the fleet used to produce went "
        "dark (prior >= 30% prevalence, >= 60% relative drop). Exit code 1: treat it as a "
        "deploy-round gate.",
        "* **collapse of a BAD signal** (INFO) — a failure_reason/status:failed*/retried:*/"
        "gate:* family collapsing is an IMPROVEMENT (a fix landed); reported, never gates.",
        "* **spike** — a BAD-signal family or cost (mean or p95) growing >= 2.5x is CRITICAL "
        "(exit 1); other spikes are WARNING.",
        "* **outlier** (WARNING, non-gating) — trailing max cost >= 10x prior p95: a SINGLE "
        "burned job. Dilution limit, honestly: one job among ~70 moves neither the mean nor "
        "the p95 (p95 needs ~n/20 affected jobs) — this channel exists for exactly that case.",
        "* **gate_meta** (CRITICAL) — a QA gate axis failing >= 80% of gated jobs: the gate is "
        "punishing a symptom the pipeline manufactures; read the gate, then fix the pipeline.",
        "* **applicability** — motion:*/video_url_present count only clip-bearing jobs; "
        "`clips_present` is the single mix-level signal (audio-only/QA-campaign weeks shift "
        "the mix without faking a motion collapse).",
        "* **[ACKED …]** — expected change muted to INFO via ack.json "
        '(`{"acks": [{"metric", "until", "segment?", "note?"}]}` next to this report; '
        "metric accepts fnmatch globs; entries expire after their date).",
        "* **transition/suspects** — first persistently-collapsed daily bucket, correlated "
        "against Cloud Run worker revision deploy times (2-3 nearest).",
        "",
    ]
    return "\n".join(lines)


def write_reports(out_dir: Path, result: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    day = result["generated_at"][:10]
    md_path, json_path = out_dir / f"{day}.md", out_dir / f"{day}.json"
    md_path.write_text(render_markdown(result))
    json_path.write_text(json.dumps(result, indent=2, default=str))
    return md_path, json_path


def save_baseline(out_dir: Path, snapshot: dict, now: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / BASELINE_FILENAME
    path.write_text(json.dumps({"generated_at": now.isoformat(), "battery": snapshot},
                               indent=2, default=str))
    return path


def load_baseline(out_dir: Path) -> dict | None:
    path = out_dir / BASELINE_FILENAME
    if not path.exists():
        return None
    try:
        return (json.loads(path.read_text()) or {}).get("battery") or None
    except (json.JSONDecodeError, OSError):
        return None


def load_acks(out_dir: Path) -> list[dict]:
    """scratch/reports/drift/ack.json — {"acks": [{metric, until, segment?, note?}]} or a bare
    list. Missing/malformed file → [] (fail-open: never crash, never silently mute)."""
    path = out_dir / ACK_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    entries = data.get("acks") if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


# ────────────────────────────────────────────────────────────────────────────
# 7. THIN ADAPTERS — the ONLY code that touches Firestore / gcloud (not unit-
#    tested; everything above runs on synthetic dicts).
# ────────────────────────────────────────────────────────────────────────────

class FirestoreReader:
    """Projection-only reader with a MIXED-TYPE created_at guard.

    `created_at` is a Firestore timestamp on recent podcast_jobs but an ISO STRING on legacy
    jobs and on all writeups. A `where` clause only matches values of its own type (Firestore
    type-partitions ordering: string docs sort AFTER all timestamp docs descending, which
    silently starved a naive ordered-desc/early-break scan of every timestamp-dated job). So:
    one bounded, type-correct query per created_at type, results merged; a doc matches exactly
    one partition, so no dedup is needed. Client-side parse_ts re-guards the boundary."""

    def __init__(self, project: str) -> None:
        # lazy import: unit tests never touch this adapter (namespace pkg confuses mypy)
        from google.cloud import firestore  # type: ignore[attr-defined]
        from google.cloud.firestore_v1.base_query import FieldFilter

        self._firestore = firestore
        self._field_filter = FieldFilter
        self.db = firestore.Client(project=project)

    def _stream(self, collection: str, projection: list[str], since: datetime,
                limit: int) -> Iterator[dict]:
        for since_typed in (since, since.isoformat()):  # timestamp docs, then ISO-string docs
            q = (
                self.db.collection(collection)
                .select(projection)
                .where(filter=self._field_filter("created_at", ">=", since_typed))
                .order_by("created_at", direction=self._firestore.Query.DESCENDING)
                .limit(limit)
            )
            for snap in q.stream():
                doc = snap.to_dict() or {}
                created = parse_ts(doc.get("created_at"))
                if created is not None and created >= since:
                    yield doc

    def stream_podcast_jobs(self, since: datetime, limit: int) -> Iterator[dict]:
        return self._stream("podcast_jobs", PODCAST_JOB_PROJECTION, since, limit)

    def stream_writeups(self, since: datetime, limit: int) -> Iterator[dict]:
        return self._stream("writeups", WRITEUP_PROJECTION, since, limit)


def fetch_worker_revisions(services: Iterable[str], region: str, project: str) -> list[dict]:
    """[{service, revision, deployed_at}] via `gcloud run revisions list`. Fail-open to []."""
    revisions: list[dict] = []
    for svc in services:
        try:
            out = subprocess.run(
                ["gcloud", "run", "revisions", "list", "--service", svc,
                 "--region", region, "--project", project, "--format", "json",
                 "--limit", "20"],
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout
            for item in json.loads(out or "[]"):
                meta = item.get("metadata") or {}
                deployed = parse_ts(meta.get("creationTimestamp")
                                    or (item.get("status") or {}).get("deployedTime"))
                if deployed is not None:
                    revisions.append({"service": svc, "revision": meta.get("name") or "?",
                                      "deployed_at": deployed})
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
            continue  # fail-open: correlation is best-effort garnish, never a blocker
    return revisions


# ────────────────────────────────────────────────────────────────────────────
# 8. CLI
# ────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Standing dark-feature / fleet-drift detector ($0, deterministic).")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=2000, help="max docs per collection")
    ap.add_argument("--project", default="kitesforu-dev")
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--services", default=",".join(DEFAULT_SUSPECT_SERVICES),
                    help="comma-separated Cloud Run services for deploy correlation")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--now", default=None,
                    help="ISO timestamp override for the window anchor (testing/backfill)")
    ap.add_argument("--once-baseline", action="store_true",
                    help="cold start: write the current trailing battery as the baseline prior")
    ap.add_argument("--skip-revisions", action="store_true",
                    help="skip gcloud deploy correlation (offline/CI)")
    ap.add_argument("--quiet", action="store_true", help="cron mode: files + exit code only")
    args = ap.parse_args(argv)

    cfg = DriftConfig(window_days=args.window_days)
    now = parse_ts(args.now) or datetime.now(timezone.utc)
    since = now - timedelta(days=2 * cfg.window_days)

    reader = FirestoreReader(args.project)
    rows: list[dict] = []
    for doc in reader.stream_podcast_jobs(since, args.limit):
        row = extract_job_features(doc)  # immediate reduction — clips never accumulate
        if row is not None:
            rows.append(row)
    for doc in reader.stream_writeups(since, args.limit):
        row = extract_writeup_features(doc)
        if row is not None:
            rows.append(row)

    if args.once_baseline:
        trailing, _ = assign_windows(rows, now, cfg.window_days)
        path = save_baseline(args.out_dir, battery_snapshot(group_by_segment(trailing)), now)
        if not args.quiet:
            print(f"baseline written: {path} ({len(trailing)} trailing jobs)")
        return 0

    revisions = [] if args.skip_revisions else fetch_worker_revisions(
        [s for s in args.services.split(",") if s], args.region, args.project)
    result = run_sentinel(rows, now, cfg, revisions, baseline=load_baseline(args.out_dir),
                          acks=load_acks(args.out_dir))
    md_path, json_path = write_reports(args.out_dir, result)

    if not args.quiet:
        print(f"jobs analyzed: {len(rows)} | findings: {len(result['findings'])} "
              f"({result['critical_count']} CRITICAL, {result['acked_count']} acked)")
        for f in result["findings"]:
            print(f"  [{f['severity']}] {f['kind']} {f['segment']}/{f['metric']} — "
                  f"{f['detail']}"
                  + (f" (transition {f['transition_date']})" if f["transition_date"] else ""))
        print(f"report: {md_path}\njson:   {json_path}")
    return 1 if has_critical(result) else 0


if __name__ == "__main__":
    sys.exit(main())
