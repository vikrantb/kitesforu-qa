"""Sample-alignment helper for the listen-test verifier (D21 L5.5).

Bug surfaced 2026-05-29 (live verify on horror job 0ff85d79 / 23a5f5c6):
  speech_only.mp3 duration = 131.6 s
  final.mp3       duration = 129.7 s
  → 2 s offset (music_renderer pads intro/outro)

The verifier's previous ``min(len(s), len(m))`` clip simply truncated
both signals to the shorter length WITHOUT first aligning them. STOI
on time-misaligned signals collapses to ~0; SMR / music_presence /
SFX-transient axes all return garbage.

This module provides ``align_mono_signals(speech, mixed, sr)`` which
returns aligned ``(speech_aligned, mixed_aligned)`` of equal length,
correctly shifted so the speech onset in ``mixed`` lines up with the
speech in ``speech``.

Implementation: cross-correlation on a short (5 s) window of the
loudest portion of each signal. The lag at the correlation peak is
applied as a sample-shift to ``mixed`` before the truncate-to-common-
length step. Fail-soft: degenerate inputs return the original signals.

Performance: scipy.signal.fftconvolve handles the FFT; for a 5 s
window at 48 kHz that's 240k samples — well under 100 ms.
"""

from __future__ import annotations


def align_mono_signals(
    speech: object, mixed: object, sr: int,
    *,
    window_seconds: float = 2.0,
    max_offset_seconds: float = 30.0,
) -> tuple[object, object, int]:
    """Time-align ``mixed`` to ``speech`` via cross-correlation peak lag.

    Both signals MUST be 1-D mono numpy arrays of the same sample rate.
    Returns ``(speech_aligned, mixed_aligned, lag_samples)`` where the
    aligned arrays are equal-length (the shorter of the two after
    shift) and ``lag_samples`` is the integer shift applied to mixed
    (positive = mixed was delayed vs speech and got truncated from
    the start; negative = mixed was earlier and got front-padded with
    silence). A 0 lag means the signals were already aligned.

    Fail-soft: returns ``(speech, mixed, 0)`` with the original min-
    length clip when scipy/numpy are missing, when one signal is
    shorter than the analysis window, or when the correlation peak
    falls outside ``max_offset_seconds`` (likely a false-positive on
    a music-only intro).
    """
    try:
        import numpy as np
        from scipy.signal import fftconvolve
    except ImportError:
        return speech, mixed, 0

    n_min = int(min(len(speech), len(mixed)))  # type: ignore[arg-type]
    if n_min < int(sr):
        return speech[:n_min], mixed[:n_min], 0  # type: ignore[index]

    try:
        s_arr = np.asarray(speech, dtype="float32")
        m_arr = np.asarray(mixed, dtype="float32")
    except Exception:
        return speech, mixed, 0

    # ── First-audible-onset alignment ───────────────────────────────────
    # Anchor on the first speech onset in each signal — that's the most
    # reliable feature across the music_renderer's intro/outro padding.
    # Cross-correlation on the loudest-N-seconds window FAILS when the
    # loudest moment in each signal happens at different absolute times
    # (the music_renderer can splice extra music mid-program, shifting
    # the loudest-speech timestamp relative to the speech-only timeline).
    def _first_onset_idx(arr, threshold_dbfs: float = -40.0) -> int:
        """Return the sample index of the first 100ms window above the
        threshold. -40 dBFS catches speech onsets but skips room tone."""
        win_n = max(1, int(0.1 * sr))
        n_windows = len(arr) // win_n
        thresh_lin = 10 ** (threshold_dbfs / 20.0)
        for i in range(n_windows):
            chunk = arr[i*win_n:(i+1)*win_n]
            rms = float(np.sqrt(np.mean(chunk.astype("float64") ** 2)))
            if rms > thresh_lin:
                return i * win_n
        return 0  # no onset detected → no shift

    try:
        s_onset = _first_onset_idx(s_arr)
        m_onset = _first_onset_idx(m_arr)
    except Exception:
        return speech, mixed, 0

    # If either signal has no detectable onset, bail (silence-dominated).
    if s_onset == 0 and m_onset == 0:
        s_rms = float(np.sqrt(np.mean(s_arr[:int(sr)].astype("float64") ** 2)))
        if s_rms < 1e-3:
            return speech[:n_min], mixed[:n_min], 0  # type: ignore[index]

    # Onset-based estimate (positive = mixed is delayed vs speech).
    lag_estimate = m_onset - s_onset
    max_offset = int(max_offset_seconds * sr)

    # ── Refine with a short cross-correlation around the estimate ──────
    # Take a 2s window starting at each onset, cross-correlate within a
    # ±0.5s slack to pin the lag at sample precision.
    refine_win = int(window_seconds * sr)
    refine_slack = int(0.5 * sr)
    try:
        s_win = s_arr[s_onset: s_onset + refine_win]
        m_lo = max(0, s_onset + lag_estimate - refine_slack)
        m_hi = min(len(m_arr), s_onset + lag_estimate + refine_win + refine_slack)
        m_win = m_arr[m_lo:m_hi]
        if len(s_win) >= int(0.5 * sr) and len(m_win) >= len(s_win):
            s_demean = s_win - float(np.mean(s_win))
            m_demean = m_win - float(np.mean(m_win))
            corr = fftconvolve(m_demean, s_demean[::-1], mode="full")
            peak = int(np.argmax(corr))
            lag_in_mwin = peak - (len(s_win) - 1)
            lag_samples = (m_lo + lag_in_mwin) - s_onset
        else:
            lag_samples = lag_estimate
    except Exception:
        lag_samples = lag_estimate

    if abs(lag_samples) > max_offset:
        # Out of plausible range — fall back to the simpler onset-only
        # estimate. If THAT's also bad, fall through to no-shift.
        lag_samples = lag_estimate if abs(lag_estimate) <= max_offset else 0

    # Apply the shift. Positive lag = mixed is DELAYED by lag_samples
    # (speech onset in mixed happens later) → trim mixed's start by
    # lag_samples so mixed[lag:] aligns with speech[0:].
    if lag_samples > 0:
        m_shifted = m_arr[lag_samples:]
        s_shifted = s_arr
    elif lag_samples < 0:
        # Mixed is EARLIER than speech → trim speech's start.
        s_shifted = s_arr[-lag_samples:]
        m_shifted = m_arr
    else:
        s_shifted = s_arr
        m_shifted = m_arr

    # Equal-length crop at the end so the helpers' min-clip is idempotent.
    n_final = int(min(len(s_shifted), len(m_shifted)))
    return s_shifted[:n_final], m_shifted[:n_final], int(lag_samples)


__all__ = ["align_mono_signals"]
