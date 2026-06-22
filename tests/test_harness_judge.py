"""Judge-layer tests — judge checks build prompts; a LOCAL evaluator scores them; never blocks.

The judge checks (explainer insight/handwavy/practical-examples) encode the founder's deep-quality
concern that deterministic checks can't measure. A mock evaluator exercises the pass/fail/skip paths
without hitting Gemini.
"""
from kitesforu_qa.harness import Artifact, judge_checks_for, run_judges


class _MockEval:
    def __init__(self, verdict):
        self.verdict = verdict

    def evaluate_json(self, prompt):
        return self.verdict


EXPLAINER = {
    "job_id": "e",
    "status": "completed",
    "episode_profile": {"genre": "educational"},
    "outputs": {"script": {"dialogue": [
        {"text": "A B-tree is a balanced index. For example, imagine a library catalog. It works by "
                 "keeping keys sorted so a lookup touches only a few pages.", "speaker": "Maya"},
    ]}},
}


def test_judge_checks_registered_for_explainer():
    ids = {c.id for c in judge_checks_for(Artifact.from_doc(EXPLAINER))}
    assert "explainer.real_insight_not_handwavy" in ids
    assert "explainer.genuine_understanding" in ids


def test_run_judges_pass():
    art = Artifact.from_doc(EXPLAINER)
    ev = _MockEval({"passed": True, "score": 0.9, "reason": "dense + worked example"})
    results = run_judges(art, judge_checks_for(art), evaluator=ev)
    active = [r for r in results if not r.skipped]
    assert active and all(r.passed for r in active)
    assert any(r.score == 0.9 for r in active)


def test_run_judges_fail_handwavy():
    art = Artifact.from_doc(EXPLAINER)
    ev = _MockEval({"passed": False, "score": 0.2, "reason": "handwavy, surface-level"})
    results = run_judges(art, judge_checks_for(art), evaluator=ev)
    failed = [r for r in results if not r.passed and not r.skipped]
    assert failed and "handwavy" in failed[0].evidence


def test_judges_skip_when_no_script():
    art = Artifact.from_doc({"job_id": "x", "episode_profile": {"genre": "educational"}})
    results = run_judges(art, judge_checks_for(art), evaluator=_MockEval({"passed": True, "score": 1.0}))
    assert results and all(r.skipped for r in results)


def test_judges_skip_when_evaluator_broken():
    class _Broken:
        def evaluate_json(self, p):
            raise RuntimeError("no api key")

    art = Artifact.from_doc(EXPLAINER)
    results = run_judges(art, judge_checks_for(art), evaluator=_Broken())
    # an unavailable/flaky judge SKIPS — it's enrichment, not the gate
    assert results and all(r.skipped for r in results)
