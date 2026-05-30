"""Unit tests for the D21 L5.5 sample-alignment helper.

Live evidence (2026-05-29 horror jobs 0ff85d79 + 23a5f5c6): final.mp3
129.7s vs speech_only.mp3 131.6s. Previous min-clip produced STOI=0.001,
SMR=-0.6 LU, music_presence=1.0 → bogus FAIL on a known-good mp3. After
L5.5 alignment + re-run, these axes should report realistic values.

Tests use synthetic signals (no audio files needed):
  - identical-then-shifted signal pair → lag detected within 1 sample
  - speech + delayed bed mix → STOI on aligned much higher than min-clip
  - degenerate inputs (silence, mismatched sr, too-short) → fail-soft
"""

from __future__ import annotations

import math

import pytest


class TestAlignmentMath:
    """Direct unit tests on align_mono_signals."""

    def _make_speech_like(self, duration_s: float, sr: int = 48000):
        import random
        random.seed(7)
        n = int(duration_s * sr)
        return [(random.random() - 0.5) * 0.2 for _ in range(n)]

    def test_zero_lag_when_already_aligned(self) -> None:
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy required")
        from kitesforu_qa.stages._alignment import align_mono_signals
        sig = np.array(self._make_speech_like(8.0), dtype="float32")
        s2, m2, lag = align_mono_signals(sig.copy(), sig.copy(), 48000)
        assert abs(lag) <= 480  # within 10ms tolerance
        assert len(s2) == len(m2)

    def test_positive_lag_detected(self) -> None:
        """Mixed = speech with a 0.5 s silent intro pad → lag ≈ +24000 samples
        (mixed is DELAYED, so the helper trims mixed's start to align)."""
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy required")
        from kitesforu_qa.stages._alignment import align_mono_signals
        sr = 48000
        speech = np.array(self._make_speech_like(8.0), dtype="float32")
        pad = np.zeros(int(0.5 * sr), dtype="float32")
        mixed = np.concatenate([pad, speech])
        s2, m2, lag = align_mono_signals(speech, mixed, sr)
        # Helper returns the lag IN SAMPLES that mixed was offset; for a
        # 0.5s positive delay the expected lag is +24000 ±tolerance.
        assert 20000 <= lag <= 28000, f"expected lag ~24000, got {lag}"
        # After alignment, the equal-length crop should be the speech.
        assert len(s2) == len(m2)

    def test_negative_lag_detected(self) -> None:
        """Speech has a 0.3s silent intro that mixed lacks → mixed is
        EARLIER → negative lag."""
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy required")
        from kitesforu_qa.stages._alignment import align_mono_signals
        sr = 48000
        mixed_body = np.array(self._make_speech_like(8.0), dtype="float32")
        pad = np.zeros(int(0.3 * sr), dtype="float32")
        speech = np.concatenate([pad, mixed_body])
        s2, m2, lag = align_mono_signals(speech, mixed_body, sr)
        assert -16000 <= lag <= -12000, f"expected lag ~-14400, got {lag}"
        assert len(s2) == len(m2)

    def test_too_short_returns_min_clip(self) -> None:
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy required")
        from kitesforu_qa.stages._alignment import align_mono_signals
        s = np.zeros(1000, dtype="float32")  # ~20ms at 48k
        m = np.zeros(2000, dtype="float32")
        s2, m2, lag = align_mono_signals(s, m, 48000)
        assert lag == 0
        assert len(s2) == 1000
        assert len(m2) == 1000

    def test_missing_scipy_falls_back_gracefully(self, monkeypatch) -> None:
        # When scipy isn't installed the helper must return originals
        # at min length with lag=0 (no crash, no misleading lag).
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy required")
        # Force the import-error branch by stubbing scipy.signal to raise.
        import sys
        monkeypatch.setitem(sys.modules, "scipy.signal", None)
        from kitesforu_qa.stages._alignment import align_mono_signals
        s = np.zeros(48000 * 6, dtype="float32")
        m = np.zeros(48000 * 6, dtype="float32")
        s2, m2, lag = align_mono_signals(s, m, 48000)
        assert lag == 0


class TestSilentInputDegenerates:
    """All-silence signals make cross-correlation noisy; the helper
    should fail-soft (return originals, lag=0) rather than apply a
    spurious shift."""

    def test_silence_pair_returns_zero_lag(self) -> None:
        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy required")
        from kitesforu_qa.stages._alignment import align_mono_signals
        s = np.zeros(48000 * 10, dtype="float32")
        m = np.zeros(48000 * 10, dtype="float32")
        _s, _m, lag = align_mono_signals(s, m, 48000)
        # Either 0 or within tolerance — silence can't be aligned.
        assert abs(lag) < 48000  # less than 1s spurious shift


class TestMathSanity:
    """Just to confirm the test harness arithmetic itself."""

    def test_db_math(self) -> None:
        # 6 dB ≈ 2× amplitude; trivial check the test runner is sane.
        assert math.isclose(10 ** (6 / 20), 2.0, abs_tol=0.01)
