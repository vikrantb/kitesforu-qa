"""The QA image-ledger scripts must read what the producer WRITES, and the fiction mirror must agree.

WHY THIS EXISTS. Census scripts that measure a production rule are only safe while they agree with
it, and a drifted one does not fail: it reports a WRONG NUMBER with total confidence.

* ``post_deploy_fiction_census.is_fiction`` deliberately mirrors ``_is_fiction_job`` so the census
  can classify a job; it is pinned case by case below. Measured 2026-08-28 against live Firestore:
  ``is_fiction`` vs ``_is_fiction_job`` agreed on 4160/4160 jobs.
* The image ledger is NOT mirrored any more (#181 round 2, design SF1). The three scripts call
  workers' ``image_cost_ledger`` (``paid_assets``, ``asset_id``, ``clip_price``) for the current
  rule and ``image_census_rules`` for the one frozen legacy rule. What can still drift is the
  FIELD CONTRACT: the keys of ``costs.visuals_images.meta`` that #3239 writes and these scripts
  read. So the ledger tests below build the block with the producer's real
  ``image_cost_ledger.roll_up_images`` against an in-memory doc and run each script's reader on it
  (round-2 critic S1: every reader test used a hand-written literal, and renaming the three keys in
  the producer left this file green while the census read every new stamp as legacy).

SKIP ONLY WHEN THE PRODUCER IS ABSENT, FAIL WHEN IT IS WRONG (round-2 design SF3, claims S1). The
old module-level ``skipif`` turned ANY import error into a green skip of all 30 cases: a renamed
symbol, a missing module, the 11 unrelated ``is_fiction`` cases with them. Now each mirror imports
what it needs on its own. A test SKIPS only when the ``workers`` package cannot be found at all and
``WORKERS_SRC`` was not set; it FAILS when ``workers`` imports but a named symbol is missing, and
when ``WORKERS_SRC`` was set and points at no ``workers`` package.

Offline and $0: no Firestore, no Cloud Logging, no provider.
"""
from __future__ import annotations

import collections
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_QA_ROOT = Path(__file__).resolve().parents[1]
_EXPLICIT_SRC = os.environ.get("WORKERS_SRC")
_WORKERS_SRC = _EXPLICIT_SRC or str(_QA_ROOT.parent / "kitesforu-workers" / "src")
for _p in (str(_QA_ROOT / "scripts"), _WORKERS_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def need(module: str, name: str) -> Any:
    """``module.name``, or SKIP when the producer is absent, or FAIL when it is present but wrong."""
    if importlib.util.find_spec("workers") is None:
        if _EXPLICIT_SRC:
            pytest.fail(f"WORKERS_SRC={_EXPLICIT_SRC} holds no `workers` package: the mirror cannot "
                        "be checked against a tree that is not there.")
        pytest.skip("kitesforu-workers is not importable here (set WORKERS_SRC). SKIPPED, not "
                    "passed: this test cannot vouch for a producer it cannot load.")
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as exc:
        pytest.fail(f"`workers` imports but {module}.{name} does not ({type(exc).__name__}: {exc}). "
                    "The producer moved or renamed something these scripts depend on.")


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
    prod_is_fiction = need("workers.common.architect_wiring", "_is_fiction_job")
    qa_is_fiction = need("post_deploy_fiction_census", "is_fiction")
    produced = bool(prod_is_fiction(
        audio_config=job.get("audio_config"), preferences=job.get("preferences")
    ))
    mirrored = bool(qa_is_fiction(job))
    assert mirrored == produced, (
        f"{label}: the QA mirror says {mirrored} and production says {produced}. "
        "post_deploy_fiction_census.is_fiction has drifted from _is_fiction_job — every fiction "
        "count that script prints is wrong until they agree again."
    )


# ── the block the producer WRITES ─────────────────────────────────────────────
FLUX = "flux-schnell"


class _Snap:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data, self.exists = data, bool(data)

    def to_dict(self) -> Dict[str, Any]:
        return {"costs": dict(self._data.get("costs") or {})}


class _Doc:
    """A job doc that applies dotted updates and ``Increment`` the way Firestore does. ``get`` takes
    the producer's keyword arguments (``field_paths``, ``retry``, ``timeout``) and ignores them."""

    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}

    def get(self, *_a: Any, **_k: Any) -> _Snap:
        return _Snap(self.data)

    def update(self, payload: Dict[str, Any], *_a: Any, **_k: Any) -> None:
        from google.cloud import firestore

        for path, value in payload.items():
            parts = path.split(".")
            node = self.data
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            if isinstance(value, firestore.Increment):
                node[parts[-1]] = float(node.get(parts[-1]) or 0.0) + float(value.value)
            else:
                node[parts[-1]] = value


