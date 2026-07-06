#!/usr/bin/env python3
"""audio_quality_judge — cheap ($0) spectral judge of a rendered master's MUSIC quality.

The gap this closes (founder 2026-07-06): unit tests assert "a music bed EXISTS"; a human
LISTENS and says "this is a horrible hum". A passing presence-check is fully compatible with
a tuneless sine-drone. This measures QUALITY, not existence — the artifact-quality half of
human-like testing. No LLM, no provider — ffmpeg + numpy FFT on the already-rendered file.

Heuristic (a synthesized sine-drone vs real music):
  * DRONE / HUM: energy concentrated in a FEW STATIC low-frequency bins that barely move over
    time — high tonality (low spectral flatness) + very LOW spectral flux (the notes never
    change) + a low, stable centroid. That is exactly _build_audible_pad_filtergraph's output.
  * REAL MUSIC: melody/harmony → the dominant pitches CHANGE over time → high spectral flux +
    a higher, moving centroid + broadband harmonic content.
  * SILENT / NO BED: near-zero energy in the music band.

Usage: audio_quality_judge.py <audio.mp3|.mp4>   → prints a verdict + the metrics.
"""
import subprocess
import sys

import numpy as np

SR = 16000


def _decode_mono(path: str) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def judge(path: str) -> dict:
    x = _decode_mono(path)
    if x.size < SR:
        return {"verdict": "too_short", "seconds": round(x.size / SR, 2)}
    win = 2048
    hop = 1024
    frames = []
    for i in range(0, x.size - win, hop):
        frames.append(np.abs(np.fft.rfft(x[i:i + win] * np.hanning(win))))
    Sfull = np.asarray(frames) + 1e-9        # [t, freq]
    freqs = np.fft.rfftfreq(win, 1 / SR)
    # ISOLATE THE MUSIC BED: speech dominates the master, so analyse the QUIETEST ~30% of
    # frames (speech gaps) where the bed/drone is exposed. A drone is CONSTANT, so it is
    # exactly what survives in the quiet frames; real music's melody still MOVES there.
    energy = Sfull.sum(axis=1)
    quiet = energy <= np.quantile(energy, 0.30)
    S = Sfull[quiet]
    if S.shape[0] < 4:
        S = Sfull
    # spectral flux across the quiet frames (change of the normalised spectrum over time).
    Sn = S / S.sum(axis=1, keepdims=True)
    flux = float(np.mean(np.sqrt(np.sum(np.diff(Sn, axis=0) ** 2, axis=1)))) if S.shape[0] > 1 else 0.0
    # tonality: 1 - spectral flatness. ~1 = a few pure tones (a sine drone), ~0 = noise-like.
    flat = float(np.mean(np.exp(np.mean(np.log(S), axis=1)) / np.mean(S, axis=1)))
    tonality = 1 - flat
    # centroid (Hz) of the quiet frames + how much it MOVES (a drone barely moves).
    cen = (S * freqs).sum(axis=1) / S.sum(axis=1)
    centroid_hz = float(np.mean(cen))
    centroid_move = float(np.std(cen))
    # music-band (55-350 Hz) energy fraction in the quiet frames — the drone's home.
    band = (freqs >= 55) & (freqs <= 350)
    band_frac = float(S[:, band].sum() / S.sum())

    # verdict on the quiet (music-exposed) frames — a sine-drone is TONAL + STATIC (low
    # flux, low centroid-movement) + concentrated in the low music band.
    drone = tonality > 0.5 and flux < 0.06 and centroid_move < 600 and band_frac > 0.3
    if band_frac < 0.03 and centroid_hz > 1500:
        verdict = "no_music_bed (quiet frames are near-silent / speech-only)"
    elif drone:
        verdict = "DRONE/HUM (tuneless static low bed — the 'horrible hum' class)"
    elif flux > 0.08 and centroid_move > 500:
        verdict = "likely_real_music (the bed's pitch content moves over time)"
    else:
        verdict = "ambiguous (inspect — quiet/repetitive bed or low SNR)"
    return {
        "verdict": verdict, "seconds": round(x.size / SR, 1),
        "spectral_flux": round(flux, 4), "tonality": round(tonality, 3),
        "centroid_hz": round(centroid_hz, 1), "centroid_move_hz": round(centroid_move, 1),
        "music_band_frac": round(band_frac, 3),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: audio_quality_judge.py <audio.mp3|.mp4>", file=sys.stderr)
        sys.exit(2)
    r = judge(sys.argv[1])
    print(f"VERDICT: {r['verdict']}")
    for k, v in r.items():
        if k != "verdict":
            print(f"  {k}: {v}")
