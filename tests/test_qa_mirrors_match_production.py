"""A QA mirror of a production predicate must still agree with it.

WHY THIS EXISTS. Two census scripts deliberately re-implement a production rule so they can be
run standalone, and each says so in its docstring:

    post_deploy_fiction_census.is_fiction      "Mirrors `_is_fiction_job`'s ordering"
    post_deploy_fiction_census.paid_asset_ids  "Mirrors the CLIP rule of `image_cost_ledger.paid_assets`"

A copy is only safe while it agrees. Nothing checked that, and a silently-drifted mirror does not
fail — it reports a WRONG NUMBER with total confidence, which is the most expensive failure a
measurement tool has. `countable_paid`'s own docstring already names the cost: "Using a different
definition than the producer inflates disagreement in BOTH directions."

Measured 2026-08-28 before writing this, against live Firestore:
    is_fiction     vs _is_fiction_job          4160/4160 agree, 0 disagree
    countable_paid vs _sum_visuals_image_cost   817/817  agree, 0 disagree
So this pins agreement that HOLDS today; it is a drift alarm, not a bug report.

workers #3239 DELETED `_sum_visuals_image_cost` (the per-clip rule) and moved the pricer to
`image_cost_ledger`, which counts each paid ASSET once however many clips show it. The import of
the deleted name skipped this whole module — all 21 cases, the 11 unrelated `is_fiction` ones
included — so the mirror now pins `paid_asset_ids` against `paid_assets`, with fixtures that
carry `content_hash` (the round-D finding: the old fixtures carried none, so a deduping mirror
and a per-clip one agreed on every one of them).

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
    from workers.stages.visuals.image_cost_ledger import paid_assets as prod_paid_assets
    from post_deploy_fiction_census import countable_paid as qa_countable_paid
    from post_deploy_fiction_census import paid_asset_ids as qa_paid_asset_ids
    from post_deploy_fiction_census import stamp_vs_clips
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


# ── paid assets ───────────────────────────────────────────────────────────────
_STILL = "gs://b/visuals/j/3c840ca113.png"
PAID_CASES = [
    ("model_id clip counts",        [{"model_id": "gemini-3-pro-image", "content_hash": "a"}]),
    ("reused re-cut skipped",       [{"model_id": "x", "content_hash": "own",
                                      "imagination_event": {"reused": True}}]),
    ("re-cut not reused counts",    [{"model_id": "x", "content_hash": "a",
                                      "imagination_event": {"reused": False}}]),
    ("ai_generated relimage",       [{"ai_generated": True, "diagram_debug": {"kind": "relimage"},
                                      "content_hash": "r"}]),
    ("ai_generated non-relimage",   [{"ai_generated": True, "diagram_debug": {"kind": "chart"}}]),
    ("plain $0 card",               [{"modality": "diagram", "content_hash": "card"}]),
    ("scene_image without model_id", [{"modality": "scene_image", "content_hash": "lib"}]),
    ("rendered_model_id alone",     [{"rendered_model_id": "gemini-2.5-flash-image",
                                      "content_hash": "g"}]),
    # e032d06d: one paid still, three showings (span-recut revisits).
    ("revisit: one still, 3 clips", [{"model_id": "flux-schnell", "content_hash": "3c840ca113",
                                      "start_ms": t} for t in (5767, 10547, 15327)]),
    # A verify REGEN ships under a NEW hash; the clip rule sees only the shipped one (the
    # discarded first render is booked by the receipt, which no clip carries).
    ("regen: the shipped hash only", [{"model_id": "flux-schnell", "content_hash": "regen-hash"},
                                      {"modality": "scene_image", "content_hash": "crop-0"}]),
    ("uri names the asset",         [{"model_id": "flux-schnell", "asset_uri": _STILL},
                                     {"model_id": "flux-schnell", "asset_uri": _STILL}]),
    ("no identity at all",          [{"model_id": "flux-schnell"}, {"model_id": "flux-schnell"}]),
    ("mixed array",                 [{"model_id": "a", "content_hash": "h1"},
                                     {"model_id": "b", "content_hash": "h2",
                                      "imagination_event": {"reused": True}},
                                     {"ai_generated": True, "diagram_debug": {"kind": "relimage"},
                                      "content_hash": "h3"},
                                     {"model_id": "a", "content_hash": "h1", "start_ms": 9},
                                     {"modality": "diagram"}]),
    ("empty array",                 []),
]


@pytest.mark.parametrize("label,clips", PAID_CASES, ids=[c[0] for c in PAID_CASES])
def test_paid_asset_mirror_matches_production(label, clips):
    assets, unnamed, _usd = prod_paid_assets(clips)
    produced = (set(assets), sum(unnamed.values()))
    mirrored = qa_paid_asset_ids(clips)
    assert mirrored == produced, (
        f"{label}: the QA mirror sees {mirrored} and production sees {produced}. "
        "paid_asset_ids has drifted from image_cost_ledger.paid_assets — the census's image-cost "
        "stamp-vs-settled comparison is measuring two different definitions."
    )
    assert qa_countable_paid(clips) == len(produced[0]) + produced[1]


def test_scene_image_without_model_id_is_not_paid():
    """The specific wrong predicate that cost a real measurement on 2026-08-28.

    Hand-rolling "paid" as `modality == scene_image` reported 25% of jobs missing a cost stamp;
    the producer's rule (model_id / relimage) reported 5.6%. A $0 licensed photograph is a
    scene_image too. Pinned so the cheap-looking definition cannot come back.
    """
    clips = [{"modality": "scene_image"}, {"modality": "scene_image"}]
    assert qa_countable_paid(clips) == 0
    assert prod_paid_assets(clips)[0] == {}


# ── the census reads each stamp by its own definition ─────────────────────────
def _witness_clips():
    return [{"model_id": "flux-schnell", "content_hash": "c2", "start_ms": t} for t in (1, 2, 3)]


def test_a_ledger_stamp_is_a_superset_of_the_clips_never_over():
    """e032d06d after #3239: 5 renders booked, the clips show 1 still three times."""
    block = {"meta": {"scenes": 5, "assets": {"c2": {}, "c0@a1": {}, "c0@a2": {},
                                              "c1@a3": {}, "c1@a4": {}}}}
    assert stamp_vs_clips(block, _witness_clips()) == "exact"


def test_a_shown_asset_missing_from_the_ledger_is_under():
    block = {"meta": {"scenes": 1, "assets": {"other": {}}}}
    assert stamp_vs_clips(block, _witness_clips()) == "under"


def test_a_free_asset_is_not_missing():
    """A cross-job cache hit or a CC0 photo is shown, not paid: `meta.free` lists it."""
    block = {"meta": {"scenes": 0, "assets": {}, "free": ["c2"]}}
    assert stamp_vs_clips(block, _witness_clips()) == "exact"


def test_a_legacy_stamp_is_read_per_clip():
    """A pre-#3239 stamp was written once per CLIP; read it that way, or every revisit job
    reads UNDER (the #2749 signature) under the new definition."""
    assert stamp_vs_clips({"meta": {"scenes": 3}}, _witness_clips()) == "exact"
    assert stamp_vs_clips({"meta": {"scenes": 2}}, _witness_clips()) == "under"
    assert stamp_vs_clips({"meta": {"scenes": 4}}, _witness_clips()) == "over"
    assert stamp_vs_clips(None, _witness_clips()) == "none"
