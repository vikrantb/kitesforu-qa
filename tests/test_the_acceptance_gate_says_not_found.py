"""The acceptance gate resolves the job BEFORE it judges anything.

A missing doc used to come back as ``{}`` and grade as "NOT SURFACED: visual.video_url empty", a BLOCKER that
names the producer, for a job whose artifact was fine: a truncated id was simply not found. These pins run the
real ``_fetch_job`` and ``run_gate`` against an in-memory Firestore double. Every id here is synthetic.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_GATE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "acceptance_gate.py"

_FULL_A = "aaaa1111-0000-4000-8000-000000000001"
_FULL_B = "aaaa1111-0000-4000-8000-000000000002"   # shares the 8-character prefix with _FULL_A
_FULL_C = "cccc3333-0000-4000-8000-000000000003"   # the only doc with its prefix
_EXACT_SHORT = "bbbb2222"                              # a doc whose whole id is also another doc's prefix
_LONG_B = "bbbb2222-0000-4000-8000-000000000004"
_MANY = [f"dddd4444-0000-4000-8000-00000000001{i}" for i in range(5)]  # five docs share one prefix
# A doc that resolves but is not ready, so grading stops at the readiness rule with no download.
_NOT_READY = {"topic": "synthetic", "visual": {"video_status": "rendering"}}


def _load_gate():
    spec = importlib.util.spec_from_file_location("acceptance_gate_not_found", _GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Snap:
    def __init__(self, doc_id: str, data: dict | None) -> None:
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data is not None else None


class _Ref:
    def __init__(self, docs: dict[str, dict], doc_id: str) -> None:
        self._docs, self.id = docs, doc_id

    def get(self) -> _Snap:
        return _Snap(self.id, self._docs.get(self.id))


class _Query:
    """Only the shape `_fetch_job` uses: two `__name__` bounds, a limit, an empty select, a stream."""

    def __init__(self, docs: dict[str, dict], bounds: tuple = (), cap: int | None = None) -> None:
        self._docs, self._bounds, self._cap = docs, bounds, cap

    def where(self, *, filter):  # noqa: A002 — the real API's keyword
        assert filter.field_path == "__name__"
        return _Query(self._docs, self._bounds + ((filter.op_string, filter.value.id),), self._cap)

    def limit(self, n: int) -> "_Query":
        return _Query(self._docs, self._bounds, n)

    def select(self, _fields) -> "_Query":
        return self

    def stream(self):
        ids = sorted(self._docs)
        for op, bound in self._bounds:
            ids = [i for i in ids if (i >= bound if op == ">=" else i < bound)]
        return [_Snap(i, self._docs[i]) for i in ids[: self._cap]]


class _Collection(_Query):
    def document(self, doc_id: str) -> _Ref:
        return _Ref(self._docs, doc_id)


class _Client:
    def __init__(self, docs: dict[str, dict]) -> None:
        self._docs = docs

    def collection(self, name: str) -> _Collection:
        assert name == "podcast_jobs"
        return _Collection(self._docs)


@pytest.fixture
def gate(monkeypatch):
    from google.cloud import firestore

    docs = {k: dict(_NOT_READY) for k in (_FULL_A, _FULL_B, _FULL_C, _EXACT_SHORT, _LONG_B, *_MANY)}
    monkeypatch.setattr(firestore, "Client", lambda project=None: _Client(docs))
    return _load_gate()


def test_an_id_no_doc_has_is_NOT_FOUND_and_never_a_content_defect(gate) -> None:
    out = gate.run_gate("ffff9999-0000-4000-8000-000000000009")
    assert out["verdict"] == "NOT_FOUND"
    msg = out["issues"][0]["msg"]
    assert msg.startswith("NOT RESOLVED") and "not a content defect" in msg
    assert "NOT SURFACED" not in msg


def test_a_prefix_two_docs_share_is_AMBIGUOUS_and_names_both(gate) -> None:
    out = gate.run_gate("aaaa1111")
    assert out["verdict"] == "AMBIGUOUS_ID"
    assert out["candidates"] == [_FULL_A, _FULL_B]


def test_a_prefix_one_doc_has_resolves_to_that_doc(gate) -> None:
    out = gate.run_gate("cccc3333")
    # It got PAST resolution: grading judged the real doc, which is not ready yet.
    assert out["verdict"] == "FAIL"
    assert out["job_id"] == _FULL_C
    assert out["issues"][0]["msg"].startswith("NOT SURFACED: the video is")


def test_a_missing_job_and_an_unsurfaced_video_get_different_verdicts(gate) -> None:
    missing = gate.run_gate("eeee5555-0000-4000-8000-000000000005")
    unsurfaced = gate.run_gate(_FULL_C)
    assert missing["verdict"] != unsurfaced["verdict"]
    assert unsurfaced["job_id"] == _FULL_C


def test_an_unresolved_job_exits_2_and_a_fail_exits_1(gate, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["acceptance_gate.py", "--job-id", "ffff9999"])
    assert gate.main() == 2
    monkeypatch.setattr(sys, "argv", ["acceptance_gate.py", "--job-id", _FULL_C])
    assert gate.main() == 1
    capsys.readouterr()


def test_an_ambiguous_prefix_also_exits_2(gate, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["acceptance_gate.py", "--job-id", "aaaa1111"])
    assert gate.main() == 2
    capsys.readouterr()


def test_more_than_three_matches_are_never_reported_as_three(gate) -> None:
    out = gate.run_gate("dddd4444")
    assert out["verdict"] == "AMBIGUOUS_ID"
    assert "more than 3 podcast_jobs documents" in out["issues"][0]["msg"]
    assert len(out["candidates"]) == 3


def test_an_exact_id_wins_over_the_docs_it_prefixes(gate) -> None:
    out = gate.run_gate(_EXACT_SHORT)
    assert out["verdict"] == "FAIL"           # graded (not ready), not AMBIGUOUS_ID
    assert out["job_id"] == _EXACT_SHORT
    assert "requested_id" not in out


def test_a_blank_id_is_NOT_FOUND(gate) -> None:
    assert gate.run_gate("   ")["verdict"] == "NOT_FOUND"


def test_every_verdict_on_a_resolved_prefix_names_the_requested_id(gate) -> None:
    out = gate.run_gate("cccc3333")           # an EARLY return: the video is not ready
    assert out["job_id"] == _FULL_C
    assert out["requested_id"] == "cccc3333"
