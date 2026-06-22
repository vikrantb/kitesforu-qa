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


def test_expected_tts_from_logs():
    assert abs(_expected_tts_from_logs(Artifact.from_doc(GOOD)) - 0.06) < 1e-9


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
