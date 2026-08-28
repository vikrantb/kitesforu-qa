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


# ===========================================================================
# HOP 1 IS DELIVERY-INDEPENDENT — the population bug (2026-08-27)
# ===========================================================================


class TestHop1NeedsNoDeliveredAudio:
    """The section sourced its population from the DELIVERY rows, which skip
    jobs with no rendered audio. Hop 1 compares the persona YAML against the
    CONTRACT and never reads tts_segment_logs, so a job that was cast but never
    rendered is still perfectly checkable — and 13 such jobs / 27 entries (3.2%
    of the denominator) were being silently dropped.

    MEASURED 2026-08-27, full unordered scan of 4155 docs:
        jobs with a cast contract carrying voice_ids : 420
        of those, WITH delivered segments            : 407   <- the old population
        of those, WITHOUT                            :  13
        contract entries, all such jobs              : 848
        contract entries, delivered jobs only        : 821   <- the old denominator

    That 848 also reconciles a standing disagreement between two lanes' scans:
    848 = 820 (the old reported figure) + 27 (this bug) + 1 (a label-normalisation
    collision, pinned below).
    """

    def test_a_job_that_never_delivered_audio_still_counts(self):
        job = _job({"Host1": {
            "persona_id": "p", "provider": "inworld", "voice_id": "Ronan",
        }})
        assert "tts_segment_logs" not in job, "premise: this job rendered nothing"
        entry = _census().hop1_entry(job)
        assert entry is not None, (
            "a cast-but-never-rendered job is checkable for hop 1 — excluding it "
            "narrows the denominator by 3.2% while the section still prints it as "
            "the population"
        )
        assert entry["cast"]["host1"]["voice_id"] == "Ronan"

    def test_an_uncast_job_contributes_nothing(self):
        assert _census().hop1_entry({}) is None
        assert _census().hop1_entry({"tts_segment_logs": [{"speaker": "x"}]}) is None


class TestLabelCollisionIsVisibleNotSilent:
    """persona_cast keys by NORMALISED label, so two raw labels that normalise to
    one key collapse. Fleet-wide that is exactly ONE entry (job 8f1c4416 carries
    both ``_narrator`` and ``Narrator``) — but a silent 1 is how a silent 100
    starts, so the raw count is reported alongside it."""

    def test_raw_count_exceeds_the_normalised_count_on_a_collision(self):
        job = _job({
            "_narrator": {"persona_id": "p", "provider": "inworld", "voice_id": "Claire"},
            "Narrator": {"persona_id": "p", "provider": "inworld", "voice_id": "Carter"},
        })
        assert _census().raw_contract_entries(job) == 2
        assert len(_census().persona_cast(job)) == 1, "both normalise to 'narrator'"
        assert _census().hop1_entry(job)["raw_entries"] == 2, (
            "the raw count must survive so the section can report the difference"
        )

    def test_no_collision_means_the_counts_agree(self):
        job = _job({
            "Host1": {"persona_id": "p", "provider": "inworld", "voice_id": "Ronan"},
            "Host2": {"persona_id": "q", "provider": "inworld", "voice_id": "Zoe"},
        })
        assert _census().raw_contract_entries(job) == len(_census().persona_cast(job)) == 2

    def test_entries_without_a_voice_id_are_not_counted_raw_either(self):
        job = _job({"Host1": {"persona_id": "p", "provider": "inworld"}})
        assert _census().raw_contract_entries(job) == 0


# --- the early return hid hop 1 when nothing in the window had DELIVERED -----
#
# qa#146 made `hop1_entry` delivery-independent. `main()` then undid it one
# layer up: `if not rows: return`, where `rows` is the DELIVERY population. A
# window whose jobs are CAST but not yet RENDERED reported "nothing in this
# window" while carrying a perfectly checkable contract.
#
# Hit live on 2026-08-28 measuring a post-deploy job that was still running:
# the census said 0 jobs while the scanned doc count had already risen by one.
#
# The return was LOAD-BEARING — the delivery rates divide by len(rows) and an
# empty window raised ZeroDivisionError — so the divisions are guarded rather
# than the guard removed.


def _main_src():
    import inspect
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "vic", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "voice_identity_census.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return inspect.getsource(mod.main)


def test_the_delivery_rates_are_zero_safe():
    """The reason the early return existed. Both rates divide by len(rows)."""
    src = _main_src()
    assert "len(rows)" in src, "positive control: the rates do divide by len(rows)"
    for frag in ("100*len(frac)/max(1, len(rows))", "100*len(fb)/max(1, len(rows))"):
        assert frag in src, f"unguarded division would raise on an empty window: {frag}"


def test_hop1_is_not_skipped_merely_because_nothing_delivered():
    """The bare `if not rows: return` is what hid hop 1. It must also require
    that there is no CAST row, since hop 1 needs no delivery."""
    src = _main_src()
    assert "if not rows and not cast_rows:" in src, (
        "the early return must not fire on delivery alone — hop 1 needs none")
    assert "if not rows:\n" in src, "the delivery-only branch must still exist"
