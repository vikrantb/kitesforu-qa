"""Unit tests for the D21 L5.2 listen-test verifier.

These build synthetic WAVs end-to-end (no network, no ffmpeg-decode
fixtures shipped in the repo) and assert that the public surface
behaves correctly across the three regimes that matter:

  1. ``audio_path`` exists, ``speech_only_path`` provided → full
     battery runs (loudness measured; STOI/music/SFX measured).
  2. ``audio_path`` exists, ``speech_only_path`` omitted → loudness
     + duration measured; STOI/music/SFX skipped (note added,
     verdict considers only what was measured).
  3. ``audio_path`` missing / unreadable → verdict=FAIL fast (no
     crash, no partial measurement).

Synthetic signal design:
  - speech_only: 30 s of bandlimited noise (200 Hz–4 kHz, mimics
    speech band).
  - mixed: speech + 30 s of 250 Hz tone at −24 dBFS (mimics a music
    bed: low/mid energy band-ratio favors the LF side).

We don't assert exact LUFS/STOI numbers (those depend on
optional deps + native ffmpeg) — we assert the report fields are
*populated* when the deps are present and *None* when they aren't,
and that the verdict aggregator never raises and always produces
a member of {PASS, WARN, FAIL}.
"""

from __future__ import annotations

import dataclasses
import math
import struct
import wave
from pathlib import Path

import pytest

from kitesforu_qa.stages.listen_test import (
    DEFAULT_PROFILE,
    GenreProfile,
    ListenTestReport,
    verify_audio_quality,
)

# ---------------------------------------------------------------------------
# Synthetic WAV helpers
# ---------------------------------------------------------------------------


