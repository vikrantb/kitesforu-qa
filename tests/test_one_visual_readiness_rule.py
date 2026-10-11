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
    # e2bda0fa WITHOUT the producer's reasons: the frontend's rule, a set ladder wins (the producer's own
    # reasons end it: test_the_producers_by_design_reasons_end_a_dead_pass)
    (_visual(status="partial", video_status="rendering", video_skip_reason="audio_only_no_clips", clips=[]),
     "rendering", "video_status=rendering"),
    # 6112065b: assembling, with a skip reason the worker self-heals from
    (_visual(status="done", video_status="assembling", video_skip_reason="final_audio_pending"),
     "assembling", "video_status=assembling"),
    # the opt-in gate's own stamp, WITHOUT the producer's reasons: no ending, not yet a video either
    (_visual(status="done", video_skip_reason="fiction_or_undetermined_no_opt_in"),
     "pending", "fiction_or_undetermined_no_opt_in"),
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


def _rule_copies(path: Path) -> list:
    """Where ``path`` re-derives the readiness rule instead of asking the module: a comparison against
    ``"video_hero"`` (the hero predicate), a read of ``clips_settled_at`` (the stamp), or a comparison on
    ``video_status`` (the ladder). An AST scan, so a log line that only PRINTS a field is not a copy."""
    tree = ast.parse(path.read_text())

    def const(node, value):
        return (isinstance(node, ast.Constant) and node.value == value) or (
            isinstance(node, (ast.Tuple, ast.List, ast.Set)) and any(const(e, value) for e in node.elts))

    def reads(node, key):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            return bool(node.args) and const(node.args[0], key)
        return isinstance(node, ast.Subscript) and const(node.slice, key)

    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            if any(const(x, "video_hero") for x in sides):
                out.append(f"{path.name}:{node.lineno} compares against 'video_hero'")
            if any(reads(x, "video_status") for x in sides):
                out.append(f"{path.name}:{node.lineno} decides on video_status")
        if reads(node, "clips_settled_at"):
            out.append(f"{path.name}:{node.lineno} reads clips_settled_at")
    return out


def test_no_reader_in_scripts_or_src_keeps_its_own_copy_of_the_rule(tmp_path):
    """#175 round-6 design D2. Round 5's guard named two files, so it could not see the acceptance gate's URL
    check and hero count or the fiction census's stamp reader. It scans every module now."""
    module = QA / "src" / "kitesforu_qa" / "visual_readiness.py"
    # CONTROL: each pattern, written the way round 5's readers wrote them, is seen; a print of a field is not.
    copy = tmp_path / "a_reader.py"
    copy.write_text('heroes = [c for c in clips if c.get("modality") == "video_hero"]\n'
                    'settled = isinstance(v.get("clips_settled_at"), str)\n'
                    'done = vis["video_status"] in ("ready",)\n'
                    'print(f"{vis.get(\'video_status\')!r}")\n')
    found = _rule_copies(copy)
    assert len(found) == 3 and not any(":4 " in f for f in found), found
    files = sorted(p for d in ("scripts", "src") for p in (QA / d).rglob("*.py") if p != module)
    assert len(files) > 40, f"the scan found only {len(files)} files"
    copies = [c for p in files for c in _rule_copies(p)]
    assert not copies, copies


def test_the_readers_import_the_rule():
    for script in ("verification_job.py", "capture_starved_measurements.py", "acceptance_gate.py",
                   "post_deploy_fiction_census.py", "verification_job_census.py"):
        tree = ast.parse((QA / "scripts" / script).read_text())
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                   and n.module == "kitesforu_qa.visual_readiness" for a in n.names}
        assert imports, f"{script} does not read kitesforu_qa.visual_readiness"


