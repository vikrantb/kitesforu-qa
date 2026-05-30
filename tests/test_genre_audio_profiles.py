"""Unit tests for the D21 L5.3 per-genre profile table.

Pin tests — these enforce the SSOT contract: the bands here must agree
with workers/profiles/genre_palette/*.yaml. If the renderer's target
LRA changes, this file should change too (a green build with diverged
bands = silent confidence theater).
"""

from __future__ import annotations

import pytest

from kitesforu_qa.profiles import GENRE_AUDIO_PROFILES, get_profile
from kitesforu_qa.profiles.genre_audio_profiles import (
    _DEFAULT,
    _HORROR,
)
from kitesforu_qa.stages.listen_test import GenreProfile


class TestTableShape:
    """Don't lose profiles to a careless edit; downstream callers (the
    listen-test orchestrator + L5.4 live pin) iterate this dict."""

    def test_at_least_seven_genre_profiles_ship(self) -> None:
        # L5.3 spec: 7 genres + default. Don't accidentally collapse
        # them with a dict-comprehension typo.
        assert len(GENRE_AUDIO_PROFILES) >= 8

    def test_every_profile_is_a_genre_profile(self) -> None:
        for key, p in GENRE_AUDIO_PROFILES.items():
            assert isinstance(p, GenreProfile), f"{key!r} → {type(p).__name__}"
            assert p.genre == key, (
                f"genre slug drift: dict key={key!r} != profile.genre={p.genre!r}"
            )

    def test_required_genres_present(self) -> None:
        # These are what the architect actually emits today.
        required = {"default", "horror", "thriller", "drama",
                    "comedy", "romance", "mystery", "bedtime"}
        missing = required - set(GENRE_AUDIO_PROFILES.keys())
        assert not missing, f"missing required genres: {sorted(missing)}"


class TestBandSanity:
    """Numeric guard-rails — catches a typo like ``lufs_min=-1.7``
    (forgot the leading minus on the LU figure) or an inverted band."""

    @pytest.mark.parametrize("genre", list(GENRE_AUDIO_PROFILES.keys()))
    def test_lufs_band_ordered_and_loud_enough(self, genre: str) -> None:
        p = GENRE_AUDIO_PROFILES[genre]
        assert p.lufs_min < p.lufs_max, (
            f"{genre}: lufs_min ({p.lufs_min}) >= lufs_max ({p.lufs_max})"
        )
        # Podcasts mastered louder than −12 LUFS or quieter than −30
        # LUFS are typos or unintentional.
        assert -30.0 <= p.lufs_min <= -12.0, f"{genre} lufs_min OOB"
        assert -30.0 <= p.lufs_max <= -12.0, f"{genre} lufs_max OOB"

    @pytest.mark.parametrize("genre", list(GENRE_AUDIO_PROFILES.keys()))
    def test_lra_band_ordered_and_reachable(self, genre: str) -> None:
        p = GENRE_AUDIO_PROFILES[genre]
        assert p.lra_min < p.lra_max
        # Anything > 12 LU is unreachable for TTS speech (the
        # PR-14/15/16 LRA chase proved this empirically — bottomed at
        # 4.0 LU even after killing every compression layer).
        assert 0.0 <= p.lra_max <= 12.0, (
            f"{genre} lra_max={p.lra_max} above empirical ceiling"
        )

    @pytest.mark.parametrize("genre", list(GENRE_AUDIO_PROFILES.keys()))
    def test_music_presence_band_ordered(self, genre: str) -> None:
        p = GENRE_AUDIO_PROFILES[genre]
        assert 0.0 <= p.music_presence_pct_min <= p.music_presence_pct_max <= 1.0

    @pytest.mark.parametrize("genre", list(GENRE_AUDIO_PROFILES.keys()))
    def test_sfx_density_band_ordered(self, genre: str) -> None:
        p = GENRE_AUDIO_PROFILES[genre]
        assert 0 <= p.sfx_events_per_5min_min <= p.sfx_events_per_5min_max

    @pytest.mark.parametrize("genre", list(GENRE_AUDIO_PROFILES.keys()))
    def test_true_peak_is_negative_dbtp(self, genre: str) -> None:
        # Broadcast safety: TP ceiling must be ≤ -0.5 dBTP (we always
        # use -1.0 except bedtime which is -3.0). Positive dBTP = bug.
        p = GENRE_AUDIO_PROFILES[genre]
        assert p.tp_max_dbtp <= -0.5, f"{genre} tp_max={p.tp_max_dbtp}"


class TestProductionSsotAlignment:
    """The verifier exists ONLY to confirm the renderer hit its own
    target. So the bands here MUST agree with the shipped YAML profiles
    in workers/profiles/genre_palette/. These pins catch SSOT drift."""

    def test_horror_lra_matches_pr17_shipped(self) -> None:
        # PR-17 shipped lra_target_lu: [5, 7] on
        # workers/profiles/genre_palette/horror.yaml (audio-overhaul
        # live-verify 2026-05-29). The verifier must accept the band
        # the renderer is actually rendering to.
        assert _HORROR.lra_min == 5.0
        assert _HORROR.lra_max == 7.0

    def test_horror_lufs_matches_yaml(self) -> None:
        # horror.yaml: lufs_target: -18 → verifier band [-19, -17]
        # (centered on target with 1 LU slack each side).
        assert _HORROR.lufs_min <= -18.0 <= _HORROR.lufs_max

    def test_default_lufs_matches_yaml(self) -> None:
        # _default.yaml: lufs_target: -16 → verifier band [-17, -15].
        assert _DEFAULT.lufs_min <= -16.0 <= _DEFAULT.lufs_max


class TestResolver:
    """get_profile() is the single entry point. Aliases protect callers
    from the architect's vocab churn."""

    def test_exact_match(self) -> None:
        p = get_profile("horror")
        assert p is _HORROR

    def test_case_and_whitespace_insensitive(self) -> None:
        assert get_profile(" HORROR ") is _HORROR
        assert get_profile("Horror") is _HORROR

    def test_alias_true_crime_to_mystery(self) -> None:
        assert get_profile("true_crime").genre == "mystery"
        assert get_profile("true-crime").genre == "mystery"

    def test_alias_love_to_romance(self) -> None:
        # The architect's "love" emission was the romance-bug that
        # workers PR #612 fixed; the verifier should accept either.
        assert get_profile("love").genre == "romance"

    def test_alias_audio_overview_to_default(self) -> None:
        # NotebookLM-style explainer routes to the house default profile.
        assert get_profile("audio_overview") is _DEFAULT

    def test_unknown_genre_returns_default(self) -> None:
        assert get_profile("interpretive-dance-podcast") is _DEFAULT

    def test_empty_string_returns_default(self) -> None:
        assert get_profile("") is _DEFAULT
        assert get_profile("   ") is _DEFAULT


class TestEndToEndWithVerifier:
    """Smoke test: each profile can be passed into the listen-test
    orchestrator without crashing the verdict aggregator."""

    @pytest.mark.parametrize("genre", list(GENRE_AUDIO_PROFILES.keys()))
    def test_verifier_accepts_each_profile(
        self, genre: str, tmp_path
    ) -> None:
        from kitesforu_qa.stages.listen_test import verify_audio_quality
        missing = tmp_path / "nope.mp3"
        # File missing → FAIL fast (we're verifying the profile
        # doesn't crash the verdict aggregator, not the measurement).
        r = verify_audio_quality(
            audio_path=str(missing),
            genre=genre,
            profile=get_profile(genre),
        )
        assert r.verdict == "FAIL"
        assert r.profile_used == genre
