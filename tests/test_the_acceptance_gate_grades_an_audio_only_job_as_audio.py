"""The acceptance gate grades a job that asked for no visuals as its audio, never as a missing video.

An explainer made with visuals off carries ``visual.video_skip_reason = opted_out_no_visuals``, the producer's
``VIDEO_SKIP_OPTED_OUT``. The gate asked ``video_phase(expected=True)`` without the producer's reasons, so every such
job read as a video still pending: FAIL, "NOT SURFACED". The hero workflow then BLOCKED the run for having no frames,
so an audio-only job never reached its audio critic.

The producer's other by-design reasons (``BY_DESIGN_NO_VIDEO_REASONS``) say only that no video is coming: it stamps
them on jobs that asked for a video too, so those jobs stay NOT SURFACED.

These pins run the real ``run_gate`` on synthetic docs and a synthetic master (ffmpeg ``aevalsrc``). This
repository is public, so no job, user or script here is real. The producer's names come from the producer (the
``producer_reasons``, ``producer_opted_out`` and ``workers_src`` fixtures), never from a copy in a test.
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import uuid
from pathlib import Path

import pytest

_QA = Path(__file__).resolve().parents[1]
_GATE = _QA / "scripts" / "acceptance_gate.py"
sys.path.insert(0, str(_QA / "scripts"))
import verification_job  # noqa: E402  (stdlib-only at import)

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


@pytest.fixture(scope="module")
def producer_opted_out(workers_src, producer_reasons) -> str:
    """The producer's reason for a job that asked for no visuals, read by the reader under test from the producer's
    own file. Never a copy in a test."""
    reason = verification_job.producer_opted_out_reason(workers_src)
    assert reason in producer_reasons, "the producer no longer calls its opted-out reason by design"
    return reason


def _master(path: Path, *, seconds: int = 30, rate: int = 44100, silent_from: float | None = None) -> Path:
    """A synthetic stereo master; ``silent_from`` cuts a 6 s hole of digital silence at that second."""
    expr = _TONE if silent_from is None else f"if(between(t,{silent_from},{silent_from + 6}),0,{_TONE})"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc='{expr}|{expr}':s={rate}:d={seconds}", str(path)],
                   check=True, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    return path


def _doc(visual: dict, *, minutes: float = 0.5, url: str = _URL, turns: list | None = None) -> dict:
    return {"topic": "a synthetic topic", "audio_config": {"content_type": "explainer"},
            "inputs": {"duration_min": minutes}, "visual": visual,
            "outputs": {"audio_url": url, "script": {"dialogue": _TURNS if turns is None else turns}}}


def _run(gate, monkeypatch, doc: dict, *, master: Path | None, reasons, opted_out: str, source: str = "the producer"):
    """``run_gate`` on ``doc``; the producer says ``reasons`` and ``opted_out``; the fetch serves ``master``.
    Returns (out, fetched)."""
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
    monkeypatch.setattr(gate, "_producer_no_video_reasons",
                        lambda: gate.ProducerNoVideo(frozenset(reasons), opted_out, source), raising=False)
    return gate.run_gate(f"synthetic-{uuid.uuid4().hex}"), fetched


# ---- the defect: a job that asked for no visuals --------------------------------------------------------------

def test_a_job_that_asked_for_no_visuals_is_graded_as_its_audio(gate, monkeypatch, tmp_path, producer_reasons,
                                                               producer_opted_out):
    """The witness's shape: the producer's opted-out reason, and nothing working on the compartment."""
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": producer_opted_out}),
                        master=_master(tmp_path / "master.wav"), reasons=producer_reasons,
                        opted_out=producer_opted_out)
    assert out["verdict"] == "PASS_DETERMINISTIC", out["issues"]
    assert out["audio_only"] is True and out["audio_url"] == _URL and out["num_frames"] == 0, out
    assert fetched == [_URL], "the gate did not fetch the surfaced audio master"
    assert f"video_skip_reason={producer_opted_out}" in out["no_video"]
    assert 29.5 < out["duration"] < 30.5, out["duration"]
    ran = {c["check_id"] for c in out["audio_checks"] if not c["skipped"]}
    assert {"audio.duration_vs_requested", "audio.not_truncated", "audio.loudness_in_target_range",
            "audio.lra_in_genre_band", "audio.no_clipping", "audio.no_long_silence"} <= ran, ran
    assert out["transcript"]["words"] == 45 and out["transcript"]["text"].startswith("Host: A synthetic line")
    assert out["next"].startswith("Run the ADVERSARY: a fresh agent LISTENS"), out["next"]


def test_a_reason_that_says_only_no_video_is_coming_is_never_graded_as_audio(gate, monkeypatch, tmp_path,
                                                                            producer_reasons, producer_opted_out):
    """Every by-design reason but the opted-out one: the producer never re-renders it, but stamps it on jobs that
    asked for a video too (a stored explainer that planned visuals and rendered no clip carries one, at
    `visual.status=partial`, sometimes beside `video_status=rendering`). Not one is graded as audio."""
    others = sorted(producer_reasons - {producer_opted_out})
    assert others, "the producer calls only its opted-out reason by design: the premise of this pin is gone"
    master = _master(tmp_path / "m.wav")
    for reason in others:
        for visual in ({"video_skip_reason": reason},
                       {"status": "partial", "video_skip_reason": reason, "clips": []},
                       {"status": "partial", "video_status": "rendering", "video_skip_reason": reason, "clips": []}):
            out, fetched = _run(gate, monkeypatch, _doc(visual), master=master, reasons=producer_reasons,
                                opted_out=producer_opted_out)
            assert out["verdict"] == "FAIL" and "audio_only" not in out and not fetched, (visual, out)
            msg = out["issues"][0]["msg"]
            assert msg.startswith(f"NOT SURFACED: no video is coming (video_skip_reason={reason}"), (visual, msg)
            assert f"{reason} does not say the job wanted none" in msg, msg


def test_a_job_that_wanted_a_video_and_has_none_stays_not_surfaced(gate, monkeypatch, tmp_path, producer_reasons,
                                                                  producer_opted_out):
    """CONTROL: a failure, a retry, a dead assembly or a pass still working is not "no video by design"."""
    assert "assembly_failed" not in producer_reasons, "the premise of the failure row is gone"
    live = {"owner": "pass-2", "expires_at_ms": int(time.time() * 1000) + 600_000}
    for visual, phase in (({}, "pending"),
                          ({"video_status": "rendering", "video_skip_reason": "assembly_failed"}, "rendering"),
                          ({"video_status": "failed_assembly", "video_skip_reason": producer_opted_out}, "failed"),
                          ({"video_skip_reason": producer_opted_out, "visuals_stage_lease": live}, "pending")):
        out, fetched = _run(gate, monkeypatch, _doc(visual), master=_master(tmp_path / "m.wav"),
                            reasons=producer_reasons, opted_out=producer_opted_out)
        assert out["verdict"] == "FAIL", (visual, out)
        assert out["issues"][0]["msg"].startswith(f"NOT SURFACED: the video is {phase}"), (visual, out["issues"])
        assert "audio_only" not in out and not fetched, f"{visual} was graded as audio"


def test_a_dead_pass_whose_lease_expired_is_no_pass_still_working(gate, monkeypatch, tmp_path, producer_reasons,
                                                                 producer_opted_out):
    """The read's clock reaches the readiness rule: a lease that expired an hour ago is a dead pass, not work in
    flight. Without the clock any owned lease counts as live, and the job would stay NOT SURFACED forever."""
    dead = {"owner": "pass-1", "expires_at_ms": int(time.time() * 1000) - 3_600_000}
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": producer_opted_out, "visuals_stage_lease": dead}),
                        master=_master(tmp_path / "m.wav"), reasons=producer_reasons, opted_out=producer_opted_out)
    assert out["verdict"] == "PASS_DETERMINISTIC" and out["audio_only"] is True and fetched == [_URL], out


def test_without_the_producers_reasons_no_job_is_graded_as_audio(gate, monkeypatch, tmp_path):
    """Fail closed: when the producer cannot be read, no skip reason can say a job wanted no video, and the message
    says only that, never that the reason is not a by-design one."""
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": "opted_out_no_visuals"}),
                        master=_master(tmp_path / "m.wav"), reasons=(), opted_out="",
                        source="no origin/main in /nowhere")
    assert out["verdict"] == "FAIL" and not fetched and "audio_only" not in out, out
    msg = out["issues"][0]["msg"]
    assert "could not be read (no origin/main in /nowhere)" in msg and "video_skip_reason=opted_out_no_visuals" in msg
    assert "not a by-design reason" not in msg, msg


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

def test_a_broken_master_fails_on_the_check_that_caught_it(gate, monkeypatch, tmp_path, producer_reasons,
                                                          producer_opted_out):
    """Each failure lands at its level: high -> BLOCKER, Layer 0's duration gate -> BLOCKER, medium -> MAJOR,
    low -> MINOR. And it is the CHECK that fails the job, never a missing video."""
    visual = {"video_skip_reason": producer_opted_out}
    holed = _master(tmp_path / "holed.wav", silent_from=12)
    good = _master(tmp_path / "good.wav")
    narrow = _master(tmp_path / "8k.wav", rate=8000)
    fast = [{**_TURNS[0], "rate": 1.2}, _TURNS[1]]
    for master, doc, verdict, sev, check in (
            (holed, _doc(visual), "FAIL", "BLOCKER", "audio.no_long_silence (high)"),
            (good, _doc(visual, minutes=5), "FAIL", "BLOCKER", "audio.duration_vs_requested (medium)"),
            (narrow, _doc(visual), "REVIEW", "MAJOR", "audio.sample_rate_ok (medium)"),
            (good, _doc(visual, turns=fast), "PASS_DETERMINISTIC", "MINOR", "audio.rate_capped.tech_review (low)")):
        out, _ = _run(gate, monkeypatch, doc, master=master, reasons=producer_reasons, opted_out=producer_opted_out)
        hits = [i for i in out["issues"] if i["msg"].startswith(check)]
        assert out["verdict"] == verdict and hits and hits[0]["sev"] == sev, (check, out["verdict"], out["issues"])
        assert out["audio_only"] is True and not any("NOT SURFACED" in i["msg"] for i in out["issues"])


def test_the_gate_blocks_on_the_severities_the_battery_gates_on(gate, monkeypatch, tmp_path, producer_reasons,
                                                               producer_opted_out):
    """The blocking severities are the battery's own set, read when a check fails: a battery that starts gating on
    medium makes the gate block a failed medium check too."""
    from kitesforu_qa.harness import battery

    monkeypatch.setattr(battery, "_GATING", set(battery._GATING) | {"medium"})
    out, _ = _run(gate, monkeypatch, _doc({"video_skip_reason": producer_opted_out}),
                  master=_master(tmp_path / "8k.wav", rate=8000), reasons=producer_reasons,
                  opted_out=producer_opted_out)
    hits = [i for i in out["issues"] if i["msg"].startswith("audio.sample_rate_ok (medium)")]
    assert out["verdict"] == "FAIL" and hits and hits[0]["sev"] == "BLOCKER", out["issues"]


def test_a_battery_that_ran_no_check_fails(gate, monkeypatch, tmp_path, producer_reasons, producer_opted_out):
    """A grade with nothing measured is no grade: every check skipped, or none at all, is a BLOCKER."""
    import kitesforu_qa.harness as harness

    skipped = {"check_id": "audio.synthetic", "severity": "high", "passed": True, "skipped": True, "evidence": ""}
    for checks in ([skipped, {**skipped, "check_id": "audio.other"}], []):
        monkeypatch.setattr(harness, "run_dimension",
                            lambda art, dimension, checks=checks: types.SimpleNamespace(data={"checks": checks}))
        out, _ = _run(gate, monkeypatch, _doc({"video_skip_reason": producer_opted_out}),
                      master=_master(tmp_path / "m.wav"), reasons=producer_reasons, opted_out=producer_opted_out)
        assert out["verdict"] == "FAIL", (checks, out)
        assert {"sev": "BLOCKER", "msg": "NO AUDIO CHECK RAN: every check in the audio battery skipped"} in out["issues"]


def test_the_transcript_reads_each_turn_as_the_artifact_does(gate, monkeypatch, tmp_path, producer_reasons,
                                                            producer_opted_out):
    """One reading of a turn's keys: a turn keyed `role`/`line` keeps its speaker in the transcript, as in
    `Artifact.speakers`."""
    from kitesforu_qa.harness import Artifact

    turns = [{"role": "Host", "line": "A synthetic line keyed the other way, " * 3},
             {"name": "Guest", "text": "and a synthetic answer keyed by name. " * 3}]
    doc = _doc({"video_skip_reason": producer_opted_out}, turns=turns)
    out, _ = _run(gate, monkeypatch, doc, master=_master(tmp_path / "m.wav"), reasons=producer_reasons,
                  opted_out=producer_opted_out)
    lines = out["transcript"]["text"].split("\n")
    assert [line.split(":", 1)[0] for line in lines] == Artifact.from_doc(doc).speakers == ["Host", "Guest"], lines


def test_an_audio_only_job_with_no_audio_master_is_not_surfaced(gate, monkeypatch, producer_reasons,
                                                                producer_opted_out):
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": producer_opted_out}, url=""),
                        master=None, reasons=producer_reasons, opted_out=producer_opted_out)
    assert out["verdict"] == "FAIL" and out["audio_only"] is True and not fetched, out
    assert out["issues"][0]["msg"].startswith("NOT SURFACED: no video by design, and outputs.audio_url is empty")


def test_a_master_that_does_not_download_is_not_fetchable(gate, monkeypatch, producer_reasons, producer_opted_out):
    out, fetched = _run(gate, monkeypatch, _doc({"video_skip_reason": producer_opted_out}),
                        master=None, reasons=producer_reasons, opted_out=producer_opted_out)
    assert out["verdict"] == "FAIL" and fetched == [_URL], out
    assert out["issues"] == [{"sev": "BLOCKER", "msg": f"artifact not fetchable: {_URL}"}]


def test_the_listening_brief_never_sends_the_critic_to_frames(gate):
    for persona in (None, "aarav-audio"):
        brief = gate._adversary_brief(persona, audio_only=True)
        assert "LISTENS" in brief and "audio_url" in brief and "no video by design" in brief, brief
        assert "Read EVERY frame" not in brief and "Reads every frame" not in brief, brief
    assert "Aarav" in gate._adversary_brief("aarav-audio", audio_only=True)
    assert "an audio master and its transcript" in gate._surface_note("aarav-audio", audio_only=True)


# ---- the producer's names, read under any interpreter, from one checkout --------------------------------------

def test_the_producers_reasons_load_from_its_file_whatever_the_package_imports(tmp_path, workers_src,
                                                                               producer_reasons, producer_opted_out):
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
    assert verification_job.producer_opted_out_reason(str(tmp_path / "src")) == producer_opted_out


def test_the_gate_reads_the_producer_from_a_tree_or_origin_main(gate, monkeypatch, workers_src, producer_reasons,
                                                               producer_opted_out):
    from producer_tree import workers_repo

    monkeypatch.setenv("WORKERS_SRC", workers_src)
    assert gate._producer_no_video_reasons() == (producer_reasons, producer_opted_out, f"WORKERS_SRC={workers_src}")
    monkeypatch.delenv("WORKERS_SRC")
    repo = workers_repo()
    if repo is not None:
        monkeypatch.setenv("KFU_WORKERS_REPO", str(repo))
        by_design, opted_out, source = gate._producer_no_video_reasons()
        assert (by_design, opted_out) == (producer_reasons, producer_opted_out), source
        assert source.startswith("kitesforu-workers origin/main "), source
    monkeypatch.setenv("KFU_WORKERS_REPO", "/nonexistent/kitesforu-workers")
    assert gate._producer_no_video_reasons() == (
        frozenset(), "", "no origin/main in /nonexistent/kitesforu-workers: set KFU_WORKERS_REPO or WORKERS_SRC")


def test_with_no_env_the_gate_reads_the_checkout_the_wait_reads(gate, monkeypatch):
    """No env: the gate reads the one default checkout, wherever this tree is, never a checkout beside the tree (a
    lane's tree under ~/kfu-work has none beside it). The wait, the gate and the tests name the same path."""
    from producer_tree import workers_repo

    wait = re.search(r'^WORKERS_REPO="\$\{KFU_WORKERS_REPO:-([^}]+)\}"$',
                     (_QA / "scripts" / "create_verification_job.sh").read_text(), re.MULTILINE)
    assert wait and wait.group(1) == verification_job.WORKERS_REPO_DEFAULT, wait
    monkeypatch.delenv("WORKERS_SRC", raising=False)
    monkeypatch.delenv("KFU_WORKERS_REPO", raising=False)
    monkeypatch.setattr(verification_job, "WORKERS_REPO_DEFAULT", "/nonexistent/the-one-default")
    assert gate._producer_no_video_reasons().source == (
        "no origin/main in /nonexistent/the-one-default: set KFU_WORKERS_REPO or WORKERS_SRC")
    assert workers_repo() is None


# ---- the fetch: what a failed download leaves is never graded -------------------------------------------------

def test_a_failed_download_never_grades_the_last_runs_master(gate, tmp_path, monkeypatch):
    """`curl -f` leaves an existing file in place on a failed fetch. The master path is fixed per job, so a
    failed re-fetch graded the previous run's bytes. A REAL failed download here (nothing listens on port 9),
    with a real video left behind by an earlier run."""
    job = f"synthetic-{uuid.uuid4().hex}"
    url = "https://127.0.0.1:9/never.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=540x960:d=3", "-c:v", "mpeg4", str(tmp_path / f"ag_{job}.mp4")],
                   check=True, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: {
        "topic": "t", "visual": {"status": "done", "video_status": "ready", "video_url": url}})
    out = gate.run_gate(job)
    assert out["verdict"] == "FAIL", out
    assert out["issues"] == [{"sev": "BLOCKER", "msg": f"artifact not fetchable: {url}"}], out
    assert not (tmp_path / f"ag_{job}.mp4").exists(), "the previous run's master survived the failed fetch"


