"""Narration-alignment math — does the PICTURE follow the WORDS?

Pure functions, no I/O, no Artifact dependency, no network: every input is a plain list of dicts /
tuples, so all of this is unit-testable offline at $0. The thin ``@check`` wrappers that feed it real
job docs live in ``checks/narration_sync.py``.

WHY THIS MODULE EXISTS (the gap it closes)
------------------------------------------
Two gates already claimed to guard the "talks about one thing, shows another" class, and BOTH are
tautologies that cannot fail:

- ``visual.av_content_sync`` (stamped by the workers pipeline) on witness
  ``f6709ffc-1be9-4fb4-923e-1fd0bf0dbeb8``: ``offender_count=0``, ``median_offset_ms == max_offset_ms
  == 120``, ``checked=22`` of 91 clips. A median exactly equal to the max across 22 dissimilar clips
  is a constant, not a measurement.
- ``video_sync.clips_beat_aligned``: scored **61/61 = 1.00 PASS** on that same job — the one the
  founder pointed at and said "you will see here the mismatch".

Both ask "does the clip start inside its own beat's window?". Placement assigns clips by subdividing
that very window, so the answer is yes BY CONSTRUCTION. The checks are structurally incapable of
seeing the defect they name.

WHAT WE MEASURE INSTEAD
-----------------------
The caption cue track (``visual.captions_vtt``) is the timing spine. **Read its precision honestly**
(corrected 2026-08-06 after reading the producer — an earlier version of this docstring called these
"REAL sentence-level timings", which is only half true):

* **Segment boundaries ARE measured.** ``master_segment_timeline`` is stamped by the audio combiner
  from the synthesized audio (``master_segment_timeline_source == 'combiner'``).
* **Cue splits WITHIN a segment are ESTIMATED.** ``stages/visuals/captions.py:288`` computes
  ``c_end = start + round(span * acc / total)`` — character-proportional, not measured. A segment is
  ~10s and typically splits into 2-3 cues, so an individual cue edge can be off by a second or two.

What that means per metric: ``hold_across_sentences`` counts cues (chunking is by TEXT, so the count
is sound); ``boundary_alignment`` measures against a spine that is exact at segment edges and
approximate within, so read it as "cuts do not track speech" rather than as a millisecond claim; and
``shown_words_lag``'s witness median of 8635ms is an order of magnitude beyond the estimation error,
so that finding survives the correction. Word-level truth would need a TTS alignment payload or a
forced aligner; neither is captured today.

Against that spine:

1. ``hold_across_sentences`` — how many spoken sentences a single distinct picture is held across.
   A picture authored for one idea that sits across several sentences is the founder's complaint.
2. ``boundary_alignment`` — what fraction of visual cuts land ON a sentence edge rather than
   mid-clause. Cutting mid-clause reads as "the visuals aren't following the words".
3. ``starved_clips`` — authored visuals that receive zero screen time.
4. ``shown_words_lag`` — for cards that put SCRIPT TEXT on screen, the distance between when those
   words are DISPLAYED and when they are SPOKEN. This is the most human-visible axis of all: the
   viewer can read one sentence while hearing another. Observed on the witness at t=190s (card:
   "If something's frozen, End task or force quit it"; spoken: "That quiet click before the next app
   opens?"), t=300s and t=410s.

MEASURED FLEET BASELINE (2026-08-06) — command: ``scripts/narration_sync_audit.py --recent 40``.
Population: the 40 most recent ``podcast_jobs`` with ``status == completed``; 38 had both
``visual.clips`` and ``visual.captions_vtt``.

    median sentences one picture is held across : 2.0
    median cuts landing on a sentence boundary  : 27%   (at BOUNDARY_TOLERANCE_MS = 150)
    median cut offset from nearest boundary     : 853ms
    median shown-vs-spoken lag on text cards    : 7423ms
    jobs with >= 1 zero-duration clip           : 22/38

RE-MEASURED after the window-semantics correction (see :func:`delivered_spans`). The earlier run of
these same figures used ``duration_ms`` as the window, which silently excluded every zero-duration
clip — present in 22 of the 38 jobs, i.e. exactly the clips most likely to be mis-timed. The bias was
real and the headline HELD: 2.0 unchanged, 28% -> 27%, 845ms -> 853ms, 7423ms unchanged. Reported
because a number whose method was wrong is worth re-deriving even when the answer survives.

On witness ``f6709ffc`` specifically, of the 7 on-screen text cards whose words are traceable to a
spoken sentence, the median gap between SHOWN and SPOKEN is **8635ms** (7/7 beyond 2s, 5/7 beyond
5s, worst 30135ms).

The thresholds below are deliberately set ABOVE today's fleet median: they describe the bar we intend
to reach, not the bar we clear. See ``checks/narration_sync.py`` for the severity/ratchet policy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

# ── thresholds (single place to tune; justified against the fleet baseline above) ──────────────

#: A picture should track the sentence it illustrates. Two sentences is the fleet median today;
#: 1.5 is the bar — it permits holding across a genuinely continuous pair, not a paragraph.
MAX_SENTENCES_PER_PICTURE = 1.5

#: A cut within this of a sentence edge reads as intentional rather than arbitrary.
BOUNDARY_TOLERANCE_MS = 150

#: Fraction of cuts that must land on a sentence boundary. Fleet median today is 25%.
MIN_BOUNDARY_ALIGNED_FRAC = 0.60

#: A card showing script text must appear within this of the moment those words are spoken. Reading
#: one sentence while hearing another is the most jarring failure mode; the tolerance is deliberately
#: tight. Witness median today is 8635ms.
MAX_SHOWN_WORDS_LAG_MS = 1500

#: Word-overlap required before we claim an on-screen line IS a given spoken sentence. Below this we
#: cannot attribute the text to a moment, so the card is not counted (never guessed).
MIN_TEXT_TRACE_SIMILARITY = 0.5


@dataclass(frozen=True)
class Cue:
    """One spoken sentence with REAL timings from the synthesized audio."""

    start_ms: int
    end_ms: int
    text: str = ""


@dataclass
class HoldResult:
    """How long a single picture stays up, measured in spoken sentences."""

    median_sentences: float = 0.0
    worst_sentences: int = 0
    distinct_assets: int = 0
    assessed_clips: int = 0
    offenders: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.median_sentences <= MAX_SENTENCES_PER_PICTURE

    @property
    def evidence(self) -> str:
        return (
            f"one picture is held across a median of {self.median_sentences:.1f} spoken sentences "
            f"(worst {self.worst_sentences}); {self.distinct_assets} distinct assets over "
            f"{self.assessed_clips} clips; bar is <= {MAX_SENTENCES_PER_PICTURE}"
        )


@dataclass
class BoundaryResult:
    """Whether visual cuts land on sentence edges or mid-clause."""

    aligned_frac: float = 0.0
    median_offset_ms: float = 0.0
    aligned: int = 0
    assessed: int = 0

    @property
    def ok(self) -> bool:
        return self.aligned_frac >= MIN_BOUNDARY_ALIGNED_FRAC

    @property
    def evidence(self) -> str:
        return (
            f"{self.aligned}/{self.assessed} cuts land within {BOUNDARY_TOLERANCE_MS}ms of a spoken "
            f"sentence boundary ({self.aligned_frac:.0%}); median cut sits "
            f"{self.median_offset_ms:.0f}ms into a clause; bar is >= {MIN_BOUNDARY_ALIGNED_FRAC:.0%}"
        )


@dataclass
class StarvedResult:
    """Authored visuals that never reach the screen."""

    starved: int = 0
    total: int = 0
    beat_indexes: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.starved == 0

    @property
    def evidence(self) -> str:
        if not self.starved:
            return f"all {self.total} authored clips receive screen time"
        return (
            f"{self.starved}/{self.total} authored clips receive ZERO screen time "
            f"(beat_index {self.beat_indexes[:8]})"
        )


@dataclass
class ShownWordsResult:
    """Distance between showing a line of script text and speaking it."""

    median_lag_ms: float = 0.0
    worst_lag_ms: int = 0
    traceable: int = 0
    cards: int = 0
    offenders: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.traceable == 0 or self.median_lag_ms <= MAX_SHOWN_WORDS_LAG_MS

    @property
    def evidence(self) -> str:
        if not self.traceable:
            return f"{self.cards} text cards, none traceable to a spoken sentence"
        return (
            f"text cards appear a median of {self.median_lag_ms:.0f}ms from when their words are "
            f"spoken (worst {self.worst_lag_ms}ms) over {self.traceable}/{self.cards} traceable "
            f"cards; bar is <= {MAX_SHOWN_WORDS_LAG_MS}ms"
        )


def delivered_spans(clips: Sequence[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    """The window each clip is actually ON SCREEN for, keyed by its index in ``clips``.

    THE DELIVERED WINDOW IS THE GAP TO THE NEXT DISTINCT START, not ``duration_ms``. That is the
    assembler's own rule (``video_assembler.resolve_bounds`` uses ``end = sf[i+1]``;
    ``pacing/destrobe`` computes ``win = si[i+1] - si[i]``).

    THIS CORRECTS A BIAS IN THIS MODULE'S OWN PUBLISHED NUMBERS. Every metric here used to derive
    its window from ``duration_ms``, which silently DROPPED every zero-duration clip — and 22 of 38
    fleet jobs carry at least one. A ``duration_ms`` of 0 does not mean "not shown"; it means "no
    measured narration window", and the compositor still paints the clip until the next one starts.
    So the fleet figures published from the old code were computed on a sample that excluded exactly
    the clips most likely to be mis-timed.

    Clips stacked at one timestamp do not bound each other (the unanchored-beat cluster — witness
    4d41320d stacks six at 24403), or every one of them would collapse to a zero-width window."""
    rows = []
    for i, clip in enumerate(clips or []):
        if not isinstance(clip, dict):
            continue
        try:
            rows.append((int(clip.get("start_ms")), i, clip))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r[0])
    out: dict[int, tuple[int, int]] = {}
    for n, (start, idx, clip) in enumerate(rows):
        end = next((s for s, _, _ in rows[n + 1 :] if s > start), None)
        if end is None:
            span = _span(clip)
            end = span[1] if span else None
        if end is not None and end > start:
            out[idx] = (start, int(end))
    return out


def _span(clip: dict[str, Any]) -> tuple[int, int] | None:
    """(start_ms, end_ms) for a clip, tolerating end_ms-vs-duration_ms shapes. None if unusable.

    Used only to bound the LAST clip (which has no successor) and by :func:`starved_clips`. Every
    other consumer must use :func:`delivered_spans` — see its docstring for why ``duration_ms`` is
    the wrong window."""
    start = clip.get("start_ms")
    if start is None:
        return None
    try:
        start = int(start)
    except (TypeError, ValueError):
        return None
    end = clip.get("end_ms")
    if end is None:
        dur = clip.get("duration_ms")
        if dur is None:
            return None
        try:
            end = start + int(dur)
        except (TypeError, ValueError):
            return None
    try:
        end = int(end)
    except (TypeError, ValueError):
        return None
    return (start, end) if end > start else None


def _asset_key(clip: dict[str, Any]) -> str | None:
    """Identity of the PICTURE, so re-showing the same asset counts as one hold, not two."""
    for key in ("content_hash", "asset_uri", "image_url"):
        val = clip.get(key)
        if val:
            return str(val)
    return None


def _sentences_touched(start_ms: int, end_ms: int, cues: Sequence[Cue]) -> int:
    """How many spoken sentences overlap [start_ms, end_ms)."""
    return sum(1 for c in cues if c.end_ms > start_ms and c.start_ms < end_ms)


def hold_across_sentences(clips: Iterable[dict[str, Any]], cues: Sequence[Cue]) -> HoldResult:
    """Median number of spoken sentences a single distinct picture is held across.

    Groups by asset identity first: a picture shown, cut away from, and shown again is measured over
    its full on-screen envelope, because that is what the viewer experiences.
    """
    clips = list(clips)
    if not clips or not cues:
        return HoldResult()

    spans = delivered_spans(clips)
    envelope: dict[str, tuple[int, int]] = {}
    assessed = 0
    for i, clip in enumerate(clips):
        span = spans.get(i)
        key = _asset_key(clip)
        if span is None or key is None:
            continue
        assessed += 1
        lo, hi = span
        if key in envelope:
            prev_lo, prev_hi = envelope[key]
            envelope[key] = (min(prev_lo, lo), max(prev_hi, hi))
        else:
            envelope[key] = (lo, hi)

    if not envelope:
        return HoldResult()

    counts: list[int] = []
    offenders: list[dict[str, Any]] = []
    for key, (lo, hi) in envelope.items():
        n = _sentences_touched(lo, hi, cues)
        counts.append(n)
        if n > MAX_SENTENCES_PER_PICTURE:
            offenders.append({"asset": key[:16], "sentences": n, "start_ms": lo, "end_ms": hi})

    offenders.sort(key=lambda o: -o["sentences"])
    return HoldResult(
        median_sentences=float(median(counts)),
        worst_sentences=max(counts),
        distinct_assets=len(envelope),
        assessed_clips=assessed,
        offenders=offenders[:10],
    )


def boundary_alignment(
    clips: Iterable[dict[str, Any]],
    cues: Sequence[Cue],
    tolerance_ms: int = BOUNDARY_TOLERANCE_MS,
) -> BoundaryResult:
    """Fraction of visual cuts that land on a spoken-sentence edge rather than mid-clause."""
    clips = list(clips)
    if not clips or not cues:
        return BoundaryResult()

    boundaries = sorted({c.start_ms for c in cues} | {c.end_ms for c in cues})
    if not boundaries:
        return BoundaryResult()

    spans = delivered_spans(clips)
    offsets: list[int] = []
    for i in range(len(clips)):
        span = spans.get(i)
        if span is None:
            continue
        offsets.append(min(abs(span[0] - b) for b in boundaries))

    if not offsets:
        return BoundaryResult()

    aligned = sum(1 for o in offsets if o <= tolerance_ms)
    return BoundaryResult(
        aligned_frac=aligned / len(offsets),
        median_offset_ms=float(median(offsets)),
        aligned=aligned,
        assessed=len(offsets),
    )


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", (text or "").lower()) if len(w) > 2}


def _similarity(shown: str, spoken: str) -> float:
    """Fraction of the SHOWN line's words that appear in the spoken sentence."""
    a = _words(shown)
    return len(a & _words(spoken)) / len(a) if a else 0.0


