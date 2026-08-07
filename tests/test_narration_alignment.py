"""Unit tests for the narration-alignment math — does the PICTURE follow the WORDS?

Every metric here is premise-tested in BOTH directions: it must go RED on the broken shape measured
on witness ``f6709ffc-1be9-4fb4-923e-1fd0bf0dbeb8`` and GREEN on an aligned one. A gate that cannot
fail is worse than no gate — the two gates this module replaces (``visual.av_content_sync`` and
``video_sync.clips_beat_aligned``) both scored a perfect PASS on that witness while the founder was
looking at the mismatch, because both only ask "is the clip inside its own beat window?", which
placement guarantees by construction.
"""

from __future__ import annotations

from kitesforu_qa.harness import narration_alignment as na
from kitesforu_qa.harness.narration_alignment import (
    MAX_SENTENCES_PER_PICTURE,
    MAX_SHOWN_WORDS_LAG_MS,
    MIN_BOUNDARY_ALIGNED_FRAC,
    Cue,
    boundary_alignment,
    hold_across_sentences,
    shown_words_lag,
    starved_clips,
)

# Five spoken sentences, 2s each, back to back — the timing spine.
CUES = [
    Cue(0, 2000, "Your fans can be loud while the CPU is barely doing anything."),
    Cue(2000, 4000, "That usually means the machine is busy with junk you did not ask for."),
    Cue(4000, 6000, "A dozen little background tasks quietly stealing time."),
    Cue(6000, 8000, "Open Task Manager and sort by CPU to find the culprit."),
    Cue(8000, 10000, "One stuck browser tab is very often the whole problem."),
]


def _clip(start: int, dur: int, asset: str, **extra):
    return {"start_ms": start, "duration_ms": dur, "content_hash": asset, **extra}


class TestHoldAcrossSentences:
    """One picture must track one idea, not sit across a paragraph."""

    def test_one_picture_per_sentence_passes(self):
        clips = [_clip(i * 2000, 2000, f"asset{i}") for i in range(5)]
        r = hold_across_sentences(clips, CUES)
        assert r.median_sentences == 1.0
        assert r.ok
        assert r.distinct_assets == 5

    def test_the_witness_shape_fails(self):
        """One asset held across the whole span — the measured f6709ffc beat0 defect (10.5s->32s)."""
        clips = [_clip(i * 2000, 2000, "same-asset") for i in range(5)]
        r = hold_across_sentences(clips, CUES)
        assert r.median_sentences == 5.0
        assert not r.ok, "a picture held across 5 sentences must FAIL"
        assert r.worst_sentences == 5
        assert r.offenders and r.offenders[0]["sentences"] == 5

    def test_repeat_of_same_asset_is_measured_over_its_full_envelope(self):
        """Shown, cut away, shown again = one hold spanning both — that is what the viewer sees."""
        clips = [_clip(0, 2000, "A"), _clip(2000, 2000, "B"), _clip(4000, 2000, "A")]
        r = hold_across_sentences(clips, CUES)
        assert r.distinct_assets == 2
        # A's envelope is 0..6000 -> 3 sentences; B's is 2000..4000 -> 1.
        assert r.worst_sentences == 3

    def test_threshold_is_the_documented_bar(self):
        assert MAX_SENTENCES_PER_PICTURE == 1.5


class TestBoundaryAlignment:
    """Cuts must land on sentence edges, not mid-clause."""

    def test_cuts_on_boundaries_pass(self):
        clips = [_clip(i * 2000, 2000, f"a{i}") for i in range(5)]
        r = boundary_alignment(clips, CUES)
        assert r.aligned_frac == 1.0
        assert r.median_offset_ms == 0
        assert r.ok

    def test_mid_clause_cuts_fail(self):
        """845ms into a clause is the measured fleet median (n=38 jobs)."""
        clips = [_clip(i * 2000 + 845, 2000, f"a{i}") for i in range(5)]
        r = boundary_alignment(clips, CUES)
        assert r.median_offset_ms == 845
        assert r.aligned_frac == 0.0
        assert not r.ok

    def test_within_tolerance_still_counts_as_aligned(self):
        clips = [_clip(i * 2000 + 100, 2000, f"a{i}") for i in range(5)]
        assert boundary_alignment(clips, CUES).aligned_frac == 1.0

    def test_threshold_is_the_documented_bar(self):
        assert MIN_BOUNDARY_ALIGNED_FRAC == 0.60


