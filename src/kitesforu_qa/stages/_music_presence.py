"""Music-presence measurements for the listen-test verifier.

Spec from D21 research output: the verifier needs to know HOW MUCH
music actually rendered in the final mix — not what Firestore SAYS
was rendered. Two scalars answer this:

  - ``pct_active``: fraction of seconds where the music-bus energy
    (the diff between final-mix and speech-only) is above an audible
    floor (−45 LUFS short-term). Catches the 'cue_count=8 in Firestore
    but mp3 has no music' bug class (ecc83d7e Run 4).
  - ``band_ratio``: low-band energy (100-500 Hz) ÷ mid-band energy
    (500-3000 Hz) in the diff signal. Music has more low+low-mid
    energy than speech residuals; ratio &gt; 0.4 = music is present.

Both run on the diff signal `final_mix - speech_only`. The speech-only
upload (PR #737) makes this comparison clean; before that the verifier
had to demucs-isolate vocals from the final mix (noisier).

Fail-soft: missing deps → return ``None`` so the orchestrator falls
back to schema-field assertion. Never raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MusicPresence:
    """Output of :func:`measure_music_presence`."""

    pct_active: float           # fraction of episode where music bed is audible
    band_ratio: float           # E(100-500 Hz) / E(500-3000 Hz) of diff
    diff_lufs_integrated: float # integrated LUFS of the diff signal

    def to_dict(self) -> dict:
        return {
            "pct_active": round(self.pct_active, 3),
            "band_ratio": round(self.band_ratio, 3),
            "diff_lufs_integrated": round(self.diff_lufs_integrated, 2),
        }


def measure_music_presence(
    speech_wav_path: str, mixed_wav_path: str
) -> Optional[MusicPresence]:
    """Run the band-ratio + per-second active-fraction analysis on
    the diff signal between the speech-only and final-mix WAVs.

    Both WAVs MUST be the same sample rate and length-aligned within
    ~1s (the verifier crops to the shorter of the two). Returns None
    on missing deps, malformed input, or all-silence diff.
    """
    try:
        import numpy as np  # noqa: F401  (used implicitly by welch/sf via shared array shapes)
        import pyloudnorm as pyln  # type: ignore[import-not-found]
        import soundfile as sf
        from scipy.signal import welch
    except ImportError:
        return None

    try:
        s, sr_s = sf.read(speech_wav_path, dtype="float32", always_2d=False)
        m, sr_m = sf.read(mixed_wav_path, dtype="float32", always_2d=False)
    except Exception:
        return None
    if sr_s != sr_m:
        # Resample-mismatch is a verifier bug, not a music-presence
        # measurement. Return None so the caller flags it explicitly.
        return None

    # Mono-collapse if needed (the metric is on the diff, not on stereo
    # imaging; mono works for both).
    if s.ndim == 2:
        s = s.mean(axis=1)
    if m.ndim == 2:
        m = m.mean(axis=1)

    # L5.5 — time-align via cross-correlation so the diff signal
    # represents (mixed - aligned_speech) rather than (mixed - shifted_
    # speech). On a 2s intro pad the un-aligned diff dominates the
    # spectrum + the per-second LUFS test reports pct_active=1.0 even
    # when music is barely present.
    from ._alignment import align_mono_signals
    s, m, _lag = align_mono_signals(s, m, int(sr_s))

    n = int(min(len(s), len(m)))
    if n < int(0.5 * sr_s):  # less than 0.5s of audio
        return None
    diff = m[:n] - s[:n]

    # ── pct_active: per-second short-term LUFS on the diff ──────────
    try:
        meter = pyln.Meter(sr_s)
    except Exception:
        return None

    win = int(sr_s)  # 1-second window
    active = 0
    total = 0
    for i in range(0, n - win, win):
        chunk = diff[i:i + win]
        try:
            lufs = meter.integrated_loudness(chunk)
        except Exception:
            continue
        total += 1
        # −45 LUFS is the conventional "audible bed" floor (below this,
        # the bed is masked by speech-leak / room tone). Above = music
        # is actually playing this second.
        if lufs > -45.0:
            active += 1
    pct_active = (active / total) if total else 0.0

    # ── band_ratio: low-band vs mid-band energy in the diff ─────────
    try:
        freqs, pxx = welch(diff, sr_s, nperseg=min(4096, max(256, n // 4)))
        e_low = float(pxx[(freqs >= 100) & (freqs < 500)].mean())
        e_mid = float(pxx[(freqs >= 500) & (freqs < 3000)].mean())
        band_ratio = (e_low / e_mid) if e_mid > 1e-12 else 0.0
    except Exception:
        band_ratio = 0.0

    # ── integrated LUFS of the diff itself ──────────────────────────
    try:
        diff_lufs = float(meter.integrated_loudness(diff))
    except Exception:
        diff_lufs = -120.0

    return MusicPresence(
        pct_active=float(pct_active),
        band_ratio=float(band_ratio),
        diff_lufs_integrated=diff_lufs,
    )