def test_the_module_stays_importable_by_any_python3():
    """The shell runs it with whatever python3 is on PATH, so it may import the standard library and its
    stdlib-only sibling, nothing else."""
    tree = ast.parse((QA / "src" / "kitesforu_qa" / "visual_readiness.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    allowed = set(sys.stdlib_module_names) | {"__future__", "settled_clips"}
    assert {m.split(".")[0] for m in mods if m} <= allowed, mods


# ---- round 6: the deviation is the producer's by-design reasons, and only with no work in flight ----------

_NOW = 1_800_000_000_000
_LIVE = {"owner": "pass-2", "expires_at_ms": _NOW + 600_000}
_EXPIRED = {"owner": "pass-1", "expires_at_ms": _NOW - 1}


@pytest.mark.parametrize("visual,needle", [
    # e2bda0fa: a dead pass's `rendering` beside a by-design audio-only skip, no clips, no video
    (_visual(status="partial", video_status="rendering", video_skip_reason="audio_only_no_clips", clips=[]),
     "video_skip_reason=audio_only_no_clips"),
    # the opt-in gate's own stamp on a run that never rendered (job 9725a85c's class)
    (_visual(status="done", video_skip_reason="fiction_or_undetermined_no_opt_in"),
     "fiction_or_undetermined_no_opt_in"),
    # a by-design reason beside an EXPIRED lease: the pass that held it died
    (_visual(status="partial", video_status="rendering", video_skip_reason="audio_only_no_clips", clips=[],
             visuals_stage_lease=_EXPIRED), "audio_only_no_clips"),
])
def test_the_producers_by_design_reasons_end_a_dead_pass(producer_reasons, visual, needle):
    phase, reason = VisualReadiness.from_visual(visual).video_phase(
        expected=True, no_video_reasons=producer_reasons, now_ms=_NOW)
    assert phase == "no_video" and needle in reason, (phase, reason)


@pytest.mark.parametrize("ladder,extra", [
    ("rendering", {"visuals_stage_lease": _LIVE, "clips_settled_at": None}),   # the watchdog's retry, claimed
    ("rendering", {}),                                                         # a retry whose lease expired
    ("assembling", {"video_assembly_lease": _LIVE}),                           # the retry's mux
])
@pytest.mark.parametrize("skip", ["assembly_failed", "final_audio_missing"])
def test_a_retry_over_a_failure_is_never_terminal(producer_reasons, ladder, extra, skip):
    """#175 round-6 design D1, latency L6-1, critic F2. `_claim_write` sets `rendering` without clearing
    `video_skip_reason`, so a failure reason beside a rendering/assembling ladder is a retry the watchdog makes
    (up to twice) or still owes. Round 5 called it `no_video` and the wait exited 5 during it."""
    assert skip not in producer_reasons, f"the producer now calls {skip} by design; this test's premise moved"
    r = VisualReadiness.from_visual(_visual(status="done", video_status=ladder, video_skip_reason=skip, **extra))
    phase, reason = r.video_phase(expected=True, no_video_reasons=producer_reasons, now_ms=_NOW)
    assert phase == ladder and f"video_skip_reason={skip}" in reason, (phase, reason)


@pytest.mark.parametrize("extra,why", [
    ({"visuals_stage_lease": _LIVE}, "a live stage lease"),
    ({"video_assembly_lease": _LIVE}, "a live assembly lease"),
    ({"clips_settled_at": None}, "a cleared settle stamp"),
])
def test_work_in_flight_is_never_an_ending_even_on_a_by_design_reason(producer_reasons, extra, why):
    visual = _visual(status="partial", video_status="rendering", video_skip_reason="audio_only_no_clips",
                     clips=[], **extra)
    assert VisualReadiness.from_visual(visual).video_phase(
        expected=True, no_video_reasons=producer_reasons, now_ms=_NOW)[0] == "rendering", why
    # Without a clock, an owned lease counts as live: the direction that keeps waiting.
    if "lease" in why:
        assert VisualReadiness.from_visual(visual).video_phase(
            expected=True, no_video_reasons=producer_reasons)[0] == "rendering"


def test_a_lease_nobody_owns_is_not_work():
    visual = _visual(status="done", video_skip_reason="opted_out_no_visuals",
                     visuals_stage_lease={"expires_at_ms": _NOW + 600_000})
    r = VisualReadiness.from_visual(visual)
    assert r.stage_lease_expires_ms is None and not r.work_in_flight(_NOW)


def test_the_stage_answer_is_not_the_count_answer_on_a_hot_swap_still_pending():
    """#175 round-6 design D3, decided in writing: `is_final` / `assert_visual_final` gate the STAGE. A hot-swap
    still rendering (the stage done, the ladder `rendering`) passes them, and a clip COUNT from that read is NOT
    trustworthy; the docstrings say so, and this pins both halves."""
    doc = {"visual": _visual(status="done", video_status="rendering", clips_settled_at="t")}
    r = VisualReadiness.from_visual(doc["visual"])
    assert r.is_final and not r.clip_count_trustworthy
    assert Artifact.from_doc(doc).assert_visual_final() == r
    assert "does NOT gate a clip COUNT" in (Artifact.assert_visual_final.__doc__ or "")


def test_a_top_level_url_is_not_the_compartments_video():
    """#175 round-6 design D4: the frontend's `hasComposedVideo` reads `visual.video_url` /
    `visual.video_burned_url` only, and so does this rule. The artifact harness passes nothing else."""
    assert "top_level_video_url" not in VisualReadiness.from_visual.__code__.co_varnames
    doc = {"video_url": "https://x/v.mp4", "visual": _visual(status="done")}
    assert Artifact.from_doc(doc).visual_readiness.video_present is False
    assert VisualReadiness.from_visual(_visual(status="done", video_url="v")).video_present, "control"


# ---- round 6: the readers that kept their own rule now ask this one (design D2) ----------------------------

def test_the_acceptance_gate_grades_only_the_master_the_watch_page_shows(monkeypatch):
    """The gate took any `visual.video_url` with no ladder check. The api writes the URL before the status
    settles, so on `assembling` (a re-mux after a hot-swap) it graded a master about to be replaced, which the
    watch page does not show."""
    import uuid

    sys.path.insert(0, str(QA / "scripts"))
    import acceptance_gate as gate

    doc = {"topic": "t", "visual": _visual(status="done", video_status="assembling", video_url="https://x/v.mp4")}
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: doc)
    out = gate.run_gate(f"t-{uuid.uuid4().hex[:8]}")
    assert out["verdict"] == "FAIL" and "NOT SURFACED: the video is assembling" in out["issues"][0]["msg"], out
    # CONTROL: on a ready ladder the gate goes on to fetch the master (stubbed: it fetches nothing).
    doc["visual"]["video_status"] = "ready"
    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: None)
    out = gate.run_gate(f"t-{uuid.uuid4().hex[:8]}")
    assert out["issues"][0]["msg"].startswith("artifact not fetchable"), out


