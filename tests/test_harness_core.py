"""Harness core self-test — the framework verifies ITSELF on a known-good vs known-bad fixture.

This is the P0 gate: prove the Check primitive (coerce, skip, never-raise), the genre selection,
the battery -> StageResult bridge, and the scorecard all work — and that a good explainer passes
the gating checks while a bad one (no script value, Host1/Host2, pure banter) fails them.
"""
import importlib

from kitesforu_qa.harness import (
    Artifact,
    all_checks,
    check,
    checks_for,
    run_scorecard,
    scorecard_summary,
    skip,
)

# the submodule, NOT the `check` decorator that shadows it on the package
_chk = importlib.import_module("kitesforu_qa.harness.check")


def _snapshot():
    return list(_chk._REGISTRY)


def _restore(snap):
    _chk._REGISTRY[:] = snap


GOOD_DOC = {
    "job_id": "good1",
    "status": "completed",
    "episode_profile": {"genre": "explainer", "topic": "How a B-tree index works"},
    "inputs": {"duration_min": 2},
    "script": {"dialogue": [
        {"speaker": "Maya", "text": (
            "A B-tree is a balanced index. Imagine a library catalog: for example, you look up a "
            "letter and jump straight to the right shelf. It works by keeping keys sorted so a "
            "lookup only touches a few pages."
        )},
        {"speaker": "Theo", "text": (
            "So the database means it can find one row among millions by following a few branches. "
            "Think of it like binary search, but on disk pages instead of an array."
        )},
    ]},
    "segments_ready": [{"text": "A B-tree is a balanced index..."}, {"text": "So the database..."}],
}

BAD_DOC = {
    "job_id": "bad1",
    "status": "completed",
    "episode_profile": {"genre": "explainer"},
    "script": {"dialogue": [
        {"speaker": "Host1", "text": "Yeah totally, haha, you know, exactly, so true, love it, great question, for sure, absolutely."},
        {"speaker": "Host2", "text": "Right? I mean absolutely, lol, you know, totally."},
    ]},
    "segments_ready": [{"text": ""}],
}


def test_good_fixture_passes_gating():
    art = Artifact.from_doc(GOOD_DOC)
    summary = scorecard_summary(run_scorecard(art))
    assert summary["passed"], summary["all_issues"]
    assert summary["total_checks"] > 5


def test_bad_fixture_fails_gating():
    art = Artifact.from_doc(BAD_DOC)
    summary = scorecard_summary(run_scorecard(art))
    assert not summary["passed"]
    # the generic-host-names high-severity check must be among the failures
    assert any("no_generic_host_names" in i for i in summary["all_issues"]), summary["all_issues"]


def test_check_primitive_coerce_and_never_raises():
    snap = _snapshot()
    try:
        _chk._REGISTRY.clear()

        @check("t.bool", dimension="x", severity="low")
        def c_bool(art):
            return True

        @check("t.tuple2", dimension="x", severity="low")
        def c_t2(art):
            return False, "nope"

        @check("t.raises", dimension="x", severity="high")
        def c_raise(art):
            raise ValueError("boom")

        @check("t.skip", dimension="x", severity="low")
        def c_skip(art):
            skip("n/a")

        art = Artifact.from_doc({"job_id": "z"})
        by = {c.id: c.run(art) for c in all_checks()}
        assert by["t.bool"].passed
        assert not by["t.tuple2"].passed and by["t.tuple2"].evidence == "nope"
        # a crashing check becomes a failed result, never propagates
        assert not by["t.raises"].passed and by["t.raises"].error
        assert by["t.skip"].skipped
    finally:
        _restore(snap)


def test_checks_for_genre_selection():
    snap = _snapshot()
    try:
        _chk._REGISTRY.clear()

        @check("shared.x", dimension="d", severity="low")
        def shared(art):
            return True

        @check("horror.y", dimension="d", genre="horror", severity="low")
        def horror(art):
            return True

        @check("comedy.z", dimension="d", genre="comedy", severity="low")
        def comedy(art):
            return True

        ids = {c.id for c in checks_for(genre="horror")}
        # genre-agnostic shared check + the horror check, NOT comedy's
        assert "shared.x" in ids and "horror.y" in ids and "comedy.z" not in ids
    finally:
        _restore(snap)
