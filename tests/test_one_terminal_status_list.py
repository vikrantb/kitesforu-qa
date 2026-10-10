"""Every qa poller stops on the same terminal statuses, and that list covers the schemas enum.

``kitesforu_qa.job_status`` is the one list. These tests pin four things:

* It PARTITIONS ``kitesforu_schemas.enums.JobStatus``: every member is classified exactly once. A
  status added to the enum fails here until someone decides whether a poller may stop on it. The
  enum is read from the schemas repo's ``origin/main`` by AST, so no schemas install is needed.
  The test SKIPS only when no schemas checkout is found.
* ``canary_loop.py`` stops on a finished ``needs_review`` episode and logs it as a hold, not a stall.
  It used to poll to its 10-minute limit and page Slack (#175 round-3 design NIT-4).
* ``narration_sync_audit.py`` scores a finished ``needs_review`` episode. It used to drop every
  held one from its census (#175 round-2 design D1).
* ``KitesForUClient.wait_for_completion`` returns on a held episode, and ``kqa e2e`` grades it rather
  than calling it a failed job.

Offline and $0: no Firestore, no Playwright session, no Slack, no api.
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

QA = Path(__file__).resolve().parents[1]
SCRIPTS = QA / "scripts"
for _p in (str(SCRIPTS), str(QA / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from kitesforu_qa import job_status  # noqa: E402

_SCHEMAS_DEFAULT = Path("/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-schemas")


def _schemas_job_status() -> set[str]:
    for repo in (os.environ.get("KFU_SCHEMAS_REPO"), QA.parent / "kitesforu-schemas", _SCHEMAS_DEFAULT):
        if repo and (Path(repo) / ".git").exists():
            break
    else:
        pytest.skip("no kitesforu-schemas checkout (set KFU_SCHEMAS_REPO): SKIPPED, not passed")
    src = subprocess.run(["git", "-C", str(repo), "show", "origin/main:src/kitesforu_schemas/enums.py"],
                         check=True, capture_output=True, text=True).stdout
    assert len(src) > 1000, "the extracted enums.py is empty"
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ClassDef) and node.name == "JobStatus":
            values = {str(stmt.value.value) for stmt in node.body
                      if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)}
            assert "completed" in values, "the probe did not read JobStatus"
            return values
    raise AssertionError("JobStatus is not in kitesforu_schemas/enums.py any more")


def test_the_list_partitions_the_schemas_enum():
    enum = _schemas_job_status()
    terminal, non_terminal = job_status.TERMINAL, job_status.NON_TERMINAL
    assert not terminal & non_terminal
    assert terminal | non_terminal == enum, (
        f"unclassified: {sorted(enum - terminal - non_terminal)}; not in the enum: "
        f"{sorted((terminal | non_terminal) - enum)}")
    assert job_status.FINISHED_RENDERING <= terminal
    assert job_status.ENDED_WITHOUT_EPISODE == terminal - job_status.FINISHED_RENDERING


def test_the_held_statuses_are_finished_and_awaiting_review_is_not_terminal():
    assert {"needs_review", "failed_qa"} <= job_status.FINISHED_RENDERING
    assert "awaiting_review" in job_status.NON_TERMINAL


def test_the_shell_reads_the_same_list():
    out = subprocess.run([sys.executable, str(QA / "src" / "kitesforu_qa" / "job_status.py"), "terminal"],
                         check=True, capture_output=True, text=True).stdout.split()
    assert set(out) == job_status.TERMINAL
    assert '"$HERE/../src/kitesforu_qa/job_status.py" terminal' in (SCRIPTS / "create_verification_job.sh").read_text()


# ---- the package client and `kqa e2e` ----------------------------------------------------------

def test_the_package_client_returns_on_a_held_episode(monkeypatch):
    from kitesforu_qa.integrations import kitesforu_api
    seq = iter([{"status": "running"}, {"status": "needs_review"}])
    client = kitesforu_api.KitesForUClient(base_url="http://127.0.0.1:9", api_key="dummy")
    monkeypatch.setattr(client, "get_job", lambda _job_id: next(seq))
    monkeypatch.setattr(kitesforu_api.time, "sleep", lambda _s: None)
    assert client.wait_for_completion("job1", timeout=5)["status"] == "needs_review"


_KQA_E2E_WITH_A_FAKE_CLIENT = """
import sys
sys.path.insert(0, sys.argv[1])
from click.testing import CliRunner
from kitesforu_qa import cli as kqa

class FakeClient:
    def __init__(self, **_kw): pass
    def health_check(self): return True
    def create_job(self, **_kw): return {"job_id": "job1"}
    def wait_for_completion(self, **_kw): return {"status": "needs_review"}
    def get_job_audio(self, _job_id): return None
    def get_job_script(self, _job_id): return None