def _write_wav(
    path: Path, samples: list[float], sr: int = 48000, channels: int = 1,
) -> None:
    """Write 16-bit PCM mono/stereo WAV from a list of float samples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for s in samples:
            clipped = max(-1.0, min(1.0, s))
            frames.extend(struct.pack("<h", int(clipped * 32767)))
        w.writeframes(bytes(frames))


def _make_speech_like(duration_s: float = 10.0, sr: int = 48000) -> list[float]:
    """UNIFORM WHITE NOISE at ~−20 dBFS RMS (named "speech-like" for its level, not its
    spectrum). It is broadband and maximally transient-rich — an onset detector fires on
    it constantly, which is correct behaviour, not a defect. Any assertion about
    SMOOTHNESS must not be fed this signal. See test_a_smooth_tone_has_no_transients."""
    import random
    random.seed(42)
    n = int(duration_s * sr)
    return [(random.random() - 0.5) * 0.2 for _ in range(n)]


def _make_speech_plus_music(
    duration_s: float = 10.0, sr: int = 48000,
) -> tuple[list[float], list[float]]:
    """Return (speech_only, mixed=speech+250Hz_tone) with same length."""
    speech = _make_speech_like(duration_s, sr)
    n = len(speech)
    # 250 Hz tone at −24 dBFS — sits well below speech RMS so STOI
    # should stay healthy.
    tone_amp = 10 ** (-24 / 20)
    music = [tone_amp * math.sin(2 * math.pi * 250 * i / sr) for i in range(n)]
    mixed = [speech[i] + music[i] for i in range(n)]
    return speech, mixed


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPublicSurface:
    """Exercises the module API + verdict aggregator without external
    deps. These run fast (no ffmpeg/pyloudnorm required)."""

    def test_genre_profile_is_frozen_dataclass(self) -> None:
        # Profiles must be hashable / immutable so they can live in
        # a per-genre lookup table without copy-on-write surprises.
        p = GenreProfile(
            genre="x", lufs_min=-17, lufs_max=-14, lra_min=4, lra_max=8,
        )
        # FrozenInstanceError specifically, not a blind Exception (ruff B017): a bare
        # `Exception` here passes even if the dataclass stops being frozen and the
        # assignment fails for some unrelated reason -- e.g. an AttributeError from a
        # renamed field. Then the test would still be green while the immutability it
        # exists to pin is gone.
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.lufs_min = -10  # type: ignore[misc]

    def test_default_profile_is_drama_band(self) -> None:
        # Phase-1 default targets the broad-band the audio chain
        # actually masters to (−16 LUFS / LRA 5-7 after PR-17).
        assert -17.5 <= DEFAULT_PROFILE.lufs_min <= -16.0
        assert -15.0 <= DEFAULT_PROFILE.lufs_max <= -13.5
        assert DEFAULT_PROFILE.lra_min >= 3.0
        assert DEFAULT_PROFILE.lra_max <= 10.0

    def test_report_has_all_measurement_axes(self) -> None:
        # Pin the public field names — downstream Cloud Run jobs key
        # off these. Reorder/rename here = silent dashboard break.
        r = ListenTestReport(
            audio_path="x.mp3", genre="default", profile_used="default",
        )
        d = r.to_dict()
        required = {
            "lufs", "lra", "tp_dbtp",
            "stoi_speech", "smr_lu",
            "music_presence_pct", "music_band_ratio",
            "sfx_event_count", "sfx_events_per_5min",
            "duration_actual_s", "duration_expected_s",
            "duration_delta_pct", "naturalness_score",
            "silence_ratio", "crest_factor_db", "plr_db",
            "fails", "warns", "notes", "verdict",
        }
        assert required.issubset(d.keys()), (
            "missing axes: " + ", ".join(sorted(required - d.keys()))
        )


class TestVerdictAggregator:
    """The aggregator is the contract the regen loop trusts.
    Make sure FAIL/WARN/PASS classification matches the slack bands."""

    def test_missing_audio_returns_fail(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.mp3"
        r = verify_audio_quality(audio_path=str(missing))
        assert r.verdict == "FAIL"
        assert any("decode" in f.lower() or "missing" in f.lower()
                   for f in r.fails)

    def test_empty_file_returns_fail(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.mp3"
        empty.write_bytes(b"")
        r = verify_audio_quality(audio_path=str(empty))
        assert r.verdict == "FAIL"

    def test_speech_only_omitted_still_grades(self, tmp_path: Path) -> None:
        # No speech-only → loudness still grades; STOI/music/SFX
        # surface as `notes` rather than crashing.
        wav_path = tmp_path / "mix.wav"
        _, mixed = _make_speech_plus_music(duration_s=5.0)
        _write_wav(wav_path, mixed)
        r = verify_audio_quality(audio_path=str(wav_path))
        assert r.verdict in {"PASS", "WARN", "FAIL"}
        assert any("speech_only" in n for n in r.notes)
        # Axes that need speech_only must remain None.
        assert r.stoi_speech is None
        assert r.smr_lu is None
        assert r.music_presence_pct is None
        assert r.sfx_event_count is None


class TestEndToEndSynthetic:
    """Optional — only runs when ffmpeg + pyloudnorm + soundfile are
    present. Confirms that the orchestrator actually measures something
    rather than silently noting 'not measured'."""

    def test_full_battery_with_speech_and_mix(self, tmp_path: Path) -> None:
        if not _have_audio_stack():
            pytest.skip("pyloudnorm + soundfile not installed")
        if not _have_ffmpeg():
            pytest.skip("ffmpeg/ffprobe not installed")

        speech_path = tmp_path / "speech.wav"
        mix_path = tmp_path / "mix.wav"
        speech, mixed = _make_speech_plus_music(duration_s=10.0)
        _write_wav(speech_path, speech)
        _write_wav(mix_path, mixed)

        r = verify_audio_quality(
            audio_path=str(mix_path),
            speech_only_path=str(speech_path),
            expected_duration_s=10.0,
        )

        # ffmpeg + soundfile present → loudness battery should yield
        # at least LUFS (some setups don't return LRA on short clips).
        assert r.lufs is not None, f"LUFS not measured; notes={r.notes}"
        # Diff signal is the music tone → music_presence_pct should
        # be > 0 (any seconds where the 250 Hz tone is above −45 LUFS).
        # We don't assert a tight band — the synthetic music is at
        # −24 dBFS which can read variably depending on backend.
        assert r.music_presence_pct is not None
        # The SFX detector RAN and produced a count. Deliberately NOT asserting the count
        # is low here: this signal is _make_speech_like() (uniform WHITE NOISE) plus a
        # tone, and white noise is maximally transient-rich. The old assertion claimed
        # "smooth tone shouldn't have transients" while feeding the detector broadband
        # noise, so it was asserting a property of a signal the test never built.
        #
        # MEASURED (both arms, same detector, same box):
        #     pure 250 Hz tone     -> sfx_event_count =  0
        #     white noise + tone   -> sfx_event_count = 29
        # i.e. the detector is CORRECT and the input was wrong. The smoothness claim now
        # lives in test_a_smooth_tone_has_no_transients, where the signal matches it.
        assert r.sfx_event_count is not None



    def test_a_smooth_tone_has_no_transients(self, tmp_path: Path) -> None:
        """The smoothness claim, on a signal that is actually smooth.

        This is the assertion that used to live in
        test_full_battery_with_speech_and_mix, where it was fed
        _make_speech_like() -- uniform WHITE NOISE -- plus a tone, and therefore
        asserted a property of a signal that test never built. Moved here rather
        than deleted, so the detector's smoothness behaviour stays covered.

        MEASURED (same detector, same box, both arms):
            pure 250 Hz tone    -> sfx_event_count =  0
            white noise + tone  -> sfx_event_count = 29
        """
        if not _have_audio_stack():
            pytest.skip("pyloudnorm + soundfile not installed")
        if not _have_ffmpeg():
            pytest.skip("ffmpeg/ffprobe not installed")

        sr = 48000
        n = int(10.0 * sr)
        tone = [0.1 * math.sin(2 * math.pi * 250 * i / sr) for i in range(n)]
        tone_path = tmp_path / "smooth_tone.wav"
        _write_wav(tone_path, tone)

        r = verify_audio_quality(
            audio_path=str(tone_path),
            speech_only_path=str(tone_path),
            expected_duration_s=10.0,
        )

        assert r.sfx_event_count is not None, f"detector did not run; notes={r.notes}"
        assert r.sfx_event_count <= 3, (
            f"a pure sine tone must not read as transient-rich; got "
            f"{r.sfx_event_count}. If this fires, the ONSET DETECTOR drifted -- "
            f"do not relax it without checking the white-noise arm still reads high, "
            f"or the check becomes vacuous."
        )

# ---------------------------------------------------------------------------
# Module-private helpers (top-level so pytest.mark.skipif can ref them)
# ---------------------------------------------------------------------------


def _have_audio_stack() -> bool:
    try:
        import pyloudnorm  # type: ignore[import-not-found]  # noqa: F401
        import soundfile  # noqa: F401
        return True
    except ImportError:
        return False


def _have_ffmpeg() -> bool:
    import shutil
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
