"""Tests for scripts/quality_matrix.py — the MEASURED QUALITY ENGINE runner CLI.

``scripts/`` is not an installed package, so the module is loaded directly from its file path (same
idiom ``tests/test_short_scorecard_cli.py`` uses). These tests cover the fully-offline ``--docs-dir``
path (local job-doc JSON files, no network/Firestore/GCS) plus the pure ``load_docs_dir``/``score_all``
wiring. Live Firestore querying (``query_recent_completed_jobs``) is exercised by the real baseline
run, not unit-tested here (it needs a live project) — its date-window logic is unit-tested via
``parse_datetime`` in ``test_quality_matrix_aggregator.py``.
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
    spec = importlib.util.spec_from_file_location("quality_matrix_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["quality_matrix_cli"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def qm():
    return _load_module()


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT_PATH.exists()
    assert SCRIPT_PATH.stat().st_mode & 0o100


def test_script_has_shebang() -> None:
    assert SCRIPT_PATH.read_text().splitlines()[0].startswith("#!")


# ── load_docs_dir ──────────────────────────────────────────────────────────────


def test_load_docs_dir_reads_all_json_files(qm, tmp_path) -> None:
    (tmp_path / "job1.json").write_text(json.dumps({"job_id": "job1", "status": "completed"}))
    (tmp_path / "job2.json").write_text(json.dumps({"status": "completed"}))  # no job_id -> stamped from filename
    docs = qm.load_docs_dir(str(tmp_path))
    ids = {d["job_id"] for d in docs}
    assert ids == {"job1", "job2"}


def test_load_docs_dir_skips_malformed_json(qm, tmp_path, capsys) -> None:
    (tmp_path / "good.json").write_text(json.dumps({"job_id": "good"}))
    (tmp_path / "bad.json").write_text("{not valid json")
    docs = qm.load_docs_dir(str(tmp_path))
    assert [d["job_id"] for d in docs] == ["good"]
    assert "warning" in capsys.readouterr().err


def test_load_docs_dir_empty_dir_returns_empty_list(qm, tmp_path) -> None:
    assert qm.load_docs_dir(str(tmp_path)) == []


# ── score_all (fail-open) ───────────────────────────────────────────────────────


def _minimal_doc(job_id: str, **overrides) -> dict:
    doc = {"job_id": job_id, "format": "short_video", "episode_profile": {"genre": "horror"}, "quality_tier": "low"}
    doc.update(overrides)
    return doc


def test_score_all_scores_an_offline_doc(qm) -> None:
    docs = [_minimal_doc("job-ok")]
    cells = qm.score_all(
        docs, project="kitesforu-dev", cfg=qm.ScorecardConfig(), download_video=False,
        fetch_job_doc=lambda project, jid: {}, resolve_video=lambda *a, **k: None,
    )
    assert len(cells) == 1
    cell = cells[0]
    assert cell["_scored"] is True
    assert cell["job_id"] == "job-ok"
    assert cell["genre"] == "horror"
    assert cell["format"] == "short"
    assert cell["quality_tier"] == "low"
    assert "axes" in cell and len(cell["axes"]) == 8


def test_score_all_degrades_failing_cell_without_crashing_the_run(qm) -> None:
    docs = [_minimal_doc("job-good"), _minimal_doc("job-bad")]

    def flaky_resolve_video(video, doc, work_dir):
        if doc.get("job_id") == "job-bad":
            raise RuntimeError("simulated gsutil failure")
        return None

    cells = qm.score_all(
        docs, project="kitesforu-dev", cfg=qm.ScorecardConfig(), download_video=True,
        fetch_job_doc=lambda project, jid: {}, resolve_video=flaky_resolve_video,
    )
    assert len(cells) == 2
    good = next(c for c in cells if c["job_id"] == "job-good")
    bad = next(c for c in cells if c["job_id"] == "job-bad")
    assert good["_scored"] is True
    assert bad["_scored"] is False
    assert "simulated gsutil failure" in bad["_error"]


def test_score_all_bare_job_id_uses_fetch_job_doc(qm) -> None:
    calls = []

    def fake_fetch(project, jid):
        calls.append((project, jid))
        return _minimal_doc(jid)

    cells = qm.score_all(
        ["bare-id-1"], project="kitesforu-dev", cfg=qm.ScorecardConfig(), download_video=False,
        fetch_job_doc=fake_fetch, resolve_video=lambda *a, **k: None,
    )
    assert calls == [("kitesforu-dev", "bare-id-1")]
    assert cells[0]["_scored"] is True


def test_score_all_fetch_failure_degrades_not_crashes(qm) -> None:
    def failing_fetch(project, jid):
        raise SystemExit(f"job {jid!r} not found")

    cells = qm.score_all(
        ["missing-id"], project="kitesforu-dev", cfg=qm.ScorecardConfig(), download_video=False,
        fetch_job_doc=failing_fetch, resolve_video=lambda *a, **k: None,
    )
    assert cells[0]["_scored"] is False
    assert "missing-id" in cells[0]["job_id"]


# ── main() — fully offline via --docs-dir ───────────────────────────────────────


def test_main_docs_dir_end_to_end_writes_json_and_backlog(tmp_path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "job1.json").write_text(json.dumps(_minimal_doc("job1", episode_profile={"genre": "explainer"})))
    (docs_dir / "job2.json").write_text(json.dumps(_minimal_doc("job2", format=None, episode_profile={"genre": "storytelling"})))

    out_json = tmp_path / "result.json"
    out_backlog = tmp_path / "BACKLOG.md"

    # Run in a fresh subprocess so REPO_ROOT-relative sys.path insertion + module-level imports don't
    # collide with any already-imported ``quality_matrix_cli``/``short_scorecard_lib`` from other tests.
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
    assert result["mode"] == "docs-dir"
    assert result["n_cells_total"] == 2
    assert result["n_cells_scored"] == 2
    assert "per_axis_aggregate" in result
    assert len(result["per_axis_aggregate"]) == 8
    assert "ranked_systematic_weaknesses" in result
    assert "proposed_matrix_fill" in result

    backlog = out_backlog.read_text()
    assert "QUALITY_BACKLOG" in backlog
    assert "PROPOSED MATRIX FILL" in backlog


def test_main_requires_a_source_mode() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
