"""Fiction named-cast check — genre-gated (per peer review of #29)."""
from kitesforu_qa.harness import Artifact, run_dimension


def _doc(genre, speakers):
    return {"job_id": "f", "status": "completed", "episode_profile": {"genre": genre},
            "outputs": {"script": {"dialogue": [{"text": "hello there my friend", "speaker": s} for s in speakers]}}}


def test_fiction_generic_speaker_fails():
    sr = run_dimension(Artifact.from_doc(_doc("drama", ["Host1", "Cole"])), "structure")
    assert any("named_not_generic" in i for i in sr.issues), sr.issues


def test_fiction_named_cast_passes():
    sr = run_dimension(Artifact.from_doc(_doc("drama", ["Officer Clara Vance", "Cole"])), "structure")
    assert not any("named_not_generic" in i for i in sr.issues), sr.issues


def test_non_fiction_host_labels_skip():
    # the whole point: explainer Host1/Host2 must NOT trigger the fiction check
    sr = run_dimension(Artifact.from_doc(_doc("educational", ["Host1", "Host2"])), "structure")
    assert not any("named_not_generic" in i for i in sr.issues), sr.issues
