"""visual.monotony — calibrated against REAL artifacts, not invented thresholds.

The rule this file exists to honour: a measure must be validated against hand-labelled real
artifacts BEFORE any number it produces is allowed to drive a decision. Four earlier checks in
this repo were wrong (an inverted colour proxy, an aspect-biased stroke signal, a selector that
never matched, a false-fail) precisely because they were not.

THE LABELLED CASE. Job eaf99101, 2026-07-27, founder verdict: "the visuals were horrible, a
boring screen and same image dancing." Measured from the delivered clip list:
    48 clips / 363.8s, 29 distinct images, top-1 = 28%, top-3 = 48%, 4.8 distinct/min
It must FAIL.

THE FLEET BASELINE (n=85 completed jobs with >=5 clips, >=30s runtime, counting ONLY clips that
actually carry a picture): median top-1 = 49%, median top-3 = 95%, median 3.2 distinct/min, and
EIGHT-PLUS jobs at top-1 = 100% -- one image for the whole video (6f7a697b 68s, dbcf37b8 77s,
831123a1 52s, each with exactly ONE real asset). Those must fail too -- hence thresholds set from
the founder's standard rather than from this distribution, which is itself the defect.

MEASUREMENT CORRECTION (worth keeping, it is the classic trap): a first pass keyed clips by
``str(content_hash or asset_uri)``, which maps an asset-LESS clip to the string "None" and so
counts a job whose every clip failed as "1 unique image". a14129ec is such a job -- 18 clips, all
``status=failed / failure_reason=skipped_no_asset`` -- and it is NOT a monotony case at all; it is
a separate and worse defect (a job marked ``completed`` with ``visuals_quality: 40`` and zero
delivered pictures). ``_asset_key`` therefore returns "" for those and ``monotony`` skips them, so
this check never mistakes "no images" for "one image". 3 of 88 jobs are in that state.
"""

from __future__ import annotations

from kitesforu_qa.harness.checks.visual import monotony


class _Art:
    """Minimal artifact stand-in — monotony only reads the doc-side clip list."""

    def __init__(self, clips):
        self.doc = {"visual": {"clips": clips}}
        self.visual_clips = clips
        self.image_paths = []


def _clips(spec):
    """spec: list of (content_hash, duration_ms) -> clip dicts."""
    return [{"content_hash": h, "duration_ms": ms, "render_mode": "still"} for h, ms in spec]


def _run(clips):
    art = _Art(clips)
    try:
        return monotony.__wrapped__(art)  # type: ignore[attr-defined]
    except AttributeError:
        return monotony(art)


class TestTheLabelledBadArtifacts:
    def test_eaf99101_shape_fails(self):
        """The founder's own witness. 29 images / 364s but 4 of them own most of it."""
        spec = [("a", 102_200), ("b", 78_500), ("c", 46_700), ("d", 22_000)]
        spec += [(f"n{i}", 4_600) for i in range(25)]  # the remaining 25 images share ~115s
        ok, evidence = _run(_clips(spec))
        assert ok is False, evidence
        assert "one image owns" in evidence

    def test_one_image_for_the_whole_video_fails(self):
        """Job 6f7a697b: 68s of runtime, ONE real asset. Eight-plus fleet jobs look like this."""
        ok, evidence = _run(_clips([("only", 6_180) for _ in range(11)]))
        assert ok is False, evidence
        assert "100%" in evidence

    def test_asset_less_clips_are_not_counted_as_one_image(self):
        """a14129ec's real shape: every clip failed with skipped_no_asset. Keying on
        ``str(hash or uri)`` would map them all to "None" and report a tidy '1 unique image',
        hiding a zero-pictures job behind a monotony verdict. They must be excluded entirely."""
        import pytest

        clips = [{"duration_ms": 5_055, "status": "failed",
                  "failure_reason": "skipped_no_asset"} for _ in range(18)]
        with pytest.raises(Exception):  # no identifiable assets at all -> skip, not a verdict
            _run(clips)

    def test_fleet_median_shape_fails(self):
        """median top-1 = 51.5%, top-3 = 96%. Half the fleet looks like this; it is not OK."""
        ok, _ = _run(_clips([("a", 51_500), ("b", 30_000), ("c", 14_500), ("d", 4_000)]))
        assert ok is False


class TestAGoodArtifactPasses:
    """The gate must not simply fail everything — a false-fail is as useless as a blind pass."""

    def test_varied_visuals_pass(self):
        # 30 distinct images, 6s each = 180s, 10/min, top-1 = 3.3%
        ok, evidence = _run(_clips([(f"img{i}", 6_000) for i in range(30)]))
        assert ok is True, evidence

    def test_a_mild_repeat_is_tolerated(self):
        """One image used twice out of 20 is normal editing, not monotony."""
        spec = [("hero", 6_000), ("hero", 6_000)] + [(f"i{i}", 6_000) for i in range(18)]
        ok, evidence = _run(_clips(spec))
        assert ok is True, evidence


class TestItMeasuresIMAGESNotCLIPS:
    """The reason cadence could not see this defect: it measures clip duration, and eaf99101's
    long beat was correctly re-cut into healthy-looking short clips of the SAME picture."""

    def test_recut_into_short_clips_still_fails(self):
        # 8 sub-windows of 8.4s, all the same image, plus some variety around it.
        spec = [("same", 8_400) for _ in range(8)] + [(f"o{i}", 8_400) for i in range(4)]
        ok, evidence = _run(_clips(spec))
        assert ok is False, evidence
        assert "one image owns" in evidence

    def test_same_durations_but_distinct_images_pass(self):
        spec = [(f"d{i}", 8_400) for i in range(12)]
        ok, evidence = _run(_clips(spec))
        assert ok is True, evidence


class TestDegenerateInputsAreSafe:
    def test_no_durations_skips_rather_than_fails(self):
        import pytest

        art = _Art([{"content_hash": "a"}, {"content_hash": "b"}])
        with pytest.raises(Exception):  # skip() raises the harness' skip signal
            _run(art.visual_clips)

    def test_missing_hash_falls_back_to_asset_uri(self):
        clips = [{"asset_uri": f"gs://b/{i}.png", "duration_ms": 6_000} for i in range(30)]
        ok, evidence = _run(clips)
        assert ok is True, evidence
        assert "30 distinct images" in evidence
