"""The trace tool must read the debug entries `doc_size_guard` reroutes out of the job doc.

THE PRODUCER HAD NO CONSUMER. `workers.common.doc_size_guard` has been writing overflowed debug
entries into `podcast_jobs/{id}/debug_logs_overflow` for months — 194 such writes in the last 30
days, and production job 5f8ed80c carries 32 overflow docs — while every reader, this tool
included, read only the inline array.

So overflowed calls were written and never read by ANYTHING: invisible exactly on the biggest,
most expensive jobs, which are the ones worth tracing. Verified live before the fix: 5f8ed80c
traced 644 calls; with the overflow merged it is 676.

That gap is also what makes the inline cap unsafe to lower. Measured 2026-08-19: the 400-entry cap
at the p90 entry size (1585 B) plus the LARGEST observed product floor (418 KiB) = 101% of the
1 MiB limit. The cap wants lowering — but lowering it without this reader just hides more trace.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "trace_job", Path(__file__).resolve().parents[1] / "scripts" / "trace_job.py"
)
tj = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tj)  # type: ignore[union-attr]


class _Doc:
    def __init__(self, payload):
        self._p = payload

    def to_dict(self):
        return self._p


class _Col:
    def __init__(self, docs, raises=False):
        self._docs, self._raises = docs, raises

    def stream(self):
        if self._raises:
            raise RuntimeError("permission denied")
        return iter(self._docs)


class _Ref:
    def __init__(self, docs, raises=False):
        self._col = _Col(docs, raises)

    def collection(self, name):
        assert name == "debug_logs_overflow", f"read the wrong subcollection: {name}"
        return self._col


def _entry(i):
    return {"stage": "script", "provider": "anthropic", "input_tokens": i}


def test_overflowed_entries_are_merged_back():
    """The shape written by doc_size_guard._append_overflow: {field, entries, count, created_at}."""
    d = {"llm_call_logs": [_entry(0), _entry(1)]}
    ref = _Ref([_Doc({"field": "llm_call_logs", "entries": [_entry(2), _entry(3)], "count": 2})])
    tj._merge_overflow(ref, d)
    assert len(d["llm_call_logs"]) == 4, (
        "overflowed entries were not merged — the trace still under-reports the biggest jobs"
    )
    assert d["llm_call_logs"][-1]["input_tokens"] == 3


def test_it_merges_EVERY_evictable_field_not_just_llm_calls():
    """doc_size_guard evicts four arrays; a reader that knows only one silently drops the rest."""
    d = {}
    ref = _Ref([
        _Doc({"field": "llm_call_logs", "entries": [_entry(1)]}),
        _Doc({"field": "tts_segment_logs", "entries": [{"seg": 1}]}),
        _Doc({"field": "tool_call_logs", "entries": [{"tool": "x"}]}),
        _Doc({"field": "stage_complete_logs", "entries": [{"stage": "audio"}]}),
    ])
    tj._merge_overflow(ref, d)
    for f in ("llm_call_logs", "tts_segment_logs", "tool_call_logs", "stage_complete_logs"):
        assert len(d.get(f) or []) == 1, f"{f} was not recovered from the overflow"


def test_an_unreadable_subcollection_DEGRADES_and_says_so(capsys):
    """Tenet 9: a debug tool's own extra read must never fail the tool — but silence would
    reproduce the exact bug this fixes (a short trace that looks complete)."""
    d = {"llm_call_logs": [_entry(0)]}
    tj._merge_overflow(_Ref([], raises=True), d)
    assert d["llm_call_logs"] == [_entry(0)], "a failed overflow read corrupted the inline trace"
    assert "INLINE ENTRIES ONLY" in capsys.readouterr().err, (
        "the tool degraded SILENTLY — an undercounted trace must announce itself"
    )


def test_no_overflow_is_a_silent_no_op(capsys):
    """The common case: most jobs never overflow. No noise, no mutation."""
    d = {"llm_call_logs": [_entry(0)]}
    tj._merge_overflow(_Ref([]), d)
    assert d == {"llm_call_logs": [_entry(0)]}
    assert capsys.readouterr().err == ""


def test_a_recovery_is_ANNOUNCED(capsys):
    """A silently-merged trace and a silently-truncated one look identical — which is how this gap
    survived: the trace always rendered, just short."""
    tj._merge_overflow(_Ref([_Doc({"field": "llm_call_logs", "entries": [_entry(9)]})]), {})
    err = capsys.readouterr().err
    assert "recovered" in err and "llm_call_logs" in err


def test_a_malformed_overflow_doc_cannot_corrupt_the_trace():
    d = {"llm_call_logs": [_entry(0)]}
    ref = _Ref([
        _Doc({"field": "llm_call_logs", "entries": "not-a-list"}),
        _Doc({"field": "unknown_field", "entries": [_entry(1)]}),
        _Doc({}),
    ])
    tj._merge_overflow(ref, d)
    assert d["llm_call_logs"] == [_entry(0)]
    assert "unknown_field" not in d


def test_the_field_list_matches_doc_size_guard():
    """If doc_size_guard starts evicting a fifth array, this reader goes half-blind. Pinned so the
    divergence is a test failure, not a silently shorter trace."""
    assert set(tj.EVICTABLE_DEBUG_FIELDS) == {
        "llm_call_logs", "tts_segment_logs", "tool_call_logs", "stage_complete_logs",
    }
    assert tj.OVERFLOW_SUBCOLLECTION == "debug_logs_overflow"


def test_LOAD_DOC_ACTUALLY_CALLS_THE_MERGE():
    """THE WIRING TEST — and the one that matters most here.

    Every test above calls `_merge_overflow` directly, so they prove the function WORKS. They
    cannot prove anything CALLS it — and "the producer had no consumer" is precisely the bug being
    fixed. Deleting the call from `_load_doc` passes all of them.

    Caught by mutation: removing `_merge_overflow(ref, d)` from `_load_doc` left 7/7 green.
    """
    import inspect

    src = inspect.getsource(tj._load_doc)
    assert "_merge_overflow(" in src, (
        "_load_doc no longer merges the overflow subcollection. The function can be perfect and "
        "the trace still short — that IS the bug this file exists to prevent."
    )
    # …and it must run on the REAL doc path, not behind the from_file early return.
    before_return = src.split("return d")[0]
    assert "_merge_overflow(" in before_return, (
        "_merge_overflow is called after the Firestore doc is returned — it can never run"
    )


def test_the_merge_receives_the_DOC_REF_not_the_collection():
    """`_append_overflow` hangs the subcollection off the protected DOCUMENT. Passing a collection
    (or the client) would read the wrong place and silently recover nothing."""
    import inspect

    src = inspect.getsource(tj._load_doc)
    assert "ref = db.collection(\"podcast_jobs\").document(job_id)" in src, (
        "the doc ref is no longer bound before the merge — _merge_overflow needs the DOCUMENT ref"
    )
    assert "_merge_overflow(ref, d)" in src
