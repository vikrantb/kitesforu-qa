"""Unit tests for the EPISODES + COURSES extension of ``kitesforu_qa.harness.quality_matrix`` — the
Measured Quality Engine beyond 9:16 shorts. Pure, deterministic, $0 — no network, no Firestore, no
LLM (mirrors ``tests/test_quality_matrix_aggregator.py``'s style for the short-scorecard engine).

Two kinds of fixtures are used:
  * synthetic CHECK-based "cells" (``_ec_cell``) for the pure aggregation/ranking math — the
    check-level analogue of ``test_quality_matrix_aggregator.py``'s ``_cell`` axis fixtures.
  * a real ``Artifact.from_doc`` + ``score_episode_or_course`` smoke test to prove the wiring
    (``detect_content_class`` + ``run_scorecard`` + the cell shape) end-to-end on a minimal doc,
    without needing ffmpeg/media (the underlying checks are already covered by
    ``tests/test_check_batteries.py``; this file tests the NEW aggregation layer, not the checks).
"""
from __future__ import annotations

from kitesforu_qa.harness.artifact import Artifact
from kitesforu_qa.harness.quality_matrix import (
    CONTENT_CLASS_COURSE,
    CONTENT_CLASS_EPISODE,
    CONTENT_CLASS_SHORT,
    EPISODES_COURSES_SECTION_END,
    EPISODES_COURSES_SECTION_START,
    aggregate_all_checks,
    aggregate_all_dimensions,
    aggregate_check,
    aggregate_dimension,
    detect_content_class,
    group_cells_by_content_class,
    rank_systematic_check_failures,
    render_episode_course_section,
    score_episode_or_course,
    upsert_markdown_section,
)


def _chk(check_id: str, dimension: str, *, passed: bool = True, score: float = 1.0,
         severity: str = "medium", genre: str | None = None) -> dict:
    return {
        "check_id": check_id, "dimension": dimension, "passed": passed, "score": score,
        "severity": severity, "genre": genre, "evidence": "", "skipped": False, "error": None,
    }


def _ec_cell(job_id: str, *, genre: str = "explainer", content_class: str = "episode",
             tier: str = "low", scored: bool = True, error: str | None = None,
             checks: list[dict] | None = None) -> dict:
    if not scored:
        return {"job_id": job_id, "_scored": False, "_error": error or "boom",
                "genre": "unknown", "content_class": "unknown", "quality_tier": "unknown"}
    checks = checks or []
    return {
        "job_id": job_id, "genre": genre, "content_class": content_class, "quality_tier": tier,
        "overall_passed": all(c["passed"] for c in checks if c.get("severity") in ("critical", "high")),
        "dimensions": {
            dim: {"passed": all(c["passed"] for c in checks if c["dimension"] == dim),
                  "score": (sum(c["score"] for c in checks if c["dimension"] == dim) /
                            max(1, sum(1 for c in checks if c["dimension"] == dim))),
                  "failed": sum(1 for c in checks if c["dimension"] == dim and not c["passed"])}
            for dim in {c["dimension"] for c in checks}
        },
        "checks": checks,
        "n_checks_total": len(checks),
        "n_checks_failed": sum(1 for c in checks if not c["passed"]),
        "_scored": True,
    }


# ── detect_content_class ─────────────────────────────────────────────────────────


def test_detect_content_class_course_wins_via_parent_type() -> None:
    art = Artifact.from_doc({"job_id": "c1", "parent_type": "course", "episode_profile": {"genre": "explainer"}})
    assert detect_content_class(art) == CONTENT_CLASS_COURSE


def test_detect_content_class_short_via_format() -> None:
    art = Artifact.from_doc({"job_id": "s1", "format": "short_video", "episode_profile": {"genre": "horror"}})
    assert detect_content_class(art) == CONTENT_CLASS_SHORT


def test_detect_content_class_defaults_to_episode() -> None:
    art = Artifact.from_doc({
        "job_id": "e1", "episode_profile": {"genre": "explainer"},
        "inputs": {"duration_min": 10.0},
    })
    assert detect_content_class(art) == CONTENT_CLASS_EPISODE


def test_detect_content_class_course_wins_even_if_also_short_shaped() -> None:
    # Defensive ordering check: parent_type=='course' takes precedence over a short-shaped doc.
    art = Artifact.from_doc({"job_id": "cs1", "parent_type": "course", "format": "short_video"})
    assert detect_content_class(art) == CONTENT_CLASS_COURSE


# ── score_episode_or_course (real wiring smoke test — no media needed) ──────────


