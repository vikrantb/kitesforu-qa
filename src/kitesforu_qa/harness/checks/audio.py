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

# Loudness range (LRA) band: how wide the genre profile's dynamics window may be, plus slack. Catches
# BOTH over-compression (a horror master flattened to ~4 LU — the dynamics are dead) AND too-wide
# (a bedtime master that should be gentle/narrow). The band comes from the SSOT genre profile.
LRA_GENRE_SLACK_LU = 1.0             # extra slack added on top of the profile's own lra_slack_lu

# Per-segment WAV-as-MP3 decode-collapse guard. A premium-tier WAV mis-decoded as MP3 collapses a
# segment to a few ms (the 2026-06-19 incident: master 10.5s instead of 292s). Any segment that has
# real spoken text must render to at least this many ms — below it is a decode collapse, not a beat.
SEGMENT_MIN_DURATION_MS = 400.0
SEGMENT_MIN_TEXT_CHARS = 8           # a segment with >= this many text chars must have real duration

# Master-vs-segment-sum: the hard, drift-free WAV/PCM-as-MP3 decode-collapse invariant. The final
# master duration must equal the sum of the rendered segment durations within this fraction. (This is
# stronger than the script-word proxy in not_truncated — it compares ACTUAL rendered segment ms.)
MASTER_SEGMENT_SUM_TOLERANCE = 0.05  # |master - Σ segments| / Σ segments must be <= this

# Duration-vs-requested undershoot (the 5-min-request-ships-91s class). The rendered master must be
# at least this fraction of the user-requested (or architect-sized) duration.
DURATION_REQUEST_MIN_RATIO = 0.60

# Restrained-genre expression: analytical genres (news, tech_review, sports) must not be performed
# with high-arousal TTS tags. A small fraction is tolerated for sports (a touch of energy is fine).
_EXCITED_TAGS = ("excited", "dramatic", "intense", "playful", "ecstatic", "frantic")
EXCITED_TAG_MAX_FRACTION = 0.10      # fraction of dialogue items allowed to carry an excited tag
# TTS rate ceiling for analytical genres — anything above this is "cable-news acceleration".
RATE_CAP = 1.05
# Dramatic-pause ceiling (ms) for analytical genres — a long suspense beat doesn't belong in news.
DRAMATIC_PAUSE_MAX_MS = 500.0

# Bedtime master ceilings (far stricter than the generic -1.0 dBFS — sleep retention dies on level
# surprise). Bedtime true-peak ceiling is -3.0 dBTP in the profile; assert with a hair of slack.
BEDTIME_TRUE_PEAK_MAX_DBTP = -2.8
# Onset-spike guard: no 200ms RMS window may jump more than this above the trailing-1s average.
BEDTIME_ONSET_SPIKE_MAX_DB = 9.0
BEDTIME_ONSET_WINDOW_S = 0.200       # RMS window length for the onset scan
BEDTIME_ONSET_TRAILING_S = 1.0       # trailing average window the spike is measured against

_FFMPEG_TIMEOUT_S = 120


# ── ffmpeg stats (the GAP-1 self-serve; cached per audio_path) ───────────────


class _AudioStats:
    """Peak / loudness / silence / per-channel metrics for one file, via ffmpeg filters.

    All values may be None if ffmpeg failed or the metric was not reported — every consuming
    check guards on None and ``skip()``s rather than reporting a false failure.
    """

    __slots__ = (
        "max_volume_db", "mean_volume_db", "lufs_i", "lra_lu", "true_peak_db",
        "long_silence_gaps", "channel_rms_db",
    )

    def __init__(self) -> None:
        self.max_volume_db: float | None = None      # volumedetect peak (sample peak, dBFS)
        self.mean_volume_db: float | None = None
        self.lufs_i: float | None = None             # ebur128 integrated loudness
        self.lra_lu: float | None = None             # ebur128 loudness range (LU)
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
# Loudness range appears in the ebur128 Summary block ("    LRA:    7.3 LU"). Same anchoring
# discipline as the integrated-loudness line: read the summary-form (leading whitespace, no
# "[Parsed..]" prefix), and take the last match (the Summary block is emitted last).
_RE_LRA_SUMMARY = re.compile(r"^\s+LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", re.MULTILINE)
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
    # Loudness range (LRA): summary-form line, last match = the final Summary value.
    lra_matches = _RE_LRA_SUMMARY.findall(out1)
    if lra_matches:
        s.lra_lu = float(lra_matches[-1])
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


# ── catalog long-tail (LRA band · per-segment + master WAV-class guards · duration · per-genre) ──