class _Db:
    def __init__(self, doc: _Doc) -> None:
        self._doc = doc

    def collection(self, _n: str) -> "_Db":
        return self

    def document(self, _d: str) -> _Doc:
        return self._doc


def witness_clips() -> List[Dict[str, Any]]:
    """e032d06d's shape: one kept still shown three times, plus one picture another job paid for
    (a cross-job cache hit, shown with its model like any photoreal clip)."""
    return ([{"model_id": FLUX, "content_hash": "c2", "start_ms": t} for t in (5767, 10547, 15327)]
            + [{"model_id": "flux-dev", "content_hash": "cached-elsewhere", "start_ms": 20000}])


def produced_job() -> Dict[str, Any]:
    """The job doc the REAL ``roll_up_images`` writes for the witness: the kept still, four renders
    the beat paid for and threw away (3 initial + 2 REGEN, one of them shipped), and the free hit."""
    receipt = need("workers.stages.visuals.image_cost_ledger", "PaidRenderReceipt")()
    roll_up_images = need("workers.stages.visuals.image_cost_ledger", "roll_up_images")
    receipt.record("c2", FLUX, kept=True)
    for h in ("c0", "c0", "c1", "c1"):
        receipt.record(h, FLUX, kept=False)
    receipt.record_free("cached-elsewhere")
    doc = _Doc()
    out = roll_up_images(_Db(doc), "witness", witness_clips(), receipt)
    assert out is not None and out.wrote, "the producer wrote nothing, so this proves nothing"
    return {**doc.data, "visual": {"clips": witness_clips()}}


def _block(job: Dict[str, Any]) -> Dict[str, Any]:
    return job["costs"]["visuals_images"]


def test_the_census_reads_a_block_the_producer_wrote_as_exact():
    stamp_vs_clips = need("post_deploy_fiction_census", "stamp_vs_clips")
    assert stamp_vs_clips(_block(produced_job()), witness_clips()) == "exact"


def test_the_ledger_checker_reads_a_block_the_producer_wrote_by_its_parts():
    sys.modules.pop("check_paid_still_ledger", None)
    describe_rollup = need("check_paid_still_ledger", "describe_rollup")
    text = "\n".join(describe_rollup(_block(produced_job())))
    assert "legacy stamp" not in text, text
    assert "kept assets 1; discarded renders 4" in text, text
    assert "free (shown, not paid) 1" in text, text


def test_the_provider_census_reads_a_block_the_producer_wrote_as_booked():
    """#181 round-2 claims S2: the post-deploy instrument had never been seen to find a stamp. The
    witness's 5 accepted fal renders and 5 success lines, against the 5 the producer booked."""
    census = importlib.import_module("image_spend_provider_census")
    price_of = need("workers.stages.visuals.image_cost_ledger", "price_of")
    rows = ([{"jsonPayload": {"job_id": "witness", "message":
              'HTTP Request: POST https://queue.fal.run/fal-ai/flux/schnell "HTTP/1.1 200 OK"'}}] * 5
            + [{"jsonPayload": {"job_id": "witness", "message":
                "fal FLUX completed model=fal-ai/flux/schnell polls=3 elapsed=2.1s"}}] * 5)
    per_job, unattributed, done, undone = census.tally(rows, limit=1000)
    assert per_job["witness"] == {"fal_flux:flux-schnell": 5} and not unattributed and not undone
    row = census.job_row("witness", produced_job(), per_job["witness"], done["witness"], "test@",
                         price_of)
    assert row["booked"] == 5 and row["returned"] == 5
    assert row["booked_usd"] == pytest.approx(row["returned_usd"]) == pytest.approx(5 * price_of(FLUX))
    lines = census.summary_lines([row])
    assert "  jobs carrying meta.assets (rolled up by #3239+): 1" in lines, lines
    assert "    booked == returned (+Imagen requests) on 1 of 1" in lines, lines
    assert "    booked $ == returned $ (+Imagen requests) on 1 of 1" in lines, lines
    # Control: a legacy stamp is not a ledger stamp.
    legacy = {"costs": {"visuals_images": {"total_cost_usd": 0.009, "meta": {"scenes": 3}}},
              "visual": {"clips": witness_clips()}}
    lrow = census.job_row("legacy", legacy, per_job["witness"], done["witness"], "test@", price_of)
    assert lrow["booked"] is None
    assert "  jobs carrying meta.assets (rolled up by #3239+): 0" in census.summary_lines([lrow])