kqa.KitesForUClient = FakeClient
print(CliRunner().invoke(kqa.cli, ["e2e", "--topic", "t", "--api-key", "dummy",
                                   "--api-url", "http://127.0.0.1:9"]).output)
"""


def test_kqa_e2e_grades_a_held_episode_instead_of_failing_it():
    """Run in the interpreter that has kqa's own dependencies (``click``). This test venv does not,
    which is also why tests/test_verify_live.py's CLI tests fail on main."""
    import shutil
    py = shutil.which("python3")
    if py is None or subprocess.run([py, "-c", "import click"], capture_output=True).returncode != 0:
        pytest.skip("click, a declared kqa dependency, is not installed for python3: SKIPPED, not passed")
    r = subprocess.run([py, "-W", "ignore", "-c", _KQA_E2E_WITH_A_FAKE_CLIENT, str(QA / "src")],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout
    assert "Job failed" not in out, (out, r.stderr[-500:])
    assert "held: needs_review" in out and "No audio URL" in out, (out, r.stderr[-500:])


# ---- canary_loop ------------------------------------------------------------------------------

@pytest.fixture
def canary(monkeypatch):
    mod = importlib.import_module("canary_loop")
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    return mod


def test_the_canary_stops_on_a_finished_held_episode(canary, monkeypatch):
    docs = iter([{"status": "running"}, {"status": "needs_review"}])
    last = {}

    def fetch(_db, _job_id):
        nonlocal last
        last = next(docs, last)
        return dict(last)
    monkeypatch.setattr(canary, "fetch_job", fetch)
    monkeypatch.setattr(canary, "MAX_POLL_MINUTES", 0.002)   # ~0.1 s: a miss times out, not hangs
    doc = canary.watch_job(None, "job1")
    assert doc.get("status") == "needs_review"
    assert not doc.get("_timeout"), "a finished needs_review episode was reported as a stall"


def test_the_canary_logs_a_hold_as_a_warning_not_a_stall(canary, monkeypatch):
    logged, alerts = [], []
    monkeypatch.setattr(canary, "mint_clerk_token", lambda: "tok")
    monkeypatch.setattr(canary, "trigger_job", lambda _t: "job1")
    monkeypatch.setattr(canary, "watch_job", lambda _db, _j: {"status": "needs_review",
                                                               "audio": {"mp3_url": "https://x/a.mp3"}})
    monkeypatch.setattr(canary, "ffprobe_duration", lambda _u: (60.0, 500_000))
    monkeypatch.setattr(canary, "append_log", logged.append)
    monkeypatch.setattr(canary, "slack_alert", alerts.append)
    monkeypatch.setattr(canary, "pull_log_tail", lambda _j: "")
    res = canary.run_one(None, 1)
    assert res["outcome"] == "warn", res
    assert "held:needs_review" in logged[-1] and "STALL" not in logged[-1]
    assert alerts == [], "a finished, held episode paged Slack"


def test_the_canary_still_passes_a_clean_completed_episode(canary, monkeypatch):
    logged = []
    monkeypatch.setattr(canary, "mint_clerk_token", lambda: "tok")
    monkeypatch.setattr(canary, "trigger_job", lambda _t: "job1")
    monkeypatch.setattr(canary, "watch_job", lambda _db, _j: {"status": "completed",
                                                               "audio": {"mp3_url": "https://x/a.mp3"}})
    monkeypatch.setattr(canary, "ffprobe_duration", lambda _u: (60.0, 500_000))
    monkeypatch.setattr(canary, "append_log", logged.append)
    monkeypatch.setattr(canary, "slack_alert", lambda _m: pytest.fail("a clean pass paged Slack"))
    assert canary.run_one(None, 1)["outcome"] == "pass"
    assert "| PASS |" in logged[-1]


# ---- narration_sync_audit ---------------------------------------------------------------------

def _finished_doc(status: str) -> dict:
    return {
        "job_id": "job1",
        "status": status,
        "visual": {
            "video_url": "gs://b/v.mp4",
            "captions_vtt": ("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nThe first sentence.\n\n"
                             "00:00:02.000 --> 00:00:04.000\nThe second sentence.\n"),
            "clips": [{"beat_index": 0, "start_ms": 0, "end_ms": 2000, "modality": "scene_image"},
                      {"beat_index": 1, "start_ms": 2000, "end_ms": 4000, "modality": "scene_image"}],
        },
    }


@pytest.mark.parametrize("status", sorted(job_status.TERMINAL))
def test_the_narration_census_scores_every_terminal_episode(status):
    audit = importlib.import_module("narration_sync_audit").audit
    assert audit(_finished_doc(status)) is not None, f"a {status} episode was dropped from the census"


def test_the_narration_census_still_skips_a_job_in_flight():
    audit = importlib.import_module("narration_sync_audit").audit
    assert audit(_finished_doc("running")) is None