def _segment_durations_s(art) -> list[float]:
    """Per-segment durations in seconds from segments_ready[].duration_ms. Empty if none recorded."""
    out: list[float] = []
    for seg in art.segments:
        if not isinstance(seg, dict):
            continue
        ms = seg.get("duration_ms")
        if ms is None:
            continue
        try:
            out.append(float(ms) / 1000.0)
        except (TypeError, ValueError):
            continue
    return out


def _requested_duration_min(art) -> float | None:
    """User-requested (or architect-sized) target duration in minutes, or None if not recorded.

    Prefers outline.duration_min (architect-sized, the value the pipeline actually renders to), then
    falls back to inputs.duration_min (the raw user request) — same precedence as verify_live."""
    for path in (("outline", "duration_min"), ("inputs", "duration_min"),
                 ("episode_profile", "duration_min")):
        cur: object = art.doc
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
        if not isinstance(cur, (int, float, str)):
            continue  # missing, None, or a non-scalar shape — not a duration
        try:
            val = float(cur)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return val
    return None


def _dialogue_tag_text(d: dict) -> str:
    """The lowercased searchable text for a dialogue item's expression — emotion/expression fields
    plus the spoken text itself (inline ``[excited]``-style tags live in the text)."""
    parts = [
        str(d.get("emotion") or ""),
        str(d.get("expression") or ""),
        str(d.get("tone") or ""),
        str(d.get("text") or d.get("line") or ""),
    ]
    return " ".join(parts).lower()


def _item_rate(d: dict) -> float | None:
    """The TTS speaking rate for a dialogue item, from rate / voice_settings.rate. None if absent."""
    r = d.get("rate")
    if r is None:
        vs = d.get("voice_settings")
        if isinstance(vs, dict):
            r = vs.get("rate") or vs.get("speed")
    if r is None:
        r = d.get("speed")
    if r is None:
        return None
    try:
        return float(r)
    except (TypeError, ValueError):
        return None


