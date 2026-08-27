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


# ---------------------------------------------------------------------------
# The FORMAT is only the BASE. A planned multi-character cast RAISES it.
#
# Caught by an adversarial review of the first version of this fix, which
# mirrored HALF the SSOT: it copied select_base_script_template's format->count
# map and ignored _apply_cast_voice_floor, which the docstring itself named.
#
# The two live jobs that discriminate (re-read from Firestore):
#   8f1c4416  short, speaker_count=1, contract of 5 CANDIDATE personas, no
#             _cast_sketch -> floor does not fire -> cast 1. Correctly silent.
#   33cdaa8d  short, speaker_count=1, _cast_sketch.cast_size=3 naming Elias
#             Vorn / High Priestess Seris / Initiate Kalen, a contract for
#             exactly those 3, and a delivered script of ['Narrator'].
#             SSOT cast 3. An unconditional `return 1` dropped this genuine
#             3->1 collapse out of the denominator entirely.
# ---------------------------------------------------------------------------


def test_a_cast_sketch_raises_a_single_voice_format():
    census = _census()
    job = {"audio_config": {"audio_format": "short", "speaker_count": 1},
           "preferences": {"_cast_sketch": {"cast_size": 3}}}
    assert census.cast_size(job) == (3, "single-voice-format/cast-floor")


def test_the_sketch_is_read_from_either_carrier():
    """cast_size lives under episode_profile on 33cdaa8d and only `characters`
    under preferences, so both carriers are consulted."""
    census = _census()
    job = {"audio_config": {"audio_format": "short", "speaker_count": 1},
           "episode_profile": {"user_preferences": {"_cast_sketch": {"cast_size": 3}}}}
    assert census.cast_size(job)[0] == 3
    job2 = {"audio_config": {"audio_format": "narration", "speaker_count": 1},
            "preferences": {"_cast_sketch": {"characters": [{}, {}, {}, {}]}}}
    assert census.cast_size(job2)[0] == 4


@pytest.mark.parametrize("sketch", [{"cast_size": 1}, {"cast_size": 2},
                                    {"characters": [{}, {}]}, {}, None])
def test_the_floor_only_fires_above_two(sketch):
    """`_apply_cast_voice_floor` fires only when the cast is > 2, and only ever
    RAISES. A 2-hander must not disturb a single-voice format."""
    census = _census()
    job = {"audio_config": {"audio_format": "short", "speaker_count": 1},
           "preferences": {"_cast_sketch": sketch}}
    assert census.cast_size(job) == (1, "single-voice-format")


def test_the_floor_is_capped_at_six_like_the_ssot():
    census = _census()
    job = {"audio_config": {"audio_format": "short"},
           "preferences": {"_cast_sketch": {"cast_size": 40}}}
    assert census.cast_size(job)[0] == 6


def test_the_floor_never_lowers_a_larger_contract():
    """Additive only: the floor raises, it must never shrink a real cast."""
    census = _census()
    job = {"audio_config": {"audio_format": "drama", "speaker_count": 5},
           "voice_cast": {"contract": {"voice_map": {f"S{i}": i for i in range(5)}}},
           "preferences": {"_cast_sketch": {"cast_size": 3}}}
    assert census.cast_size(job) == (5, "contract")


# ---------------------------------------------------------------------------
# DELIVERY MUST BE READ FROM THE TAKE THAT SHIPPED.
#
# The gate stamps attempt 2's metrics even when the regen cap bails to attempt
# 1, so "the latest attempt" describes the take that was THROWN AWAY.
# MEASURED 2026-08-27 (positive control: 2896 jobs carry tts_segment_logs):
# 149 jobs are stamped regen_cap_bailed_to_attempt_1 and have delivered
# segments; on 26 the two attempts name different speakers, and on all 26 the
# census read attempt 2 while the audio was attempt 1. Job 38507678 shipped
# ['Host1','Host2'] while attempt 2 recorded ['Host1'].
# ---------------------------------------------------------------------------


def test_delivery_prefers_the_tts_log_over_the_gate():
    census = _census()
    job = {"tts_segment_logs": [{"speaker": "Host1"}, {"speaker": "Host2"}],
           "stages": {"quality_gate": {"attempt_2_metrics": {
               "speaker_balance": {"distribution": {"Host1": 1}}}}}}
    names, how = census.delivered_speakers(job)
    assert how == "tts_segment_logs"
    assert len(names) == 2, "the gate said one voice; the audio had two"


def test_the_38507678_shape_is_no_longer_a_false_under_delivery():
    census = _census()
    job = {"audio_config": {"audio_format": "dialogue", "speaker_count": 2},
           "tts_segment_logs": [{"speaker": "Host1"}, {"speaker": "Host2"}],
           "stages": {"quality_gate": {
               "attempt_1_metrics": {"speaker_balance": {"distribution": {"Host1": 1, "Host2": 1}}},
               "attempt_2_metrics": {"speaker_balance": {"distribution": {"Host1": 1}}}}}}
    assert census.classify(job) is None


def test_label_punctuation_drift_is_not_a_different_speaker():
    """The gate writes 'Prof_ James Okafor'; the TTS log writes 'Prof. James
    Okafor'. Nine of the 26 bail jobs differed only this way."""
    census = _census()
    assert census._norm("Prof_ James Okafor") == census._norm("Prof. James Okafor")


def test_delivery_falls_back_to_the_gate_when_no_tts_log_exists():
    census = _census()
    job = {"stages": {"quality_gate": {"attempt_1_metrics": {
        "speaker_balance": {"distribution": {"A": 1, "B": 1}}}}}}
    names, how = census.delivered_speakers(job)
    assert how == "gate/latest-attempt" and len(names) == 2


def test_voices_heard_is_a_different_question_from_labels():
    """Several cast labels can render through ONE voice_id: the script has a
    cast and the audio does not. 75 of the 130 over-delivering jobs are this."""
    census = _census()
    job = {"tts_segment_logs": [
        {"speaker": "Narrator", "voice_id": "v1"},
        {"speaker": "Nadia", "voice_id": "v1"},
        {"speaker": "Theo", "voice_id": "v1"}]}
    assert len(census.delivered_speakers(job)[0]) == 3
    assert len(census.delivered_voices(job)) == 1


def test_the_digit_in_host1_is_load_bearing():
    """A letters-only normaliser would merge Host1 and Host2 into one speaker.

    MEASURED 2026-08-27: 1889 of the 2896 jobs carrying tts_segment_logs (65%)
    would collapse, 1870 of them the plain ['Host1','Host2'] pair. The census
    would report the entire two-host corpus as a total voice collapse --
    silently, and it would look like a real finding.

        cd kitesforu-qa/scripts && GCP_PROJECT_ID=kitesforu-dev python3 -c \
          '<stream podcast_jobs; per job compare len({alnum(l)}) vs len({alpha(l)})
            over the tts_segment_logs speaker labels>'

    This test exists so `_norm` can never be "aligned" to a prose description
    that omits the digit. It is the guard, not the docstring.
    """
    census = _census()
    assert census._norm("Host1") != census._norm("Host2")
    assert census._norm("Sister 1") != census._norm("Sister 2")
    # ...while the drift it DOES exist to absorb still normalises together.
    assert census._norm("Prof_ James Okafor") == census._norm("Prof. James Okafor")
    assert census._norm("Co-host") == census._norm("co-host")

    job = {"tts_segment_logs": [{"speaker": "Host1"}, {"speaker": "Host2"}]}
    assert len(census.delivered_speakers(job)[0]) == 2, "the two-host corpus"
