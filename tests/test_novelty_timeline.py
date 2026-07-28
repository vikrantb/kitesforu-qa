"""novelty_timeline — the TEMPORAL facts a viewer would report, pinned on the real witness.

Founder eaf99101 measured output (from the delivered clip list):
    364s, 29 distinct pictures, 67% of runtime on an already-seen picture,
    96s longest wait for anything new, 93s longest single unchanging picture.
His stated want is a fresh picture every ~2 seconds.
"""

from __future__ import annotations

from kitesforu_qa.harness.novelty import human_sentences, novelty_timeline


def _c(key, ms):
    return {"content_hash": key, "duration_ms": ms}


class TestNoveltyFacts:
    def test_all_distinct_has_no_repeat(self):
        t = novelty_timeline([_c(f"i{i}", 2_000) for i in range(10)])
        assert t["distinct"] == 10
        assert t["repeat_share"] == 0.0
        assert t["longest_stale_s"] == 2.0

    def test_repeat_share_counts_only_reappearances(self):
        # a,b,a,b -> the 2nd a and 2nd b are repeats = half the runtime
        t = novelty_timeline([_c("a", 1_000), _c("b", 1_000), _c("a", 1_000), _c("b", 1_000)])
        assert t["repeat_share"] == 0.5
        assert t["distinct"] == 2

    def test_longest_stale_spans_until_something_NEW(self):
        """Re-showing old pictures does not reset staleness — a viewer waiting for something
        new is still waiting, which is precisely the eaf99101 experience."""
        t = novelty_timeline([_c("a", 5_000), _c("b", 5_000), _c("a", 30_000), _c("c", 1_000)])
        assert t["longest_stale_s"] == 35.0  # b(5) + a-again(30), broken only by c

    def test_longest_same_image_spans_consecutive_reruns(self):
        """The hold-cap re-cut case: one image split into 8 sub-clips is still ONE unchanging
        picture to a viewer, even though every individual clip duration looks healthy."""
        t = novelty_timeline([_c("same", 8_400) for _ in range(8)])
        assert t["longest_same_image_s"] == 67.2
        assert t["distinct"] == 1

    def test_gaps_track_time_between_new_pictures(self):
        t = novelty_timeline([_c("a", 2_000), _c("a", 3_000), _c("b", 2_000)])
        assert t["gaps_s"][-1] == 5.0  # b arrived 5s after a first appeared

    def test_asset_less_clips_are_excluded_not_counted_as_one(self):
        t = novelty_timeline([{"duration_ms": 5_000}, {"duration_ms": 5_000}])
        assert t["distinct"] == 0 and t["total_s"] == 0.0

    def test_zero_duration_clips_ignored(self):
        t = novelty_timeline([_c("a", 0), _c("b", 4_000)])
        assert t["distinct"] == 1 and t["total_s"] == 4.0

    def test_empty_is_safe(self):
        t = novelty_timeline([])
        assert t["distinct"] == 0 and t["repeat_share"] == 0.0


class TestTheWitnessShape:
    """eaf99101's measured profile must reproduce, and must read as damning in plain words."""

    def test_witness_profile(self):
        """Fixture rebuilt from eaf99101's ACTUAL clip grouping, not an approximation — the
        four dominant assets with their real per-clip durations, then the long tail. A fixture
        invented to make the assertion pass is how a check ends up measuring nothing.

            f78890b1  9.1 + 16.3 + 67.4 + 9.4  = 102.2s over beats 2,5,6,7
            8b849f00  19.6 x 4                 =  78.4s, all on beat 20 (hold-cap re-cut)
            07562bcb  2.5 + 14.7 x 3           =  46.6s
            96f754ff  6.5 x 3 + 2.5            =  22.0s
            + 25 further pictures sharing the remaining ~115s
        """
        clips = [_c("f78890b1", 9_100), _c("8b849f00", 19_600)]
        clips += [_c("07562bcb", 2_500), _c("96f754ff", 6_500)]
        clips += [_c("f78890b1", 16_300), _c("f78890b1", 67_400), _c("f78890b1", 9_400)]
        clips += [_c("8b849f00", 19_600) for _ in range(3)]
        clips += [_c("07562bcb", 14_700) for _ in range(3)]
        clips += [_c("96f754ff", 6_500), _c("96f754ff", 6_500), _c("96f754ff", 2_500)]
        clips += [_c(f"n{i}", 4_600) for i in range(25)]

        t = novelty_timeline(clips)
        # The real artifact measures 67% repeat / 96s stale / 93s unchanging. This fixture is
        # the same shape, so it must land in the same territory.
        assert t["repeat_share"] > 0.55, t
        assert t["longest_stale_s"] > 60, t
        assert t["longest_same_image_s"] > 60, t
        assert t["distinct"] == 29, t

    def test_sentences_are_human_readable(self):
        t = novelty_timeline([_c("a", 60_000), _c("b", 5_000)])
        lines = human_sentences(t)
        assert any("already seen" in s for s in lines)
        assert any("without any NEW picture" in s for s in lines)
        assert all(isinstance(s, str) and s.endswith(".") for s in lines)
