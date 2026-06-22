"""Per-genre content check tests — each genre's bar fires on a bad fixture, passes on a good one."""
from kitesforu_qa.harness import Artifact, run_dimension


def _doc(genre, text):
    return {
        "job_id": genre,
        "status": "completed",
        "episode_profile": {"genre": genre},
        "script": {"dialogue": [{"speaker": "A", "text": text}]},
    }


def _issues(genre, text):
    sr = run_dimension(Artifact.from_doc(_doc(genre, text)), "content")
    return sr, sr.issues


def test_horror_good_vs_flat():
    good, _ = _issues("horror", "The cold dark hallway. A whisper, then footsteps. Her breath caught in dread, alone.")
    assert good.passed, good.issues
    bad, issues = _issues("horror", "The man walked to the store and bought some milk on a normal afternoon.")
    assert not bad.passed
    assert any("horror.has_atmosphere" in i for i in issues)


def test_bedtime_scary_words_fail_critical():
    good, _ = _issues("bedtime", "The sleepy bunny snuggled softly into a warm cozy bed and drifted into a peaceful dream.")
    assert good.passed, good.issues
    bad, issues = _issues("bedtime", "The monster screamed as blood spilled and everyone would die in terror.")
    assert not bad.passed
    assert any("bedtime.no_scary_words" in i for i in issues), issues


def test_comedy_needs_turns():
    bad, issues = _issues("comedy", "Here are some facts about taxes presented in a flat dry manner with no jokes.")
    assert not bad.passed
    assert any("comedy.has_comedic_turns" in i for i in issues)


def test_tutorial_needs_steps():
    good, _ = _issues("tutorial", "First, open the app. Next, click settings. Then choose export. Finally, save the file.")
    assert good.passed, good.issues
    bad, issues = _issues("tutorial", "The app is generally useful and many people enjoy using it for various things.")
    assert not bad.passed
    assert any("tutorial.has_steps" in i for i in issues)


def test_no_ai_tells_fires_everywhere():
    # genre-agnostic: applies to any genre
    sr = run_dimension(Artifact.from_doc(_doc("educational", "Let's delve into this topic. It's important to note the tapestry of ideas.")), "content")
    assert not sr.passed
    assert any("no_ai_tells" in i for i in sr.issues), sr.issues


def test_wrong_genre_checks_skip():
    # a horror check must not fire on a bedtime piece
    sr = run_dimension(Artifact.from_doc(_doc("bedtime", "The sleepy bunny snuggled softly into a warm cozy peaceful dream.")), "content")
    assert sr.passed
    assert not any("horror" in i for i in sr.issues)
