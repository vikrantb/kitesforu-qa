"""Cost-correctness check tests — pin the closed cost loop (the 18x over-report can't return).

A GOOD job: tts_usd_actual matches the real provider x chars from the logs. A BAD job: the classic
18x — tts_usd_actual priced as ElevenLabs while every segment actually rendered on inworld.
"""
from kitesforu_qa.harness import Artifact, run_dimension
from kitesforu_qa.harness.checks.cost import _expected_tts_from_logs

_LOGS = [
    {"provider": "inworld", "text_length": 2000, "success": True},
    {"provider": "inworld", "text_length": 4000, "success": True},
]
# expected = (2000+4000) * 10.0 / 1e6 = 0.06

GOOD = {
    "job_id": "g",
    "status": "completed",
    "tts_segment_logs": _LOGS,
    "costs": {"tts_usd_actual": 0.06, "tts_providers": {"inworld": 6000}, "total_usd_estimate": 0.12},
}

# 18x: 6000 chars priced as elevenlabs ($183.30/M) = ~$1.10 while really inworld ($0.06)
BAD_18X = {
    "job_id": "b",
    "status": "completed",
    "tts_segment_logs": _LOGS,
    "costs": {"tts_usd_actual": 1.0998, "total_usd_estimate": 1.16},
}


# A RE-VOICED JOB. A first pass voiced two lines (2000 + 4000 chars); a re-voice rendered line 0 again
# (2500 chars) and reused line 1. The logs keep the take that shipped, and the record counts all 3 renders.
_TAKE = [
    {"provider": "inworld", "text_length": 2500, "success": True},
    {"provider": "inworld", "text_length": 4000, "success": True, "reused": True, "cost": 0.0},
]


def _re_voiced(costs, logs=_TAKE):
    return {"job_id": "r", "status": "completed", "tts_segment_logs": logs, "costs": costs}


def _record(actual, providers, renders):
    return {"tts": {"modality": "tts", "total_cost_usd": actual, "meta": {"providers": providers, "renders": renders}},
            "tts_usd_actual": actual, "tts_providers": providers, "total_usd_estimate": actual}


def _failed(doc):
    sr = run_dimension(Artifact.from_doc(doc), "cost-correctness")
    return {name for name in ("tts_actual_matches_logs", "no_provider_mismatch_overcount")
            if any(name in i for i in sr.issues)}


def test_expected_tts_from_logs():
    assert abs(_expected_tts_from_logs(Artifact.from_doc(GOOD)) - 0.06) < 1e-9


def test_a_reused_row_is_not_priced():
    assert abs(_expected_tts_from_logs(Artifact.from_doc(_re_voiced({}))) - 0.025) < 1e-9


def test_a_job_with_reused_rows_and_no_render_count_is_priced_from_its_billed_rows():
    doc = _re_voiced({"tts_usd_actual": 0.025, "tts_providers": {"inworld": 2500}, "total_usd_estimate": 0.03})
    assert _failed(doc) == set()


def test_a_re_voiced_job_matches_what_its_record_says_it_billed():
    assert _failed(_re_voiced(_record(0.085, {"inworld": 8500}, 3))) == set()


def test_a_costly_regen_is_not_a_provider_mismatch():
    """A first pass on ElevenLabs, re-voiced whole on inworld: the shipped take alone is 19x below the record."""
    logs = [{"provider": "inworld", "text_length": 6000, "success": True}]
    assert _failed(_re_voiced(_record(1.1598, {"elevenlabs": 6000, "inworld": 6000}, 2), logs)) == set()


def test_a_wrong_price_on_a_re_voiced_job_still_fails_both_checks():
    """The 18x class on the record path: 8500 inworld chars priced as ElevenLabs."""
    assert _failed(_re_voiced(_record(1.55805, {"inworld": 8500}, 3))) == {
        "tts_actual_matches_logs", "no_provider_mismatch_overcount"}


def test_a_re_voiced_record_naming_a_provider_the_logs_contradict_still_fails_both_checks():
    """The 18x class by label: the record bills ElevenLabs, and the take it shipped rendered on inworld."""
    assert _failed(_re_voiced(_record(1.55805, {"elevenlabs": 8500}, 3))) == {
        "tts_actual_matches_logs", "no_provider_mismatch_overcount"}


def test_a_re_voiced_record_short_of_its_own_billed_chars_fails():
    assert _failed(_re_voiced(_record(0.02, {"inworld": 8500}, 3))) == {"tts_actual_matches_logs"}


def test_a_job_whose_record_counts_only_its_logged_renders_is_still_checked_against_the_logs():
    """No re-voice, so the logs can check it: the record's own characters are not believed instead."""
    logs = [{"provider": "inworld", "text_length": 6000, "success": True}]
    assert _failed(_re_voiced(_record(1.0998, {"elevenlabs": 6000}, 1), logs)) == {
        "tts_actual_matches_logs", "no_provider_mismatch_overcount"}


def test_good_cost_passes():
    sr = run_dimension(Artifact.from_doc(GOOD), "cost-correctness")
    assert sr.passed, sr.issues


def test_18x_overcount_fails_critical():
    sr = run_dimension(Artifact.from_doc(BAD_18X), "cost-correctness")
    assert not sr.passed  # the critical no_provider_mismatch_overcount gates
    assert any("no_provider_mismatch_overcount" in i for i in sr.issues), sr.issues
    assert any("tts_actual_matches_logs" in i for i in sr.issues), sr.issues


def test_missing_actual_with_logs_fails():
    doc = {"job_id": "m", "status": "completed", "tts_segment_logs": _LOGS, "costs": {"total_usd_estimate": 0.1}}
    sr = run_dimension(Artifact.from_doc(doc), "cost-correctness")
    assert not sr.passed
    assert any("tts_actual_present" in i for i in sr.issues), sr.issues


def test_no_logs_skips_cleanly():
    # a job with no tts logs (e.g. not yet rendered) shouldn't fail cost checks
    doc = {"job_id": "n", "status": "completed", "costs": {"total_usd_estimate": 0.0}}
    sr = run_dimension(Artifact.from_doc(doc), "cost-correctness")
    assert sr.passed, sr.issues
