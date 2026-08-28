"""A QA mirror of a production predicate must still agree with it.

WHY THIS EXISTS. Two census scripts deliberately re-implement a production rule so they can be
run standalone, and each says so in its docstring:

    post_deploy_fiction_census.is_fiction      "Mirrors `_is_fiction_job`'s ordering"
    post_deploy_fiction_census.countable_paid  "Mirrors `_sum_visuals_image_cost`'s inclusion rules"

A copy is only safe while it agrees. Nothing checked that, and a silently-drifted mirror does not
fail — it reports a WRONG NUMBER with total confidence, which is the most expensive failure a
measurement tool has. `countable_paid`'s own docstring already names the cost: "Using a different
definition than the producer inflates disagreement in BOTH directions."

Measured 2026-08-28 before writing this, against live Firestore:
    is_fiction     vs _is_fiction_job          4160/4160 agree, 0 disagree
    countable_paid vs _sum_visuals_image_cost   817/817  agree, 0 disagree
So this pins agreement that HOLDS today; it is a drift alarm, not a bug report.

The fixtures below are offline and $0 — a unit test must not need Firestore. They cover each
branch the mirrors actually implement, so a change to either side that alters a branch fails here.

CROSS-REPO: the production side lives in kitesforu-workers. If that tree is not importable the
test SKIPS LOUDLY rather than passing vacuously — a silent skip would be the same class of defect
this file exists to catch.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_QA_ROOT = Path(__file__).resolve().parents[1]
_WORKERS_SRC = os.environ.get("WORKERS_SRC") or str(_QA_ROOT.parent / "kitesforu-workers" / "src")
for _p in (str(_QA_ROOT / "scripts"), _WORKERS_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_IMPORT_ERR = ""
try:
    from workers.common.architect_wiring import _is_fiction_job as prod_is_fiction
    from workers.stages.visuals.worker import _sum_visuals_image_cost as prod_paid_cost
    from post_deploy_fiction_census import countable_paid as qa_countable_paid
    from post_deploy_fiction_census import is_fiction as qa_is_fiction
except Exception as exc:  # noqa: BLE001
    _IMPORT_ERR = f"{type(exc).__name__}: {exc}"

pytestmark = pytest.mark.skipif(
    bool(_IMPORT_ERR),
    reason=(
        f"production side not importable ({_IMPORT_ERR}); set WORKERS_SRC to kitesforu-workers/src. "
        "SKIPPED, not passed — this test cannot vouch for the mirrors when it cannot load them."
    ),
)


# ── is_fiction ────────────────────────────────────────────────────────────────
# `_is_fiction_job` is KEYWORD-ONLY (*, audio_config, preferences). Calling it positionally
# raises "takes 0 positional arguments but 1 was given" — the same shape as the TypeError that
# cost a real job its visuals (job dcf8fbf2, is_short_video_job). The mirror takes a job dict,
# so the adapter below is where the two calling conventions meet.
FICTION_CASES = [
    ("content_category fiction",      {"preferences": {"content_category": "horror"}}),
    ("content_category non-fiction",  {"preferences": {"content_category": "educational"}}),
    ("_content_category underscore",  {"preferences": {"_content_category": "thriller"}}),
    ("story_engine primary",          {"preferences": {"_story_engine": {"primary_engine": "drama"}}}),
    ("audio_config content_type",     {"audio_config": {"content_type": "storytelling"}}),
    ("audio_config non-fiction",      {"audio_config": {"content_type": "explainer"}}),
    ("both, preferences wins",        {"preferences": {"content_category": "educational"},
                                       "audio_config": {"content_type": "storytelling"}}),
    ("empty job",                     {}),
    ("null preferences",              {"preferences": None, "audio_config": None}),
    ("unknown category",              {"preferences": {"content_category": "not-a-real-genre"}}),
    ("case + whitespace",             {"preferences": {"content_category": "  Horror  "}}),
]


@pytest.mark.parametrize("label,job", FICTION_CASES, ids=[c[0] for c in FICTION_CASES])
def test_is_fiction_mirror_matches_production(label, job):
    produced = bool(prod_is_fiction(
        audio_config=job.get("audio_config"), preferences=job.get("preferences")
    ))
    mirrored = bool(qa_is_fiction(job))
    assert mirrored == produced, (
        f"{label}: the QA mirror says {mirrored} and production says {produced}. "
        "post_deploy_fiction_census.is_fiction has drifted from _is_fiction_job — every fiction "
        "count that script prints is wrong until they agree again."
    )


# ── countable_paid ────────────────────────────────────────────────────────────
PAID_CASES = [
    ("model_id clip counts",        [{"model_id": "gemini-3-pro-image"}]),
    ("reused re-cut skipped",       [{"model_id": "x", "imagination_event": {"reused": True}}]),
    ("re-cut not reused counts",    [{"model_id": "x", "imagination_event": {"reused": False}}]),
    ("ai_generated relimage",       [{"ai_generated": True, "diagram_debug": {"kind": "relimage"}}]),
    ("ai_generated non-relimage",   [{"ai_generated": True, "diagram_debug": {"kind": "chart"}}]),
    ("plain $0 card",               [{"modality": "diagram"}]),
    ("scene_image without model_id", [{"modality": "scene_image"}]),
    ("mixed array",                 [{"model_id": "a"},
                                     {"model_id": "b", "imagination_event": {"reused": True}},
                                     {"ai_generated": True, "diagram_debug": {"kind": "relimage"}},
                                     {"modality": "diagram"}]),
    ("empty array",                 []),
]


@pytest.mark.parametrize("label,clips", PAID_CASES, ids=[c[0] for c in PAID_CASES])
def test_countable_paid_mirror_matches_production(label, clips):
    _usd, counts = prod_paid_cost(clips)
    produced = sum(counts.values())
    mirrored = qa_countable_paid(clips)
    assert mirrored == produced, (
        f"{label}: the QA mirror counts {mirrored} paid clips and production counts {produced}. "
        "countable_paid has drifted from _sum_visuals_image_cost — the census's image-cost "
        "stamp-vs-settled comparison is measuring two different definitions."
    )


def test_scene_image_without_model_id_is_not_paid():
    """The specific wrong predicate that cost a real measurement on 2026-08-28.

    Hand-rolling "paid" as `modality == scene_image` reported 25% of jobs missing a cost stamp;
    the producer's rule (model_id / relimage) reported 5.6%. A $0 licensed photograph is a
    scene_image too. Pinned so the cheap-looking definition cannot come back.
    """
    clips = [{"modality": "scene_image"}, {"modality": "scene_image"}]
    assert qa_countable_paid(clips) == 0
    assert sum(prod_paid_cost(clips)[1].values()) == 0