def test_score_episode_or_course_returns_expected_shape() -> None:
    doc = {
        "job_id": "job-ep-1",
        "status": "completed",
        "episode_profile": {"genre": "explainer"},
        "inputs": {"duration_min": 10.0},
        "script": {"dialogue": [
            {"speaker": "Host1", "text": "For example, imagine a neural network is like a filter."},
            {"speaker": "Host2", "text": "That is a great point, it works by adjusting weights."},
        ]},
        "quality_tier": "low",
    }
    art = Artifact.from_doc(doc)
    cell = score_episode_or_course(art)
    assert cell["job_id"] == "job-ep-1"
    assert cell["genre"] == "explainer"
    assert cell["content_class"] == CONTENT_CLASS_EPISODE
    assert cell["quality_tier"] == "low"
    assert cell["_scored"] is True
    assert "dimensions" in cell and "structure" in cell["dimensions"]
    assert isinstance(cell["checks"], list) and len(cell["checks"]) > 0
    assert all(not c.get("skipped") for c in cell["checks"])
    assert cell["n_checks_total"] == len(cell["checks"])


def test_score_episode_or_course_detects_course_content_class() -> None:
    doc = {
        "job_id": "job-course-1", "status": "completed", "parent_type": "course",
        "episode_profile": {"genre": "explainer"},
        "script": {"dialogue": [{"speaker": "Host1", "text": "Compliance training example content."}]},
    }
    cell = score_episode_or_course(Artifact.from_doc(doc))
    assert cell["content_class"] == CONTENT_CLASS_COURSE


# ── group_cells_by_content_class ─────────────────────────────────────────────────


def test_group_cells_by_content_class() -> None:
    cells = [
        _ec_cell("a", content_class="episode"),
        _ec_cell("b", content_class="episode"),
        _ec_cell("c", content_class="course"),
    ]
    groups = group_cells_by_content_class(cells)
    assert len(groups["episode"]) == 2
    assert len(groups["course"]) == 1


# ── aggregate_check / aggregate_all_checks ───────────────────────────────────────


def test_aggregate_check_computes_fail_rate_and_mean_score() -> None:
    cells = [
        _ec_cell("a", checks=[_chk("audio.no_clipping", "audio-mix", passed=True, score=1.0, severity="high")]),
        _ec_cell("b", checks=[_chk("audio.no_clipping", "audio-mix", passed=False, score=0.0, severity="high")]),
        _ec_cell("c", checks=[_chk("audio.no_clipping", "audio-mix", passed=False, score=0.2, severity="high")]),
    ]
    agg = aggregate_check(cells, "audio.no_clipping")
    assert agg.n_applicable == 3
    assert agg.n_failed == 2
    assert agg.fail_rate == round(2 / 3, 3)
    assert agg.mean_score == round((1.0 + 0.0 + 0.2) / 3, 3)
    assert agg.dimension == "audio-mix"
    assert agg.severity == "high"


def test_aggregate_check_excludes_unscored_cells() -> None:
    cells = [
        _ec_cell("a", checks=[_chk("structure.has_script", "structure", passed=True)]),
        _ec_cell("b", scored=False),
    ]
    agg = aggregate_check(cells, "structure.has_script")
    assert agg.n_applicable == 1


def test_aggregate_all_checks_only_includes_checks_that_appeared() -> None:
    cells = [_ec_cell("a", checks=[_chk("structure.has_script", "structure")])]
    agg = aggregate_all_checks(cells)
    assert set(agg.keys()) == {"structure.has_script"}


def test_aggregate_check_unknown_id_is_empty() -> None:
    agg = aggregate_check([_ec_cell("a", checks=[])], "nonexistent.check")
    assert agg.n_applicable == 0
    assert agg.fail_rate is None
    assert agg.mean_score is None


# ── rank_systematic_check_failures ───────────────────────────────────────────────


def test_widespread_and_severe_check_outranks_one_off() -> None:
    # "audio.no_clipping" (severity=high, weight=3) fails on 8/10 cells -> weighted 8*3=24.
    # "structure.has_script" (severity=critical, weight=4) fails on 1/10 cells -> weighted 1*4=4.
    cells = []
    for i in range(10):
        clip_ok = i >= 8
        script_ok = i != 0
        cells.append(_ec_cell(
            f"job{i}",
            checks=[
                _chk("audio.no_clipping", "audio-mix", passed=clip_ok, severity="high"),
                _chk("structure.has_script", "structure", passed=script_ok, severity="critical"),
            ],
        ))
    ranked = rank_systematic_check_failures(cells)
    ids = [w.check_id for w in ranked]
    assert ids.index("audio.no_clipping") < ids.index("structure.has_script")
    top = ranked[0]
    assert top.check_id == "audio.no_clipping"
    assert top.n_failed == 8
    assert top.severity_weighted == 24.0


def test_check_with_zero_failures_is_omitted() -> None:
    cells = [_ec_cell("a", checks=[_chk("structure.has_script", "structure", passed=True)])] * 5
    ranked = rank_systematic_check_failures(cells)
    assert all(w.check_id != "structure.has_script" for w in ranked)


def test_min_applicable_guard_excludes_low_sample_checks() -> None:
    # Only 1 applicable instance, 100% fail rate -- must NOT be ranked as "systematic".
    cells = [_ec_cell("a", checks=[_chk("rare.check", "content", passed=False)])]
    ranked = rank_systematic_check_failures(cells, min_applicable=2)
    assert ranked == []
    # Lowering the guard surfaces it.
    ranked_loose = rank_systematic_check_failures(cells, min_applicable=1)
    assert any(w.check_id == "rare.check" for w in ranked_loose)


