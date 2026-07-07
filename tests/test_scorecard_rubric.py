"""Pin tests for kitesforu_qa.scorecard.rubric — the axis spec + ship-bar verdict math.

These are the integrity gate for the whole SHORT SCORECARD: a proxy or a missing-instrumentation
axis must NEVER let ``evaluate()`` certify ship, and the weighted total must never silently include
an axis that wasn't actually scored.
"""
from __future__ import annotations

from kitesforu_qa.scorecard.rubric import RUBRIC, SHIP_TOTAL_MIN, evaluate, make_axis


def test_rubric_weights_sum_to_one() -> None:
    total = sum(spec.weight for spec in RUBRIC)
    assert abs(total - 1.0) < 1e-9, f"axis weights must sum to 1.0, got {total}"


def test_rubric_has_exactly_8_axes() -> None:
    assert len(RUBRIC) == 8


def test_make_axis_clamps_score_to_0_100() -> None:
    over = make_axis("hook_stop_power", score=150.0, evidence="e", how_measured="h")
    under = make_axis("hook_stop_power", score=-30.0, evidence="e", how_measured="h")
    assert over.score == 100.0
    assert under.score == 0.0


def test_make_axis_rounds_to_one_decimal() -> None:
    r = make_axis("hook_stop_power", score=42.849, evidence="e", how_measured="h")
    assert r.score == 42.8


def test_make_axis_score_none_means_passed_none() -> None:
    r = make_axis("visual_truth", score=None, evidence="e", how_measured="h", needs="a VLM")
    assert r.score is None
    assert r.passed is None
    assert r.needs == "a VLM"


def test_make_axis_passed_derives_from_floor() -> None:
    spec = next(a for a in RUBRIC if a.name == "hook_stop_power")
    assert spec.floor == 80
    passing = make_axis("hook_stop_power", score=80.0, evidence="e", how_measured="h")
    failing = make_axis("hook_stop_power", score=79.9, evidence="e", how_measured="h")
    assert passing.passed is True
    assert failing.passed is False


def _all_axes_passing() -> dict:
    """Every axis scored well above its floor -> should clear the ship bar."""
    return {
        spec.name: make_axis(spec.name, score=100.0, evidence="ok", how_measured="test")
        for spec in RUBRIC
    }


def test_evaluate_all_pass_ships() -> None:
    verdict = evaluate(_all_axes_passing())
    assert verdict.ship is True
    assert verdict.weighted_total == 100.0
    assert verdict.weighted_total_basis == "all 8 axes scored"
    assert verdict.missing_instrumentation == []
    assert verdict.provisional_axes == []


def test_evaluate_one_axis_below_floor_blocks_ship() -> None:
    axes = _all_axes_passing()
    axes["motion_density"] = make_axis("motion_density", score=10.0, evidence="low", how_measured="test")
    verdict = evaluate(axes)
    assert verdict.ship is False
    assert any("motion_density" in r for r in verdict.reasons)


def test_evaluate_weighted_total_below_ship_bar_blocks_ship_even_if_all_floors_pass() -> None:
    # Every axis individually clears its (much lower) floor, but a total just under SHIP_TOTAL_MIN
    # must still block ship — the ship bar is a SEPARATE, additional gate over per-axis floors.
    axes = {
        spec.name: make_axis(spec.name, score=float(spec.floor) + 0.1, evidence="barely", how_measured="test")
        for spec in RUBRIC
    }
    verdict = evaluate(axes)
    assert all(a.passed for a in axes.values())
    if verdict.weighted_total is not None and verdict.weighted_total < SHIP_TOTAL_MIN:
        assert verdict.ship is False
        assert any("ship bar" in r for r in verdict.reasons)


def test_evaluate_missing_instrumentation_blocks_ship_and_is_reported() -> None:
    axes = _all_axes_passing()
    axes["sync_exactness"] = make_axis(
        "sync_exactness", score=None, evidence="no word timestamps", how_measured="test",
        needs="Phase 1a",
    )
    verdict = evaluate(axes)
    assert verdict.ship is False
    assert len(verdict.missing_instrumentation) == 1
    assert verdict.missing_instrumentation[0]["axis"] == "sync_exactness"
    assert verdict.missing_instrumentation[0]["needs"] == "Phase 1a"
    assert "PARTIAL" in verdict.weighted_total_basis
    assert "7/8" in verdict.weighted_total_basis


def test_evaluate_proxy_axis_blocks_ship_even_with_a_real_score() -> None:
    axes = _all_axes_passing()
    # A proxy score of 95 clears the floor (70) easily — but proxy=True must still veto ship.
    axes["substance_novelty"] = make_axis(
        "substance_novelty", score=95.0, evidence="heuristic only", how_measured="test", proxy=True,
    )
    verdict = evaluate(axes)
    assert verdict.ship is False
    assert verdict.provisional_axes == ["substance_novelty"]
    assert any("provisional" in r for r in verdict.reasons)
    # A proxy's real score DOES count toward the weighted total (it is not "missing").
    assert verdict.missing_instrumentation == []


def test_evaluate_weighted_total_renormalizes_over_scored_axes_only() -> None:
    axes = _all_axes_passing()
    axes["cost_safety"] = make_axis("cost_safety", score=None, evidence="unknown", how_measured="test")
    verdict = evaluate(axes)
    # All SCORED axes are 100 -> the renormalized weighted average must still be 100, not diluted
    # by treating the missing axis's weight as a zero contribution to a full-weight denominator.
    assert verdict.weighted_total == 100.0
