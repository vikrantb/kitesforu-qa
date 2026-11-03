"""The acceptance gate grades a job with no video BY DESIGN as its audio, never as a missing video.

An explainer made with visuals off carries ``visual.video_skip_reason = opted_out_no_visuals``, and the producer
lists that reason as by design (``video_skip_reasons.BY_DESIGN_NO_VIDEO_REASONS``). The gate asked
``video_phase(expected=True)`` without the producer's reasons, so every such job read as a video still pending:
FAIL, "NOT SURFACED". The hero workflow then BLOCKED the run for having no frames, so an audio-only job never
reached its audio critic.

These pins run the real ``run_gate`` on synthetic docs and a synthetic master (ffmpeg ``aevalsrc``). This
repository is public, so no job, user or script here is real. The producer's reasons come from the producer (the
``producer_reasons`` and ``workers_src`` fixtures), never from a copy in a test.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

_QA = Path(__file__).resolve().parents[1]
_GATE = _QA / "scripts" / "acceptance_gate.py"

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required to synthesize the audio master",
)

#: A 220 Hz tone under a slow swell: about -16 LUFS with an LRA of about 7 LU, inside the explainer band.
_TONE = "0.25*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*t/6))"
_URL = "https://synthetic.invalid/podcasts/synthetic/master.mp3"
_TURNS = [{"speaker": "Host", "text": "A synthetic line for a synthetic episode, " * 3},
          {"speaker": "Guest", "text": "and a second synthetic line that answers it. " * 3}]


def _load_gate():
    spec = importlib.util.spec_from_file_location("acceptance_gate_audio_only", _GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gate(monkeypatch, tmp_path):
    """A fresh gate whose fetched masters land in this test's own directory."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return _load_gate()


def _master(path: Path, *, seconds: int = 30, rate: int = 44100, silent_from: float | None = None) -> Path:
    """A synthetic stereo master; ``silent_from`` cuts a 6 s hole of digital silence at that second."""
    expr = _TONE if silent_from is None else f"if(between(t,{silent_from},{silent_from + 6}),0,{_TONE})"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc='{expr}|{expr}':s={rate}:d={seconds}", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


def _doc(visual: dict, *, minutes: float = 0.5, url: str = _URL, turns: list | None = None) -> dict:
    return {"topic": "a synthetic topic", "audio_config": {"content_type": "explainer"},
            "inputs": {"duration_min": minutes}, "visual": visual,
            "outputs": {"audio_url": url, "script": {"dialogue": _TURNS if turns is None else turns}}}


def _run(gate, monkeypatch, doc: dict, *, master: Path | None, reasons, source: str = "the producer"):
    """``run_gate`` on ``doc``; the producer says ``reasons``; the fetch serves ``master``. Returns (out, fetched)."""
    fetched: list[str] = []

    def fetch(url: str, path: str) -> bool:
        fetched.append(url)
        if master is None:
            return False
        shutil.copyfile(master, path)
        return True

    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: dict(doc))
    # raising=False: a gate without these seams still runs, so the same pin can be read on the tree before them.
    monkeypatch.setattr(gate, "_fetch_master", fetch, raising=False)
    monkeypatch.setattr(gate, "_producer_no_video_reasons", lambda: (frozenset(reasons), source), raising=False)
    return gate.run_gate(f"synthetic-{uuid.uuid4().hex}"), fetched


# ---- the defect: a job with no video by design --------------------------------------------------------------

def test_every_by_design_reason_is_graded_as_its_audio(gate, monkeypatch, tmp_path, producer_reasons):
    """The witness's shape, for EVERY reason the producer calls by design, not only `opted_out_no_visuals`."""
    assert "opted_out_no_visuals" in producer_reasons, "the producer no longer calls the witness's reason by design"
    master = _master(tmp_path / "master.wav")
    for reason in sorted(producer_reasons):
        out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": reason}), master=master,
                            reasons=producer_reasons)
        assert out["verdict"] == "PASS_DETERMINISTIC", (reason, out["issues"])
        assert out["audio_only"] is True and out["audio_url"] == _URL and out["num_frames"] == 0, out
        assert fetched == [_URL], "the gate did not fetch the surfaced audio master"
        assert f"video_skip_reason={reason}" in out["no_video"]
        assert 29.5 < out["duration"] < 30.5, out["duration"]
        ran = {c["check_id"] for c in out["audio_checks"] if not c["skipped"]}
        assert {"audio.duration_vs_requested", "audio.not_truncated", "audio.loudness_in_target_range",
                "audio.lra_in_genre_band", "audio.no_clipping", "audio.no_long_silence"} <= ran, ran
        assert out["transcript"]["words"] == 45 and out["transcript"]["text"].startswith("Host: A synthetic line")
        assert out["next"].startswith("Run the ADVERSARY: a fresh agent LISTENS"), out["next"]