def test_rank_systematic_check_failures_handles_no_cells() -> None:
    assert rank_systematic_check_failures([]) == []


def test_affected_job_ids_only_lists_failing_jobs() -> None:
    cells = [
        _ec_cell("pass1", checks=[_chk("x.check", "content", passed=True)]),
        _ec_cell("fail1", checks=[_chk("x.check", "content", passed=False)]),
        _ec_cell("fail2", checks=[_chk("x.check", "content", passed=False)]),
    ]
    ranked = rank_systematic_check_failures(cells, min_applicable=1)
    top = next(w for w in ranked if w.check_id == "x.check")
    assert top.affected_job_ids == ["fail1", "fail2"]


# ── aggregate_dimension / aggregate_all_dimensions ───────────────────────────────


def test_aggregate_dimension_mean_and_pass_rate() -> None:
    cells = [
        _ec_cell("a", checks=[_chk("audio.a", "audio-mix", passed=True, score=1.0)]),
        _ec_cell("b", checks=[_chk("audio.b", "audio-mix", passed=False, score=0.0)]),
    ]
    agg = aggregate_dimension(cells, "audio-mix")
    assert agg.n_cells == 2
    assert agg.n_total_cells == 2
    assert agg.mean_score == 0.5
    assert agg.n_checks_total == 2
    assert agg.n_checks_failed == 1
    # cell "b"'s StageResult.passed is False (its one audio-mix check failed) -> pass_rate 1/2
    assert agg.pass_rate == 0.5


def test_aggregate_dimension_cells_without_the_dimension_excluded_from_n_cells() -> None:
    cells = [
        _ec_cell("a", checks=[_chk("audio.a", "audio-mix")]),
        _ec_cell("b", checks=[_chk("content.b", "content")]),
    ]
    agg = aggregate_dimension(cells, "audio-mix")
    assert agg.n_cells == 1
    assert agg.n_total_cells == 2


def test_aggregate_all_dimensions_covers_every_dimension_seen() -> None:
    cells = [_ec_cell("a", checks=[_chk("x", "content"), _chk("y", "structure")])]
    agg = aggregate_all_dimensions(cells)
    assert set(agg.keys()) == {"content", "structure"}


def test_aggregate_all_dimensions_empty_cells_is_empty() -> None:
    assert aggregate_all_dimensions([]) == {}


# ── render_episode_course_section ────────────────────────────────────────────────


def test_render_episode_course_section_covers_both_classes() -> None:
    cells = [
        _ec_cell("e1", content_class="episode", checks=[_chk("audio.no_clipping", "audio-mix", passed=False, severity="high")] * 1),
        _ec_cell("c1", content_class="course", checks=[_chk("structure.has_script", "structure", passed=False, severity="critical")]),
        _ec_cell("bad", scored=False),
    ]
    body = render_episode_course_section(cells, generated_at="2026-07-09T00:00:00+00:00", project="kitesforu-dev", mode="test-mode")
    assert "EPISODES + COURSES" in body
    assert "## EPISODES" in body
    assert "## COURSES" in body
    assert "audio-mix" in body


def test_render_episode_course_section_handles_empty_set() -> None:
    body = render_episode_course_section([], generated_at="now", project="p", mode="m")
    assert "## EPISODES" in body and "## COURSES" in body


# ── upsert_markdown_section ───────────────────────────────────────────────────────


def test_upsert_appends_when_no_existing_markers() -> None:
    existing = "# QUALITY_BACKLOG\n\nsome short-scorecard content\n"
    out = upsert_markdown_section(existing, "NEW BODY", start_marker=EPISODES_COURSES_SECTION_START, end_marker=EPISODES_COURSES_SECTION_END)
    assert existing.strip() in out
    assert EPISODES_COURSES_SECTION_START in out
    assert "NEW BODY" in out
    assert EPISODES_COURSES_SECTION_END in out


def test_upsert_is_idempotent_replacing_only_its_own_block() -> None:
    existing = "# QUALITY_BACKLOG\n\nSHORT BASELINE HERE\n"
    once = upsert_markdown_section(existing, "FIRST BODY", start_marker=EPISODES_COURSES_SECTION_START, end_marker=EPISODES_COURSES_SECTION_END)
    twice = upsert_markdown_section(once, "SECOND BODY", start_marker=EPISODES_COURSES_SECTION_START, end_marker=EPISODES_COURSES_SECTION_END)
    assert "SHORT BASELINE HERE" in twice
    assert "FIRST BODY" not in twice
    assert "SECOND BODY" in twice
    assert twice.count(EPISODES_COURSES_SECTION_START) == 1


def test_upsert_on_empty_existing_text() -> None:
    out = upsert_markdown_section("", "BODY", start_marker=EPISODES_COURSES_SECTION_START, end_marker=EPISODES_COURSES_SECTION_END)
    assert "BODY" in out
    assert EPISODES_COURSES_SECTION_START in out
