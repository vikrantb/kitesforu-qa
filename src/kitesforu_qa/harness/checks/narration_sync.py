"""Narration-sync checks — does the PICTURE follow the WORDS? ($0, deterministic timing math.)

Thin ``@check`` wrappers over the pure math in ``..narration_alignment``. All the logic (and all the
unit tests) live there; this file only adapts a real ``Artifact`` to those pure functions.

WHY THESE EXIST ALONGSIDE ``video_sync``
----------------------------------------
``video_sync`` asks "is the VIDEO in sync with the AUDIO" (stream-level: duration, caption coverage,
gaps). These ask "is the PICTURE about what is being SAID" (content-level). Different question, so a
separate module rather than more branches in a 1000-line file.

``video_sync.clips_beat_aligned`` already claims the "talks-one/shows-other" class, but it scored
**61/61 = 1.00 PASS** on witness ``f6709ffc-1be9-4fb4-923e-1fd0bf0dbeb8`` — the exact job the founder
pointed at and said "you will see here the mismatch". It only asks whether a clip starts inside its
own beat's window, and placement assigns clips by subdividing that window, so it cannot fail. The
same is true of the pipeline-stamped ``visual.av_content_sync`` (``offender_count=0``,
``median_offset_ms == max_offset_ms == 120``). These four checks all FAIL on that witness.

SEVERITY POLICY (deliberate, not an oversight)
---------------------------------------------
Registered at ``medium`` — scored, reported, but NOT gating (``battery._GATING`` is
``{critical, high}``). The measured fleet baseline is that essentially every current job fails these
axes, so registering them as gating in the same change that introduces them would turn every existing
QA consumer red at once (``morning_proof_batch``, ``quality_matrix`` baselines,
``fleet_drift_sentinel``) — a real regression to working tooling.

**RATCHET:** these move to ``high`` in the final PR of the narration-bound-visual-timing campaign,
once the fix lands and the fleet can actually clear the bar. Tracked in
``kitesforu-docs/proposals/narration-bound-visual-timing-2026-08-06/PROPOSAL.md``.
"""

from __future__ import annotations

from typing import Any

from ..check import check, skip
from ..narration_alignment import (
    Cue,
    boundary_alignment,
    hold_across_sentences,
    shown_words_lag,
    starved_clips,
)
from .video_sync import _clips, _parse_vtt_cues

_DIMENSION = "narration-sync"


def _cues(art: Any) -> list[Cue]:
    """Spoken sentences with REAL timings, from the caption track the renderer burned in."""
    vtt = getattr(art, "captions_vtt", None)
    if not vtt:
        visual = getattr(art, "visual", None) or {}
        vtt = visual.get("captions_vtt") or (art.doc or {}).get("captions_vtt")
    if not vtt:
        return []
    return [Cue(c["start_ms"], c["end_ms"], c.get("text") or "") for c in _parse_vtt_cues(vtt)]


def _on_screen_text(art: Any, beat_index: Any) -> str | None:
    """The line a card puts on screen, from the art-director enrichment keyed by beat index."""
    enrichment = (art.doc or {}).get("_art_director_enrichment") or {}
    entry = enrichment.get(str(beat_index)) or {}
    return entry.get("on_screen_text") if isinstance(entry, dict) else None


def _require_spine(art: Any) -> tuple[list[dict[str, Any]], list[Cue]]:
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    cues = _cues(art)
    if not cues:
        skip("no caption cues — no narration timing spine to measure against")
    return clips, cues


@check("narration_sync.picture_tracks_the_sentence", dimension=_DIMENSION, severity="medium")
def picture_tracks_the_sentence(art):
    "A single picture must not sit across several spoken sentences while the words move on."
    clips, cues = _require_spine(art)
    result = hold_across_sentences(clips, cues)
    if not result.distinct_assets:
        skip("no clips carrying an asset identity")
    return result.ok, result.median_sentences, result.evidence


@check("narration_sync.cuts_respect_speech", dimension=_DIMENSION, severity="medium")
def cuts_respect_speech(art):
    "Visual cuts must land on spoken-sentence edges, not mid-clause."
    clips, cues = _require_spine(art)
    result = boundary_alignment(clips, cues)
    if not result.assessed:
        skip("no clips with usable start times")
    return result.ok, result.aligned_frac, result.evidence


@check("narration_sync.shown_words_are_spoken_words", dimension=_DIMENSION, severity="medium")
def shown_words_are_spoken_words(art):
    "A card's on-screen text must appear while those words are being spoken, not seconds away."
    clips, cues = _require_spine(art)
    cards = []
    for clip in clips:
        text = _on_screen_text(art, clip.get("beat_index"))
        if text:
            cards.append({**clip, "text": text})
    if not cards:
        skip("no cards carrying on-screen text")
    result = shown_words_lag(cards, cues)
    if not result.traceable:
        skip("no on-screen line could be attributed to a spoken sentence")
    return result.ok, float(result.median_lag_ms), result.evidence


@check("narration_sync.every_visual_gets_screen_time", dimension=_DIMENSION, severity="medium")
def every_visual_gets_screen_time(art):
    "An authored visual must actually reach the screen (no zero-duration clips)."
    clips = _clips(art)
    if not clips:
        skip("no visual clips")
    result = starved_clips(clips)
    return result.ok, float(result.total - result.starved), result.evidence