def test_a_job_that_wanted_a_video_and_has_none_stays_not_surfaced(gate, monkeypatch, tmp_path, producer_reasons):
    """CONTROL: a failure, a retry, a dead assembly or a pass still working is not "no video by design"."""
    assert "assembly_failed" not in producer_reasons, "the premise of the failure row is gone"
    by_design = sorted(producer_reasons)[0]
    live = {"owner": "pass-2", "expires_at_ms": int(time.time() * 1000) + 600_000}
    for visual, phase in (({}, "pending"),
                          ({"video_status": "rendering", "video_skip_reason": "assembly_failed"}, "rendering"),
                          ({"video_status": "failed_assembly", "video_skip_reason": by_design}, "failed"),
                          ({"video_skip_reason": by_design, "visuals_stage_lease": live}, "pending")):
        out, fetched = _run(gate, monkeypatch, _doc(visual), master=_master(tmp_path / "m.wav"),
                            reasons=producer_reasons)
        assert out["verdict"] == "FAIL", (visual, out)
        assert out["issues"][0]["msg"].startswith(f"NOT SURFACED: the video is {phase}"), (visual, out["issues"])
        assert "audio_only" not in out and not fetched, f"{visual} was graded as audio"


def test_without_the_producers_reasons_no_job_is_graded_as_audio(gate, monkeypatch, tmp_path):
    """Fail closed: when the producer cannot be read, no skip reason can say a job wanted no video."""
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": "opted_out_no_visuals"}),
                        master=_master(tmp_path / "m.wav"), reasons=(), source="no origin/main in /nowhere")
    assert out["verdict"] == "FAIL" and not fetched and "audio_only" not in out, out
    assert "could not be read (no origin/main in /nowhere)" in out["issues"][0]["msg"], out["issues"]


def test_a_ready_video_never_asks_the_producer(gate, monkeypatch):
    """The video path reads no workers tree: the producer is asked only when the video is not ready."""
    def asked():
        raise AssertionError("a ready video read the producer")

    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: {
        "topic": "t", "visual": {"status": "done", "video_status": "ready", "video_url": "https://x.invalid/v.mp4"}})
    monkeypatch.setattr(gate, "_producer_no_video_reasons", asked, raising=False)
    monkeypatch.setattr(gate, "_fetch_master", lambda url, path: False, raising=False)
    out = gate.run_gate(f"synthetic-{uuid.uuid4().hex}")
    assert out["issues"][0]["msg"] == "artifact not fetchable: https://x.invalid/v.mp4", out


# ---- what the audio grade finds -----------------------------------------------------------------------------

def test_a_broken_master_fails_on_the_check_that_caught_it(gate, monkeypatch, tmp_path, producer_reasons):
    """Each failure lands at its level: high -> BLOCKER, Layer 0's duration gate -> BLOCKER, medium -> MAJOR,
    low -> MINOR. And it is the CHECK that fails the job, never a missing video."""
    reason = sorted(producer_reasons)[0]
    holed = _master(tmp_path / "holed.wav", silent_from=12)
    good = _master(tmp_path / "good.wav")
    narrow = _master(tmp_path / "8k.wav", rate=8000)
    fast = [{**_TURNS[0], "rate": 1.2}, _TURNS[1]]
    for master, doc, verdict, sev, check in (
            (holed, _doc({"video_skip_reason": reason}), "FAIL", "BLOCKER", "audio.no_long_silence (high)"),
            (good, _doc({"video_skip_reason": reason}, minutes=5), "FAIL", "BLOCKER",
             "audio.duration_vs_requested (medium)"),
            (narrow, _doc({"video_skip_reason": reason}), "REVIEW", "MAJOR", "audio.sample_rate_ok (medium)"),
            (good, _doc({"video_skip_reason": reason}, turns=fast), "PASS_DETERMINISTIC", "MINOR",
             "audio.rate_capped.tech_review (low)")):
        out, _ = _run(gate, monkeypatch, doc, master=master, reasons=producer_reasons)
        hits = [i for i in out["issues"] if i["msg"].startswith(check)]
        assert out["verdict"] == verdict and hits and hits[0]["sev"] == sev, (check, out["verdict"], out["issues"])
        assert out["audio_only"] is True and not any("NOT SURFACED" in i["msg"] for i in out["issues"])


