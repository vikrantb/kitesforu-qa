"""The census denominator must be the REAL cast, not the planning number.

`audio_config.speaker_count` is a PLANNING number. Measured 2026-08-27 across the
full collection: of 295 census-eligible jobs, 197 carried
`voice_cast.contract.voice_map`, and on 39 the two DISAGREED.

Cross-checked against the worker log `cast_contract.persisted job_id=... speakers=N`
for the 48 under-delivering jobs still inside log retention: 9 were jobs where
delivered == the real cast (nothing lost — the denominator was wrong) and 39 were
genuine losses. All 9 shared one signature: speaker_count=3, real cast=2, delivered=2.
"""
import importlib.util
import pathlib

import pytest

_SPEC = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "speaker_delivery_census.py"


def _census():
    spec = importlib.util.spec_from_file_location("speaker_delivery_census", _SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "job,expected,why",
    [
        (
            {"voice_cast": {"contract": {"voice_map": {"Host1": 1, "Host2": 2}}},
             "audio_config": {"speaker_count": 3}},
            (2, "contract"),
            "the contract WINS over a disagreeing planning number — this is the bug",
        ),
        (
            {"audio_config": {"speaker_count": 3}},
            (3, "speaker_count"),
            "falls back when no contract was persisted (older jobs; 98 of 300)",
        ),
        (
            {"voice_cast": {"contract": {"voice_map": {}}},
             "audio_config": {"speaker_count": 2}},
            (2, "speaker_count"),
            "an EMPTY voice_map is missing data, NOT a cast of zero",
        ),
        ({}, (None, "none"), "neither present — say none, do not invent a number"),
    ],
)
def test_cast_size_prefers_the_contract(job, expected, why):
    assert _census().cast_size(job) == expected, why


def test_the_denominator_is_attributable():
    """A denominator you cannot attribute is a number you cannot defend.

    cast_size returns WHICH source it used so the census can report the split
    (measured: 202 contract / 98 speaker_count).
    """
    _, src = _census().cast_size(
        {"voice_cast": {"contract": {"voice_map": {"a": 1, "b": 2}}}}
    )
    assert src == "contract"


def test_classify_uses_the_same_denominator():
    """classify() must not silently keep reading speaker_count.

    If it did, the headline count and the per-cause breakdown would disagree.
    """
    census = _census()
    job = {
        "voice_cast": {"contract": {"voice_map": {"Host1": 1, "Host2": 2}}},
        "audio_config": {"speaker_count": 3},
        "stages": {"quality_gate": {"attempt_1_metrics": {
            "speaker_balance": {"distribution": {"Host1": 1, "Host2": 1}}}}},
    }
    # real cast 2, delivered 2 -> nothing lost. Under the OLD denominator (3) this
    # returned a cause and inflated the census.
    assert census.classify(job) is None
