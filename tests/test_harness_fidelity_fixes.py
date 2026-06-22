"""Fidelity-fix pin tests — the audit's firing jobs must now behave correctly (no silent regression)."""
from datetime import datetime, timezone

from kitesforu_qa.harness import Artifact, run_dimension


def test_cost_check_skips_pre_reconcile():
    # pre-2026-06-22 job legitimately lacks tts_usd_actual — must NOT fire (the #1 noise source)
    doc = {"job_id": "old", "status": "completed", "created_at": datetime(2026, 6, 16, 3, 0, tzinfo=timezone.utc),
           "tts_segment_logs": [{"provider": "inworld", "text_length": 100}], "costs": {}}
    sr = run_dimension(Artifact.from_doc(doc), "cost-correctness")
    assert not any("tts_actual_present" in i for i in sr.issues), sr.issues


def test_cost_check_still_fires_post_reconcile():
    # a POST-fix job missing the cost is a real regression — must STILL fire
    doc = {"job_id": "new", "status": "completed", "created_at": datetime(2026, 6, 22, 5, 0, tzinfo=timezone.utc),
           "tts_segment_logs": [{"provider": "inworld", "text_length": 100}], "costs": {}}
    sr = run_dimension(Artifact.from_doc(doc), "cost-correctness")
    assert any("tts_actual_present" in i for i in sr.issues), "post-fix missing cost must still fire"


def test_drama_conflict_passes_noir():
    doc = {"job_id": "d", "status": "completed", "episode_profile": {"genre": "drama"},
           "outputs": {"script": {"dialogue": [{"text": "He pulled the gun. She was dead. The arrest came, but he wanted to bury the secret.", "speaker": "A"}]}}}
    sr = run_dimension(Artifact.from_doc(doc), "content")
    assert not any("has_conflict" in i for i in sr.issues), sr.issues


def test_lands_takeaway_passes_demystify():
    doc = {"job_id": "e", "status": "completed", "episode_profile": {"genre": "educational"},
           "outputs": {"script": {"dialogue": [{"text": "blah blah. So next time a query returns instantly, that's a B-tree. That's it. Not magic.", "speaker": "A"}]}}}
    sr = run_dimension(Artifact.from_doc(doc), "content")
    assert not any("lands_takeaway" in i for i in sr.issues), sr.issues


def test_example_numbers_passes_spelled():
    doc = {"job_id": "e2", "status": "completed", "episode_profile": {"genre": "educational"},
           "outputs": {"script": {"dialogue": [{"text": "For example, with ten million rows it only touches a dozen pages.", "speaker": "A"}]}}}
    sr = run_dimension(Artifact.from_doc(doc), "content")
    assert not any("example_has_numbers" in i for i in sr.issues), sr.issues


def test_segments_present_skips_when_script_present():
    doc = {"job_id": "s", "status": "completed", "outputs": {"script": {"dialogue": [{"text": "hello world this is a real rendered script", "speaker": "A"}]}}}
    sr = run_dimension(Artifact.from_doc(doc), "structure")
    assert not any("segments_present" in i for i in sr.issues), sr.issues
