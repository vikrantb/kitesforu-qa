"""Artifact.llm_brief accessor + the grounded check (brief verifiable via stages.llm_brief)."""
from kitesforu_qa.harness import Artifact, run_dimension


def _doc(brief_chars=None, research=False):
    d = {"job_id": "x", "status": "completed", "episode_profile": {"genre": "educational"},
         "outputs": {"script": {"dialogue": [{"text": "A B-tree is a balanced index. For example imagine a library catalog.", "speaker": "A"}]}}}
    if brief_chars:
        d["stages"] = {"job-research-planner": {"llm_brief": {"brief_chars": brief_chars, "angles": ["mechanism"]}, "route": {"research_mode": "llm"}}}
    if research:
        d["research_results"] = [{"content": "x"}]
    return d


def test_llm_brief_accessor():
    art = Artifact.from_doc(_doc(brief_chars=3514))
    assert art.llm_brief.get("brief_chars") == 3514
    assert art.research_mode == "llm"


def test_grounded_with_brief_passes():
    sr = run_dimension(Artifact.from_doc(_doc(brief_chars=3514)), "content")
    assert not any("grounded_research_or_brief" in i for i in sr.issues), sr.issues


def test_ungrounded_explainer_flagged():
    sr = run_dimension(Artifact.from_doc(_doc()), "content")
    assert any("grounded_research_or_brief" in i for i in sr.issues), sr.issues
