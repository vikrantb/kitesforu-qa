"""HOP 1 — the persona stamp, and the two ways of reading it silently wrong.

The chain from a persona to an audible voice is TWO hops, and conflating them
produced a wrong answer twice on 2026-08-27:

    hop 1  persona YAML voice -> voice_cast.contract.voice_map[label].voice_id
    hop 2  contract voice_id  -> tts_segment_logs voice_id

This file pins hop 1's helpers. Both of the mistakes below were made for real
before these tests existed.

MISTAKE 1 — reading ONE key shape. Personas do not declare voices consistently:
the ``inworld`` block says ``voice:`` and the ``elevenlabs`` block says
``voice_id:`` (measured over personas/*.yaml: 50 inworld ``voice``, 29
elevenlabs ``voice_id``). A reader of only ``voice`` sees zero checkable
ElevenLabs entries and concludes "substitution is exclusively an Inworld
phenomenon". With both key shapes read, ElevenLabs substitutes at 69% and
Inworld at 70% — while OpenAI is 0%. The blind spot did not just lose data, it
inverted the finding.

MISTAKE 2 — comparing ACROSS providers. A persona declares a voice PER
provider, so an ``inworld`` contract entry may only be checked against the
persona's ``inworld`` declaration. Comparing an inworld contract voice against
an elevenlabs declaration reports every provider swap as a substitution — the
same bare-id error this census warns about for delivery, where an id means
nothing without its provider.

A third rule the tests pin: UNCHECKABLE IS NOT MATCHING. An entry with no
persona_id, no provider, or a persona this directory does not define is
excluded from BOTH numerator and denominator, never counted as agreement.
"""

import importlib.util
import pathlib

import pytest

_SPEC = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "voice_identity_census.py"
)


def _census():
    spec = importlib.util.spec_from_file_location("voice_identity_census", _SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _job(voice_map):
    return {"voice_cast": {"contract": {"voice_map": voice_map}}}


# ===========================================================================
# persona_cast — the stamp itself
# ===========================================================================


class TestPersonaCast:
    def test_extracts_the_triple_and_normalises_the_label(self):
        cast = _census().persona_cast(
            _job({"Prof. James Okafor": {
                "persona_id": "prof-james-okafor",
                "provider": "inworld",
                "voice_id": "Ronan",
            }})
        )
        assert cast == {"profjamesokafor": {
            "persona_id": "prof-james-okafor",
            "provider": "inworld",
            "voice_id": "Ronan",
        }}

    def test_an_entry_with_no_voice_id_is_dropped(self):
        # Nothing to compare — it must not inflate the denominator.
        assert _census().persona_cast(
            _job({"Host1": {"persona_id": "p", "provider": "inworld"}})
        ) == {}

    def test_an_entry_with_no_persona_id_is_KEPT_with_none(self):
        """Dropping these would make stamp coverage look like 100% when it is
        not. The coverage line has to be able to report the gap."""
        cast = _census().persona_cast(
            _job({"Host1": {"provider": "inworld", "voice_id": "Ashley"}})
        )
        assert cast["host1"]["persona_id"] is None

    def test_a_missing_contract_is_empty_not_an_error(self):
        assert _census().persona_cast({}) == {}
        assert _census().persona_cast({"voice_cast": {"contract": {}}}) == {}


# ===========================================================================
# load_persona_voices — MISTAKE 1
# ===========================================================================


class TestBothKeyShapesAreRead:
    def test_reads_voice_AND_voice_id(self, tmp_path):
        """The premise test for the inverted finding described above."""
        pytest.importorskip("yaml")
        (tmp_path / "p.yaml").write_text(
            "id: dr-sarah-chen\n"
            "voice_config:\n"
            "  inworld:\n"
            "    voice: Jessica\n"
            "  elevenlabs:\n"
            "    voice_id: 21m00Tcm4TlvDq8ikWAM\n"
            "  openai:\n"
            "    voice: nova\n"
        )
        got = _census().load_persona_voices(str(tmp_path))
        assert got["dr-sarah-chen"] == {
            "inworld": "Jessica",
            "elevenlabs": "21m00Tcm4TlvDq8ikWAM",
            "openai": "nova",
        }, "an elevenlabs declaration read as absent inverts the by-provider finding"

    def test_a_missing_directory_returns_empty_not_an_exception(self):
        # hop 1 is opt-in and cross-repo; it must degrade, never kill the scan.
        assert _census().load_persona_voices("/nonexistent/personas") == {}

    def test_a_malformed_yaml_does_not_kill_the_scan(self, tmp_path):
        pytest.importorskip("yaml")
        (tmp_path / "bad.yaml").write_text("id: x\n  : : not yaml : :\n")
        (tmp_path / "ok.yaml").write_text("id: good\nv:\n  inworld:\n    voice: Hades\n")
        got = _census().load_persona_voices(str(tmp_path))
        assert got.get("good") == {"inworld": "Hades"}


# ===========================================================================
# hop1_substitutions — MISTAKE 2, and uncheckable-is-not-matching
# ===========================================================================


class TestComparisonIsPerProvider:
    def test_a_substitution_within_one_provider_is_reported(self):
        rows = _census().hop1_substitutions(
            {"host1": {"persona_id": "p", "provider": "inworld", "voice_id": "Ronan"}},
            {"p": {"inworld": "Arthur"}},
        )
        assert rows == [("host1", "p", "Arthur", "Ronan")]

    def test_a_match_within_one_provider_is_silent(self):
        assert _census().hop1_substitutions(
            {"host1": {"persona_id": "p", "provider": "inworld", "voice_id": "Arthur"}},
            {"p": {"inworld": "Arthur"}},
        ) == []

    def test_a_DIFFERENT_provider_is_never_compared(self):
        """The persona was cast on OpenAI; its inworld declaration says nothing
        about that. Comparing them would report every provider swap as a
        substitution — MISTAKE 2."""
        assert _census().hop1_substitutions(
            {"host1": {"persona_id": "p", "provider": "openai", "voice_id": "nova"}},
            {"p": {"inworld": "Arthur"}},
        ) == []

    def test_each_provider_is_checked_against_its_own_declaration(self):
        rows = _census().hop1_substitutions(
            {
                "a": {"persona_id": "p", "provider": "inworld", "voice_id": "Ronan"},
                "b": {"persona_id": "p", "provider": "openai", "voice_id": "nova"},
            },
            {"p": {"inworld": "Arthur", "openai": "nova"}},
        )
        assert rows == [("a", "p", "Arthur", "Ronan")], "openai matched, inworld did not"


class TestUncheckableIsNotMatching:
    @pytest.mark.parametrize(
        "cast,voices",
        [
            # no persona_id on the entry
            ({"a": {"persona_id": None, "provider": "inworld", "voice_id": "X"}},
             {"p": {"inworld": "Y"}}),
            # no provider on the entry
            ({"a": {"persona_id": "p", "provider": None, "voice_id": "X"}},
             {"p": {"inworld": "Y"}}),
            # the persona is not defined in this directory
            ({"a": {"persona_id": "unknown", "provider": "inworld", "voice_id": "X"}},
             {"p": {"inworld": "Y"}}),
            # the persona defines no voice for THIS provider
            ({"a": {"persona_id": "p", "provider": "elevenlabs", "voice_id": "X"}},
             {"p": {"inworld": "Y"}}),
        ],
    )
    def test_silent_rather_than_counted_either_way(self, cast, voices):
        assert _census().hop1_substitutions(cast, voices) == []