@pytest.mark.skipif(not shutil.which("curl"), reason="curl required for the real fetch")
def test_a_download_that_dies_part_way_is_never_graded(gate, monkeypatch, tmp_path, producer_reasons,
                                                       producer_opted_out):
    """A REAL fetch from a local server that promises the whole master and sends 30% of it. curl exits 18, and the
    partial file's header still states the full duration, so a grade of it measured a third and passed the rest."""
    body = _master(tmp_path / "whole.wav", seconds=20).read_bytes()

    class PartWay(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (the stdlib's name)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[: len(body) * 3 // 10])
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)

        def log_message(self, format, *args):  # noqa: A002 (the stdlib's signature)
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PartWay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/master.wav"
        partial = tmp_path / "partial.wav"
        assert gate._fetch_master(url, str(partial)) is False
        assert not partial.exists(), "what the failed fetch left behind was kept"
        monkeypatch.setattr(gate, "_fetch_job", lambda job_id: _doc({"video_skip_reason": producer_opted_out},
                                                                     url=url))
        monkeypatch.setattr(gate, "_producer_no_video_reasons",
                            lambda: gate.ProducerNoVideo(frozenset(producer_reasons), producer_opted_out, "stub"))
        out = gate.run_gate(f"synthetic-{uuid.uuid4().hex}")
    finally:
        server.shutdown()
        server.server_close()
    assert out["verdict"] == "FAIL" and out["issues"] == [{"sev": "BLOCKER", "msg": f"artifact not fetchable: {url}"}]


def test_the_gate_leaves_its_stdin_unread(tmp_path, producer_reasons, producer_opted_out):
    """A shell loop feeds the gate job ids on stdin. ffmpeg reads its stdin for keys, so every child the gate starts
    is given none: an audio grade and a video grade each leave the ids after them unread."""
    master = _master(tmp_path / "m.wav")
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=540x960:d=6", "-c:v", "mpeg4", str(video)],
                   check=True, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    ready = {"topic": "t", "visual": {"status": "done", "video_status": "ready", "video_url": "https://x.invalid/v.mp4"}}
    script = tmp_path / "grade_then_read_stdin.py"
    script.write_text(
        "import importlib.util, json, shutil, sys\n"
        f"spec = importlib.util.spec_from_file_location('gate_stdin', {str(_GATE)!r})\n"
        "gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)\n"
        "doc, source = json.loads(sys.argv[1]), sys.argv[2]\n"
        "gate._fetch_job = lambda job_id: doc\n"
        "gate._fetch_master = lambda url, path: bool(shutil.copyfile(source, path))\n"
        f"gate._producer_no_video_reasons = lambda: gate.ProducerNoVideo(frozenset({sorted(producer_reasons)!r}), "
        f"{producer_opted_out!r}, 'stub')\n"
        f"out = gate.run_gate('synthetic-stdin', {str(tmp_path / 'frames')!r})\n"
        "print(json.dumps({'audio_only': out.get('audio_only', False), 'checks': len(out.get('audio_checks') or []), "
        "'frames': out.get('num_frames'), 'left': sys.stdin.read()}))\n")
    for doc, source, audio_only in ((_doc({"video_skip_reason": producer_opted_out}), master, True),
                                    (ready, video, False)):
        r = subprocess.run([sys.executable, str(script), json.dumps(doc), str(source)], input="id-2\nid-3\n",
                           capture_output=True, text=True, timeout=300, env={**os.environ, "TMPDIR": str(tmp_path)})
        assert r.returncode == 0, r.stderr[-2000:]
        result = json.loads(r.stdout.strip().splitlines()[-1])
        assert result["audio_only"] is audio_only and (result["checks"] if audio_only else result["frames"]), result
        assert result["left"] == "id-2\nid-3\n", f"a child of the gate read its stdin: {result}"
