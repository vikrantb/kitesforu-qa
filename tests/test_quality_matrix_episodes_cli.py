"""Tests for ``scripts/quality_matrix.py --content-class episodes`` — the EPISODES + COURSES CLI
mode. Mirrors ``tests/test_quality_matrix_cli.py``'s style/idiom (the module is loaded by file path
since ``scripts/`` isn't an installed package) but exercises the NEW episode/course path exclusively;
the short-mode CLI tests in that sibling file are untouched and still cover the default
``--content-class short`` behavior end-to-end.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "quality_matrix.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("quality_matrix_episodes_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def qm():
    return _load_module()


def _episode_doc(job_id: str, **overrides) -> dict:
    doc = {
        "job_id": job_id,
        "status": "completed",
        "episode_profile": {"genre": "explainer"},
        "inputs": {"duration_min": 10.0},
        "script": {"dialogue": [
            {"speaker": "Host1", "text": "For example, imagine a neural network like a filter."},
        ]},
        "quality_tier": "low",
    }
    doc.update(overrides)
    return doc


def _course_doc(job_id: str, **overrides) -> dict:
    doc = _episode_doc(job_id, parent_type="course", parent_id="courses/abc")
    doc.update(overrides)
    return doc


def _short_doc(job_id: str, **overrides) -> dict:
    doc = {
        "job_id": job_id, "status": "completed", "format": "short_video",
        "episode_profile": {"genre": "horror"}, "quality_tier": "low",
    }
    doc.update(overrides)
    return doc


# ── resolve_audio ────────────────────────────────────────────────────────────────


def test_resolve_audio_no_url_returns_none(qm, tmp_path) -> None:
    assert qm.resolve_audio({"outputs": {}}, str(tmp_path)) is None
    assert qm.resolve_audio({}, str(tmp_path)) is None


def test_resolve_audio_local_existing_path_passthrough(qm, tmp_path) -> None:
    local = tmp_path / "ep.mp3"
    local.write_bytes(b"fake-audio")
    doc = {"outputs": {"audio_url": str(local)}}
    assert qm.resolve_audio(doc, str(tmp_path)) == str(local)


def test_resolve_audio_local_missing_path_returns_none(qm, tmp_path) -> None:
    doc = {"outputs": {"audio_url": str(tmp_path / "does-not-exist.mp3")}}
    assert qm.resolve_audio(doc, str(tmp_path)) is None


def test_resolve_audio_gs_uri_downloads_via_gsutil(qm, tmp_path, monkeypatch) -> None:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(kwargs.get("cwd", str(tmp_path)))  # no-op, just to touch kwargs
        # Simulate gsutil actually writing the destination file.
        dest = cmd[-1]
        Path(dest).write_bytes(b"downloaded")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(qm.subprocess, "run", fake_run)
    doc = {"outputs": {"audio_url": "gs://bucket/ep.mp3"}}
    result = qm.resolve_audio(doc, str(tmp_path))
    assert result is not None
    assert Path(result).exists()
    assert calls and calls[0][0] == "gsutil"


def test_resolve_audio_gs_uri_failure_degrades_to_none(qm, tmp_path, monkeypatch, capsys) -> None:
    def failing_run(cmd, **kwargs):
        raise RuntimeError("simulated gsutil auth failure")

    monkeypatch.setattr(qm.subprocess, "run", failing_run)
    doc = {"outputs": {"audio_url": "gs://bucket/ep.mp3"}}
    assert qm.resolve_audio(doc, str(tmp_path)) is None
    assert "warning" in capsys.readouterr().err


# ── score_all_episodes_courses ───────────────────────────────────────────────────


def test_score_all_episodes_courses_scores_episode_and_course(qm) -> None:
    docs = [_episode_doc("ep1"), _course_doc("co1")]
    cells = qm.score_all_episodes_courses(
        docs, project="kitesforu-dev", download_video=False, download_audio=False,
        fetch_job_doc=lambda project, jid: {}, resolve_video=lambda *a, **k: None,
        resolve_audio=lambda *a, **k: None,
    )
    assert len(cells) == 2
    by_id = {c["job_id"]: c for c in cells}
    assert by_id["ep1"]["content_class"] == "episode"
    assert by_id["co1"]["content_class"] == "course"
    assert all(c["_scored"] for c in cells)


def test_score_all_episodes_courses_excludes_shorts_entirely(qm) -> None:
    docs = [_episode_doc("ep1"), _short_doc("sh1")]
    cells = qm.score_all_episodes_courses(
        docs, project="kitesforu-dev", download_video=False, download_audio=False,
        fetch_job_doc=lambda project, jid: {}, resolve_video=lambda *a, **k: None,
        resolve_audio=lambda *a, **k: None,
    )
    ids = [c["job_id"] for c in cells]
    assert "sh1" not in ids
    assert "ep1" in ids
    assert len(cells) == 1  # the short produced NO cell at all -- not scored, not unscored


def test_score_all_episodes_courses_fail_open_on_exception(qm) -> None:
    docs = [_episode_doc("ep-good"), _episode_doc("ep-bad")]

    def flaky_resolve_audio(doc, work_dir):
        if doc.get("job_id") == "ep-bad":
            raise RuntimeError("simulated failure")
        return None

    cells = qm.score_all_episodes_courses(
        docs, project="kitesforu-dev", download_video=False, download_audio=True,
        fetch_job_doc=lambda project, jid: {}, resolve_video=lambda *a, **k: None,
        resolve_audio=flaky_resolve_audio,
    )
    assert len(cells) == 2
    good = next(c for c in cells if c["job_id"] == "ep-good")
    bad = next(c for c in cells if c["job_id"] == "ep-bad")
    assert good["_scored"] is True
    assert bad["_scored"] is False
    assert "simulated failure" in bad["_error"]


def test_score_all_episodes_courses_bare_job_id_uses_fetch_job_doc(qm) -> None:
    calls = []

    def fake_fetch(project, jid):
        calls.append((project, jid))
        return _episode_doc(jid)

    cells = qm.score_all_episodes_courses(
        ["bare-id-1"], project="kitesforu-dev", download_video=False, download_audio=False,
        fetch_job_doc=fake_fetch, resolve_video=lambda *a, **k: None, resolve_audio=lambda *a, **k: None,
    )
    assert calls == [("kitesforu-dev", "bare-id-1")]
    assert cells[0]["_scored"] is True


# ── main() — fully offline via --docs-dir --content-class episodes ──────────────


def test_main_episodes_end_to_end_writes_json_and_upserts_backlog(tmp_path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ep1.json").write_text(json.dumps(_episode_doc("ep1")))
    (docs_dir / "co1.json").write_text(json.dumps(_course_doc("co1")))
    (docs_dir / "sh1.json").write_text(json.dumps(_short_doc("sh1")))

    out_json = tmp_path / "result.json"
    out_backlog = tmp_path / "QUALITY_BACKLOG.md"
    out_backlog.write_text("# QUALITY_BACKLOG\n\nSHORT BASELINE PLACEHOLDER — must survive.\n")

    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--content-class", "episodes",
            "--docs-dir", str(docs_dir),
            "--no-video", "--no-audio",
            "--out-json", str(out_json),
            "--out-backlog", str(out_backlog),
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    result = json.loads(out_json.read_text())
    assert result["content_class"] == "episodes"
    assert result["n_job_specs"] == 3
    assert result["n_excluded_short"] == 1
    assert result["n_cells_total"] == 2
    assert result["n_cells_scored"] == 2
    assert "episode_dimension_aggregate" in result
    assert "course_dimension_aggregate" in result
    assert "episode_ranked_systematic_check_failures" in result
    assert "course_ranked_systematic_check_failures" in result

    backlog = out_backlog.read_text()
    assert "SHORT BASELINE PLACEHOLDER — must survive." in backlog
    assert "EPISODES + COURSES" in backlog
    assert "## EPISODES" in backlog and "## COURSES" in backlog

    # Idempotent re-run: only the marked block changes, the pre-existing content stays intact.
    proc2 = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--content-class", "episodes",
            "--docs-dir", str(docs_dir),
            "--no-video", "--no-audio",
            "--out-json", str(out_json),
            "--out-backlog", str(out_backlog),
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc2.returncode == 0, proc2.stderr
    backlog2 = out_backlog.read_text()
    assert "SHORT BASELINE PLACEHOLDER — must survive." in backlog2
    assert backlog2.count("EPISODES + COURSES") == 1


def test_main_default_content_class_is_short_unchanged(tmp_path) -> None:
    """Sanity pin: omitting --content-class must still take the SHORT path (default unchanged)."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "job1.json").write_text(json.dumps({
        "job_id": "job1", "format": "short_video", "episode_profile": {"genre": "explainer"},
    }))
    out_json = tmp_path / "result.json"
    out_backlog = tmp_path / "BACKLOG.md"
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT_PATH),
            "--docs-dir", str(docs_dir),
            "--no-video",
            "--out-json", str(out_json),
            "--out-backlog", str(out_backlog),
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out_json.read_text())
    assert "per_axis_aggregate" in result  # the short-scorecard shape, not the episodes shape
    assert "content_class" not in result