def shown_words_lag(cards: Iterable[dict[str, Any]], cues: Sequence[Cue]) -> ShownWordsResult:
    """How far a card's on-screen words sit from the moment those words are spoken.

    ``cards`` are clip dicts that additionally carry a ``text`` key (the on-screen line). A card whose
    text cannot be attributed to any spoken sentence above ``MIN_TEXT_TRACE_SIMILARITY`` is counted
    but not scored — we never guess which moment a line belongs to.
    """
    cards = list(cards)
    if not cards or not cues:
        return ShownWordsResult()

    spans = delivered_spans(cards)
    lags: list[int] = []
    offenders: list[dict[str, Any]] = []
    for i, card in enumerate(cards):
        span = spans.get(i)
        text = card.get("text")
        if span is None or not text:
            continue
        best_cue, best_sim = None, 0.0
        for cue in cues:
            sim = _similarity(text, cue.text)
            if sim > best_sim:
                best_cue, best_sim = cue, sim
        if best_cue is None or best_sim < MIN_TEXT_TRACE_SIMILARITY:
            continue
        lag = abs(span[0] - best_cue.start_ms)
        lags.append(lag)
        if lag > MAX_SHOWN_WORDS_LAG_MS:
            offenders.append(
                {
                    "shown_ms": span[0],
                    "spoken_ms": best_cue.start_ms,
                    "lag_ms": lag,
                    "on_screen": text[:70],
                    "heard": best_cue.text[:70],
                }
            )

    if not lags:
        return ShownWordsResult(cards=len(cards))

    offenders.sort(key=lambda o: -o["lag_ms"])
    return ShownWordsResult(
        median_lag_ms=float(median(lags)),
        worst_lag_ms=max(lags),
        traceable=len(lags),
        cards=len(cards),
        offenders=offenders[:10],
    )