class TestShownWordsLag:
    """The words on screen must be the words being spoken."""

    def test_card_shown_while_its_words_are_spoken_passes(self):
        cards = [{"start_ms": 6000, "duration_ms": 2000, "text": "Open Task Manager and sort by CPU"}]
        r = shown_words_lag(cards, CUES)
        assert r.traceable == 1
        assert r.median_lag_ms == 0
        assert r.ok

    def test_the_witness_defect_fails(self):
        """t=190s on the witness: the card showed sentence 4's words during sentence 1."""
        cards = [{"start_ms": 0, "duration_ms": 2000, "text": "Open Task Manager and sort by CPU"}]
        r = shown_words_lag(cards, CUES)
        assert r.median_lag_ms == 6000
        assert not r.ok, "reading one sentence while hearing another must FAIL"
        assert r.offenders[0]["lag_ms"] == 6000

    def test_untraceable_text_is_counted_but_never_guessed(self):
        cards = [{"start_ms": 0, "duration_ms": 2000, "text": "Sponsored by a totally unrelated brand"}]
        r = shown_words_lag(cards, CUES)
        assert r.cards == 1
        assert r.traceable == 0
        assert r.ok, "we cannot attribute it, so we must not accuse it"

    def test_threshold_is_the_documented_bar(self):
        assert MAX_SHOWN_WORDS_LAG_MS == 1500


class TestStarvedClips:
    """Authored visuals that never reach the screen — 22/38 fleet jobs carry at least one."""

    def test_all_clips_visible_passes(self):
        clips = [_clip(i * 2000, 2000, f"a{i}") for i in range(3)]
        assert starved_clips(clips).ok

    def test_zero_duration_clip_is_caught(self):
        clips = [_clip(0, 2000, "a"), _clip(2000, 0, "b", beat_index=7)]
        r = starved_clips(clips)
        assert r.starved == 1
        assert not r.ok
        assert r.beat_indexes == [7]

    def test_zero_length_and_unplaced_is_the_worst_case_not_a_skipped_one(self):
        """Regression pin: an earlier guard required start_ms and hid these (7/38 vs the true 22/38)."""
        clips = [_clip(0, 2000, "a"), {"duration_ms": 0, "content_hash": "b", "beat_index": 9}]
        r = starved_clips(clips)
        assert r.starved == 1, "a clip with no start AND no length must count as starved"
        assert r.beat_indexes == [9]

    def test_end_before_start_is_starved(self):
        clips = [{"start_ms": 5000, "end_ms": 5000, "content_hash": "a", "beat_index": 3}]
        assert starved_clips(clips).starved == 1

    def test_clip_with_no_length_information_at_all_is_starved(self):
        clips = [{"start_ms": 1000, "content_hash": "a", "beat_index": 4}]
        assert starved_clips(clips).starved == 1


class TestDegenerateInputsFailSoft:
    """No crash, no false accusation, on any shape the pipeline can emit."""

    def test_no_cues(self):
        clips = [_clip(0, 2000, "a")]
        assert hold_across_sentences(clips, []).median_sentences == 0.0
        assert boundary_alignment(clips, []).assessed == 0
        assert shown_words_lag([{**clips[0], "text": "x"}], []).traceable == 0

    def test_no_clips(self):
        assert hold_across_sentences([], CUES).distinct_assets == 0
        assert boundary_alignment([], CUES).assessed == 0
        assert starved_clips([]).ok

    def test_missing_and_malformed_fields_are_skipped_not_crashed(self):
        clips = [
            {"duration_ms": 2000},                                  # no start
            {"start_ms": "abc", "duration_ms": 2000},               # unparseable start
            {"start_ms": 0},                                        # no duration or end
            {"start_ms": 0, "duration_ms": 2000},                   # no asset identity
            _clip(0, 2000, "good"),
        ]
        r = hold_across_sentences(clips, CUES)
        assert r.assessed_clips == 1
        assert boundary_alignment(clips, CUES).assessed >= 1

    def test_end_ms_shape_is_accepted_as_well_as_duration_ms(self):
        clips = [{"start_ms": 0, "end_ms": 2000, "content_hash": "a"}]
        assert hold_across_sentences(clips, CUES).assessed_clips == 1


# -- card_provenance_lag: EXACT provenance, never a guess ------------------------------

