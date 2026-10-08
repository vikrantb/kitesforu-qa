"""Every script that fetched an artifact its own way now goes through the shared downloader.

Each of these turned a failed fetch into an absence: ``None``, ``(None, None)``, a skipped pair, or a
mislabelled verdict. Now a failure is raised, or named where the script reports. The downloader itself
is stubbed here; its guarantees are pinned in ``tests/test_one_downloader_for_every_artifact.py``.
Offline and $0. ffmpeg builds the small media files a few of these need.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys

import pytest

from kitesforu_qa.integrations.download import Downloaded, DownloadError

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
URL = "https://storage.googleapis.com/kitesforu-dev-podcasts/audio/job/final.mp3"
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="needs ffmpeg and ffprobe")


def _load(name):
    """Each script as a module, registered first: dataclasses resolve their module through
    ``sys.modules``."""
    spec = importlib.util.spec_from_file_location(f"_grader_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _writes(body: bytes, generation=None, seen=None):
    def fake(uri, local_path, **_kw):
        if seen is not None:
            seen.append((uri, local_path))
        pathlib.Path(local_path).write_bytes(body)
        return Downloaded(uri, local_path, len(body), generation, None)
    return fake


def _fails(cause="Forbidden: 403", not_found=False):
    def fake(uri, local_path, **_kw):
        raise DownloadError(f"{uri}: {cause}", uri=uri, not_found=not_found)
    return fake


# ── verify_pr_against_live ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def vpal():
    return _load("verify_pr_against_live")


@pytest.mark.parametrize("job, url", [
    ({"outputs": {"audio_url": URL}}, URL),
    ({"audio_url": "gs://b/legacy.mp3"}, "gs://b/legacy.mp3"),
    ({"audio_gcs_uri": "gs://b/a.mp3"}, None),        # a field no completed job carries
])
def test_verify_pr_reads_the_one_accessor(vpal, tmp_path, monkeypatch, job, url):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(vpal, "download", _writes(b"x", seen=seen))
    got = vpal._download_audio(job, str(tmp_path / "post.mp3"))
    assert seen == ([(url, str(tmp_path / "post.mp3"))] if url else [])
    assert got == (str(tmp_path / "post.mp3") if url else None)


def test_verify_pr_names_a_failed_download_on_the_rung(vpal, tmp_path, monkeypatch):
    """It used to log a warning and hand rungs 4 and 5 no audio, with nothing on the rungs."""
    monkeypatch.setattr(vpal, "download", _fails())
    path, why = vpal._try_download_audio({"outputs": {"audio_url": URL}}, str(tmp_path / "p.mp3"))
    assert path is None and "403" in why
    rung = vpal.RungResult(rung=4, name="audio_measurements", verdict=vpal.Verdict.PASS, detail="ok")
    vpal._note_download_errors(rung, post=why, baseline=None)
    assert rung.evidence["audio_download_failed"] == {"post": why}
    assert rung.detail.startswith("ok; local audio not downloaded: post:")
    clean = vpal.RungResult(rung=5, name="ab_improvement", verdict=vpal.Verdict.PASS, detail="ok")
    assert vpal._note_download_errors(clean, post=None).detail == "ok"


# ── canary_loop ───────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def canary():
    return _load("canary_loop")


def test_the_canary_reads_the_one_accessor(canary):
    """It read ``audio.mp3_url`` first, which none of the 3,162 completed jobs carries."""
    doc = {"status": "completed", "outputs": {"audio_url": URL},
           "stages": {"job-audio": {"result": {"audio_url": "gs://b/stage.mp3"}}}}
    assert canary.diagnose_terminal(doc, "j")["mp3_url"] == URL
    doc = {"status": "completed", "stages": {"job-audio": {"result": {"audio_url": URL}}}}
    assert canary.diagnose_terminal(doc, "j")["mp3_url"] == URL


@needs_ffmpeg
def test_the_canary_probes_what_the_downloader_fetched(canary, tmp_path, monkeypatch):
    wav = tmp_path / "one_second.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    str(wav)], check=True)
    monkeypatch.setattr(canary, "download", _writes(wav.read_bytes()))
    duration, size = canary.ffprobe_duration(URL)
    assert duration == pytest.approx(1.0, abs=0.05) and size == wav.stat().st_size


def test_an_unfetchable_mp3_raises_instead_of_reading_as_an_mp3_that_is_off(canary, monkeypatch):
    """``(None, None)`` used to be logged as ``completed_but_mp3_off``: a product defect it never saw."""
    monkeypatch.setattr(canary, "download", _fails("SSLError: CERTIFICATE_VERIFY_FAILED"))
    with pytest.raises(DownloadError, match="CERTIFICATE_VERIFY_FAILED"):
        canary.ffprobe_duration(URL)


# ── frames_vs_captions ────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fvc():
    return _load("frames_vs_captions")


def test_frames_vs_captions_fetches_through_the_downloader(fvc, tmp_path, monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(fvc, "download", _writes(b"mp4", seen=seen))
    fvc._download(URL, tmp_path / "master.mp4")
    assert seen == [(URL, str(tmp_path / "master.mp4"))]
    monkeypatch.setattr(fvc, "download", _fails("HTTP 404", not_found=True))
    with pytest.raises(SystemExit, match="could not fetch the master .*HTTP 404"):
        fvc._download(URL, tmp_path / "master.mp4")


# ── frame_proof: it fetches through the downloader, and gains the master tie ──────────────────

class _Firestore:
    def __init__(self, doc):
        self._doc = doc

    def collection(self, _name):
        return self

    def document(self, _id):
        return self

    def get(self):
        return self

    def to_dict(self):
        return self._doc


@needs_ffmpeg
@pytest.mark.parametrize("stamped_generation, source", [(111, "stamp (stamp)"),
                                                        (222, "estimated (trusted; stale_master)")])
def test_frame_proof_holds_the_stamp_to_the_master_it_fetched(tmp_path, monkeypatch, capsys,
                                                              stamped_generation, source):
    """frame_proof fetched with gsutil, which reports no generation, so it had the length check
    only. The downloader reports the object it fetched, so a sidecar that names another master is
    ``stale_master`` here too."""
    from google.cloud import firestore

    from kitesforu_qa.harness import delivered_timeline as dt
    from kitesforu_qa.harness.painted_timeline_sidecar import SidecarRead

    master = tmp_path / "made.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=96x54:rate=10:duration=3",
                    "-c:v", "mpeg4", str(master)], check=True)
    size = master.stat().st_size
    stamp = {"version": 1, "master_ms": 3000, "master_generation": stamped_generation, "master_size": size,
             "windows": [{"clip": 0, "source_clip": 0, "start_ms": 0, "end_ms": 3000,
                          "modality": "scene_image", "render_mode": "still", "motion_render": None,
                          "asset_kind": "image"}],
             "close": {"applied": False, "fade_start_ms": None, "fade_ms": None, "reason": "test"}}
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"video_url": "https://storage.googleapis.com/b/visuals/j/episode_video.mp4",
                      "painted_timeline_uri": "https://storage.googleapis.com/b/visuals/j/painted_timeline.json",
                      "clips": [{"start_ms": 0, "modality": "scene_image", "asset_uri": "gs://b/0.png",
                                 "status": "done", "render_mode": "still"}]}}
    monkeypatch.setattr(firestore, "Client", lambda project=None: _Firestore(doc))
    monkeypatch.setattr(dt, "read_sidecar", lambda d: SidecarRead("read", "u", parsed=stamp, bytes_read=1))
    fp = _load("frame_proof")
    monkeypatch.setattr(fp, "download", _writes(master.read_bytes(), generation=111))
    monkeypatch.setattr(sys, "argv", ["frame_proof.py", "j"])
    assert fp.main() == 0
    assert f"timeline: {source}" in capsys.readouterr().out


def test_frame_proof_reports_an_unfetchable_master(tmp_path, monkeypatch, capsys):
    from google.cloud import firestore

    doc = {"visual": {"video_url": "https://storage.googleapis.com/b/visuals/j/episode_video.mp4",
                      "clips": [{"start_ms": 0}]}}
    monkeypatch.setattr(firestore, "Client", lambda project=None: _Firestore(doc))
    fp = _load("frame_proof")
    monkeypatch.setattr(fp, "download", _fails("HTTP 404", not_found=True))
    monkeypatch.setattr(sys, "argv", ["frame_proof.py", "j"])
    assert fp.main() == 1
    assert "could not fetch" in capsys.readouterr().err


# ── music_bed_presence: a pair that never downloads is counted ────────────────────────────────

def test_the_music_bed_census_counts_what_it_could_not_fetch(monkeypatch, capsys):
    mbp = _load("music_bed_presence")
    pairs = [("aaaaaaaa1", "https://x/a_m.mp3", "https://x/a_s.mp3"),
             ("bbbbbbbb2", "https://x/b_m.mp3", "https://x/b_s.mp3")]
    monkeypatch.setattr(mbp, "_pairs", lambda want: pairs)

    def fake(uri, local_path, **_kw):
        if "/a_" in uri:
            raise DownloadError(f"{uri}: HTTP 404", uri=uri, not_found=True)
        pathlib.Path(local_path).write_bytes(b"x" * 10)
        return Downloaded(uri, local_path, 10, None, "audio/mpeg")

    monkeypatch.setattr(mbp, "download", fake)
    monkeypatch.setattr(sys, "argv", ["music_bed_presence.py", "2"])
    assert mbp.main() == 0
    out = capsys.readouterr().out
    assert "pairs not fetched: 1 [('aaaaaaaa'" in out and "under 5000 bytes: 1 ['bbbbbbbb']" in out


def test_the_scripts_no_longer_fetch_their_own_way():
    """No urllib, curl or gsutil download left in the sites this PR moved onto the downloader."""
    for name in ("quality_matrix", "short_scorecard", "verify_pr_against_live", "canary_loop",
                 "frames_vs_captions", "frame_proof", "acceptance_gate", "music_bed_presence"):
        src = (SCRIPTS / f"{name}.py").read_text()
        for needle in ("urlretrieve(", "urlopen(", '["gsutil"', '["curl"'):
            assert needle not in src, f"{name}.py still has {needle}"
    checker = (SCRIPTS / "full_artifact_checker.sh").read_text()
    assert "curl -s -D" not in checker and "download(url" in checker
