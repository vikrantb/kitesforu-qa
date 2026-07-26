"""Tests for scripts/fleet_drift_sentinel.py — the standing dark-feature / drift detector.

All tests are PURE-FUNCTION over synthetic doc dicts: extraction, windowing, collapse/spike/
new-signal/gate-meta detection, volume guards, transition-day bisection, revision correlation,
baseline fallback, report writing, exit-code logic. No Firestore, no gcloud (those live in thin
adapters the tests never touch). The script is loaded by file path (``scripts/`` is not an
installed package — same idiom as tests/test_quality_matrix_cli.py).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "fleet_drift_sentinel.py"

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _load_module():
    spec = importlib.util.spec_from_file_location("fleet_drift_sentinel", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_drift_sentinel"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fds():
    return _load_module()


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT_PATH.exists()
    assert SCRIPT_PATH.stat().st_mode & 0o100


# ── synthetic doc builders ────────────────────────────────────────────────────

def _clip(mode: str, chash: str) -> dict:
    return {"render_mode": mode, "content_hash": chash, "asset_uri": f"gs://b/{chash}.png"}


def job_doc(created_at, *, status="completed", fmt="short_video", tier="low",
            clips=None, video_url="https://cdn/x.mp4", music_dropped=False,
            failure_reason=None, cost=0.03, retries=None, gates=None) -> dict:
    """A synthetic podcast_jobs doc in the same nested shape a projection rebuilds."""
    doc: dict = {
        "created_at": created_at,
        "status": status,
        "format": fmt,
        "quality_tier": tier,
        "inputs": {"duration_min": 0.5 if fmt == "short_video" else 8.0},
        "visual": {"clips": clips if clips is not None else
                   [_clip("parallax", "aa"), _clip("kenburns", "bb"), _clip("still", "cc")],
                   "video_url": video_url},
        "costs": {"total_usd_estimate": cost},
        "stages": {"job-audio": {"qa": {"music_dropped": music_dropped}}},
    }
    if failure_reason:
        doc["failure_reason"] = failure_reason
    for k, v in (retries or {}).items():
        doc[k] = v
    for axis, passed in (gates or {}).items():
        doc["stages"][axis] = {"passed": passed}
    return doc


def writeup_doc(created_at, *, status="completed", figures=1, seo=True, sources=True,
                credits=2.0) -> dict:
    return {
        "created_at": created_at,
        "status": status,
        "credits_used": credits,
        "formats_generated": {
            "blog_post": {
                "figures": [{"kind": "chart"}] * figures,
                "seo": {"description": "d"} if seo else None,
                "sources": [{"url": "https://x"}] if sources else [],
            },
        },
    }


def _rows(fds, docs, extractor="extract_job_features"):
    fn = getattr(fds, extractor)
    rows = [fn(d) for d in docs]
    return [r for r in rows if r is not None]


def _days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


# ── extraction ────────────────────────────────────────────────────────────────

def test_extract_job_features_full(fds) -> None:
    row = fds.extract_job_features(job_doc(_days_ago(1)))
    assert row is not None
    assert row["segment"] == "short"
    assert row["flags"]["motion:parallax"] is True
    assert row["flags"]["motion:kenburns"] is True
    assert row["flags"]["motion:video"] is False
    assert row["flags"]["motion:any"] is True
    assert row["flags"]["video_url_present"] is True
    assert row["flags"]["music_delivered"] is True
    assert row["flags"]["completed"] is True
    assert row["numerics"]["clip_count"] == 3.0
    assert row["numerics"]["distinct_content_hashes"] == 3.0
    assert row["numerics"]["cost_usd"] == pytest.approx(0.03)
    assert row["categoricals"]["quality_tier"] == "low"
    assert row["categoricals"]["failure_reason"] == "none"


def test_extract_missing_created_at_returns_none(fds) -> None:
    assert fds.extract_job_features({"status": "completed"}) is None
    assert fds.extract_writeup_features({"status": "completed"}) is None


def test_extract_created_at_iso_string_and_datetime_both_parse(fds) -> None:
    a = fds.extract_job_features(job_doc("2026-07-24T10:00:00Z"))
    b = fds.extract_job_features(job_doc(datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)))
    assert a is not None and b is not None
    assert a["created_at"] == b["created_at"]


def test_extract_episode_segment_and_failed_job(fds) -> None:
    row = fds.extract_job_features(job_doc(
        _days_ago(1), status="failed", fmt="dialogue", failure_reason="tts_provider_error"))
    assert row is not None
    assert row["segment"] == "episode"
    assert row["flags"]["completed"] is False
    # output-shaped features are NOT applicable on failed jobs
    assert "motion:any" not in row["flags"]
    assert "video_url_present" not in row["flags"]
    assert row["categoricals"]["failure_reason"] == "tts_provider_error"


def test_extract_all_same_hash_monotony_flag(fds) -> None:
    clips = [_clip("still", "same"), _clip("still", "same"), _clip("still", "same")]
    row = fds.extract_job_features(job_doc(_days_ago(1), clips=clips))
    assert row is not None
    assert row["flags"]["clips_all_same_hash"] is True
    assert row["flags"]["motion:any"] is False
    assert row["numerics"]["distinct_content_hashes"] == 1.0


def test_extract_gate_axes(fds) -> None:
    doc = job_doc(_days_ago(1), gates={"quality_gate": False, "content_craft": True},
                  retries={"visual_gate_retry_count": 2})
    row = fds.extract_job_features(doc)
    assert row is not None
    assert row["gate_axes"]["quality_gate"] is True      # failed
    assert row["gate_axes"]["content_craft"] is False    # passed
    assert row["gate_axes"]["music_delivery"] is False   # delivered
    assert row["gate_axes"]["short_visual_gate"] is True  # retry counter > 0
    assert row["flags"]["retried:visual_gate_retry_count"] is True


def test_extract_gate_axes_absent_when_no_verdict(fds) -> None:
    doc = job_doc(_days_ago(1), fmt="dialogue")
    del doc["stages"]["job-audio"]  # no qa blob → music axis not gated
    row = fds.extract_job_features(doc)
    assert row is not None
    assert "quality_gate" not in row["gate_axes"]
    assert "music_delivery" not in row["gate_axes"]
    assert "short_visual_gate" not in row["gate_axes"]  # episodes are never visual-gated
    assert "music_delivered" not in row["flags"]


def test_extract_writeup_features(fds) -> None:
    row = fds.extract_writeup_features(writeup_doc(_days_ago(1), figures=3))
    assert row is not None
    assert row["segment"] == "writeup"
    assert row["flags"]["figures_present"] is True
    assert row["flags"]["seo_present"] is True
    assert row["flags"]["sources_present"] is True
    assert row["flags"]["credits_recorded"] is True
    assert row["numerics"]["figures_count"] == 3.0
    assert row["numerics"]["credits_used"] == pytest.approx(2.0)


def test_extract_writeup_bare(fds) -> None:
    row = fds.extract_writeup_features(
        {"created_at": "2026-07-24T00:00:00Z", "status": "failed"})
    assert row is not None
    assert row["flags"] == {"completed": False}
    assert row["numerics"] == {}


# ── windowing ─────────────────────────────────────────────────────────────────

def test_assign_windows_boundaries(fds) -> None:
    docs = [job_doc(_days_ago(d)) for d in (0.5, 6.9, 7.1, 13.9, 14.1)]
    rows = _rows(fds, docs)
    trailing, prior = fds.assign_windows(rows, NOW, 7)
    assert len(trailing) == 2   # 0.5d, 6.9d
    assert len(prior) == 2      # 7.1d, 13.9d — 14.1d is outside both


def test_window_feature_stats_applicability(fds) -> None:
    docs = [job_doc(_days_ago(1)) for _ in range(3)]
    docs.append(job_doc(_days_ago(1), status="failed", failure_reason="oom"))
    stats = fds.window_feature_stats(_rows(fds, docs))
    # motion applicable only to the 3 completed jobs
    assert stats["motion:any"]["n"] == 3 and stats["motion:any"]["p"] == 1.0
    assert stats["completed"]["n"] == 4 and stats["completed"]["p"] == pytest.approx(0.75)
    assert stats["failure_reason:oom"]["p"] == pytest.approx(0.25)
    assert stats["status:completed"]["pos"] == 3


def test_stat_for_synthesizes_zero_for_vanished_categorical(fds) -> None:
    stats = fds.window_feature_stats(_rows(fds, [job_doc(_days_ago(1))] * 4))
    zero = fds._stat_for(stats, "failure_reason:oom")
    assert zero is not None and zero["p"] == 0.0 and zero["n"] == 4
    assert fds._stat_for(stats, "flag_never_seen") is None


# ── collapse / spike / new-signal detection ───────────────────────────────────

def _stats(fds, pairs: dict[str, tuple[int, int]]) -> dict:
    """{name: (positives, n)} → the stats shape the detectors read."""
    return {name: {"p": pos / n, "n": n, "pos": pos} for name, (pos, n) in pairs.items()}


def test_collapse_detected(fds) -> None:
    cfg = fds.DriftConfig()
    prior = _stats(fds, {"motion:any": (18, 20)})     # 90%
    trailing = _stats(fds, {"motion:any": (1, 20)})   # 5% → 94% relative drop
    found = fds.detect_prevalence_drift(trailing, prior, cfg, "short")
    assert len(found) == 1
    f = found[0]
    assert f["kind"] == "collapse" and f["severity"] == "CRITICAL"
    assert f["metric"] == "motion:any" and f["segment"] == "short"


def test_collapse_needs_min_prior_prevalence(fds) -> None:
    cfg = fds.DriftConfig()
    prior = _stats(fds, {"rare_feature": (4, 20)})    # 20% < collapse_min_prior 30%
    trailing = _stats(fds, {"rare_feature": (0, 20)})
    assert fds.detect_prevalence_drift(trailing, prior, cfg, "short") == []


def test_collapse_needs_60pct_relative_drop(fds) -> None:
    cfg = fds.DriftConfig()
    prior = _stats(fds, {"f": (16, 20)})              # 80%
    trailing = _stats(fds, {"f": (8, 20)})            # 40% → only 50% relative drop
    assert fds.detect_prevalence_drift(trailing, prior, cfg, "short") == []


def test_spike_detected_and_ratio_guard(fds) -> None:
    cfg = fds.DriftConfig()
    prior = _stats(fds, {"failure_reason:oom": (2, 20)})     # 10%
    trailing = _stats(fds, {"failure_reason:oom": (8, 20)})  # 40% = 4x
    found = fds.detect_prevalence_drift(trailing, prior, cfg, "episode")
    assert [f["kind"] for f in found] == ["spike"]
    assert found[0]["severity"] == "WARNING"
    # 2x is under the 2.5x threshold
    trailing2 = _stats(fds, {"failure_reason:oom": (4, 20)})
    assert fds.detect_prevalence_drift(trailing2, prior, cfg, "episode") == []


def test_new_signal_from_zero_prior(fds) -> None:
    cfg = fds.DriftConfig()
    prior = _stats(fds, {"failure_reason:new_bug": (0, 20)})
    trailing = _stats(fds, {"failure_reason:new_bug": (6, 20)})  # 30% >= 25%, 6 >= 3 positives
    found = fds.detect_prevalence_drift(trailing, prior, cfg, "episode")
    assert [f["kind"] for f in found] == ["new_signal"]
    # only 2 positives → guarded
    trailing2 = _stats(fds, {"failure_reason:new_bug": (2, 8)})
    prior2 = _stats(fds, {"failure_reason:new_bug": (0, 20)})
    assert fds.detect_prevalence_drift(trailing2, prior2, cfg, "episode") == []


def test_volume_guard_blocks_small_windows(fds) -> None:
    cfg = fds.DriftConfig()
    prior = _stats(fds, {"motion:any": (7, 7)})       # n=7 < 8
    trailing = _stats(fds, {"motion:any": (0, 20)})
    assert fds.detect_prevalence_drift(trailing, prior, cfg, "short") == []


def test_numeric_collapse_and_spike(fds) -> None:
    cfg = fds.DriftConfig()
    prior = {"distinct_content_hashes": {"mean": 5.0, "n": 20},
             "cost_usd": {"mean": 0.02, "n": 20}}
    trailing = {"distinct_content_hashes": {"mean": 1.0, "n": 20},   # 80% drop
                "cost_usd": {"mean": 0.06, "n": 20}}                 # 3x
    found = fds.detect_numeric_drift(trailing, prior, cfg, "short")
    kinds = {f["metric"]: f["kind"] for f in found}
    assert kinds == {"distinct_content_hashes": "collapse", "cost_usd": "spike"}
    sev = {f["metric"]: f["severity"] for f in found}
    assert sev["distinct_content_hashes"] == "CRITICAL"


def test_numeric_volume_guard(fds) -> None:
    cfg = fds.DriftConfig()
    prior = {"cost_usd": {"mean": 0.02, "n": 5}}      # n < 8
    trailing = {"cost_usd": {"mean": 0.2, "n": 20}}
    assert fds.detect_numeric_drift(trailing, prior, cfg, "short") == []


# ── gate-meta ─────────────────────────────────────────────────────────────────

def _gate_rows(fds, n_failed: int, n_passed: int, axis: str = "short_visual_gate"):
    rows = []
    for i in range(n_failed + n_passed):
        rows.append({
            "created_at": _days_ago(1), "segment": "short", "categoricals": {},
            "flags": {}, "numerics": {}, "gate_axes": {axis: i < n_failed},
        })
    return rows


def test_gate_meta_fires_at_80pct(fds) -> None:
    cfg = fds.DriftConfig()
    found = fds.detect_gate_meta(_gate_rows(fds, 18, 2), cfg, "short")
    assert len(found) == 1
    f = found[0]
    assert f["kind"] == "gate_meta" and f["severity"] == "CRITICAL"
    assert f["metric"] == "gate:short_visual_gate"
    assert "gate-punishing-manufactured-symptom" in f["detail"]


def test_gate_meta_quiet_below_threshold_and_volume(fds) -> None:
    cfg = fds.DriftConfig()
    assert fds.detect_gate_meta(_gate_rows(fds, 10, 10), cfg, "short") == []   # 50%
    assert fds.detect_gate_meta(_gate_rows(fds, 5, 0), cfg, "short") == []     # n=5 < 8


# ── transition bisect + revision correlation ──────────────────────────────────

def test_bisect_transition_day_finds_step(fds) -> None:
    cfg = fds.DriftConfig()
    series = [(date(2026, 7, d), 1.0 if d < 20 else 0.0, 10) for d in range(12, 26)]
    day = fds.bisect_transition_day(series, fds.collapse_predicate(0.9, cfg))
    assert day == date(2026, 7, 20)


def test_bisect_ignores_isolated_bad_day(fds) -> None:
    cfg = fds.DriftConfig()
    values = {15: 0.0}  # one bad day mid-good, then good again, then collapse at 22
    series = [(date(2026, 7, d), values.get(d, 1.0 if d < 22 else 0.0), 10)
              for d in range(12, 26)]
    day = fds.bisect_transition_day(series, fds.collapse_predicate(0.9, cfg))
    assert day == date(2026, 7, 22)


def test_bisect_none_when_never_collapsed(fds) -> None:
    cfg = fds.DriftConfig()
    series = [(date(2026, 7, d), 0.9, 10) for d in range(12, 26)]
    assert fds.bisect_transition_day(series, fds.collapse_predicate(0.9, cfg)) is None
    assert fds.bisect_transition_day([], fds.collapse_predicate(0.9, cfg)) is None


def test_daily_series_skips_low_volume_days(fds) -> None:
    docs = [job_doc(_days_ago(1)) for _ in range(4)] + [job_doc(_days_ago(2))]  # day2: n=1
    series = fds.daily_series(_rows(fds, docs), "motion:any", "prevalence", 3)
    assert len(series) == 1
    assert series[0][1] == 1.0 and series[0][2] == 4


def test_nearest_revisions_orders_caps_and_windows(fds) -> None:
    t = date(2026, 7, 20)
    revs = [
        {"service": "s1", "revision": "r-near", "deployed_at": datetime(2026, 7, 19, 22, tzinfo=timezone.utc)},
        {"service": "s2", "revision": "r-far", "deployed_at": datetime(2026, 7, 10, 0, tzinfo=timezone.utc)},
        {"service": "s3", "revision": "r-day2", "deployed_at": datetime(2026, 7, 21, 12, tzinfo=timezone.utc)},
        {"service": "s4", "revision": "r-day3", "deployed_at": datetime(2026, 7, 22, 12, tzinfo=timezone.utc)},
        {"service": "s5", "revision": "r-same", "deployed_at": datetime(2026, 7, 20, 1, tzinfo=timezone.utc)},
    ]
    out = fds.nearest_revisions(t, revs, k=3)
    assert [r["revision"] for r in out] == ["r-same", "r-near", "r-day2"]  # nearest 3, no r-far
    assert all("delta_hours" in r for r in out)


# ── end-to-end pure run (the motion-incident scenario) ────────────────────────

def _motion_incident_docs():
    """Prior week: healthy motion shorts. Trailing week: still-only clips + punishing gate."""
    docs = []
    for d in range(8, 14):  # prior window days
        for _ in range(3):
            docs.append(job_doc(_days_ago(d + 0.1)))
    still = [_clip("still", "same"), _clip("still", "same"), _clip("still", "same")]
    for d in range(0, 6):   # trailing window days
        for _ in range(3):
            docs.append(job_doc(_days_ago(d + 0.1), clips=still,
                                retries={"visual_gate_retry_count": 1}))
    return docs


def test_run_sentinel_motion_incident(fds) -> None:
    cfg = fds.DriftConfig()
    rows = _rows(fds, _motion_incident_docs())
    revs = [{"service": "kitesforu-worker-visuals", "revision": "rev-abc",
             "deployed_at": _days_ago(6.2)}]
    result = fds.run_sentinel(rows, NOW, cfg, revisions=revs)
    by_metric = {(f["segment"], f["metric"]): f for f in result["findings"]}
    motion = by_metric[("short", "motion:any")]
    assert motion["kind"] == "collapse" and motion["severity"] == "CRITICAL"
    assert motion["transition_date"] is not None
    assert any(s["revision"] == "rev-abc" for s in motion["suspect_revisions"])
    gate = by_metric[("short", "gate:short_visual_gate")]
    assert gate["kind"] == "gate_meta" and gate["severity"] == "CRITICAL"
    assert fds.has_critical(result)
    # CRITICAL findings sort first
    assert result["findings"][0]["severity"] == "CRITICAL"


def test_run_sentinel_healthy_fleet_quiet(fds) -> None:
    cfg = fds.DriftConfig()
    docs = [job_doc(_days_ago(d + 0.1)) for d in range(0, 14) for _ in range(2)]
    result = fds.run_sentinel(_rows(fds, docs), NOW, cfg)
    assert result["findings"] == []
    assert not fds.has_critical(result)


def test_run_sentinel_insufficient_prior_skips_but_still_gate_checks(fds) -> None:
    cfg = fds.DriftConfig()
    still = [_clip("still", "same")] * 3
    docs = [job_doc(_days_ago(d % 6 + 0.1), clips=still,
                    retries={"visual_gate_retry_count": 1}) for d in range(12)]
    result = fds.run_sentinel(_rows(fds, docs), NOW, cfg)  # no prior window at all
    assert result["segments"]["short"]["skipped"].startswith("insufficient prior")
    kinds = {f["kind"] for f in result["findings"]}
    assert kinds == {"gate_meta"}  # no collapse possible, but the gate alarm still fires


def test_run_sentinel_baseline_fallback(fds) -> None:
    cfg = fds.DriftConfig()
    still = [_clip("still", "x")] * 3
    trailing_docs = [job_doc(_days_ago(d % 6 + 0.1), clips=still) for d in range(12)]
    rows = _rows(fds, trailing_docs)
    # baseline snapshot from a healthy earlier battery
    healthy = _rows(fds, [job_doc(_days_ago(1))] * 12)
    baseline = fds.battery_snapshot(fds.group_by_segment(healthy))
    result = fds.run_sentinel(rows, NOW, cfg, baseline=baseline)
    motion = [f for f in result["findings"]
              if f["metric"] == "motion:any" and f["segment"] == "short"]
    assert len(motion) == 1
    assert motion[0]["kind"] == "collapse"
    assert motion[0]["prior_source"] == "baseline"
    assert result["segments"]["short"].get("prior_source") == "baseline"


# ── reports + baseline files ──────────────────────────────────────────────────

def test_write_reports_and_baseline_roundtrip(fds, tmp_path: Path) -> None:
    cfg = fds.DriftConfig()
    rows = _rows(fds, _motion_incident_docs())
    result = fds.run_sentinel(rows, NOW, cfg)
    md_path, json_path = fds.write_reports(tmp_path, result)
    assert md_path.name == "2026-07-25.md" and json_path.name == "2026-07-25.json"
    md = md_path.read_text()
    assert "Fleet Drift Sentinel" in md and "motion:any" in md
    loaded = json.loads(json_path.read_text())
    assert loaded["critical_count"] == result["critical_count"]

    snap = fds.battery_snapshot(fds.group_by_segment(rows))
    fds.save_baseline(tmp_path, snap, NOW)
    assert fds.load_baseline(tmp_path) == json.loads(json.dumps(snap))
    assert fds.load_baseline(tmp_path / "missing") is None


def test_render_markdown_no_findings(fds) -> None:
    result = {"generated_at": NOW.isoformat(), "window_days": 7,
              "segments": {"short": {"trailing_n": 10, "prior_n": 10}},
              "findings": [], "critical_count": 0}
    md = fds.render_markdown(result)
    assert "No drift detected" in md
