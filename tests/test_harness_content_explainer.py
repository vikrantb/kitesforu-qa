"""Explainer content long-tail — catalog exp-c-* battery (arc + teacher-voice + anti-meta)."""
from kitesforu_qa.harness import Artifact, run_dimension

GOOD_TEXT = (
    "Ever wondered how a database finds one row among millions? Most people think it scans every "
    "single row. Turns out it uses a B-tree index. For example, with 1,000,000 rows a B-tree touches "
    "only about 20 pages. The takeaway: keep your keys sorted and lookups stay fast."
)


def _doc(text, topic="How a B-tree database index finds rows"):
    return {
        "job_id": "e", "status": "completed",
        "episode_profile": {"genre": "educational", "topic": topic},
        "outputs": {"script": {"dialogue": [{"text": text, "speaker": "Host1"}]}},
    }


def test_good_explainer_passes_gating():
    sr = run_dimension(Artifact.from_doc(_doc(GOOD_TEXT)), "content")
    assert sr.passed, sr.issues


def test_npr_meta_fails():
    sr = run_dimension(Artifact.from_doc(_doc(
        "Welcome to the show! We're your hosts. In this episode we'll talk B-trees. Thanks for tuning in."
    )), "content")
    assert any("no_npr_meta" in i for i in sr.issues), sr.issues


def test_condescension_fails():
    sr = run_dimension(Artifact.from_doc(_doc(
        "As we all know, obviously a B-tree is, needless to say, the fast database index everyone knows that."
    )), "content")
    assert any("no_condescension" in i for i in sr.issues), sr.issues


def test_off_topic_fails_critical():
    sr = run_dimension(Artifact.from_doc(_doc(
        "Let me tell you about my favorite recipes for pasta and pizza we are cooking tonight.",
        topic="How public key cryptography works",
    )), "content")
    assert not sr.passed
    assert any("stays_on_topic" in i for i in sr.issues), sr.issues


def test_today_we_discuss_opening_fails():
    sr = run_dimension(Artifact.from_doc(_doc(
        "Today we'll discuss how B-trees work in databases and indexes and why they matter."
    )), "content")
    assert any("no_today_we_discuss_opening" in i for i in sr.issues), sr.issues
