"""Audio-mix deterministic check battery ($0 — ffmpeg/ffprobe only, no LLM/cloud).

These checks assert the *final master* is mechanically sound: it isn't clipped, it lands in a
sane loudness window, it has no dead-air gap, it wasn't truncated to a stub, its channels are
balanced, and it's at a sane sample rate. They are the cheap deterministic floor under the
LLM/heavier judge layer — every one is true on a good master and false on a degraded one.

GAP note (flagged to Claude-main, NOT silently patched into artifact.py):
``art.audio_info`` is ffprobe-only (``format/duration/bitrate/size_bytes/codec/sample_rate/
channels``) — it has NO peak/LUFS/silence data. So the peak/loudness/silence checks here
self-serve those metrics by shelling ffmpeg filters (``volumedetect`` + ``ebur128`` +
``silencedetect`` + ``astats``) on ``art.audio_path``. That is still $0 (ffmpeg only, no librosa,
no cloud). The cleaner shared contract would be an ``art.audio_stats`` accessor on the Artifact;
that is Claude-main's to add — requested, not added here.

Genre bands (LUFS window, true-peak ceiling, silence ratio, duration tolerance) come from the
SSOT ``profiles.genre_audio_profiles.get_profile(genre)`` so the harness asks for the SAME bands
the renderer targets (no cross-layer drift).
"""
from __future__ import annotations

import re
import subprocess

from ..check import check, skip

# ── named thresholds (constants, not magic numbers) ──────────────────────────

# Clipping: a true master is mastered to a true-peak ceiling below 0 dBFS. A sample peak at/above
# this is digital clipping (square-wave artifacts). volumedetect.max_volume is reported as <=0 dB.
CLIP_MAX_VOLUME_DBFS = -0.1          # max_volume >= this ⇒ clipped/brickwalled to 0
CLIP_MAX_VOLUME_HARD_DBFS = 0.0      # exactly 0.0 dB = definitely clamped

# Loudness sanity floor when no genre band applies. Real spoken masters sit roughly -26..-9 LUFS;
# anything outside is a broken mix (silent stub, or brick-walled to ~0). The per-genre band (when
# resolvable) is preferred over this wide fallback.
LUFS_SANE_MIN = -32.0
LUFS_SANE_MAX = -6.0
LUFS_GENRE_SLACK_LU = 3.0            # tolerance added around the genre's [lufs_min, lufs_max] band

# Long-silence: a mid-audio gap longer than this (quieter than the noise floor) is a dropout /
# missing-segment hole — not a deliberate dramatic beat (those are shorter; mystery allows ~3s).
LONG_SILENCE_S = 4.0                 # gap longer than this mid-audio = a hole
SILENCE_NOISE_FLOOR_DB = -50.0       # below this RMS counts as "silent" for silencedetect

# Truncation: the rendered audio must be at least this fraction of the duration the script implies
# (~150 spoken words/min). A 247-word "5-min" stub that ships at 91s is the classic undershoot bug.
SPOKEN_WORDS_PER_MIN = 150.0
TRUNCATION_MIN_RATIO = 0.40          # audio shorter than 40% of script-implied length = truncated
TRUNCATION_FLOOR_S = 5.0             # any usable episode is at least this long

# Channel balance: for stereo, L vs R integrated RMS should not diverge by more than this — a big
# gap means one channel is dead or the image collapsed.
CHANNEL_BALANCE_MAX_DB = 6.0

# Sample rate: speech/music masters ship at >= this. 8 kHz telephone-grade = a broken encode.
SAMPLE_RATE_MIN_HZ = 16000
SAMPLE_RATE_OK_SET = {16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000}

_FFMPEG_TIMEOUT_S = 120


# ── ffmpeg stats (the GAP-1 self-serve; cached per audio_path) ───────────────


class _AudioStats:
    """Peak / loudness / silence / per-channel metrics for one file, via ffmpeg filters.

    All values may be None if ffmpeg failed or the metric was not reported — every consuming
    check guards on None and ``skip()``s rather than reporting a false failure.
    """

    __slots__ = (
        "max_volume_db", "mean_volume_db", "lufs_i", "true_peak_db",
        "long_silence_gaps", "channel_rms_db",
    )

    def __init__(self) -> None:
        self.max_volume_db: float | None = None      # volumedetect peak (sample peak, dBFS)
        self.mean_volume_db: float | None = None
        self.lufs_i: float | None = None             # ebur128 integrated loudness
        self.true_peak_db: float | None = None       # ebur128 true peak (dBFS)
        self.long_silence_gaps: list[tuple[float, float]] = []  # (start_s, duration_s)
        self.channel_rms_db: list[float] = []        # per-channel RMS level dB (astats)


_STATS_CACHE: dict[str, _AudioStats] = {}


