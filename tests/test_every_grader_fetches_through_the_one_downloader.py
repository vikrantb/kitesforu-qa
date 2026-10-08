"""Every script that fetched an artifact its own way now goes through the shared downloader.

Each of these turned a failed fetch into an absence: ``None``, ``(None, None)``, a skipped pair, or a
mislabelled verdict. Now a failure is raised, or named where the script reports. The downloader itself
is stubbed here; its guarantees are pinned in ``tests/test_one_downloader_for_every_artifact.py``.
Offline and $0. ffmpeg builds the small media files a few of these need.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
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


def test_run_ladder_names_both_failed_downloads_on_rungs_4_and_5(vpal, monkeypatch):
    """Round-2 critic: the helper was tested, the WIRING was not. Dropping either call in
    ``run_ladder``, or handing it ``None``, left every test green."""
    docs = {"base": {"status": "completed", "outputs": {"audio_url": URL + "?b"}},
            "post": {"status": "completed", "outputs": {"audio_url": URL + "?p"}}}
    monkeypatch.setattr(vpal, "_fetch_job", lambda project, job_id: docs[job_id])
    monkeypatch.setattr(vpal, "download", _fails("Forbidden: 403"))
    made = []
    real = vpal.tempfile.TemporaryDirectory

    def tracked(**kw):
        made.append(real(**kw))
        return made[-1]

    monkeypatch.setattr(vpal.tempfile, "TemporaryDirectory", tracked)
    verdict = vpal.run_ladder("pr", "audio", "base", "post", "proj", "qa", skip_rung_1=True)
    rungs = {r.rung: r for r in verdict.rungs}
    assert set(rungs[4].evidence["audio_download_failed"]) == {"post"}
    assert set(rungs[5].evidence["audio_download_failed"]) == {"post", "baseline"}
    assert "403" in rungs[5].evidence["audio_download_failed"]["baseline"]
    assert made and not pathlib.Path(made[0].name).exists()     # the downloads did not outlive the run


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


@pytest.mark.parametrize("probe_error", [subprocess.TimeoutExpired("ffprobe", 60),
                                         FileNotFoundError(2, "No such file", "ffprobe")])
def test_a_probe_that_times_out_or_is_missing_is_an_unread_file_not_an_escape(
        canary, monkeypatch, probe_error):
    """Round-2 critic #2: the narrowed catch let ``TimeoutExpired`` and a missing ffprobe escape
    ``ffprobe_duration``, and ``run_one`` logged a completed job as ``TRIGGER_ERR`` with a Slack
    hypothesis about token mint."""
    monkeypatch.setattr(canary, "download", _writes(b"x" * 200_000))

    def raising(*_a, **_k):
        raise probe_error

    monkeypatch.setattr(canary.subprocess, "run", raising)
    assert canary.ffprobe_duration(URL) == (None, 200_000)


def test_run_one_logs_an_unfetchable_mp3_as_such(canary, monkeypatch):
    """The label on the logged row, not only the raise (round-2 critic: ``if fetch_error:`` →
    ``if False:`` left every test green)."""
    logged = []
    monkeypatch.setattr(canary, "mint_clerk_token", lambda: "token")
    monkeypatch.setattr(canary, "trigger_job", lambda token: "job-1")
    monkeypatch.setattr(canary, "watch_job", lambda db, job_id: {"status": "completed"})
    monkeypatch.setattr(canary, "diagnose_terminal", lambda final, job_id: {
        "status": "completed", "is_timeout": False, "mp3_url": URL})
    monkeypatch.setattr(canary, "download", _fails("HTTP 503"))
    monkeypatch.setattr(canary, "append_log", logged.append)
    result = canary.run_one(None, 7)
    assert result["outcome"] == "warn"
    assert len(logged) == 1 and "mp3_unfetchable (" in logged[0] and "HTTP 503" in logged[0]


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
    exists = True

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
@pytest.mark.parametrize("stamped_generation, source", [
    (111, "stamp (stamp; master identity verified)"),
    (222, "estimated (trusted; stale_master)"),
])
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


# ── the ratchet: no fetch around the downloader, anywhere in scripts/ or src/ ──────────────────

QA = pathlib.Path(__file__).resolve().parents[1]
#: Calls that fetch bytes: by their last attribute (any object), or by their full dotted name.
_FETCH_ATTRS = {"urlretrieve", "urlopen", "download_to_filename", "download_as_bytes",
                "download_as_string", "download_as_text", "download_to_file"}
_FETCH_CALLS = {"requests.get", "requests.head", "requests.request", "httpx.get", "httpx.stream",
                "httpx.request", "storage.Client"}
#: A list or tuple literal that starts with one of these is a command line.
_FETCH_PROGRAMS = {"curl", "wget", "gsutil"}
_SHELL_FETCH = re.compile(r"(^|[\s;|&(`$])(curl|wget)(\s|$)|gsutil\s+(-\S+\s+)*(cp|cat|rsync)\b")

#: The fetch sites that may not go through the downloader, each with its reason. Anything else the
#: scan finds is a grader fetching its own way, which is the class this PR closes.
ALLOWED = {
    ("src/kitesforu_qa/integrations/download.py", "requests.get"): "the one downloader",
    ("src/kitesforu_qa/integrations/gcs.py", "storage.Client"): "the one GCS client constructor",
    ("src/kitesforu_qa/integrations/kitesforu_api.py", "requests.get"):
        "the KitesForU API client: JSON calls, never artifact bytes",
    ("scripts/model_catalog_reconcile.py", "curl"):
        "probes providers' model catalogs (JSON), credentials on stdin; never an artifact",
    ("scripts/create_verification_job.sh", "curl"):
        "POSTs the create API and polls its status (JSON); never an artifact",
    ("scripts/full_artifact_checker.sh", "curl"):
        "step 7's HEAD probe (-I, no body) of the surfaced URL; step 7 fetches through the downloader",
}


def _dotted(node):
    parts = []
    while isinstance(node, (ast.Attribute, ast.Call)):
        if isinstance(node, ast.Call):
            node = node.func
            continue
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _fetch_sites(path: pathlib.Path, rel: str):
    """``(rel, primitive, line)`` for every fetch primitive in one file: Python by its syntax tree (a
    docstring or a comment that names one is not a call), shell by its non-comment lines."""
    text = path.read_text()
    if path.suffix == ".sh":
        for n, line in enumerate(text.splitlines(), 1):
            m = None if line.lstrip().startswith("#") else _SHELL_FETCH.search(line)
            if m:
                yield rel, m.group(2) or "gsutil", n
        return
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name.rsplit(".", 1)[-1] in _FETCH_ATTRS or name in _FETCH_CALLS:
                yield rel, name if name in _FETCH_CALLS else name.rsplit(".", 1)[-1], node.lineno
        if (isinstance(node, (ast.List, ast.Tuple)) and node.elts
                and isinstance(node.elts[0], ast.Constant) and node.elts[0].value in _FETCH_PROGRAMS):
            yield rel, node.elts[0].value, node.lineno


def _scan(root: pathlib.Path):
    for sub in ("scripts", "src"):
        for path in sorted((root / sub).rglob("*")):
            if path.suffix in (".py", ".sh") and "__pycache__" not in path.parts:
                yield from _fetch_sites(path, path.relative_to(root).as_posix())


def test_the_scanner_finds_every_primitive_it_looks_for(tmp_path):
    """The ratchet below is only as good as this scan, so the scan is made to fire first, on one
    file per primitive (round-2 design: the old guard checked a closed list of 8 files for 4 needles,
    and could see neither of the two graders that still fetched their own way)."""
    py = {
        "a.py": "blob.download_to_filename(p)", "b.py": "x = client.bucket(b).blob(o).download_as_bytes()",
        "c.py": "import urllib.request\nurllib.request.urlretrieve(u, p)", "d.py": "requests.get(u)",
        "e.py": "subprocess.run(['gsutil', 'cp', u, p])", "f.py": "cmd = ('curl', '-o', p, u)",
        "g.py": "storage.Client()", "h.py": "urlopen(u).read()",
    }
    sh = {"a.sh": "curl -s -o out \"$U\"", "b.sh": "X=$(wget -q \"$U\")", "c.sh": "gsutil -q cp gs://b/o .",
          "d.sh": "# curl in a comment is not a fetch"}
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    for name, body in {**py, **sh}.items():
        (tmp_path / "scripts" / name).write_text(body + "\n")
    (tmp_path / "src" / "doc.py").write_text('"""Fetched with requests.get(url) and gsutil cp."""\n')
    found = {rel.split("/")[-1] for rel, _, _ in _scan(tmp_path)}
    assert found == set(py) | {"a.sh", "b.sh", "c.sh"}


def test_no_script_or_module_fetches_around_the_downloader():
    """Every fetch primitive in scripts/ and src/ is one of ALLOWED, with its reason. A new grader
    that fetches its own way goes red here. (The adopted sites' behaviour is pinned above.)"""
    found = {(rel, prim): line for rel, prim, line in _scan(QA)}
    unexpected = sorted(f"{rel}:{line} {prim}" for (rel, prim), line in found.items()
                        if (rel, prim) not in ALLOWED)
    assert not unexpected, f"fetching around integrations.download: {unexpected}"
    stale = sorted(f"{rel} {prim}" for rel, prim in ALLOWED if (rel, prim) not in found)
    assert not stale, f"ALLOWED entries that no longer occur (delete them): {stale}"


# ── measure_delivered_clips and perclip_lit_census: a failed fetch is counted, never an absence ───

CLIPS = [{"asset_uri": f"gs://b/visuals/j/{i}_motion.mp4", "modality": "diagram"} for i in range(3)]


def test_measure_delivered_clips_says_a_total_fetch_failure_is_not_an_absence(
        tmp_path, monkeypatch, capsys):
    """Round-2 design HIGH: every clip failing to download printed "no .mp4 clips found", the
    absence of clips, and the operator read it as a job that rendered none."""
    from google.cloud import firestore

    mdc = _load("measure_delivered_clips")
    monkeypatch.setattr(firestore, "Client", lambda project=None: _Firestore({"visual": {"clips": CLIPS}}))
    monkeypatch.setattr(mdc, "download", _fails("Forbidden: 403"))
    monkeypatch.setattr(sys, "argv", ["measure_delivered_clips.py", "job-1"])
    assert mdc.main() == 2
    out = capsys.readouterr().out
    assert "NOT MEASURED: 3 of 3 .mp4 clips failed to download" in out
    assert "fetch failure, not an absence" in out and "no .mp4 clips found" not in out


def test_measure_delivered_clips_measures_what_it_downloaded_and_keeps_nothing(
        tmp_path, monkeypatch, capsys):
    from google.cloud import firestore

    mdc = _load("measure_delivered_clips")
    seen = []
    monkeypatch.setattr(firestore, "Client", lambda project=None: _Firestore({"visual": {"clips": CLIPS}}))
    monkeypatch.setattr(mdc, "download", _writes(b"mp4", seen=seen))
    monkeypatch.setattr(mdc, "measure_clip", lambda path: {
        "frames": 1, "static_frac": 0.0, "p90_delta": 0.1, "dead_frames": 0, "span_w": 0.9,
        "span_h": 0.9, "ink": 0.2})
    monkeypatch.setattr(sys, "argv", ["measure_delivered_clips.py", "job-1"])
    assert mdc.main() == 0
    assert [uri for uri, _ in seen] == [c["asset_uri"] for c in CLIPS]
    assert not any(pathlib.Path(local).exists() for _, local in seen)
    assert not pathlib.Path(seen[0][1]).parent.exists()          # the run's directory is gone too


def test_perclip_lit_census_counts_a_fetch_failure_apart(tmp_path, monkeypatch, capsys):
    """Round-2 design HIGH: a fetch failure was folded into "skipped" with non-gs URIs and
    undecodable frames, so ``measured=0 skipped=N`` could not say which happened."""
    from google.cloud import firestore

    plc = _load("perclip_lit_census")
    clips = CLIPS + [{"asset_uri": "https://x/y.png"}]
    monkeypatch.setattr(firestore, "Client", lambda project=None: _Firestore({"visual": {"clips": clips}}))
    monkeypatch.setattr(plc, "download", _fails("HTTP 503"))
    monkeypatch.setattr(sys, "argv", ["perclip_lit_census.py", "job-1"])
    with pytest.raises(SystemExit, match="FETCH FAILED on all 3 fetched assets"):
        plc.main()
    assert "measured=0 skipped=4 (not gs:// 1, fetch failed 3, no frame 0)" in capsys.readouterr().out