def test_the_provider_arm_keys_by_model_so_a_wrong_model_shows_in_dollars():
    """#181 round-2 cost SF1: ``fal_flux`` folded flux/dev ($0.025) and flux/schnell ($0.003) into one
    count, so a dispatch booked at the wrong model kept the COUNT right and the $ off by 8x."""
    census = importlib.import_module("image_spend_provider_census")
    price_of = need("workers.stages.visuals.image_cost_ledger", "price_of")
    dev = 'HTTP Request: POST https://queue.fal.run/fal-ai/flux/dev "HTTP/1.1 200 OK"'
    schnell = 'HTTP Request: POST https://queue.fal.run/fal-ai/flux/schnell "HTTP/1.1 200 OK"'
    assert census.classify(dev) == "fal_flux:flux-dev"
    assert census.classify(schnell) == "fal_flux:flux-schnell"
    assert price_of("flux-dev") > 5 * price_of("flux-schnell") > 0
    # One flux-dev render, booked as one schnell-priced still: the counts agree, the dollars do not.
    job = {"costs": {"visuals_images": {"total_cost_usd": price_of(FLUX), "meta": {
        "scenes": 1, "models": {FLUX: 1}, "assets": {"x": {"m": FLUX, "usd": price_of(FLUX)}}}}},
        "visual": {"clips": []}}
    done = collections.Counter({"fal_flux:flux-dev": 1})
    row = census.job_row("j", job, collections.Counter({"fal_flux:flux-dev": 1}), done, "test@",
                         price_of)
    lines = census.summary_lines([row])
    assert "    booked == returned (+Imagen requests) on 1 of 1" in lines
    assert "    booked $ == returned $ (+Imagen requests) on 0 of 1" in lines, lines


def test_an_imagen_render_counts_on_the_provider_side_because_it_logs_no_success_line():
    """#181 round-2 cost NIT1: Imagen logs no success line, so it can never reach ``returned``."""
    census = importlib.import_module("image_spend_provider_census")
    price_of = need("workers.stages.visuals.image_cost_ledger", "price_of")
    line = ('HTTP Request: POST https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/'
            'us-central1/publishers/google/models/imagen-4.0-fast-generate-001:predict "HTTP/1.1 200 OK"')
    veo = line.replace("imagen-4.0-fast-generate-001:predict", "veo-3.1:predictLongRunning")
    assert census.classify(line) == "vertex_imagen:imagen-4.0-fast-generate-001"
    assert census.classify(veo) is None, "Veo settles in its own stage"
    row = census.job_row("j", {}, collections.Counter({census.classify(line): 1}),
                         collections.Counter(), "test@", price_of)
    assert row["returned"] == 0 and row["returned_or_imagen"] == 1
    assert row["returned_or_imagen_usd"] == pytest.approx(price_of("imagen-4.0-fast-generate-001"))


def test_a_log_read_that_hits_its_limit_is_an_error_not_a_lower_bound():
    """#181 round-2 cost NIT2."""
    census = importlib.import_module("image_spend_provider_census")
    rows = [{"jsonPayload": {"message": "x"}}] * 3
    with pytest.raises(census.TruncatedLogRead):
        census.tally(rows, limit=3)
    census.tally(rows, limit=4)  # control: under the limit reads normally


# ── the readers on hand-written shapes the producer writes ────────────────────
def test_a_shown_asset_missing_from_the_ledger_is_under():
    stamp_vs_clips = need("post_deploy_fiction_census", "stamp_vs_clips")
    block = {"meta": {"scenes": 1, "assets": {"other": {"m": FLUX, "usd": 0.003}}}}
    assert stamp_vs_clips(block, witness_clips()[:3]) == "under"


