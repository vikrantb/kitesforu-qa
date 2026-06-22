"""News content long-tail tests."""
from kitesforu_qa.harness import Artifact, run_dimension


def _doc(text):
    return {"job_id": "n", "status": "completed", "episode_profile": {"genre": "news"},
            "outputs": {"script": {"dialogue": [{"text": text, "speaker": "A"}]}}}


def test_attributed_neutral_passes():
    sr = run_dimension(Artifact.from_doc(_doc("According to officials, the bill passed today. The agency confirmed the vote in a statement.")), "content")
    assert not any("has_attribution" in i or "no_editorializing" in i or "has_recency" in i for i in sr.issues), sr.issues


def test_editorializing_fails():
    sr = run_dimension(Artifact.from_doc(_doc("In an outrageous and disgraceful move today, officials said the shocking bill passed.")), "content")
    assert any("no_editorializing" in i for i in sr.issues), sr.issues


def test_unattributed_fails():
    sr = run_dimension(Artifact.from_doc(_doc("The bill passed today. It will change everything for everyone everywhere very soon.")), "content")
    assert any("has_attribution" in i for i in sr.issues), sr.issues
