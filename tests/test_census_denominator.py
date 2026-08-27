"""NEITHER cast field is authoritative, so a disagreement takes the SMALLER.

Two fields claim to say how many voices a job was cast with, and they DISAGREE
on 106 of 420 jobs, in BOTH directions. Measured 2026-08-27 over the 57
disagreeing jobs with a measurable delivery (positive control: 643 jobs carry
>=1 quality_gate attempt):

    19  delivered == contract      < speaker_count   speaker_count over-plans
    11  delivered == speaker_count < contract        contract over-lists
    16  delivered <  both                            a real loss either way
     6  delivered >= both                            no loss either way
     5  delivered between the two

    command:
      cd kitesforu-qa/scripts && GCP_PROJECT_ID=kitesforu-dev python3 -c '...'
      (stream podcast_jobs; keep jobs where len(voice_map) != speaker_count and
       a quality_gate attempt exists; bucket len(_speakers(qg, latest)) against
       both candidates)

MAX scores rows 1, 2 and 5 as losses -- 30 false positives out of 57.
MIN scores all five rows the way the delivered audio actually reads.

Why this file exists: on 2026-08-27 a contract-preferring denominator (qa#138,
also mine) made born-short 8f1c4416 the ONLY "under-delivering" job in the
post-deploy window. The instrument manufactured the single data point it would
have reported. An inflated census is NOT the safe direction to be wrong in.
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


def _job(fmt=None, contract=None, count=None, delivered=None, first=None):
    job = {"audio_config": {}}
    if fmt is not None:
        job["audio_config"]["audio_format"] = fmt
    if count is not None:
        job["audio_config"]["speaker_count"] = count
    if contract is not None:
        job["voice_cast"] = {"contract": {"voice_map": {f"S{i}": i for i in range(contract)}}}
    if delivered is not None:
        attempts = {"attempt_1_metrics": {"speaker_balance": {
            "distribution": {f"S{i}": 1 for i in range(first if first is not None else delivered)}}}}
        if first is not None:
            attempts["attempt_2_metrics"] = {"speaker_balance": {
                "distribution": {f"S{i}": 1 for i in range(delivered)}}}
        job["stages"] = {"quality_gate": attempts}
    return job


@pytest.mark.parametrize(
    "job,expected,why",
    [
        (_job(contract=2, count=3), (2, "disagree/min"),
         "the qa#138 worker-log case: 9 jobs, speaker_count=3, real cast=2, "
         "delivered=2 -> nothing lost. MIN preserves that verdict"),
        (_job(contract=3, count=2), (2, "disagree/min"),
         "and symmetrically when the CONTRACT is the larger one (11 jobs)"),
        (_job(contract=2, count=2), (2, "contract"),
         "agreement needs no tie-break"),
        (_job(count=3), (3, "speaker_count"),
         "falls back when no contract was persisted (older jobs)"),
        (_job(contract=0, count=2), (2, "speaker_count"),
         "an EMPTY voice_map is missing data, NOT a cast of zero"),
        ({}, (None, "none"), "neither present — say none, do not invent a number"),
    ],
)
def test_cast_size_takes_the_smaller_on_a_disagreement(job, expected, why):
    assert _census().cast_size(job) == expected, why


@pytest.mark.parametrize("fmt", ["short", "narration", "monologue", "SHORT", " short "])
def test_a_single_voice_format_can_never_under_deliver(fmt):
    """Born-short 8f1c4416: audio_format=short, speaker_count=1, FIVE personas
    in the contract, delivered 1 — correct. Scoring it as losing four is the
    false positive that started this fix.

    Mirrors the SSOT in kitesforu-workers
    stages/script/cast_voice_sizing.select_base_script_template, which maps
    NARRATION/MONOLOGUE/SHORT -> speaker_count 1.
    """
    census = _census()
    job = _job(fmt=fmt, contract=5, count=1, delivered=1)
    assert census.cast_size(job) == (1, "single-voice-format")
    assert census.classify(job) is None, "a one-voice format cannot lose a voice"


def test_a_real_loss_still_counts():
    """PREMISE TEST: the rule must still be ABLE to fail.

    A dialogue cast for 3 that delivered 1 is a genuine loss on either
    candidate (the 16-job 'below BOTH' row). If this ever returns None the
    census has been softened into uselessness.
    """
    census = _census()
    job = _job(fmt="dialogue", contract=3, count=3, delivered=1)
    assert census.cast_size(job) == (3, "contract")
    assert census.classify(job) == "script single-speaker from attempt 1"


def test_a_disagreement_is_reported_not_hidden():
    """Taking the smaller must not make the disagreement invisible.

    A contract that collapsed below the plan is an UPSTREAM defect. It is
    counted on its own line rather than folded into the under-delivery total,
    because conflating the two is what made the headline number untrustworthy.
    """
    census = _census()
    assert census.cast_disagreement(_job(contract=1, count=3)) == (1, 3)
    assert census.cast_disagreement(_job(contract=2, count=2)) is None
    assert census.cast_disagreement(_job(count=2)) is None
