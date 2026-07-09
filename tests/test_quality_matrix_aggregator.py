"""Unit tests for ``kitesforu_qa.harness.quality_matrix`` — the MEASURED QUALITY ENGINE's aggregator
and ranker. Pure, deterministic, $0 — no network, no Firestore, no LLM. Every fixture is a synthetic
"cell" matching the shape ``kitesforu_qa.scorecard.score_short()`` actually returns
(``{job_id, axes: {axis_name: {score, floor, ...}}, genre, format, quality_tier, _scored}``).
"""
from __future__ import annotations

from datetime import datetime, timezone

from kitesforu_qa.harness.quality_matrix import (
    DEFAULT_TARGET_FORMATS,
    DEFAULT_TARGET_GENRES,
    aggregate_all_axes,
    aggregate_axis,
    aggregate_by_group,
    group_cells,
    instrumentation_gaps,
    parse_datetime,
    propose_matrix_fill,
    rank_systematic_weaknesses,
    render_backlog_markdown,
)
from kitesforu_qa.scorecard.rubric import RUBRIC, RUBRIC_BY_NAME

ALL_AXES = [spec.name for spec in RUBRIC]


def _axis(score: float | None, floor: int | None = None) -> dict:
    return {"score": score, "floor": floor if floor is not None else 0, "pass": (score is not None and floor is not None and score >= floor)}


def _cell(
    job_id: str,
    *,
    genre: str = "explainer",
    fmt: str = "short",
    tier: str = "low",
    scored: bool = True,
    error: str | None = None,
    **axis_scores: float | None,
) -> dict:
    if not scored:
        return {"job_id": job_id, "_scored": False, "_error": error or "boom", "genre": "unknown", "format": "unknown", "quality_tier": "unknown"}
    axes = {name: _axis(axis_scores.get(name), RUBRIC_BY_NAME[name].floor) for name in ALL_AXES}
    return {"job_id": job_id, "axes": axes, "genre": genre, "format": fmt, "quality_tier": tier, "_scored": True}


# ── parse_datetime ─────────────────────────────────────────────────────────────


def test_parse_datetime_none() -> None:
    assert parse_datetime(None) is None