_RE_PAUSE_MS = re.compile(r"\b(?:pause|break|silence)[^0-9]{0,12}(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
_RE_BREAK_S = re.compile(r"<break\s+time=[\"']?(\d+(?:\.\d+)?)s", re.IGNORECASE)


def _item_pause_ms(d: dict) -> list[float]:
    """All explicit pause/break durations (ms) referenced in a dialogue item's text/break field."""
    text = str(d.get("text") or d.get("line") or "")
    found = [float(x) for x in _RE_PAUSE_MS.findall(text)]
    found += [float(x) * 1000.0 for x in _RE_BREAK_S.findall(text)]
    b = d.get("break_ms") or d.get("pause_ms")
    if b is not None:
        try:
            found.append(float(b))
        except (TypeError, ValueError):
            pass
    return found


@check("audio.lra_in_genre_band", "audio-mix", severity="medium")
def lra_in_genre_band(art):
    """Loudness range (dynamics width) must land inside the genre's LRA band (with slack).

    Catches over-compression (a horror master flattened to ~4 LU = dead dynamics) AND a too-wide
    mix for a genre that should be gentle/narrow (bedtime). One banded check covers every genre
    that has a profile; unknown genres fall back to the default profile's band."""
    s = _stats(art)
    lra = s.lra_lu
    if lra is None or lra < 0:
        skip("ffmpeg reported no loudness range (LRA)")
    try:
        from ...profiles.genre_audio_profiles import get_profile
        prof = get_profile(art.genre)
        slack = float(prof.lra_slack_lu) + LRA_GENRE_SLACK_LU
        lo = float(prof.lra_min) - slack
        hi = float(prof.lra_max) + slack
    except Exception:  # noqa: BLE001
        skip("no genre LRA profile available")
    in_band = lo <= lra <= hi
    return (in_band, 1.0 if in_band else 0.0,
            f"LRA {lra:.1f} LU vs band [{lo:.1f}, {hi:.1f}] (genre={art.genre})")


@check("audio.no_degenerate_segment", "audio-mix", severity="critical")
def no_degenerate_segment(art):
    """No single rendered segment with real text collapsed to a few ms (per-segment WAV-decode class).

    A premium-tier WAV mis-decoded as MP3 yields ~8ms for a segment that should be seconds long. The
    master-level not_truncated check can pass while one segment hides this collapse — this is the
    per-segment floor. skip() if there is no audio or no segments_ready on the doc."""
    if not art.has_audio:
        skip("no audio on artifact (audio-mix dimension is N/A)")
    segs = [s for s in art.segments if isinstance(s, dict)]
    if not segs:
        skip("no segments_ready on the doc")
    bad: list[tuple[int, float]] = []
    checked = 0
    for i, seg in enumerate(segs):
        text = str(seg.get("text") or seg.get("text_preview") or "").strip()
        if len(text) < SEGMENT_MIN_TEXT_CHARS:
            continue  # silence anchor / trivial segment — no real duration expected
        ms = seg.get("duration_ms")
        if ms is None:
            continue
        try:
            ms_f = float(ms)
        except (TypeError, ValueError):
            continue
        checked += 1
        if ms_f < SEGMENT_MIN_DURATION_MS:
            bad.append((i, ms_f))
    if checked == 0:
        skip("no segments carry both text and a duration_ms")
    ok = not bad
    worst = f"; worst seg#{bad[0][0]}={bad[0][1]:.0f}ms" if bad else ""
    return (ok, 1.0 if ok else 0.0,
            f"{len(bad)}/{checked} text segment(s) below {SEGMENT_MIN_DURATION_MS:.0f}ms{worst}")


@check("audio.master_matches_segment_sum", "audio-mix", severity="critical")
def master_matches_segment_sum(art):
    """Master duration must equal the SUM of rendered segment durations (WAV/PCM-as-MP3 decode class).

    The highest-value, drift-free decode-collapse guard: when a premium WAV is mis-decoded as MP3 the
    master collapses (10.5s vs 292s) while the segment durations on the doc stay correct — so master
    vs Σ-segments diverges hard. Compares ACTUAL rendered segment ms (not the soft word proxy in
    not_truncated). skip() if no segment durations recorded or no master duration."""
    seg_s = _segment_durations_s(art)
    if not seg_s:
        skip("no segment durations on the doc")
    total = sum(seg_s)
    if total <= 0:
        skip("segment durations sum to zero")
    master = art.audio_duration_s
    if master <= 0:
        skip("no master audio duration (ffprobe gave 0)")
    rel = abs(master - total) / total
    ok = rel <= MASTER_SEGMENT_SUM_TOLERANCE
    score = max(0.0, min(1.0, 1.0 - rel))
    return (ok, score,
            f"master {master:.1f}s vs Σ{len(seg_s)} segments {total:.1f}s "
            f"(|Δ|/Σ={rel:.2%}, tol {MASTER_SEGMENT_SUM_TOLERANCE:.0%})")


@check("audio.duration_vs_requested", "audio-mix", severity="medium")
def duration_vs_requested(art):
    """Rendered master must be at least a floor fraction of the requested duration (undershoot class).

    The 5-min-request-ships-91s bug: the architect-sized/user-requested duration_min propagates but
    the LLM undershoots. skip() if no requested duration is recorded (older jobs)."""
    req_min = _requested_duration_min(art)
    if req_min is None:
        skip("no requested duration_min on the doc")
    master = art.audio_duration_s
    if master <= 0:
        skip("no master audio duration (ffprobe gave 0)")
    req_s = req_min * 60.0
    ratio = master / req_s if req_s > 0 else 1.0
    ok = ratio >= DURATION_REQUEST_MIN_RATIO
    return (ok, max(0.0, min(1.0, ratio)),
            f"{master:.1f}s vs requested {req_s:.0f}s ({req_min:g}min) "
            f"ratio {ratio:.2f} (min {DURATION_REQUEST_MIN_RATIO})")


# ── per-genre expression/rate/pause restraint (news / tech_review / sports) ──────────────────────


def _check_restrained_tags(art):
    """A restrained/analytical genre must not be performed with high-arousal TTS tags."""
    items = [d for d in art.dialogue if isinstance(d, dict)]
    tagged = [d for d in items if any(d.get(k) is not None for k in ("emotion", "expression", "tone"))
              or "[" in str(d.get("text") or "")]
    if not tagged:
        skip("no emotion/expression metadata on dialogue")
    excited = sum(1 for d in items if any(t in _dialogue_tag_text(d) for t in _EXCITED_TAGS))
    frac = excited / len(items) if items else 0.0
    ok = frac <= EXCITED_TAG_MAX_FRACTION
    return (ok, 1.0 if ok else max(0.0, 1.0 - frac),
            f"{excited}/{len(items)} item(s) carry an excited tag "
            f"(frac {frac:.0%}, max {EXCITED_TAG_MAX_FRACTION:.0%})")


def _check_rate_capped(art):
    """No dialogue item may exceed the TTS rate ceiling (cable-news acceleration tell)."""
    rates = [(i, r) for i, d in enumerate(art.dialogue)
             if isinstance(d, dict) and (r := _item_rate(d)) is not None]
    if not rates:
        skip("no rate/speed metadata on dialogue")
    over = [(i, r) for i, r in rates if r > RATE_CAP]
    ok = not over
    worst = f"; worst item#{over[0][0]} rate={over[0][1]:.2f}" if over else ""
    return (ok, 1.0 if ok else 0.0,
            f"{len(over)}/{len(rates)} item(s) over rate {RATE_CAP}{worst}")


def _check_no_long_pause(art):
    """No analytical-genre item may carry a dramatic-pause longer than the ceiling."""
    items = [d for d in art.dialogue if isinstance(d, dict)]
    pauses = [(i, p) for i, d in enumerate(items) for p in _item_pause_ms(d)]
    if not pauses:
        skip("no explicit pause/break tags on dialogue")
    over = [(i, p) for i, p in pauses if p > DRAMATIC_PAUSE_MAX_MS]
    ok = not over
    worst = f"; worst item#{over[0][0]}={over[0][1]:.0f}ms" if over else ""
    return (ok, 1.0 if ok else 0.0,
            f"{len(over)}/{len(pauses)} pause(s) over {DRAMATIC_PAUSE_MAX_MS:.0f}ms{worst}")


for _genre in ("news", "tech_review", "sports"):
    check(f"audio.no_excited_tags_in_restrained_genre.{_genre}", "audio-mix",
          genre=_genre, severity="medium",
          description="Analytical genre must not be performed with high-arousal TTS tags.",
          )(_check_restrained_tags)
    check(f"audio.rate_capped.{_genre}", "audio-mix",
          genre=_genre, severity="low",
          description="No dialogue item exceeds the rate ceiling (cable-news acceleration).",
          )(_check_rate_capped)

for _genre in ("news", "sports"):
    check(f"audio.no_long_dramatic_pause.{_genre}", "audio-mix",
          genre=_genre, severity="low",
          description="No long dramatic pause belongs in an analytical genre.",
          )(_check_no_long_pause)


# ── bedtime-specific master ceilings (quietest profile pinned beyond the generic band) ──────────


@check("audio.bedtime_true_peak_quiet", "audio-mix", genre="bedtime", severity="critical")
def bedtime_true_peak_quiet(art):
    """Bedtime master true-peak must sit well below the generic ceiling (no level surprise = sleep)."""
    s = _stats(art)
    peak = s.true_peak_db if s.true_peak_db is not None else s.max_volume_db
    if peak is None:
        skip("ffmpeg reported no peak level")
    ok = peak <= BEDTIME_TRUE_PEAK_MAX_DBTP
    score = max(0.0, min(1.0, (BEDTIME_TRUE_PEAK_MAX_DBTP - peak) / 6.0 + 0.5)) if ok else 0.0
    return (ok, score,
            f"true peak {peak:.2f} dBTP (bedtime ceiling {BEDTIME_TRUE_PEAK_MAX_DBTP} dBTP)")


@check("audio.bedtime_no_onset_spikes", "audio-mix", genre="bedtime", severity="high")
def bedtime_no_onset_spikes(art):
    """No sudden loudness spike vs the trailing average (a startle = a woken sleeper).

    Slides a 200ms RMS window over the master (ffmpeg astats per-window, $0) and asserts no window
    jumps more than the spike ceiling above the trailing-1s average. Slower than the other checks
    (one extra ffmpeg pass) but the single most important bedtime guard."""
    path = art.audio_path
    if not path:
        skip("no audio file on artifact")
    win = BEDTIME_ONSET_WINDOW_S
    out = _run_ffmpeg([
        "-i", path,
        "-af", f"asetnsamples=n={int(win * 48000)}:p=0,astats=metadata=1:reset=1,"
               "ametadata=print:key=lavfi.astats.Overall.RMS_level",
    ])
    rms_vals: list[float] = []
    for raw in re.findall(r"lavfi\.astats\.Overall\.RMS_level=(-?\d+(?:\.\d+)?|-?inf|nan)", out):
        if raw.lower() in ("inf", "-inf", "nan"):
            rms_vals.append(-90.0)  # treat a silent window as a floor level
            continue
        rms_vals.append(float(raw))
    if len(rms_vals) < 10:
        skip("not enough RMS windows to assess onset spikes")
    trailing_n = max(1, int(BEDTIME_ONSET_TRAILING_S / win))
    worst_jump = 0.0
    worst_at = 0
    for i in range(trailing_n, len(rms_vals)):
        trailing = rms_vals[i - trailing_n:i]
        avg = sum(trailing) / len(trailing)
        jump = rms_vals[i] - avg
        if jump > worst_jump:
            worst_jump, worst_at = jump, i
    ok = worst_jump <= BEDTIME_ONSET_SPIKE_MAX_DB
    return (ok, 1.0 if ok else 0.0,
            f"worst onset jump {worst_jump:.1f} dB at window {worst_at} "
            f"(max {BEDTIME_ONSET_SPIKE_MAX_DB} dB)")
