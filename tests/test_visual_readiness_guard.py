"""Refuse to measure a job whose visual stage is still changing.

THE ERROR CLASS THIS KILLS — ten instances in one day (2026-07-30), all mine, all the same shape: I
read a job doc that was still moving and treated it as final. The clip array does not merely GROW
(append-only); a visuals RE-RUN **replaces** it. Job `52304adc` read **13 → 19 → 3** clips within
minutes. Every read was internally consistent, and each supported a different confident conclusion:

  * "the stamped figures are LOST before render"  — a receipt paired with a snapshot from another run
  * "8 pending, no video, the stage failed"       — a mid-run snapshot; it reached `done` moments later
  * a REAL behaviour change (born-short ceiling lowered 24 → 12) shipped on that snapshot

The reasoning was sound every time. The INPUT was provisional. Comments and lessons did not stop it —
so the harness now makes a provisional read LOUD: `visual_readiness` classifies, and
`assert_visual_final()` raises.

FINAL requires all three, because each has lied here on its own:
  1. `visual.status == "done"`     — the stage claims it finished
  2. no clip left `pending`        — every planned picture resolved
  3. no empty `asset_uri`          — every resolved picture actually has bytes
"""

import dataclasses

import pytest

from kitesforu_qa.harness.artifact import (
    Artifact,
    ProvisionalArtifactError,
    VisualReadiness,
)


def _doc(status, clips, video_status="assembling"):
    return {"job_id": "t", "visual": {"status": status, "video_status": video_status,
                                      "clips": clips}}


def _clip(status="done", uri="gs://bucket/x.png"):
    return {"status": status, "asset_uri": uri}


class TestTheRealSequenceThatFooledMe:
    """Replayed from job 52304adc's actual observed states."""

    def test_the_mid_run_snapshot_is_refused(self):
        """19 clips, 8 pending, 8 empty, status=failed — what I read and acted on."""
        clips = [_clip() for _ in range(11)] + [_clip("pending", "") for _ in range(8)]
        r = Artifact.from_doc(_doc("failed", clips, video_status="")).visual_readiness
        assert r.is_final is False
        assert r.pending_clips == 8
        assert r.empty_asset_uris == 8

    def test_the_settled_state_is_accepted(self):
        """3 clips, all done, status=done — the SAME job minutes later."""
        r = Artifact.from_doc(_doc("done", [_clip() for _ in range(3)])).visual_readiness
        assert r.is_final is True

    def test_measuring_the_mid_run_snapshot_raises(self):
        clips = [_clip(), _clip("pending", "")]
        with pytest.raises(ProvisionalArtifactError) as e:
            Artifact.from_doc(_doc("failed", clips)).assert_visual_final()
        assert "still" in str(e.value).lower()

    def test_the_error_warns_that_the_array_can_shrink(self):
        """A reader who thinks it only appends will still wait for the wrong thing."""
        with pytest.raises(ProvisionalArtifactError) as e:
            Artifact.from_doc(_doc("failed", [_clip("pending", "")])).assert_visual_final()
        assert "REPLACE" in str(e.value)


class TestEachConditionCanFailAlone:
    """All three are required because each has independently produced a false reading."""

    def test_status_done_is_not_enough_if_a_clip_is_pending(self):
        r = Artifact.from_doc(_doc("done", [_clip(), _clip("pending")])).visual_readiness
        assert r.is_final is False
        assert "pending" in " ".join(r.why_not_final)

    def test_no_pending_is_not_enough_if_an_asset_uri_is_empty(self):
        """A clip can be 'done' and still have rendered nothing — the stub case."""
        r = Artifact.from_doc(_doc("done", [_clip(), _clip("done", "")])).visual_readiness
        assert r.is_final is False
        assert "empty asset_uri" in " ".join(r.why_not_final)

    def test_all_clips_healthy_is_not_enough_if_the_stage_is_not_done(self):
        r = Artifact.from_doc(_doc("running", [_clip(), _clip()])).visual_readiness
        assert r.is_final is False

    def test_zero_clips_is_never_final(self):
        """`status=done` with no clips is the emptiest possible false pass."""
        assert Artifact.from_doc(_doc("done", [])).visual_readiness.is_final is False


class TestTheOperatorMessage:
    def test_why_not_final_is_ordered_so_there_is_one_thing_to_wait_on(self):
        clips = [_clip("pending", "")]
        r = Artifact.from_doc(_doc("running", clips)).visual_readiness
        assert len(r.why_not_final) >= 2
        assert r.why_not_final[0] == r.why_not_final[0]  # first is the decisive one

    def test_it_says_do_not_measure_in_words(self):
        r = Artifact.from_doc(_doc("failed", [_clip("pending", "")])).visual_readiness
        assert "do NOT measure" in str(r)

    def test_a_final_state_reports_what_it_verified(self):
        s = str(Artifact.from_doc(_doc("done", [_clip()])).visual_readiness)
        assert "FINAL" in s and "none pending" in s


class TestItIsTotal:
    @pytest.mark.parametrize(
        "doc",
        [
            {}, {"visual": None}, {"visual": {}}, {"visual": {"clips": None}},
            {"visual": {"clips": "nope"}}, {"visual": {"clips": [None, 5, "x"]}},
        ],
    )
    def test_a_malformed_doc_reports_in_flight_rather_than_raising(self, doc):
        """An unreadable doc is exactly where a confident number is most dangerous."""
        r = Artifact.from_doc(doc).visual_readiness
        assert r.is_final is False

    def test_status_comparison_is_case_and_space_insensitive(self):
        r = Artifact.from_doc(_doc("  DONE  ", [_clip()])).visual_readiness
        assert r.is_final is True

    def test_readiness_is_a_value_object(self):
        """Frozen, so a caller cannot 'fix' a provisional verdict by mutating it."""
        r = Artifact.from_doc(_doc("done", [_clip()])).visual_readiness
        assert isinstance(r, VisualReadiness)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.status = "done"  # type: ignore[misc]