def _normalize(text: str) -> str:
    """Collapse to comparable prose: lowercase, letters/digits/spaces only, single-spaced.

    Deliberately NOT a similarity score. The card sentence is authored FROM the script, so when
    the pairing is right the sentence appears in a segment VERBATIM; normalization only absorbs
    punctuation/casing/whitespace differences introduced by rendering.
    """
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def card_provenance_lag(
    cards: Iterable[dict[str, Any]],
    segments: Sequence[tuple[int, int, str]],
) -> ShownWordsResult:
    """EXACT-provenance lag: how late a card appears relative to when its own sentence is SPOKEN.

    ``cards`` are clip dicts carrying ``card_text`` (the rendered sentence, persisted by
    workers ``beat_restamp``). ``segments`` are ``(start_ms, end_ms, text_full)`` from
    ``segments_ready`` joined to the REAL offsets in ``master_segment_timeline`` — which is a
    LIST of ``{index,start_ms,end_ms}``, not a dict; reading it as a dict silently yields gapless
    offsets and a wrong answer (that mistake produced a retracted 260ms median).

    WHY EXACT AND NOT :func:`shown_words_lag`'s similarity. The fuzzy sibling needs a threshold,
    and thresholded keyword scoring is precisely what made workers PR #2153 anchor 498 of 978
    beats to narration that mentions nothing of them (precision 89% -> 61%). Here a card is
    scored ONLY when its normalized sentence is a SUBSTRING of a segment's normalized text. No
    match -> not scored, never guessed. That is why this can be trusted to drive a fix.

    Witness ``19327d06``: the card at 21188ms renders "Textbooks miss it: not all sand makes good
    concrete." and seg1 (4523-10289ms) speaks it verbatim -> a ~11s lag, exactly reproducible.
    """
    cards = list(cards)
    if not cards or not segments:
        return ShownWordsResult()

    norm_segments = [(a, b, _normalize(t)) for a, b, t in segments if t]
    spans = delivered_spans(cards)
    lags: list[int] = []
    offenders: list[dict[str, Any]] = []
    for i, card in enumerate(cards):
        span = spans.get(i)
        text = str(card.get("card_text") or "").strip()
        if span is None or not text:
            continue
        needle = _normalize(text)
        if not needle:
            continue
        owner = next(((a, b) for a, b, hay in norm_segments if needle in hay), None)
        if owner is None:
            continue  # not spoken verbatim anywhere -> NOT scored, never guessed
        lag = abs(span[0] - owner[0])
        lags.append(lag)
        if lag > MAX_SHOWN_WORDS_LAG_MS:
            offenders.append(
                {
                    "shown_ms": span[0],
                    "spoken_ms": owner[0],
                    "lag_ms": lag,
                    "on_screen": text[:70],
                }
            )

    if not lags:
        return ShownWordsResult(cards=len(cards))
    offenders.sort(key=lambda o: -o["lag_ms"])
    return ShownWordsResult(
        median_lag_ms=float(median(lags)),
        worst_lag_ms=max(lags),
        traceable=len(lags),
        cards=len(cards),
        offenders=offenders[:10],
    )