def _run_ffmpeg(args: list[str]) -> str:
    """Run ffmpeg with the given filter args, returning combined stderr (where filters report)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", *args, "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S)
    return proc.stderr or ""


_RE_MAX_VOL = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_RE_MEAN_VOL = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
# Integrated LUFS appears BOTH on every per-frame running line ("[Parsed...] t: .. I: -70.0 LUFS ..")
# AND in the final "Summary:" block ("    I:         -22.3 LUFS"). We must read the SUMMARY one —
# the running lines start at the -70 LUFS sentinel and would mis-parse. So anchor on a line that
# starts with whitespace + "I:" (the summary form has no "[Parsed..]" prefix).
_RE_LUFS_I_SUMMARY = re.compile(r"^\s+I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
_RE_TRUE_PEAK = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS")
_RE_CH_RMS = re.compile(r"RMS level dB:\s*(-?\d+(?:\.\d+)?|inf|nan)", re.IGNORECASE)
_RE_SIL_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_RE_SIL_DUR = re.compile(r"silence_duration:\s*(\d+(?:\.\d+)?)")


def _compute_stats(audio_path: str) -> _AudioStats:
    s = _AudioStats()

    # Pass 1: volumedetect (sample peak + mean) and ebur128 (integrated LUFS + true peak).
    out1 = _run_ffmpeg(["-i", audio_path, "-af", "volumedetect,ebur128=peak=true"])
    m = _RE_MAX_VOL.search(out1)
    if m:
        s.max_volume_db = float(m.group(1))
    m = _RE_MEAN_VOL.search(out1)
    if m:
        s.mean_volume_db = float(m.group(1))
    # Integrated LUFS: take the LAST summary-form "I:" line (the running per-frame lines also start
    # with whitespace once past the [Parsed..] tag, so the summary block — emitted last — wins).
    lufs_matches = _RE_LUFS_I_SUMMARY.findall(out1)
    if lufs_matches:
        s.lufs_i = float(lufs_matches[-1])
    # True peak: take the LAST reported (ebur128 emits a running line + a summary); last = final.
    tps = _RE_TRUE_PEAK.findall(out1)
    if tps:
        s.true_peak_db = float(tps[-1])

    # Pass 2: silencedetect for long mid-audio gaps.
    out2 = _run_ffmpeg([
        "-i", audio_path,
        "-af", f"silencedetect=noise={SILENCE_NOISE_FLOOR_DB}dB:d={LONG_SILENCE_S}",
    ])
    starts = [float(x) for x in _RE_SIL_START.findall(out2)]
    durs = [float(x) for x in _RE_SIL_DUR.findall(out2)]
    for i, dur in enumerate(durs):
        start = starts[i] if i < len(starts) else 0.0
        s.long_silence_gaps.append((start, dur))

    # Pass 3: astats per-channel RMS for channel balance. astats prints each channel's RMS, then a
    # final overall summary RMS line — so per-channel are all but the last when there are >=2.
    out3 = _run_ffmpeg(["-i", audio_path, "-af", "astats=metadata=1:measure_overall=0"])
    rms_vals: list[float] = []
    for raw in _RE_CH_RMS.findall(out3):
        if raw.lower() in ("inf", "nan", "-inf"):
            continue
        rms_vals.append(float(raw))
    s.channel_rms_db = rms_vals

    return s


def _stats(art) -> _AudioStats:
    """Cached ffmpeg-stats for the artifact's audio. skip() if there is no audio at all."""
    path = art.audio_path
    if not path:
        skip("no audio file on artifact")
    if path not in _STATS_CACHE:
        try:
            _STATS_CACHE[path] = _compute_stats(path)
        except Exception as e:  # noqa: BLE001
            # A broken ffmpeg invocation should not become a false failure — record empty stats.
            empty = _AudioStats()
            _STATS_CACHE[path] = empty
            raise RuntimeError(f"ffmpeg stats failed: {e}") from e
    return _STATS_CACHE[path]


def _genre_lufs_band(art) -> tuple[float, float]:
    """Resolve the genre's LUFS window (with slack), falling back to the wide sane window."""
    try:
        from ...profiles.genre_audio_profiles import get_profile
        prof = get_profile(art.genre)
        lo = float(prof.lufs_min) - LUFS_GENRE_SLACK_LU
        hi = float(prof.lufs_max) + LUFS_GENRE_SLACK_LU
        # Never tighter than the absolute sane window — the band is a refinement, not a regression.
        return (min(lo, LUFS_SANE_MAX), max(hi, LUFS_SANE_MIN))
    except Exception:  # noqa: BLE001
        return (LUFS_SANE_MIN, LUFS_SANE_MAX)


# ── checks ───────────────────────────────────────────────────────────────────


