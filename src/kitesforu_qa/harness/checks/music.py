"""Music & SFX deterministic checks (Claude-parallel lane) — $0, pure Python over the job doc.

The founder's recurring pain (job 6e507451, 2026-06-10): the pipeline keeps *planning* a music
bed and then the render/mix layer silently DROPS it — "selected but never used" (pipeline rule #1).
The workers already ship an on-device, $0 listening-QA pass that LISTENS to the delivered bytes and
persists a verdict at ``stages["job-audio"]["qa"]["listening"]`` with a ``music`` sub-dict
(``expected`` / ``detected`` / ``method`` / ``fail`` / ``observed_margin_db`` / ``floor_margin_db``)
— see ``kitesforu-workers/.../audio/quality/listening_qa.py``. These checks PIN that verdict so a
future change that re-introduces the silent music-drop fails here (the harness is the discovery
engine), without re-doing the librosa decode (that's the heavy JUDGE tier, not this $0 lane).

The "genre has no music" skip is driven by the DOC's own ``expected`` flag (music_enabled=False or
an empty cue sheet → expected=False) — NOT by the genre profile, because every profiled genre in
``profiles/genre_audio_profiles`` has ``music_presence_pct_min > 0`` (they all expect a bed). Using
the doc's expected flag is the correct, render-grounded applicability signal.

intro/outro music duration is read from ``stages["job-audio"]["result"]["intro_duration_ms"]`` — the
real field the intro generator persists (target 12 s).
"""
from __future__ import annotations

from typing import Any

from ..check import check, skip

# ── named thresholds ──────────────────────────────────────────────────────────
# Music bed must sit ABOVE the speech-only quiet floor by at least this much for the bed to be
# audibly present (the bed fills the inter-speech gaps the speech-only mix leaves near-silent).
# Mirrors the workers' detect_music_presence default (_FLOOR_MARGIN_DB_DEFAULT = 6.0 dB).
_MIN_BED_MARGIN_DB = 6.0
# A bed that lifts the quiet floor by more than this isn't a *bed under speech* any more — it's the
# music drowning the gaps / mastered far too hot. Generous ceiling: speech still dominates < ~24 dB.
_MAX_BED_MARGIN_DB = 24.0

# intro music duration band (ms). Renderer targets 12 s (intro_duration_target_ms=12000); a healthy
# intro is a few seconds to a long-but-sane opener — not a 0 ms drop nor a runaway clip.
_INTRO_MS_MIN = 3_000
_INTRO_MS_MAX = 20_000


def _listening_verdict(art: Any) -> dict[str, Any]:
    """The persisted listening-QA verdict, or {} if the stage never ran for this job."""
    v = (((art.stages.get("job-audio") or {}).get("qa") or {}).get("listening") or {})
    return v if isinstance(v, dict) else {}


def _music_verdict(art: Any) -> dict[str, Any]:
    m = _listening_verdict(art).get("music")
    return m if isinstance(m, dict) else {}


def _audio_result(art: Any) -> dict[str, Any]:
    r = (art.stages.get("job-audio") or {}).get("result") or {}
    return r if isinstance(r, dict) else {}


@check("music.present_when_expected", dimension="music-sfx", severity="high")
def present_when_expected(art):
    "When a music bed was planned, the DELIVERED mix must actually contain it (no silent drop)."
    m = _music_verdict(art)
    if not m:
        skip("no listening-QA music verdict on this job")
    if not m.get("expected"):
        skip("music not expected for this job (music disabled / empty cue sheet)")
    detected = m.get("detected")
    # The certain failure: byte-identical to speech_only => no bed reached the mix (job 6e507451).
    if m.get("fail") or detected is False:
        return False, 0.0, f"music expected but absent (method={m.get('method')}, detected={detected})"
    if detected is None:
        # Inconclusive (couldn't decode) but the byte-identity check already cleared the certain
        # failure — pass with reduced confidence rather than false-fail a missing-codec environment.
        return True, 0.7, f"music expected; detection inconclusive (method={m.get('method')})"
    return True, 1.0, f"music expected and detected (method={m.get('method')})"


@check("music.below_speech", dimension="music-sfx", severity="medium")
def below_speech(art):
    "The music bed must sit UNDER speech — present (floor lifted) yet not drowning it."
    m = _music_verdict(art)
    if not m:
        skip("no listening-QA music verdict on this job")
    if not m.get("expected"):
        skip("music not expected for this job")
    margin = m.get("observed_margin_db")
    if margin is None:
        # No quiet-floor measurement (byte-identical / undecodable / not-expected path).
        skip(f"no quiet-floor margin measured (method={m.get('method')})")
    margin = float(margin)
    ok = _MIN_BED_MARGIN_DB <= margin <= _MAX_BED_MARGIN_DB
    return (
        ok,
        f"bed quiet-floor margin {margin:.1f} dB over speech "
        f"(want {_MIN_BED_MARGIN_DB}-{_MAX_BED_MARGIN_DB} dB: present but under speech)",
    )


@check("music.intro_outro_bounded", dimension="music-sfx", severity="low")
def intro_outro_bounded(art):
    "Intro music duration must be sane — present, not a 0 ms drop nor a runaway clip."
    if not art.stages:
        skip("no stages telemetry on this job")
    intro_ms = _audio_result(art).get("intro_duration_ms")
    if intro_ms is None:
        skip("no intro_duration_ms persisted (genre/format has no intro music)")
    intro_ms = float(intro_ms)
    ok = _INTRO_MS_MIN <= intro_ms <= _INTRO_MS_MAX
    return (
        ok,
        f"intro music {intro_ms / 1000:.1f}s "
        f"(want {_INTRO_MS_MIN / 1000:.0f}-{_INTRO_MS_MAX / 1000:.0f}s)",
    )
