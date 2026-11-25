"""A typical user (`harshness: typical`) is briefed as an ordinary person, never as a refuting critic.

The persona runners in this repo, `acceptance_gate.py --persona` and `story_judge.py --persona`, used to
read no `harshness` at all, so a typical user was handed the expert's refute-by-default brief: an expert
under her name. Each persona's kind is read here from the raw YAML text, independently of the code under
test, and pinned both ways: every typical user gets the ordinary-person brief, every expert keeps REFUTE.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import acceptance_gate  # noqa: E402
import story_judge  # noqa: E402

_PERSONA_DIR = Path(__file__).resolve().parent.parent / "hero_users" / "personas"
_ALL = sorted(p.stem for p in _PERSONA_DIR.glob("*.yaml"))
_TYPICAL = [n for n in _ALL
            if re.search(r"^harshness:\s*typical\b", (_PERSONA_DIR / f"{n}.yaml").read_text(), re.M)]
_EXPERTS = [n for n in _ALL if n not in _TYPICAL]

_EXPERT_ONLY = ("REFUTE", "agrees to be agreeable", "Run the ADVERSARY")


def test_both_kinds_of_persona_exist() -> None:
    """Positive control for the parametrized tests below: an empty list would pass them by collecting nothing."""
    assert len(_TYPICAL) >= 4 and len(_EXPERTS) >= 7, (_TYPICAL, _EXPERTS)


@pytest.mark.parametrize("name", _ALL)
def test_the_runner_reads_harshness_as_the_yaml_declares_it(name: str) -> None:
    assert story_judge.is_typical_persona(name) is (name in _TYPICAL)


@pytest.mark.parametrize("audio_only", [False, True], ids=["video", "audio-only"])
@pytest.mark.parametrize("name", _TYPICAL)
def test_a_typical_user_gets_the_ordinary_person_brief(name: str, audio_only: bool) -> None:
    b = acceptance_gate._adversary_brief(name, audio_only=audio_only)
    leaked = [w for w in _EXPERT_ONLY if w in b]
    assert not leaked, f"{name} was handed the expert's brief: {leaked}"
    for w in ("ORDINARY PERSON, NOT A CRITIC", "ONCE", "stopped_at_s", "would_finish", "would_share",
              "Run the TYPICAL USER"):
        assert w in b, f"{name}: {w!r} missing from the typical brief"
    assert story_judge.load_persona(name) in b, "the persona's own block did not reach the brief"
    if audio_only:
        assert "audio_url" in b and "LISTENS" in b and "frames_dir" not in b
    else:
        assert "frames_dir" in b and "audio_url" not in b


@pytest.mark.parametrize("audio_only", [False, True], ids=["video", "audio-only"])
@pytest.mark.parametrize("name", _EXPERTS)
def test_an_expert_keeps_the_refute_brief(name: str, audio_only: bool) -> None:
    b = acceptance_gate._adversary_brief(name, audio_only=audio_only)
    assert all(w in b for w in _EXPERT_ONLY), f"{name} lost the refute brief"
    assert "ORDINARY PERSON" not in b and "Run the TYPICAL USER" not in b
    assert b.endswith(acceptance_gate._adversary_brief(None, audio_only=audio_only))


@pytest.mark.parametrize("name", _TYPICAL)
def test_the_craft_judge_refuses_a_typical_user(name: str) -> None:
    """story_judge scores writing on a craft rubric; a typical user grades no craft, so she is pointed at her runner."""
    with pytest.raises(ValueError, match=r"typical user .*acceptance_gate\.py --persona"):
        story_judge.critic_block(name)
    with pytest.raises(ValueError):
        story_judge.build_prompt("a topic", "fiction", 300, "a transcript", "story", name)


def test_the_craft_judge_refuses_before_reading_the_job_or_paying_for_classify() -> None:
    class _NoDb:
        def collection(self, *_a, **_k):
            raise AssertionError("judge_job read Firestore before refusing the typical user")

    with pytest.raises(ValueError, match="typical user"):
        story_judge.judge_job(_NoDb(), "a-job", "a-model", _TYPICAL[0])


def test_the_craft_judge_cli_refuses_a_typical_user_with_a_usage_error() -> None:
    p = subprocess.run([sys.executable, str(_SCRIPTS / "story_judge.py"), "a-job", "--persona", _TYPICAL[0]],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and "typical user" in p.stderr, (p.returncode, p.stderr[-400:])


@pytest.mark.parametrize("name", _EXPERTS)
def test_the_craft_judge_still_runs_an_expert(name: str) -> None:
    assert story_judge.critic_block(name) == story_judge.load_persona(name)
    assert story_judge.critic_block(None) == story_judge.CRITIC_PERSONA
