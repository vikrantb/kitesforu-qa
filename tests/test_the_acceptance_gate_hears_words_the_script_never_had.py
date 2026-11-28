"""The acceptance gate flags a master that says words its script never had.

A TTS request split mid-sentence can make the model finish the sentence itself, in words no caption shows and no
fact check read. The producer's pronunciation check already counts them (``stages["job-audio"].qa.pronunciation``);
the gate reads that count, and a clean take's speech-recognition noise (a stray word or three) stays below both
floors. Synthetic documents only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[1] / "scripts" / "acceptance_gate.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("acceptance_gate_spoken_words", _GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _doc(**pron) -> dict:
    return {"stages": {"job-audio": {"qa": {"pronunciation": {"enabled": True, "skipped": False, **pron}}}}}


def test_an_invented_clause_is_a_major(gate):
    issues = gate._spoken_not_written_issues(_doc(insertions=13, word_count_reference=62))
    assert [i["sev"] for i in issues] == ["MAJOR"]
    assert issues[0]["msg"].startswith("SPOKEN, NOT WRITTEN: the master says 13 words its script does not (21% of 62)")
    assert gate._verdict(issues) == "REVIEW"


def test_the_smallest_invented_clause_is_caught(gate):
    assert gate._spoken_not_written_issues(_doc(insertions=5, word_count_reference=79))


@pytest.mark.parametrize("inserted, reference", [(3, 92), (2, 232), (1, 66), (4, 50), (6, 300), (0, 56)])
def test_recognition_noise_on_a_clean_take_is_not_a_finding(gate, inserted, reference):
    assert gate._spoken_not_written_issues(_doc(insertions=inserted, word_count_reference=reference)) == []


@pytest.mark.parametrize("doc", [
    {},
    {"stages": {"job-audio": {"qa": {}}}},
    _doc(insertions=13, word_count_reference=62, skipped=True),
    _doc(insertions=13, word_count_reference=62, enabled=False),
    _doc(insertions=13, word_count_reference=0),
    _doc(insertions="many", word_count_reference=62),
])
def test_a_check_that_did_not_run_or_left_no_counts_says_nothing(gate, doc):
    assert gate._spoken_not_written_issues(doc) == []
