"""Tests for scripts/short_scorecard.py — the SHORT SCORECARD CLI entry point.

``scripts/`` is not an installed package, so the module is loaded directly from its file path
(same idiom the script itself uses for ``scripts/audio_quality_judge.py``). These tests cover the
pure/offline wiring (``resolve_video``, ``fetch_job_doc`` with a faked Firestore client, and
``main()``'s fully-offline ``--doc-file`` path) — no network, no real GCS/Firestore calls.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "short_scorecard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("short_scorecard_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["short_scorecard_cli"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sc():
    return _load_module()


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT_PATH.exists()
    assert SCRIPT_PATH.stat().st_mode & 0o100  # owner-executable bit


def test_script_has_shebang() -> None:
    first_line = SCRIPT_PATH.read_text().splitlines()[0]
    assert first_line.startswith("#!")


# ── resolve_video ────────────────────────────────────────────────────────────────


def test_resolve_video_returns_existing_local_path(sc, tmp_path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    assert sc.resolve_video(str(video), {}, str(tmp_path)) == str(video)


def test_resolve_video_missing_local_path_returns_none(sc, tmp_path) -> None:
    assert sc.resolve_video(str(tmp_path / "nope.mp4"), {}, str(tmp_path)) is None


def test_resolve_video_falls_back_to_doc_video_burned_url(sc, tmp_path, monkeypatch) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"fake")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sc.subprocess, "run", fake_run)
    doc = {"visual": {"video_burned_url": "gs://bucket/path/episode_video_captioned.mp4"}}
    result = sc.resolve_video(None, doc, str(tmp_path))
    assert result == str(tmp_path / "episode_video_captioned.mp4")
    assert calls and calls[0][0] == "gsutil"


def test_resolve_video_gsutil_failure_returns_none_not_raise(sc, tmp_path, monkeypatch) -> None:
    def failing_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(sc.subprocess, "run", failing_run)
    result = sc.resolve_video("gs://bucket/path/video.mp4", {}, str(tmp_path))
    assert result is None


def test_resolve_video_returns_none_when_nothing_to_resolve(sc, tmp_path) -> None:
    assert sc.resolve_video(None, {}, str(tmp_path)) is None


# ── fetch_job_doc ──────────────────────────────────────────────────────────────


class _FakeSnap:
    def __init__(self, exists, data):
        self.exists = exists
        self._data = data

    def to_dict(self):
        return self._data


class _FakeDocRef:
    def __init__(self, snap):
        self._snap = snap

    def get(self):
        return self._snap


class _FakeCollection:
    def __init__(self, snap):
        self._snap = snap

    def document(self, job_id):
        return _FakeDocRef(self._snap)


class _FakeFirestoreClient:
    def __init__(self, snap):
        self._snap = snap

    def collection(self, name):
        assert name == "podcast_jobs"
        return _FakeCollection(self._snap)


def test_fetch_job_doc_raises_when_missing(sc, monkeypatch) -> None:
    from google.cloud import firestore

    monkeypatch.setattr(firestore, "Client", lambda project=None: _FakeFirestoreClient(_FakeSnap(False, {})))
    with pytest.raises(SystemExit):
        sc.fetch_job_doc("kitesforu-dev", "nonexistent-job")


def test_fetch_job_doc_returns_dict_and_stamps_job_id(sc, monkeypatch) -> None:
    from google.cloud import firestore

    monkeypatch.setattr(
        firestore, "Client",
        lambda project=None: _FakeFirestoreClient(_FakeSnap(True, {"status": "completed"})),
    )
    doc = sc.fetch_job_doc("kitesforu-dev", "job-123")
    assert doc["status"] == "completed"
    assert doc["job_id"] == "job-123"


# ── main() — fully offline via --doc-file ───────────────────────────────────────


def test_main_offline_doc_file_prints_a_valid_scorecard(sc, tmp_path, capsys) -> None:
    doc_file = tmp_path / "job.json"
    doc_file.write_text(json.dumps({"job_id": "offline-1", "format": "short_video"}))

    exit_code = sc.main(["--doc-file", str(doc_file)])

    captured = capsys.readouterr()
    assert exit_code == 0
    result = json.loads(captured.out)
    assert result["job_id"] == "offline-1"
    assert "axes" in result and len(result["axes"]) == 8
    assert "ship" in result


def test_main_writes_out_file_when_requested(sc, tmp_path) -> None:
    doc_file = tmp_path / "job.json"
    doc_file.write_text(json.dumps({"job_id": "offline-2"}))
    out_file = tmp_path / "result.json"

    sc.main(["--doc-file", str(doc_file), "--out", str(out_file)])

    written = json.loads(out_file.read_text())
    assert written["job_id"] == "offline-2"


def test_main_requires_job_id_or_doc_file(sc) -> None:
    with pytest.raises(SystemExit):
        sc.main([])
