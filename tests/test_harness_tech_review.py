"""Tech-review content long-tail tests."""
from kitesforu_qa.harness import Artifact, run_dimension


def _doc(text):
    return {"job_id": "tr", "status": "completed", "episode_profile": {"genre": "tech_review"},
            "outputs": {"script": {"dialogue": [{"text": text, "speaker": "A"}]}}}


def test_hype_fails_critical():
    sr = run_dimension(Artifact.from_doc(_doc("This phone is a game-changer, absolutely mind-blowing and revolutionary!")), "content")
    assert any("no_hype_phrases" in i for i in sr.issues), sr.issues


def test_spec_grounded_passes():
    sr = run_dimension(Artifact.from_doc(_doc("It has 16GB RAM, a 6.7 inch 120Hz display, 5000mAh battery, and scores 2100 in Geekbench.")), "content")
    assert not any("has_spec_numbers" in i or "benchmark_named" in i for i in sr.issues), sr.issues


def test_vibes_only_fails_specs():
    sr = run_dimension(Artifact.from_doc(_doc("It feels really fast and smooth and the screen looks gorgeous and bright.")), "content")
    assert any("has_spec_numbers" in i for i in sr.issues), sr.issues