def test_parse_datetime_iso_string_with_z() -> None:
    dt = parse_datetime("2026-07-01T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 7 and dt.day == 1
    assert dt.tzinfo is not None


def test_parse_datetime_garbage_string_returns_none() -> None:
    assert parse_datetime("not-a-date") is None


def test_parse_datetime_naive_datetime_gets_utc_stamped() -> None:
    dt = parse_datetime(datetime(2026, 7, 1, 12, 0, 0))
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_datetime_aware_datetime_passthrough() -> None:
    aware = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert parse_datetime(aware) == aware


def test_parse_datetime_duck_typed_object_with_tzinfo() -> None:
    class FakeTimestamp:
        tzinfo = timezone.utc

    fake = FakeTimestamp()
    assert parse_datetime(fake) is fake


def test_parse_datetime_unparseable_object_returns_none() -> None:
    assert parse_datetime(12345) is None


# ── aggregate_axis / aggregate_all_axes ─────────────────────────────────────────


def test_aggregate_axis_computes_mean_min_max() -> None:
    cells = [
        _cell("a", hook_stop_power=100.0),
        _cell("b", hook_stop_power=80.0),
        _cell("c", hook_stop_power=60.0),
    ]
    agg = aggregate_axis(cells, "hook_stop_power")
    assert agg.n_scored == 3
    assert agg.mean == 80.0
    assert agg.min == 60.0
    assert agg.max == 100.0
    assert agg.n_below_floor == 1  # 60 < floor 80
    assert agg.pass_rate == round(2 / 3, 3)


def test_aggregate_axis_excludes_none_scores_not_zero() -> None:
    cells = [
        _cell("a", hook_stop_power=100.0),
        _cell("b", hook_stop_power=None),  # missing instrumentation — must NOT count as 0
    ]
    agg = aggregate_axis(cells, "hook_stop_power")
    assert agg.n_scored == 1
    assert agg.mean == 100.0  # not (100+0)/2 = 50


def test_aggregate_axis_excludes_unscored_cells() -> None:
    cells = [
        _cell("a", hook_stop_power=90.0),
        _cell("b", scored=False),
    ]
    agg = aggregate_axis(cells, "hook_stop_power")
    assert agg.n_scored == 1
    assert agg.n_total_cells == 2
    assert agg.mean == 90.0


def test_aggregate_axis_no_scores_is_all_none() -> None:
    cells = [_cell("a", scored=False)]
    agg = aggregate_axis(cells, "hook_stop_power")
    assert agg.n_scored == 0
    assert agg.mean is None
    assert agg.min is None
    assert agg.max is None
    assert agg.pass_rate is None


def test_aggregate_all_axes_returns_all_eight() -> None:
    cells = [_cell("a", **dict.fromkeys(ALL_AXES, 90.0))]
    agg = aggregate_all_axes(cells)
    assert set(agg.keys()) == set(ALL_AXES)


# ── group_cells / aggregate_by_group ─────────────────────────────────────────────


def test_group_cells_groups_by_genre_and_format() -> None:
    cells = [
        _cell("a", genre="horror", fmt="short"),
        _cell("b", genre="horror", fmt="short"),
        _cell("c", genre="horror", fmt="episode"),
        _cell("d", genre="explainer", fmt="short"),
    ]
    groups = group_cells(cells)
    assert len(groups[("horror", "short")]) == 2
    assert len(groups[("horror", "episode")]) == 1
    assert len(groups[("explainer", "short")]) == 1


def test_aggregate_by_group_computes_per_group_axes() -> None:
    cells = [
        _cell("a", genre="horror", fmt="short", hook_stop_power=100.0),
        _cell("b", genre="explainer", fmt="short", hook_stop_power=50.0),
    ]
    by_group = aggregate_by_group(cells)
    assert by_group[("horror", "short")]["hook_stop_power"].mean == 100.0
    assert by_group[("explainer", "short")]["hook_stop_power"].mean == 50.0


# ── rank_systematic_weaknesses ──────────────────────────────────────────────────


def test_systematic_weakness_ranks_above_one_off() -> None:
    # modality_mix floor=70: 8/10 cells score 40 (systematic, gap=30); hook_stop_power floor=80:
    # only 1/10 cells scores 20 (a one-off, gap=60). Severity = n_below * mean_gap:
    #   modality_mix: 8 * 30 = 240
    #   hook_stop_power: 1 * 60 = 60
    # -> modality_mix must rank first despite a SMALLER per-cell gap.
    cells = []
    for i in range(10):
        modality = 40.0 if i < 8 else 90.0
        hook = 20.0 if i == 0 else 95.0
        cells.append(_cell(f"job{i}", modality_mix=modality, hook_stop_power=hook))
    ranked = rank_systematic_weaknesses(cells)
    names = [w.axis for w in ranked]
    assert names.index("modality_mix") < names.index("hook_stop_power")
    top = ranked[0]
    assert top.axis == "modality_mix"
    assert top.n_below_floor == 8
    assert top.severity == 240.0
    assert top.hypothesis  # hypothesis text populated
    assert top.fix_area


def test_axis_with_zero_below_floor_is_omitted() -> None:
    cells = [_cell("a", cost_safety=100.0), _cell("b", cost_safety=100.0)]
    ranked = rank_systematic_weaknesses(cells)
    assert all(w.axis != "cost_safety" for w in ranked)


def test_rank_systematic_weaknesses_handles_no_cells() -> None:
    assert rank_systematic_weaknesses([]) == []


def test_rank_systematic_weaknesses_excludes_unscored_cells_from_gaps() -> None:
    cells = [_cell("a", hook_stop_power=10.0), _cell("b", scored=False)]
    ranked = rank_systematic_weaknesses(cells)
    hook = next(w for w in ranked if w.axis == "hook_stop_power")
    assert hook.affected_job_ids == ["a"]


# ── instrumentation_gaps ─────────────────────────────────────────────────────────


def test_instrumentation_gap_flags_zero_coverage_axis() -> None:
    # sync_exactness never scored (None on every cell) -> 0% coverage, should be flagged.
    cells = [_cell(f"job{i}", hook_stop_power=90.0, sync_exactness=None) for i in range(5)]
    gaps = instrumentation_gaps(cells)
    axes_flagged = [g.axis for g in gaps]
    assert "sync_exactness" in axes_flagged
    assert "hook_stop_power" not in axes_flagged  # fully covered, must not be flagged


def test_instrumentation_gap_respects_coverage_floor() -> None:
    # 60% coverage should NOT be flagged at the default 50% floor, but WOULD be at a 70% floor.
    cells = [_cell(f"job{i}", hook_stop_power=(90.0 if i < 3 else None)) for i in range(5)]
    assert not any(g.axis == "hook_stop_power" for g in instrumentation_gaps(cells, coverage_floor=0.5))
    assert any(g.axis == "hook_stop_power" for g in instrumentation_gaps(cells, coverage_floor=0.7))


def test_instrumentation_gaps_empty_cells_no_crash() -> None:
    assert instrumentation_gaps([]) == []


# ── propose_matrix_fill ──────────────────────────────────────────────────────────


def test_propose_matrix_fill_identifies_covered_and_missing() -> None:
    cells = [_cell("a", genre="explainer", fmt="short")]
    result = propose_matrix_fill(cells, target_genres=("explainer", "horror"), target_formats=("short", "episode"))
    assert result["target_cells"] == 4
    assert result["covered_cells"] == 1
    missing_pairs = {(m["genre"], m["format"]) for m in result["missing_cells"]}
    assert ("explainer", "short") not in missing_pairs
    assert ("horror", "short") in missing_pairs
    assert ("horror", "episode") in missing_pairs
    assert ("explainer", "episode") in missing_pairs


def test_propose_matrix_fill_cost_estimate_scales_with_gaps() -> None:
    cells: list[dict] = []
    result = propose_matrix_fill(cells, target_genres=("a", "b"), target_formats=("short",))
    assert result["covered_cells"] == 0
    assert len(result["missing_cells"]) == 2
    assert result["t3_fill_estimate_usd"] == round(2 * 0.025, 3)


def test_propose_matrix_fill_ignores_unscored_cells() -> None:
    cells = [_cell("a", genre="explainer", fmt="short", scored=False)]
    result = propose_matrix_fill(cells, target_genres=("explainer",), target_formats=("short",))
    assert result["covered_cells"] == 0  # the unscored cell doesn't count as coverage


def test_propose_matrix_fill_default_targets_are_nonempty() -> None:
    assert len(DEFAULT_TARGET_GENRES) > 0
    assert len(DEFAULT_TARGET_FORMATS) > 0


# ── render_backlog_markdown ──────────────────────────────────────────────────────


def test_render_backlog_markdown_empty_cells_does_not_crash() -> None:
    md = render_backlog_markdown([], generated_at="2026-07-08T00:00:00Z", project="kitesforu-dev", mode="test")
    assert "QUALITY_BACKLOG" in md
    assert "nothing systematic to rank" in md


def test_render_backlog_markdown_contains_all_sections() -> None:
    cells = [_cell("a", genre="horror", fmt="short", hook_stop_power=10.0)]
    md = render_backlog_markdown(cells, generated_at="2026-07-08T00:00:00Z", project="kitesforu-dev", mode="test")
    for heading in (
        "## Baseline Summary",
        "## Per (Genre x Format) Breakdown",
        "## Top Ranked Systematic Weaknesses",
        "## Instrumentation Gaps",
        "## Unscored Jobs",
        "## PROPOSED MATRIX FILL",
    ):
        assert heading in md


def test_render_backlog_markdown_respects_top_n() -> None:
    # Make every axis fail badly so all 8 are systematic weaknesses; top_n=2 must cap the section.
    cells = [_cell(f"job{i}", **dict.fromkeys(ALL_AXES, 1.0)) for i in range(3)]
    md = render_backlog_markdown(cells, generated_at="x", project="p", mode="m", top_n=2)
    assert md.count("###") == 2


def test_render_backlog_markdown_lists_unscored_jobs() -> None:
    cells = [_cell("bad-job", scored=False, error="job not found")]
    md = render_backlog_markdown(cells, generated_at="x", project="p", mode="m")
    assert "bad-job" in md
    assert "job not found" in md