@check("audio.no_clipping", "audio-mix", severity="high")
def no_clipping(art):
    """The master must not be clipped — sample peak must sit below 0 dBFS with headroom."""
    s = _stats(art)
    peak = s.true_peak_db if s.true_peak_db is not None else s.max_volume_db
    if peak is None:
        skip("ffmpeg reported no peak level")
    clipped = peak >= CLIP_MAX_VOLUME_DBFS
    # score scales with headroom: at -6 dBFS or lower = full marks, at 0 dBFS = zero.
    score = max(0.0, min(1.0, (-peak) / 6.0))
    return (not clipped, score,
            f"peak {peak:.2f} dBFS (clip threshold {CLIP_MAX_VOLUME_DBFS} dBFS)")


@check("audio.loudness_in_target_range", "audio-mix", severity="high")
def loudness_in_target_range(art):
    """Integrated loudness must land inside the genre's LUFS window (or the sane fallback)."""
    s = _stats(art)
    lufs = s.lufs_i
    if lufs is None or lufs <= -70.0:
        # -70 LUFS = ffmpeg's "effectively silent" sentinel → covered by the truncation/silence checks.
        skip("no measurable integrated loudness (silent or ffmpeg gave no LUFS)")
    lo, hi = _genre_lufs_band(art)
    in_band = lo <= lufs <= hi
    return (in_band, 1.0 if in_band else 0.0,
            f"integrated {lufs:.1f} LUFS vs band [{lo:.1f}, {hi:.1f}] (genre={art.genre})")


@check("audio.no_long_silence", "audio-mix", severity="high")
def no_long_silence(art):
    """No dead-air gap longer than the long-silence threshold mid-audio (a dropout / missing seg)."""
    s = _stats(art)
    gaps = s.long_silence_gaps
    if not gaps:
        return (True, 1.0, f"no gap > {LONG_SILENCE_S}s")
    worst = max(gaps, key=lambda g: g[1])
    return (False, 0.0,
            f"{len(gaps)} silent gap(s) > {LONG_SILENCE_S}s; worst {worst[1]:.1f}s at {worst[0]:.1f}s")


@check("audio.not_truncated", "audio-mix", severity="critical")
def not_truncated(art):
    """Rendered audio must be at least a floor fraction of the script-implied duration (undershoot)."""
    dur = art.audio_duration_s
    if dur <= 0:
        skip("no audio duration (ffprobe gave 0)")
    words = art.word_count
    if words <= 0:
        # No script to compare against → just enforce the absolute floor.
        ok = dur >= TRUNCATION_FLOOR_S
        return (ok, 1.0 if ok else 0.0, f"{dur:.1f}s audio, no script word count (floor {TRUNCATION_FLOOR_S}s)")
    implied_s = (words / SPOKEN_WORDS_PER_MIN) * 60.0
    ratio = dur / implied_s if implied_s > 0 else 1.0
    ok = dur >= TRUNCATION_FLOOR_S and ratio >= TRUNCATION_MIN_RATIO
    return (ok, max(0.0, min(1.0, ratio)),
            f"{dur:.1f}s audio vs {implied_s:.1f}s implied by {words} words (ratio {ratio:.2f}, min {TRUNCATION_MIN_RATIO})")


@check("audio.channel_balance", "audio-mix", severity="medium")
def channel_balance(art):
    """For stereo, L and R integrated RMS must not diverge beyond tolerance (no collapsed channel)."""
    info = art.audio_info
    channels = int(info.get("channels") or 0)
    if channels < 2:
        skip(f"mono/no-channel audio (channels={channels})")
    s = _stats(art)
    rms = s.channel_rms_db
    if len(rms) < 2:
        skip("astats reported fewer than 2 channel RMS values")
    # Use the first two channel RMS values (L, R); astats lists per-channel before any summary.
    left, right = rms[0], rms[1]
    diff = abs(left - right)
    ok = diff <= CHANNEL_BALANCE_MAX_DB
    score = max(0.0, min(1.0, 1.0 - diff / CHANNEL_BALANCE_MAX_DB)) if ok else 0.0
    return (ok, score,
            f"L {left:.1f} dB vs R {right:.1f} dB, |Δ|={diff:.1f} dB (max {CHANNEL_BALANCE_MAX_DB} dB)")


@check("audio.sample_rate_ok", "audio-mix", severity="medium")
def sample_rate_ok(art):
    """Sample rate must be a standard speech/music rate at or above the minimum (no 8 kHz stub)."""
    info = art.audio_info
    sr = int(info.get("sample_rate") or 0)
    if sr <= 0:
        skip("ffprobe reported no sample rate")
    ok = sr >= SAMPLE_RATE_MIN_HZ and sr in SAMPLE_RATE_OK_SET
    return (ok, 1.0 if ok else 0.0,
            f"{sr} Hz (min {SAMPLE_RATE_MIN_HZ} Hz, standard set {sorted(SAMPLE_RATE_OK_SET)})")