def test_the_census_reads_its_population_and_writes_one_only_when_asked(tmp_path):
    """#175 round-6 claims 4: the census re-queried and OVERWROTE its "frozen" population on every run, and its
    newest-600 sorted 2 string-dated docs first. It reads the file when it exists; --refresh-population queries
    the TIMESTAMP-typed docs only, and is the one path that writes."""
    pytest.importorskip("google.cloud.firestore")
    from types import SimpleNamespace

    sys.path.insert(0, str(QA / "scripts"))
    import verification_job_census as census

    pop = tmp_path / "population.txt"
    pop.write_text("a\nb\n")

    class _NoQuery:
        def collection(self, *_):
            raise AssertionError("a run with a population file queried a new one")

    assert census._population(_NoQuery(), pop, refresh=False, n=600) == (["a", "b"], "read")
    assert pop.read_text() == "a\nb\n"
    seen = {}

    class _Query:
        def where(self, filter=None):
            seen["filter"] = filter
            return self

        def order_by(self, *_a, **_k):
            return self

        def select(self, *_a):
            return self

        def limit(self, n):
            seen["n"] = n
            return self

        def stream(self):
            return iter([SimpleNamespace(id="c")])

    class _Db:
        def collection(self, name):
            seen["collection"] = name
            return _Query()

    assert census._population(_Db(), pop, refresh=True, n=5) == (["c"], "written")
    assert pop.read_text() == "c\n" and seen["n"] == 5 and seen["collection"] == "podcast_jobs"
    f = seen["filter"]
    assert (f.field_path, f.op_string, f.value) == ("created_at", ">=", census._EPOCH), "the string group sorts first"
