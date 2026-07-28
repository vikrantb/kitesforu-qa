"""`visual.diagram_fills_frame` must divide by the region the engine was ALLOWED to paint.

MEASURED (workers, job 7f42c33b + the schematic engine test): on a 9:16 short the burned kinetic
caption owns a fixed lower band, so `render.safe_area.usable_diagram_height` caps a diagram's
drawing area at `caption_band_top_frac()` (~0.62) and every engine honours it. A correctly
composed schematic fills 84% of that usable region — and only 52% of the FRAME.

Against a flat 20%-of-frame floor that reads as "fine"; against any RAISED floor it would read as
thin and false-fail every correct portrait figure. Painted-area-over-full-frame is simply the wrong
question for portrait.

The workers stamp `usable_height_frac` per clip (workers #1897, declared in schemas 2.56.0) so this
consumer reads what the render ACTUALLY used. Hardcoding 0.62 here would drift the moment the
caption band is tuned — the stale-denominator class that produced seven wrong measurements in one
session.
"""

from __future__ import annotations

from kitesforu_qa.harness.checks.visual import _job_usable_height_frac


class _Art:
    def __init__(self, clips):
        self.doc = {"visual": {"clips": clips}}
        self.visual_clips = clips
        self.image_paths = []


class TestTheDenominatorComesFromTheRender:
    def test_portrait_stamp_is_read(self):
        assert _job_usable_height_frac(_Art([{"usable_height_frac": 0.62}])) == 0.62

    def test_landscape_stamp_is_read(self):
        assert _job_usable_height_frac(_Art([{"usable_height_frac": 1.0}])) == 1.0

    def test_it_scans_past_unstamped_clips(self):
        """A legacy clip mixed with stamped ones must not mask the real value."""
        art = _Art([{"beat_index": 0}, {"usable_height_frac": 0.62}])
        assert _job_usable_height_frac(art) == 0.62


class TestLegacyJobsAreByteIdentical:
    """The safety property. Every clip that exists today lacks the stamp; the check must behave
    EXACTLY as before for them, or this 'improvement' rewrites history's verdicts."""

    def test_no_stamp_anywhere_means_full_frame(self):
        assert _job_usable_height_frac(_Art([{"beat_index": 0}, {"beat_index": 1}])) == 1.0

    def test_empty_clips_mean_full_frame(self):
        assert _job_usable_height_frac(_Art([])) == 1.0


class TestGarbageCannotCorruptTheDenominator:
    """A wrong denominator is worse than none — it silently rescales every verdict."""

    def test_out_of_range_values_are_ignored(self):
        for bad in (0.0, -0.5, 1.5, 99):
            assert _job_usable_height_frac(_Art([{"usable_height_frac": bad}])) == 1.0, bad

    def test_bool_is_rejected(self):
        """bool subclasses int; True would otherwise pass as 1.0 and silently mean 'full frame'."""
        assert _job_usable_height_frac(_Art([{"usable_height_frac": True}])) == 1.0

    def test_non_numeric_is_ignored(self):
        for bad in ("0.62", None, {}, []):
            assert _job_usable_height_frac(_Art([{"usable_height_frac": bad}])) == 1.0, bad

    def test_a_hostile_artifact_cannot_raise(self):
        class Hostile:
            doc = {}
            @property
            def visual_clips(self):
                raise RuntimeError("boom")

        assert _job_usable_height_frac(Hostile()) == 1.0
