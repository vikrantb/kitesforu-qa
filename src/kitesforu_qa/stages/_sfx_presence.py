"""SFX transient detection for the listen-test verifier.

Counts discrete SFX events in the final mix by detecting transients
on the diff signal (final_mix - speech_only), high-passed at 200 Hz
to remove music sub-rumble. The point: VERIFY that the trimmer's
kept_count actually corresponds to audible cues — not that the
sheet says 'kept 8' while the audio has zero transient hits.

Per the L1 live-verify finding (rev 00732-wrm), 8 spot/designed cues
were kept by the trimmer but the renderer's loop only iterated
ambience → 0 transient events in the final mp3. L_sfx_render (#738)
fixed the renderer; this verifier confirms cues actually fire.

Fail-soft: missing deps → None; never raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SfxDetection:
    """Output of :func:`detect_sfx_transients`."""

    count: int                          # number of discrete events
    timestamps: list[float] = field(default_factory=list)  # event onsets (s)

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "timestamps": [round(t, 2) for t in self.timestamps],
        }


def detect_sfx_transients(
    speech_wav_path: str, mixed_wav_path: str,
) -> Optional[SfxDetection]:
    """Count discrete SFX events in the diff signal.

    Pipeline:
      1. Read both WAVs, mono-collapse, length-align.
      2. Compute diff = mixed - speech_only.
      3. High-pass 200 Hz (removes music sub-rumble; SFX transients
         live in 200 Hz–6 kHz typically).
      4. Take the percussive component (separates events from drones).
      5. Onset-detect with adaptive threshold.
      6. Cluster onsets within 250 ms (one slammed door is ONE event,
         not 4).

    Returns None on missing deps / malformed input.
    """
    try:
        import librosa
        import numpy as np
        import soundfile as sf
        from scipy.signal import butter, sosfilt
    except ImportError:
        return None

    try:
        s, sr_s = sf.read(speech_wav_path, dtype="float32", always_2d=False)
        m, sr_m = sf.read(mixed_wav_path, dtype="float32", always_2d=False)
    except Exception:
        return None
    if sr_s != sr_m:
        return None

    if s.ndim == 2:
        s = s.mean(axis=1)
    if m.ndim == 2:
        m = m.mean(axis=1)

    n = int(min(len(s), len(m)))
    if n < int(0.5 * sr_s):
        return None
    diff = m[:n] - s[:n]

    # High-pass 200 Hz: speech residuals + music sub-rumble live
    # below 200 Hz and would dominate the onset detector. SFX
    # transients (door slam, glass break, footstep, thunder crack)
    # all have significant 200 Hz+ content.
    try:
        sos = butter(4, 200, btype="highpass", fs=sr_s, output="sos")
        # sosfilt returns the filtered array (ignoring the zi tuple form).
        hp_raw = sosfilt(sos, diff)
        hp = np.asarray(hp_raw, dtype="float32")
    except Exception:
        hp = diff

    # Percussive separation: focus on the energy bursts (not the
    # drones / sustained tones). librosa.effects.percussive is the
    # cheap HPSS variant.
    try:
        perc = librosa.effects.percussive(hp, margin=3.0)
    except Exception:
        # Skip the HPSS if librosa fails — onset_detect still works
        # but with more false-positives.
        perc = hp

    try:
        onsets = librosa.onset.onset_detect(
            y=np.ascontiguousarray(perc), sr=sr_s, units="time",
            backtrack=False, pre_max=10, post_max=10, delta=0.07,
        )
    except Exception:
        return None

    # Cluster onsets that are < 250 ms apart (one slammed door is
    # not 4 events — the onset detector picks up the attack +
    # ring-out edges).
    clustered: list[float] = []
    for t in onsets.tolist():
        if not clustered or (t - clustered[-1]) > 0.25:
            clustered.append(float(t))

    return SfxDetection(count=len(clustered), timestamps=clustered)
