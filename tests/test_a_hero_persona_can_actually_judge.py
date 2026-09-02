"""A hero-user persona can be RUN, and a story has a reviewer at all.

TWO GAPS, found by auditing before building (the audit is the reason this is ~60 lines instead of
a second judge):

1. **No persona covered story/emotional content.** The six existing personas are audio quality,
   L&D, technical correctness, study, job-seeking and short-form craft — every one of them reviews
   INFORMATIONAL content. `.claude/rules/02-done.md` routes social-short -> Sofia, audio -> Aarav,
   course -> Elena, and had NO row for a romance or drama. That is exactly the content class the
   founder complained about.

2. **Nothing could RUN a persona.** `hero_users/` contains no executable at all; the personas were
   data for a human to paste. `story_judge.py` had the whole harness — model call, JSON contract,
   rubric, anti-sycophancy anchors, report — behind ONE hardcoded critic (Ruth Keller).

So this extends `story_judge.py` rather than adding a second judge (rule #19). `--persona` swaps
the critic block; everything else is unchanged, and omitting it is byte-identical to before.

WHAT IS DELIBERATELY NOT CLAIMED: `story_judge` scores the WRITING — its own prompt says voice,
music and visuals are graded elsewhere. So this does not yet answer the founder's visual complaint
("what are the slides based animations doing here"); it answers the narrative half. The remaining
half needs a runner over the DELIVERED video, and that is filed rather than pretended.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import story_judge  # noqa: E402

_PERSONA_DIR = Path(__file__).resolve().parent.parent / "hero_users" / "personas"


def _prompt(persona=None):
    return story_judge.build_prompt("a love story", "fiction", 600, "a transcript", "story", persona)


class TestTheStoryPersonaExists:
    def test_a_story_persona_is_present(self):
        assert (_PERSONA_DIR / "nadia-story-listener.yaml").exists(), (
            "no persona covers fiction/drama — the content class the founder complained about"
        )

    def test_it_carries_the_calibration_fixture_that_proves_it_is_not_a_rubber_stamp(self):
        import yaml

        p = yaml.safe_load((_PERSONA_DIR / "nadia-story-listener.yaml").read_text())
        # Every persona in this directory carries a known_bad_fixture for the same reason: a critic
        # that passes it is broken, and a critic nobody has calibrated is decoration.
        assert "to put a concrete number on it" in str(p.get("known_bad_fixture", "")).lower(), (
            "the calibration fixture must be the founder's actual witness line"
        )
        assert p.get("model_family_must_differ_from_generator") is True
        assert p.get("cold") is True, "a critic that sees the 'it works' claim is not independent"


class TestThePersonaIsLIVE:
    """A flag that does not reach the prompt is the defect this repo keeps shipping."""

    def test_omitting_the_flag_is_byte_identical(self):
        assert _prompt() == _prompt(None)
        assert "RUTH KELLER" in _prompt()

    def test_the_flag_actually_swaps_the_critic(self):
        n = _prompt("nadia-story-listener")
        assert "Nadia" in n
        assert "RUTH KELLER" not in n, "the persona was appended, not substituted"
        assert n != _prompt(), "the prompt did not change — the flag is inert"

    def test_the_personas_own_verdict_question_reaches_the_prompt(self):
        """Not just her name — the question she is actually answering."""
        assert "1am" in _prompt("nadia-story-listener")

    @pytest.mark.parametrize("name", ["sofia-creator", "aarav-audio", "maya-student"])
    def test_every_other_persona_loads_too(self, name: str):
        """This is a shared runner, not a Nadia-shaped hole."""
        out = story_judge.load_persona(name)
        assert len(out) > 200 and "You see ONLY the artifact" in out


def test_a_typo_RAISES_instead_of_silently_using_the_default():
    """The failure mode that makes a review worthless without announcing itself.

    A silent fallback to Ruth Keller would make `--persona nadia-storey-listener` look like it
    worked and produce a confident report from the wrong reviewer.
    """
    with pytest.raises(FileNotFoundError) as e:
        story_judge.load_persona("no-such-persona")
    assert "available:" in str(e.value), "the error must name the valid personas"


class TestTheGateCanRunAPersonaOnTheDELIVEREDVIDEO:
    """The other half of the founder's ask — the one `story_judge` structurally cannot answer.

    `story_judge` scores the WRITING; its own prompt says voice, music and visuals are graded
    elsewhere. So it cannot answer "what are the slides based animations doing here". The frames
    live in `acceptance_gate.py`, which extracts them at fps=1/3 across the FULL duration and emits
    a manifest — but its ADVERSARY instruction was generic, with no way to say which hero user is
    reviewing. The persona system and the frame system could not meet.

    `--persona` joins them, reusing `story_judge.load_persona` so there is ONE owner of what a
    persona brief looks like.
    """

    def _brief(self, persona=None):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import acceptance_gate

        return acceptance_gate._adversary_brief(persona)

    def test_omitting_the_persona_is_byte_identical(self):
        b = self._brief()
        assert b.startswith("Run the ADVERSARY:")
        assert "Nadia" not in b

    def test_a_persona_brief_carries_the_reviewer_AND_the_frame_instruction(self):
        b = self._brief("nadia-story-listener")
        assert "Nadia" in b, "the persona did not reach the brief"
        assert "1am" in b, "her own verdict question is missing"
        # The half that makes it about the DELIVERED VIDEO rather than a script.
        assert "EVERY frame" in b and "never" in b and "one hero frame" in b, (
            "the brief does not tell the reviewer to judge the whole duration"
        )
        assert b.endswith(self._brief()), "the generic adversary brief was dropped, not extended"

    def test_a_typo_RAISES_here_too(self):
        """Same contract as story_judge: a silent fallback would produce a confident verdict from
        the wrong reviewer, on frames."""
        with pytest.raises(FileNotFoundError):
            self._brief("nadia-storey-listener")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE HOLE THE #172 CODE-CRITIC FOLLOW-UP FOUND.
#
# `test_every_other_persona_loads_too` asserted `len(out) > 200` and that an anchor sentence is
# present. BOTH are satisfied by the static f-string scaffolding in `load_persona` itself — neither
# reads a byte of the YAML. Verified by gutting `sofia-creator.yaml` to a single `name:` line:
# len=400, anchor present, 12 tests green. Renaming any key the template reads (`register` ->
# `voice_register`, the realistic drift) is equally invisible.
#
# That matters more than a normal test gap. The persona file IS the anti-rubber-stamp mechanism
# (rule 02: "a review that agrees to be agreeable is a failed review"). A hollow persona does not
# fail loudly — it yields a generic, uncalibrated critic that still returns a confident verdict.
# `load_persona`'s own docstring says it exists to stop exactly this one level up: "a silent
# fallback to Ruth Keller would make a `--persona` typo look like it worked". The typo path raises
# correctly. The empty-or-renamed-key path did not.
#
# Compounding it, the old parametrize list was hardcoded to three of the seven personas, so
# elena-ld, marcus-technical and priya-jobseeker were never loaded by any test — and a persona
# added later would not be either. The list below GLOBS the directory so new personas are covered
# the day they land.
# ══════════════════════════════════════════════════════════════════════════════════════════════

#: Every field `story_judge.load_persona` interpolates. Each must reach the rendered brief, or that
#: section of the critic's character is silently blank.
_RENDERED_KEYS = (
    "name", "archetype", "background", "goal", "home_domain",
    "quality_bar", "error_class", "verdict_question", "register",
)

_ALL_PERSONAS = sorted(p.stem for p in _PERSONA_DIR.glob("*.yaml"))


def test_the_persona_directory_is_not_empty() -> None:
    """POSITIVE CONTROL for the glob below: an empty directory would make every globbed test vacuously
    pass by collecting zero cases, which is the same class of hole this section exists to close."""
    assert len(_ALL_PERSONAS) >= 7, f"expected the 7 known personas, globbed {_ALL_PERSONAS}"


@pytest.mark.parametrize("name", _ALL_PERSONAS)
def test_every_persona_renders_its_own_content_not_just_scaffolding(name: str) -> None:
    """The persona's OWN words must reach the brief. Fails if a key is renamed, emptied or dropped —
    the drift the two length/anchor assertions could not see."""
    import yaml

    raw = yaml.safe_load((_PERSONA_DIR / f"{name}.yaml").read_text()) or {}
    out = story_judge.load_persona(name)
    missing = []
    for key in _RENDERED_KEYS:
        val = str(raw.get(key) or "").strip()
        assert val, f"{name}.yaml has no non-empty {key!r} — the brief renders that section blank"
        # First line is enough: several fields are multi-line prose the template re-wraps.
        probe = val.splitlines()[0].strip()
        if probe and probe not in out:
            missing.append(key)
    assert not missing, (
        f"{name}: {missing} present in the YAML but absent from the rendered brief — "
        f"load_persona is dropping them (renamed key, or template drift)"
    )


@pytest.mark.parametrize("name", _ALL_PERSONAS)
def test_every_persona_renders_its_rejection_triggers(name: str) -> None:
    """`rejection_triggers` is the list that makes the critic REFUSE. It renders through a separate
    join, so it drifts independently of the scalar fields above."""
    import yaml

    raw = yaml.safe_load((_PERSONA_DIR / f"{name}.yaml").read_text()) or {}
    triggers = [str(t).strip() for t in (raw.get("rejection_triggers") or []) if str(t).strip()]
    assert triggers, f"{name}.yaml has no rejection_triggers — the critic refuses nothing"
    out = story_judge.load_persona(name)
    absent = [t for t in triggers if t.splitlines()[0].strip() not in out]
    assert not absent, f"{name}: rejection triggers missing from the rendered brief: {absent}"



def test_a_malformed_persona_file_fails_by_naming_itself(tmp_path, monkeypatch) -> None:
    """#172 code-critic finding 8. A persona that parses to a list or a bare scalar used to escape
    `load_persona`'s careful contract with `AttributeError: 'list' object has no attribute 'get'` —
    which never names the file, making a malformed persona HARDER to diagnose than a typo'd name.
    Same failure mode as the FileNotFoundError beside it, so it gets the same treatment."""
    import yaml  # noqa: F401 — the loader imports it lazily; fail early here if it is absent

    for body, kind in (("- a\n- b\n", "list"), ("just a string\n", "str")):
        probe = _PERSONA_DIR / "zz-malformed-probe.yaml"
        probe.write_text(body)
        try:
            with pytest.raises(ValueError, match=r"parsed to \w+, not a mapping"):
                story_judge.load_persona("zz-malformed-probe")
        finally:
            probe.unlink(missing_ok=True)
        assert not probe.exists(), f"{kind} probe left behind"


def test_the_typo_path_still_raises_and_still_lists_what_exists() -> None:
    """CONTROL for the test above: widening the error handling must not swallow the typo contract
    `load_persona`'s docstring depends on — a silent fallback would make a `--persona` typo look
    like it worked."""
    with pytest.raises(FileNotFoundError) as e:
        story_judge.load_persona("nope-not-a-persona")
    assert "nadia-story-listener" in str(e.value), "the error no longer lists available personas"
