"""File-based judge path — export prompts → (local agent scores) → load verdicts. No metered API."""
import json

from kitesforu_qa.harness import Artifact
from kitesforu_qa.harness.judge import export_judge_prompts, load_judge_verdicts


def _explainer():
    return Artifact.from_doc({
        "job_id": "e", "status": "completed", "episode_profile": {"genre": "educational"},
        "outputs": {"script": {"dialogue": [
            {"text": "A B-tree keeps keys sorted. For example, with a million rows it touches about 20 pages. That's the whole trick.", "speaker": "A"}]}}})


def test_export_then_load_roundtrip(tmp_path):
    art = _explainer()
    pj = tmp_path / "prompts.jsonl"
    n = export_judge_prompts(art, str(pj))
    assert n >= 1, "explainer should export at least one judge prompt"
    rows = [json.loads(line) for line in pj.read_text().splitlines() if line.strip()]
    assert all({"check_id", "question", "excerpt", "instruction"} <= set(r) for r in rows)
    # simulate a LOCAL agent scoring each prompt
    verdicts = tmp_path / "verdicts.jsonl"
    verdicts.write_text("\n".join(
        json.dumps({"check_id": r["check_id"], "passed": True, "score": 0.9, "reason": "mechanism explained"})
        for r in rows))
    results = load_judge_verdicts(str(verdicts))
    assert len(results) == n
    assert all(r.passed and r.score == 0.9 for r in results)
    assert {r.check_id for r in results} == {r["check_id"] for r in rows}


def test_load_tolerates_blank_and_unknown(tmp_path):
    vp = tmp_path / "v.jsonl"
    vp.write_text('\n{"check_id": "nope.unknown", "passed": true, "score": 1.0}\n\n')
    assert load_judge_verdicts(str(vp)) == []  # unknown id + blanks skipped, no crash