class TestCardProvenanceLag:
    """The witness, reproduced. Card at 21188ms renders "Textbooks miss it: not all sand makes
    good concrete."; seg1 (4523-10289ms) SPEAKS that sentence verbatim. The picture arrives
    16665ms after its own sentence begins.

    This exists because the fuzzy sibling (``shown_words_lag``) needs a similarity THRESHOLD,
    and thresholded keyword scoring is exactly what made workers PR #2153 anchor 498 of 978
    beats to narration mentioning nothing of them. Here: verbatim substring or not scored.
    """

    SEGMENTS = [
        (0, 4062, "Imagine the glittering skyscrapers of Dubai."),
        (4523, 10289, "Textbooks miss it: not all sand makes good concrete. Concrete needs "
                      "interlocking particles for a strong, stable skeleton."),
        (10672, 23096, "Desert sand, those beautiful round quartz grains, are too smooth, "
                       "like tiny, polished marbles."),
    ]

    def test_the_witness_lag_reproduces(self):
        cards = [{"card_text": "Textbooks miss it: not all sand makes good concrete.",
                  "start_ms": 21188, "duration_ms": 2500}]
        r = na.card_provenance_lag(cards, self.SEGMENTS)
        assert r.traceable == 1
        assert r.median_lag_ms == 16665.0
        assert r.offenders[0]["spoken_ms"] == 4523 and r.offenders[0]["shown_ms"] == 21188

    def test_a_sentence_spoken_nowhere_is_not_scored_rather_than_guessed(self):
        """The #2153 failure mode, made structurally impossible."""
        cards = [{"card_text": "a sentence the script never contains",
                  "start_ms": 5000, "duration_ms": 2500}]
        r = na.card_provenance_lag(cards, self.SEGMENTS)
        assert r.traceable == 0 and r.cards == 1 and r.offenders == []

    def test_a_correctly_paired_card_scores_zero_lag(self):
        """Symmetry: the metric must reward a right answer, not only punish a wrong one."""
        cards = [{"card_text": "Textbooks miss it: not all sand makes good concrete.",
                  "start_ms": 4523, "duration_ms": 2500}]
        r = na.card_provenance_lag(cards, self.SEGMENTS)
        assert r.traceable == 1 and r.median_lag_ms == 0.0 and r.offenders == []

    def test_punctuation_and_casing_do_not_break_the_match(self):
        cards = [{"card_text": "TEXTBOOKS MISS IT -- not all sand makes good concrete!!",
                  "start_ms": 4523, "duration_ms": 2500}]
        assert na.card_provenance_lag(cards, self.SEGMENTS).traceable == 1

    def test_clips_without_card_text_are_ignored(self):
        cards = [{"start_ms": 4523, "duration_ms": 2500},
                 {"card_text": "", "start_ms": 9000, "duration_ms": 2500}]
        r = na.card_provenance_lag(cards, self.SEGMENTS)
        assert r.traceable == 0

    def test_empty_inputs_are_safe(self):
        assert na.card_provenance_lag([], self.SEGMENTS).traceable == 0
        assert na.card_provenance_lag([{"card_text": "x", "start_ms": 0}], []).traceable == 0


class TestAuditSkipsMidFlightDocs:
    """A visuals re-run REPLACES the clip array rather than appending: 13 -> 19 -> 3 on one job,
    and 17 -> 6 -> 17 observed on 1ab08626 while this metric was being built. Scoring a snapshot
    reports a number that never existed, so the audit must SKIP, not score."""

    @staticmethod
    def _audit():
        import importlib.util
        from pathlib import Path

        p = Path(__file__).resolve().parents[1] / "scripts" / "narration_sync_audit.py"
        spec = importlib.util.spec_from_file_location("nsa", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def _doc(self, status, video_url):
        return {
            "status": status,
            "visual": {
                "clips": [{"beat_index": 0, "start_ms": 0, "duration_ms": 2000,
                           "content_hash": "a"}],
                **({"video_url": video_url} if video_url else {}),
            },
            "captions_vtt": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello there friend\n",
        }

    def test_a_running_job_is_skipped(self):
        assert self._audit().audit(self._doc("running", "gs://x/v.mp4")) is None

    def test_a_completed_job_without_a_surfaced_video_is_skipped(self):
        """The exact state observed on 1ab08626: status=completed, video_url absent, clips
        still being rewritten."""
        assert self._audit().audit(self._doc("completed", None)) is None

    def test_master_segment_timeline_is_read_as_a_list_not_a_dict(self):
        """Reading it as a dict silently degrades to gapless offsets — that produced a 260ms
        median that had to be retracted."""
        m = self._audit()
        doc = {
            "segments_ready": [{"index": 0, "text_full": "hello there"},
                               {"index": 1, "text_full": "second segment here"}],
            "master_segment_timeline": [{"index": 0, "start_ms": 0, "end_ms": 1000},
                                        {"index": 1, "start_ms": 4523, "end_ms": 9000}],
        }
        rows = m._spoken_segments(doc)
        assert rows == [(0, 1000, "hello there"), (4523, 9000, "second segment here")]
