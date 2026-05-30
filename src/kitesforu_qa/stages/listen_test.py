"""D21 L5.2 (audio-overhaul, 2026-05-29) — programmatic listen-test
verifier.

Why this exists
---------------
The schema-only pin tests in ``test_audio_overhaul_pipeline_invariants``
verify Firestore SHAPE (cue_count=5, target_LRA=[5,7]). They never
download the audio. So:

  - ``cue_count=8`` passes the pin while the renderer dropped 7 of 8
    cues at mix time (silent failure — fixed in L_sfx_render).
  - ``target_LRA=5`` passes the pin while mastering ran with LRA=11.
  - The whole D24 confidence-theater arc.

This module measures the FINAL MIX, not the schema. ``verify_audio_quality()``
orchestrates loudness + speech intelligibility + music presence + SFX
transient count + duration delta + (optional) UTMOS naturalness, then
applies a per-genre verdict (PASS / WARN / FAIL). Every measurement
sources from a known-clean signal: the speech_only.mp3 uploaded by
PR #737 + the final mix mp3.

Phase 1 ships one default profile (drama-leaning). L5.3 adds the full
7-genre table.

Design rules
------------
- Pure orchestrator. Reuses `_music_presence` + `_sfx_presence`
  helpers; never re-implements ebur128 or STOI.
- Never raises. Missing deps degrade the affected metric to None
  + a `notes` entry; only HARD failures (file missing, ffmpeg
  missing) yield verdict=FAIL early.
- Never persists. Returns the report dict for the caller to write.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

Verdict = Literal["PASS", "WARN", "FAIL"]


# ---------------------------------------------------------------------------
# GenreProfile + Default
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenreProfile:
    """Per-genre acceptance bands. L5.3 fills the 7-genre table; this
    file ships a single drama-leaning default so L5.2 is usable
    immediately."""

    genre: str

    # Loudness bands (slack = ±slack_lu around band edges = WARN; > slack = FAIL).
    lufs_min: float
    lufs_max: float
    lra_min: float
    lra_max: float
    lufs_slack_lu: float = 1.0
    lra_slack_lu: float = 1.5

    tp_max_dbtp: float = -1.0     # one-sided: > tp_max → FAIL

    # Speech intelligibility (STOI ∈ [0, 1]; SMR in LU)
    stoi_min: float = 0.85
    smr_lu_min: float = 12.0      # speech ≥ N LU above music bed

    # Music presence on the diff signal (% of seconds with audible bed)
    music_presence_pct_min: float = 0.20
    music_presence_pct_max: float = 0.65

    # SFX events per 5 minutes
    sfx_events_per_5min_min: int = 0
    sfx_events_per_5min_max: int = 8

    # Duration delta as a fraction (|actual - expected| / expected)
    duration_delta_pct_max: float = 0.10

    # Naturalness (UTMOS / DNSMOS), 1-5 scale. Only enforced when
    # the optional naturalness backend is installed.
    naturalness_min: float = 3.5

    # Silence ratio: fraction of seconds where RMS < -50 dBFS. > max = WARN.
    silence_ratio_max: float = 0.10


# Drama-leaning default (covers fiction, drama, mystery, romance, comedy
# at the broad-band level). L5.3 will add the per-genre tightenings.
DEFAULT_PROFILE = GenreProfile(
    genre="default",
    lufs_min=-17.0, lufs_max=-14.0,
    lra_min=4.0, lra_max=8.0,
)


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------


@dataclass
class ListenTestReport:
    """Result of verify_audio_quality()."""

    audio_path: str
    genre: str
    profile_used: str

    # Measurements (None = could not measure)
    lufs: Optional[float] = None
    lra: Optional[float] = None
    tp_dbtp: Optional[float] = None
    stoi_speech: Optional[float] = None
    smr_lu: Optional[float] = None
    music_presence_pct: Optional[float] = None
    music_band_ratio: Optional[float] = None
    sfx_event_count: Optional[int] = None
    sfx_events_per_5min: Optional[float] = None
    duration_actual_s: Optional[float] = None
    duration_expected_s: Optional[float] = None
    duration_delta_pct: Optional[float] = None
    naturalness_score: Optional[float] = None
    silence_ratio: Optional[float] = None
    crest_factor_db: Optional[float] = None
    plr_db: Optional[float] = None

    # Verdict aggregation
    fails: List[str] = field(default_factory=list)
    warns: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    verdict: Verdict = "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audio_path": self.audio_path,
            "genre": self.genre,
            "profile_used": self.profile_used,
            "lufs": self.lufs,
            "lra": self.lra,
            "tp_dbtp": self.tp_dbtp,
            "stoi_speech": self.stoi_speech,
            "smr_lu": self.smr_lu,
            "music_presence_pct": self.music_presence_pct,
            "music_band_ratio": self.music_band_ratio,
            "sfx_event_count": self.sfx_event_count,
            "sfx_events_per_5min": self.sfx_events_per_5min,
            "duration_actual_s": self.duration_actual_s,
            "duration_expected_s": self.duration_expected_s,
            "duration_delta_pct": self.duration_delta_pct,
            "naturalness_score": self.naturalness_score,
            "silence_ratio": self.silence_ratio,
            "crest_factor_db": self.crest_factor_db,
            "plr_db": self.plr_db,
            "fails": list(self.fails),
            "warns": list(self.warns),
            "notes": list(self.notes),
            "verdict": self.verdict,
        }


# ---------------------------------------------------------------------------
# Helpers — loudness/duration/STOI primitives
# ---------------------------------------------------------------------------


def _ensure_wav(audio_path: str, work_dir: str) -> Optional[str]:
    """Decode MP3/AAC/whatever to a temporary 48k stereo WAV for the
    measurement helpers. Returns the WAV path or None if ffmpeg can't
    read the input."""
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        return None
    if audio_path.lower().endswith(".wav"):
        return audio_path
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    wav_path = os.path.join(
        work_dir, "decoded_" + os.path.basename(audio_path) + ".wav"
    )
    try:
        subprocess.run(
            [ff, "-y", "-v", "error", "-i", audio_path,
             "-ac", "2", "-ar", "48000", wav_path],
            check=True, timeout=120,
        )
    except Exception:
        return None
    return wav_path if os.path.exists(wav_path) else None


def _ffprobe_duration_s(audio_path: str) -> Optional[float]:
    fp = shutil.which("ffprobe")
    if not fp:
        return None
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-show_format", "-of", "json", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "{}")
        d = (data.get("format") or {}).get("duration")
        return float(d) if d else None
    except Exception:
        return None


def _measure_loudness_battery(wav_path: str) -> Dict[str, Optional[float]]:
    """Return integrated LUFS, LRA, TP, silence_ratio + crest_factor for
    the WAV. Reuses ffmpeg loudnorm JSON for LUFS/LRA/TP and a soundfile
    pass for RMS-based silence + crest factor."""
    out: Dict[str, Optional[float]] = {
        "lufs": None, "lra": None, "tp_dbtp": None,
        "silence_ratio": None, "crest_factor_db": None,
    }
    ff = shutil.which("ffmpeg")
    if not ff:
        return out

    # Loudness via ffmpeg loudnorm analysis pass.
    try:
        cmd = [
            ff, "-hide_banner", "-nostats", "-i", wav_path,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-",
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        err = r.stderr.decode("utf-8", "ignore")
        start = err.rfind("{")
        end = err.rfind("}")
        if start != -1 and end > start:
            data = json.loads(err[start:end + 1])
            i_val = float(data.get("input_i", "nan"))
            lra_val = float(data.get("input_lra", "nan"))
            tp_val = float(data.get("input_tp", "nan"))
            if i_val == i_val and -70.0 < i_val < 0.0:
                out["lufs"] = round(i_val, 2)
            if lra_val == lra_val and 0.0 <= lra_val < 30.0:
                out["lra"] = round(lra_val, 2)
            if tp_val == tp_val and -60.0 < tp_val < 6.0:
                out["tp_dbtp"] = round(tp_val, 2)
    except Exception:
        pass

    # RMS-based silence ratio + crest factor via soundfile + numpy.
    try:
        import numpy as np
        import soundfile as sf
        data, sr = sf.read(wav_path, dtype="float32")
        if data.ndim == 2:
            mono = data.mean(axis=1)
        else:
            mono = data
        if mono.size > 0:
            sp = float(np.max(np.abs(mono)))
            rms_total = float(np.sqrt(np.mean(mono.astype("float64") ** 2)))
            if rms_total > 1e-9:
                # crest factor = peak(dB) − RMS(dB)
                crest = 20.0 * np.log10(max(sp, 1e-9) / rms_total)
                out["crest_factor_db"] = round(float(crest), 2)
            # Silence ratio: fraction of 50ms windows with RMS < -50 dBFS.
            win = max(1, int(0.05 * sr))
            pad = (-mono.size) % win
            padded = np.concatenate(
                [mono, np.zeros(pad, dtype="float32")]
            ) if pad else mono
            framed = padded.reshape(-1, win)
            win_rms = np.sqrt(
                np.mean(framed.astype("float64") ** 2, axis=1)
            )
            with np.errstate(divide="ignore"):
                win_db = 20.0 * np.log10(np.maximum(win_rms, 1e-9))
            silent = float((win_db < -50.0).mean())
            out["silence_ratio"] = round(silent, 3)
    except Exception:
        pass

    return out


def _measure_stoi_and_smr(
    speech_wav: str, mixed_wav: str,
) -> Dict[str, Optional[float]]:
    """STOI + speech-to-music ratio. Mirrors workers/stages/audio/quality/
    music_blend.assess_blend but kqa-local (no cross-repo import)."""
    result: Dict[str, Optional[float]] = {"stoi": None, "smr_lu": None}
    try:
        import numpy as np
        import pyloudnorm as pyln  # type: ignore[import-not-found]
        import soundfile as sf
        from pystoi import stoi as stoi_fn  # type: ignore[import-not-found]
    except ImportError:
        return result

    try:
        s, sr_s = sf.read(speech_wav, dtype="float32", always_2d=False)
        m, sr_m = sf.read(mixed_wav, dtype="float32", always_2d=False)
    except Exception:
        return result
    if sr_s != sr_m:
        return result

    if s.ndim == 2:
        s = s.mean(axis=1)
    if m.ndim == 2:
        m = m.mean(axis=1)
    n = int(min(len(s), len(m)))
    if n < int(sr_s):
        return result

    # L5.5 — time-align via cross-correlation before STOI/SMR math.
    # Without this, a 2 s music_renderer intro pad on `mixed.mp3`
    # collapses STOI to ~0 even on identical-content signals. See
    # ``stages/_alignment.py`` for the algorithm + the live evidence
    # (jobs 0ff85d79 / 23a5f5c6, 2026-05-29).
    from ._alignment import align_mono_signals
    s_aligned, m_aligned, _lag = align_mono_signals(s, m, int(sr_s))
    n = int(min(len(s_aligned), len(m_aligned)))
    if n < int(sr_s):
        return result

    # STOI (extended version handles modulated maskers better — D12)
    try:
        result["stoi"] = round(
            float(stoi_fn(s_aligned[:n], m_aligned[:n], sr_s, extended=True)), 3
        )
    except Exception:
        pass

    # SMR = LUFS(speech) - LUFS(diff). LUFS-relative, matches the music
    # renderer's ducking contract.
    try:
        meter = pyln.Meter(sr_s)
        speech_lufs = float(meter.integrated_loudness(s_aligned[:n]))
        diff = m_aligned[:n] - s_aligned[:n]
        diff_lufs = float(meter.integrated_loudness(diff))
        smr = speech_lufs - diff_lufs
        if -30.0 < smr < 80.0:
            result["smr_lu"] = round(smr, 2)
    except Exception:
        pass

    # numpy is imported but only needed via the helper paths above.
    _ = np

    return result


def _maybe_utmos(audio_path: str) -> Optional[float]:
    """Optional naturalness MOS (UTMOS via speechmos). Returns None if
    the sidecar isn't installed — we don't want a heavy ML dep in the
    default kqa image. L5.4 spec ships it as a separate extras_require."""
    try:
        from speechmos import utmos  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        # speechmos returns {'utmos': float}
        out = utmos.run(audio_path, 22050)
        val = out.get("utmos") if isinstance(out, dict) else out
        return float(val) if val is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Verdict aggregator
# ---------------------------------------------------------------------------


def _grade(report: ListenTestReport, profile: GenreProfile) -> None:
    """Apply the profile bands to the measured values. Mutates `report`
    in place — appends to fails/warns and sets verdict."""

    def band(name: str, val: Optional[float], lo: float, hi: float,
             slack_lo: float, slack_hi: float) -> None:
        if val is None:
            report.notes.append(f"{name}: not measured (helper unavailable)")
            return
        if val < lo - slack_lo or val > hi + slack_hi:
            report.fails.append(
                f"{name}={val:.2f} far outside [{lo}, {hi}]"
            )
        elif val < lo or val > hi:
            report.warns.append(
                f"{name}={val:.2f} outside [{lo}, {hi}]"
            )

    band("LUFS", report.lufs,
         profile.lufs_min, profile.lufs_max,
         profile.lufs_slack_lu, profile.lufs_slack_lu)
    band("LRA", report.lra,
         profile.lra_min, profile.lra_max,
         profile.lra_slack_lu, profile.lra_slack_lu)
    band("STOI", report.stoi_speech,
         profile.stoi_min, 1.0, 0.03, 0.0)
    band("music_presence_pct", report.music_presence_pct,
         profile.music_presence_pct_min, profile.music_presence_pct_max,
         0.05, 0.05)
    band("sfx_per_5min", report.sfx_events_per_5min,
         float(profile.sfx_events_per_5min_min),
         float(profile.sfx_events_per_5min_max),
         1.0, 2.0)
    band("duration_delta_pct", report.duration_delta_pct,
         0.0, profile.duration_delta_pct_max, 0.0, 0.05)
    band("silence_ratio", report.silence_ratio,
         0.0, profile.silence_ratio_max, 0.0, 0.05)
    band("smr_lu", report.smr_lu,
         profile.smr_lu_min, 60.0, 1.5, 0.0)

    # True-peak is one-sided.
    if report.tp_dbtp is not None and report.tp_dbtp > profile.tp_max_dbtp:
        report.fails.append(
            f"true-peak {report.tp_dbtp:.1f} dBTP > {profile.tp_max_dbtp}"
        )

    # Naturalness only when measured.
    if report.naturalness_score is not None:
        band("naturalness", report.naturalness_score,
             profile.naturalness_min, 5.0, 0.2, 0.0)

    if report.fails:
        report.verdict = "FAIL"
    elif report.warns:
        report.verdict = "WARN"
    else:
        report.verdict = "PASS"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_audio_quality(
    *,
    audio_path: str,
    speech_only_path: Optional[str] = None,
    genre: str = "default",
    profile: Optional[GenreProfile] = None,
    expected_duration_s: Optional[float] = None,
    work_dir: Optional[str] = None,
) -> ListenTestReport:
    """Run the full battery against the final mp3 and return a report.

    Args
    ----
    audio_path:
        Local path to the final mastered MP3 (download via gsutil/
        signed URL before calling).
    speech_only_path:
        Local path to the pre-music speech bed (uploaded by PR #737).
        When provided, STOI / SMR / music_presence / SFX-transient
        are computed. When None, those metrics stay None + warn in
        ``notes``; loudness/duration still measured.
    genre / profile:
        Either pass a ``GenreProfile`` instance OR a genre string
        (uses ``DEFAULT_PROFILE`` for now; L5.3 expands the table).
    expected_duration_s:
        Required for the duration_delta check; if None, the duration
        axis stays None.
    work_dir:
        Where to write decoded WAV intermediates. Defaults to a
        tempdir that's cleaned on function exit.
    """
    if profile is None:
        profile = DEFAULT_PROFILE
    report = ListenTestReport(
        audio_path=audio_path,
        genre=genre,
        profile_used=profile.genre,
        duration_expected_s=expected_duration_s,
    )

    # Workdir for decoded WAVs. Use tempfile if not provided.
    own_work_dir = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix="kqa_listen_")
    try:
        # 1) ffprobe duration on the input file directly.
        report.duration_actual_s = _ffprobe_duration_s(audio_path)
        if expected_duration_s and report.duration_actual_s:
            report.duration_delta_pct = round(
                abs(report.duration_actual_s - expected_duration_s)
                / max(0.01, expected_duration_s),
                4,
            )

        # 2) Decode mixed mp3 → wav for loudness/STOI.
        mixed_wav = _ensure_wav(audio_path, work_dir)
        if not mixed_wav:
            report.fails.append("could not decode mixed audio to WAV")
            report.verdict = "FAIL"
            return report

        # 3) Loudness battery.
        loud = _measure_loudness_battery(mixed_wav)
        report.lufs = loud["lufs"]
        report.lra = loud["lra"]
        report.tp_dbtp = loud["tp_dbtp"]
        report.silence_ratio = loud["silence_ratio"]
        report.crest_factor_db = loud["crest_factor_db"]
        if report.tp_dbtp is not None and report.lufs is not None:
            report.plr_db = round(report.tp_dbtp - report.lufs, 2)

        # 4) STOI / SMR / music presence / SFX — all need speech_only.
        if speech_only_path and os.path.exists(speech_only_path):
            speech_wav = _ensure_wav(speech_only_path, work_dir)
            if speech_wav:
                stoi_smr = _measure_stoi_and_smr(speech_wav, mixed_wav)
                report.stoi_speech = stoi_smr["stoi"]
                report.smr_lu = stoi_smr["smr_lu"]

                from ._music_presence import measure_music_presence
                from ._sfx_presence import detect_sfx_transients
                mp = measure_music_presence(speech_wav, mixed_wav)
                if mp is not None:
                    report.music_presence_pct = round(mp.pct_active, 3)
                    report.music_band_ratio = round(mp.band_ratio, 3)
                sfx = detect_sfx_transients(speech_wav, mixed_wav)
                if sfx is not None:
                    report.sfx_event_count = sfx.count
                    if (report.duration_actual_s
                            and report.duration_actual_s > 0):
                        report.sfx_events_per_5min = round(
                            sfx.count / (report.duration_actual_s / 300.0),
                            2,
                        )
            else:
                report.notes.append("speech_only path provided but decode failed")
        else:
            report.notes.append(
                "speech_only not provided — STOI/SMR/music_presence/SFX "
                "skipped (verdict considers only loudness + duration)"
            )

        # 5) Optional naturalness sidecar.
        nat = _maybe_utmos(mixed_wav)
        if nat is not None:
            report.naturalness_score = round(nat, 2)

        # 6) Apply profile + verdict.
        _grade(report, profile)

    finally:
        if own_work_dir and work_dir and os.path.isdir(work_dir):
            try:
                shutil.rmtree(work_dir)
            except Exception:
                pass

    return report


__all__ = [
    "GenreProfile",
    "ListenTestReport",
    "DEFAULT_PROFILE",
    "verify_audio_quality",
]
