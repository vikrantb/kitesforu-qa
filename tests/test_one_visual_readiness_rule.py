"""ONE readiness rule for every qa reader: kitesforu_qa.visual_readiness (kitesforu-qa #175 round 5).

The shapes below are the ones the round-5 census found on real jobs (read-only, newest 600 ``podcast_jobs``
by ``created_at``, 2026-10-10; ``kitesforu-qa`` PR #175 body has the command), one test per shape, plus the
frontend's ``resolveVideoDeliverable`` precedence this module mirrors, and its one stated deviation.

Offline and $0.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from kitesforu_qa import visual_readiness as vr
from kitesforu_qa.harness.artifact import Artifact
from kitesforu_qa.settled_clips import clips_fingerprint
from kitesforu_qa.visual_readiness import VisualReadiness

QA = Path(__file__).resolve().parents[1]


def _visual(**kw):
    clips = kw.pop("clips", [{"beat_index": 0, "modality": "scene_image", "render_mode": "still",
                              "status": "done", "asset_uri": "gs://b/a.png"}])
    return {"clips": clips, **kw}


@pytest.mark.parametrize("visual,phase,needle", [
    # cd73b210: the common delivered shape
    (_visual(status="done", video_status="ready", video_url="https://x/v.mp4", clips_settled_at="t"),
     "ready", "video_status=ready"),
    # a43bddcf: delivered before the ladder existed (no video_status), a URL
    (_visual(status="done", video_url="https://x/v.mp4"), "ready", "no video_status ladder"),
    # af9bd9da: the assembly died; the ladder says so
    (_visual(status="done", video_status="failed_assembly"), "failed", "video_status=failed_assembly"),
    # e2bda0fa: a dead pass's `rendering` beside a by-design audio-only skip, no clips, no video
    (_visual(status="partial", video_status="rendering", video_skip_reason="audio_only_no_clips", clips=[]),
     "no_video", "video_skip_reason=audio_only_no_clips"),
    # 6112065b: assembling, with a skip reason the worker self-heals from
    (_visual(status="done", video_status="assembling", video_skip_reason="final_audio_pending"),
     "assembling", "video_status=assembling"),
    # the opt-in gate's own stamp on a run that never rendered
    (_visual(status="done", video_skip_reason="fiction_or_undetermined_no_opt_in"),
     "no_video", "fiction_or_undetermined_no_opt_in"),
    # nothing written yet
    ({}, "pending", "no video yet"),
    # the old wait loop's only exit, kept
    (_visual(status="failed"), "failed", "visual.status=failed"),
])
def test_every_census_shape_has_one_answer(visual, phase, needle):
    got, reason = VisualReadiness.from_visual(visual).video_phase(expected=True)
    assert got == phase and needle in reason, (got, reason)


def test_the_ladder_wins_over_a_present_url_as_the_frontend_says():
    """The api writes the URL before the status settles, so a set, not-ready ladder EXCLUDES."""
    r = VisualReadiness.from_visual(_visual(status="done", video_status="assembling", video_url="https://x/v.mp4"))
    assert r.video_phase(expected=True)[0] == "assembling"


def test_an_unknown_ladder_value_is_not_finished():
    r = VisualReadiness.from_visual(_visual(status="done", video_status="muxing_v2"))
    assert r.video_phase(expected=True)[0] == "pending"


def test_a_video_is_never_expected_of_a_run_that_did_not_ask():
    r = VisualReadiness.from_visual(_visual(status="done", video_status="failed_assembly"))
    assert r.video_phase(expected=False)[0] == "not_expected"


def test_a_terminal_skip_never_overrides_a_delivered_video():
    """The skip reason is stale once a video exists (the assembler deletes it, but a reader can race)."""
    r = VisualReadiness.from_visual(_visual(status="done", video_skip_reason="assembly_failed",
                                            video_url="https://x/v.mp4"))
    assert r.video_phase(expected=True)[0] == "ready"


@pytest.mark.parametrize("visual,state", [
    ({"clips_settled_at": "2026-10-10T10:00:00+00:00"}, "settled"),
    ({"clips_settled_at": None}, "in_flight"),
    ({"clips_settled_at": "  "}, "in_flight"),
    ({}, "absent"),
    (None, "absent"),
])
def test_the_settle_stamp_has_three_states(visual, state):
    assert vr.settle_stamp_state({"visual": visual}) == state
    assert VisualReadiness.from_visual(visual).settle_stamp == state


def test_a_count_is_trusted_only_from_a_ready_unwritten_read():
    ready = _visual(status="done", video_status="ready", video_url="v", clips_settled_at="t")
    assert VisualReadiness.from_visual(ready).clip_count_trustworthy
    assert not VisualReadiness.from_visual({**ready, "clips_settled_at": None}).clip_count_trustworthy
    assert not VisualReadiness.from_visual({**ready, "video_status": "rendering"}).clip_count_trustworthy


def test_the_hero_predicate_is_the_producers():
    clips = [{"modality": "video_hero", "render_mode": "video"},
             {"modality": "video_hero", "render_mode": "still"},      # a reclaimed still (degrade_ladder)
             {"modality": "scene_image", "render_mode": "video"}]
    assert VisualReadiness.from_visual({"clips": clips}).hero_clips == 1


def test_a_url_signature_does_not_move_the_fingerprint_but_content_does():
    a = [{"asset_uri": "https://storage.googleapis.com/b/x.png?X-Goog-Signature=1"}]
    b = [{"asset_uri": "https://storage.googleapis.com/b/x.png?X-Goog-Signature=2"}]
    c = [{"asset_uri": "https://storage.googleapis.com/b/y.png?X-Goog-Signature=1"}]
    assert clips_fingerprint(a) == clips_fingerprint(b) != clips_fingerprint(c)
    # An unsigned read (Firestore) hashes exactly as it always did: the json of the list itself.
    import hashlib
    import json
    raw = [{"asset_uri": "gs://b/x.png", "beat_index": 1}]
    assert clips_fingerprint(raw) == hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:16]


def test_the_artifact_and_the_wait_read_the_same_class():
    doc = {"visual": _visual(status="done", video_status="ready", video_url="v", clips_settled_at="t")}
    assert Artifact.from_doc(doc).visual_readiness == VisualReadiness.from_visual(doc["visual"])


def test_the_scripts_import_the_rule_and_keep_no_copy():
    """The wait (verification_job.py) and the starved-measurement capture both read the package's rule. A
    local definition of the stamp states or of the hero predicate is the drift this file exists to end."""
    for script in ("verification_job.py", "capture_starved_measurements.py"):
        tree = ast.parse((QA / "scripts" / script).read_text())
        defs = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        assert not defs & {"settle_stamp_state", "VisualReadiness", "is_delivered_hero_clip"}, (script, defs)
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                   and n.module == "kitesforu_qa.visual_readiness" for a in n.names}
        assert imports, f"{script} does not read kitesforu_qa.visual_readiness"
    vj = (QA / "scripts" / "verification_job.py").read_text()
    assert '.get("modality") == "video_hero"' not in vj, "a hand-typed hero predicate came back"


def test_the_module_stays_importable_by_any_python3():
    """The shell runs it with whatever python3 is on PATH, so it may import the standard library and its
    stdlib-only sibling, nothing else."""
    tree = ast.parse((QA / "src" / "kitesforu_qa" / "visual_readiness.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    allowed = set(sys.stdlib_module_names) | {"__future__", "settled_clips"}
    assert {m.split(".")[0] for m in mods if m} <= allowed, mods