def test_an_unnamed_still_beyond_the_booked_high_water_mark_is_under():
    stamp_vs_clips = need("post_deploy_fiction_census", "stamp_vs_clips")
    unnamed = [{"model_id": FLUX}, {"model_id": FLUX}]
    block = {"meta": {"scenes": 1, "assets": {}, "unnamed": {FLUX: {"n": 1, "usd": 0.003}}}}
    assert stamp_vs_clips(block, unnamed) == "under"
    block["meta"]["unnamed"][FLUX]["n"] = 2
    assert stamp_vs_clips(block, unnamed) == "exact"


def test_a_legacy_stamp_is_read_per_clip():
    """A pre-#3239 stamp was written once per CLIP; read it that way, or every revisit job
    reads UNDER (the #2749 signature) under the new definition."""
    stamp_vs_clips = need("post_deploy_fiction_census", "stamp_vs_clips")
    clips = witness_clips()[:3]
    assert stamp_vs_clips({"meta": {"scenes": 3}}, clips) == "exact"
    assert stamp_vs_clips({"meta": {"scenes": 2}}, clips) == "under"
    assert stamp_vs_clips({"meta": {"scenes": 4}}, clips) == "over"
    assert stamp_vs_clips(None, clips) == "none"


# ── the frozen legacy rule ─────────────────────────────────────────────────────
LEGACY_CASES = [
    ("model_id clip",               {"model_id": "gemini-3-pro-image"},                          True),
    ("rendered_model_id alone",     {"rendered_model_id": "gemini-2.5-flash-image"},              True),
    ("reused re-cut",               {"model_id": "x", "imagination_event": {"reused": True}},     False),
    ("re-cut not reused",           {"model_id": "x", "imagination_event": {"reused": False}},    True),
    ("ai_generated relimage",       {"ai_generated": True, "diagram_debug": {"kind": "relimage"}}, True),
    ("ai_generated non-relimage",   {"ai_generated": True, "diagram_debug": {"kind": "chart"}},    False),
    ("non-dict diagram_debug",      {"ai_generated": True, "diagram_debug": "relimage"},           False),
    ("scene_image without model",   {"modality": "scene_image"},                                  False),
    ("not a clip",                  "junk",                                                       False),
]


@pytest.mark.parametrize("label,clip,paid", LEGACY_CASES, ids=[c[0] for c in LEGACY_CASES])
def test_the_frozen_legacy_rule(label, clip, paid):
    """The rule of the deleted ``worker._sum_visuals_image_cost``, which every pre-#3239 stamp was
    written with. On 2026-10-01 the claims lens ran the rollup census's per-clip arm against main's
    REAL function on 300 jobs (``image_rollup_dedupe_census.py --until 2026-10-01T03:43:00Z --limit
    300``, workers main ``6335bb7a``): $37.5848 in both arms, 0 jobs differing."""
    from image_census_rules import legacy_clip_model

    assert bool(legacy_clip_model(clip)) is paid, label


def test_scene_image_without_model_id_is_not_paid():
    """The specific wrong predicate that cost a real measurement on 2026-08-28.

    Hand-rolling "paid" as `modality == scene_image` reported 25% of jobs missing a cost stamp;
    the producer's rule (model_id / relimage) reported 5.6%. A $0 licensed photograph is a
    scene_image too. Pinned so the cheap-looking definition cannot come back.
    """
    from image_census_rules import legacy_per_clip_count

    paid_assets = need("workers.stages.visuals.image_cost_ledger", "paid_assets")
    clips = [{"modality": "scene_image"}, {"modality": "scene_image"}]
    assert legacy_per_clip_count(clips) == 0
    assert paid_assets(clips)[0] == {}


def test_the_census_scripts_import_no_private_ledger_name():
    """#181 round-2 design NIT: qa depends on the names workers exports, not on its privates."""
    import ast

    for script in ("image_rollup_dedupe_census.py", "image_spend_provider_census.py",
                   "post_deploy_fiction_census.py", "check_paid_still_ledger.py"):
        tree = ast.parse((_QA_ROOT / "scripts" / script).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "workers.stages.visuals.image_cost_ledger":
                private = [a.name for a in node.names if a.name.startswith("_")]
                assert not private, f"{script} imports {private} from image_cost_ledger"