def _is_starved(clip: dict[str, Any]) -> bool:
    """True when a clip can occupy no screen time at all.

    Deliberately does NOT require ``start_ms``: a clip that is zero-length AND unplaced is the WORST
    case, not an unmeasurable one. An earlier version of this guard required a start time and so
    silently skipped exactly those clips — it reported 7/38 fleet jobs affected where the true count
    is 22/38 (16 jobs carry clips with no ``start_ms``).
    """
    duration = clip.get("duration_ms")
    if duration is not None:
        try:
            return int(duration) <= 0
        except (TypeError, ValueError):
            return True
    start, end = clip.get("start_ms"), clip.get("end_ms")
    if start is not None and end is not None:
        try:
            return int(end) <= int(start)
        except (TypeError, ValueError):
            return True
    return True  # no duration and no end — nothing says this clip is ever on screen


def starved_clips(clips: Iterable[dict[str, Any]]) -> StarvedResult:
    """Authored visuals that receive zero screen time (duration <= 0, end <= start, or no length)."""
    clips = list(clips)
    if not clips:
        return StarvedResult()

    starved = [c for c in clips if _is_starved(c)]
    return StarvedResult(
        starved=len(starved),
        total=len(clips),
        beat_indexes=[c.get("beat_index") for c in starved],
    )