def test_an_audio_only_job_with_no_audio_master_is_not_surfaced(gate, monkeypatch, producer_reasons):
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": sorted(producer_reasons)[0]}, url=""),
                        master=None, reasons=producer_reasons)
    assert out["verdict"] == "FAIL" and out["audio_only"] is True and not fetched, out
    assert out["issues"][0]["msg"].startswith("NOT SURFACED: no video by design, and outputs.audio_url is empty")


def test_a_master_that_does_not_download_is_not_fetchable(gate, monkeypatch, producer_reasons):
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": sorted(producer_reasons)[0]}),
                        master=None, reasons=producer_reasons)
    assert out["verdict"] == "FAIL" and fetched == [_URL], out
    assert out["issues"] == [{"sev": "BLOCKER", "msg": f"artifact not fetchable: {_URL}"}]


def test_the_listening_brief_never_sends_the_critic_to_frames(gate):
    for persona in (None, "aarav-audio"):
        brief = gate._adversary_brief(persona, audio_only=True)
        assert "LISTENS" in brief and "audio_url" in brief and "no video by design" in brief, brief
        assert "Read EVERY frame" not in brief and "Reads every frame" not in brief, brief
    assert "Aarav" in gate._adversary_brief("aarav-audio", audio_only=True)
    assert "an audio master and its transcript" in gate._surface_note("aarav-audio", audio_only=True)


# ---- the producer's names, read under any interpreter -------------------------------------------------------

def test_the_producers_reasons_load_from_its_file_whatever_the_package_imports(tmp_path, workers_src,
                                                                               producer_reasons):
    """The qa venv cannot import the `workers` package (`No module named 'kitesforu_schemas'`): its __init__
    imports the worker base. The reasons module imports nothing, so the reader runs it alone. This tree's package
    __init__ raises on import; the reader must never execute it."""
    pkg = tmp_path / "src" / "workers"
    (pkg / "stages" / "visuals").mkdir(parents=True)
    (pkg / "__init__.py").write_text('raise ImportError("the worker base needs the workers dependencies")\n')
    (pkg / "stages" / "__init__.py").write_text("")
    (pkg / "stages" / "visuals" / "__init__.py").write_text("")
    shutil.copyfile(Path(workers_src) / "workers" / "stages" / "visuals" / "video_skip_reasons.py",
                    pkg / "stages" / "visuals" / "video_skip_reasons.py")
    r = subprocess.run([sys.executable, str(_QA / "scripts" / "verification_job.py"), "no-video-reasons",
                        f"--workers-src={tmp_path / 'src'}"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert frozenset(r.stdout.strip().split(",")) == producer_reasons


def test_the_gate_reads_the_producer_from_a_tree_or_origin_main(gate, monkeypatch, workers_src, producer_reasons):
    from producer_tree import workers_repo

    monkeypatch.setenv("WORKERS_SRC", workers_src)
    assert gate._producer_no_video_reasons() == (producer_reasons, f"WORKERS_SRC={workers_src}")
    monkeypatch.delenv("WORKERS_SRC")
    repo = workers_repo()
    if repo is not None:
        monkeypatch.setenv("KFU_WORKERS_REPO", str(repo))
        reasons, source = gate._producer_no_video_reasons()
        assert reasons == producer_reasons and source.startswith("kitesforu-workers origin/main "), source
    monkeypatch.setenv("KFU_WORKERS_REPO", "/nonexistent/kitesforu-workers")
    assert gate._producer_no_video_reasons() == (
        frozenset(), "no origin/main in /nonexistent/kitesforu-workers: set KFU_WORKERS_REPO or WORKERS_SRC")


# ---- the fetch: a failed download never grades the last run's file ------------------------------------------

def test_a_failed_download_never_grades_the_last_runs_master(gate, tmp_path, monkeypatch):
    """`curl -f` leaves an existing file in place on a failed fetch. The master path is fixed per job, so a
    failed re-fetch graded the previous run's bytes. A REAL failed download here (nothing listens on port 9),
    with a real video left behind by an earlier run."""
    job = f"synthetic-{uuid.uuid4().hex}"
    url = "https://127.0.0.1:9/never.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=540x960:d=3", "-c:v", "mpeg4", str(tmp_path / f"ag_{job}.mp4")],
                   check=True, capture_output=True, timeout=120)
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: {
        "topic": "t", "visual": {"status": "done", "video_status": "ready", "video_url": url}})
    out = gate.run_gate(job)
    assert out["verdict"] == "FAIL", out
    assert out["issues"] == [{"sev": "BLOCKER", "msg": f"artifact not fetchable: {url}"}], out
    assert not (tmp_path / f"ag_{job}.mp4").exists(), "the previous run's master survived the failed fetch"
