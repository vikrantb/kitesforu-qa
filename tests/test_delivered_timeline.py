"""What a delivered master shows when: the stamp first, then a fallback that only ever over-checks.

Every rule in ``kitesforu_qa.harness.delivered_timeline`` has a test here that goes red when the
rule is removed. Each fallback repro is one that the qa #184 round-1 code critic, or the renderer
oracle at the bottom of this file, found exempting a frame that shows a card. The oracle runs the
renderer's own coverage fill, sort, pacing, bounds and J-cut from kitesforu-workers.

Frame instants are written out as numbers: frame k shows the master at ``3000k + 1499`` ms (see
``tests/test_the_gate_reads_each_frame_at_its_true_time.py``). Offline and $0.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import random
import sys
from pathlib import Path

import pytest

from kitesforu_qa.harness import delivered_timeline as dt
from kitesforu_qa.harness.delivered_timeline import DeliveredTimeline, bleeds_by_design
from kitesforu_qa.harness.narration_alignment import delivered_spans

VEO = {"render_mode": "video", "motion_render": "veo"}


def clip(start, modality="diagram", beat=None, **extra):
    """A persisted-shape clip: it carries ``asset_uri``, as every persisted clip does."""
    c = {"start_ms": start, "modality": modality, "asset_uri": f"gs://b/{start}.png",
         "status": "done", "duration_ms": 3000}
    if beat is not None:
        c["beat_index"] = beat
    c.update(extra)
    return c


def mods(candidates):
    return sorted({"UNKNOWN" if c is None else c.get("modality") for c in candidates})


# ── the stamp is read first ────────────────────────────────────────────────────────────────────

def stamp(windows, master_ms=20000, version=1):
    return {"version": version, "master_ms": master_ms, "windows": windows,
            "close": {"applied": False, "fade_start_ms": None, "fade_ms": None, "reason": "test"}}


def window(clip_idx, start, end, modality, *, source=None, **painted):
    w = {"clip": clip_idx, "source_clip": clip_idx if source is None else source,
         "start_ms": start, "end_ms": end, "modality": modality,
         "render_mode": None, "motion_render": None, "asset_kind": None}
    w.update(painted)
    return w


def test_a_valid_stamp_wins_over_the_persisted_claims():
    """The clips claim a diagram at 6-12 s. The stamp says a photo was painted there (coverage
    fill), so the frame at 7.5 s is the photo."""
    clips = [clip(0, "scene_image"), clip(6000, "diagram", status="failed", asset_uri=""),
             clip(12000, "scene_image")]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp([
        window(0, 0, 6000, "scene_image", asset_kind="image"),
        window(1, 6000, 12000, "scene_image", source=0, asset_kind="image"),
        window(2, 12000, 20000, "scene_image", asset_kind="image")]), master_ms=20000)
    assert (tl.source, tl.diagnosis) == ("stamp", "stamp")
    assert tl.full_bleed_at(7499) is True
    assert tl.spans_by_clip() == {0: (0, 6000), 1: (6000, 12000), 2: (12000, 20000)}


def test_a_stamped_video_hero_needs_video_evidence_in_the_stamp():
    clips = [clip(0, "video_hero")]
    painted_still = stamp([window(0, 0, 20000, "video_hero", asset_kind="image",
                                  render_mode="still")])
    painted_video = stamp([window(0, 0, 20000, "video_hero", asset_kind="video")])
    assert DeliveredTimeline.from_clips(clips, stamp=painted_still).full_bleed_at(4499) is False
    assert DeliveredTimeline.from_clips(clips, stamp=painted_video).full_bleed_at(4499) is True


@pytest.mark.parametrize("bad, reason", [
    (stamp([window(0, 0, 20000, "scene_image")], version=2), "version=2"),
    (stamp([window(0, 0, 20000, "scene_image")], master_ms=30000), "master_ms"),
    (stamp([window(5, 0, 20000, "scene_image")]), "malformed_window"),
    (stamp([window(0, 9000, 9000, "scene_image")]), "malformed_window"),
    (stamp([]), "no_windows"),
    ("not a stamp", "not_a_mapping"),
])
def test_a_stamp_that_cannot_be_trusted_falls_back_and_says_why(bad, reason):
    clips = [clip(0, "diagram")]
    tl = DeliveredTimeline.from_clips(clips, stamp=bad, master_ms=20000)
    assert tl.source == "fallback" and reason in (tl.stamp_rejected or ""), tl.stamp_rejected
    assert tl.full_bleed_at(4499) is False      # the fallback read the diagram claim


def test_a_gap_in_the_stamp_and_the_intro_before_it_are_unknown():
    clips = [clip(0, "scene_image"), clip(1, "scene_image")]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp([
        window(0, 3000, 6000, "scene_image"), window(1, 9000, 20000, "scene_image")]))
    assert tl.candidates_at(1499) == []         # before the first painted window: the lead
    assert tl.candidates_at(7499) == []         # 1.5 s clear of both windows
    assert tl.full_bleed_at(10499) is True


def test_a_stamp_reports_one_envelope_per_clip_and_one_window_per_painted_span():
    clips = [clip(0, "scene_image"), clip(1, "diagram")]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp([
        window(0, 0, 4000, "scene_image"), window(1, 4000, 9000, "diagram"),
        window(0, 9000, 20000, "scene_image")]))
    assert tl.spans_by_clip() == {0: (0, 20000), 1: (4000, 9000)}
    assert [(w.start_ms, w.end_ms) for w in tl.painted_windows()] == [
        (0, 4000), (4000, 9000), (9000, 20000)]


# ── the fallback: each repro that once exempted a card ─────────────────────────────────────────

def test_a_failed_row_shows_a_neighbours_asset_so_it_is_unknown():
    """Code critic #2 (coverage carry-forward): `fill_coverage_gaps` re-points failed rows at the
    nearest rendered neighbour, which may be the NEXT one. The renderer paints the diagram from
    beat 5 at 13.5 s; the old gate exempted that frame as the video_hero at 3 s."""
    clips = [clip(0, "diagram", 0), clip(3000, "video_hero", 1, **VEO),
             clip(6000, "diagram", 2, status="failed", asset_uri=""),
             clip(9000, "diagram", 3, status="failed", asset_uri=""),
             clip(12000, "diagram", 4, status="failed", asset_uri=""), clip(15000, "diagram", 5)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=18000)
    assert None in tl.candidates_at(13499)
    assert tl.full_bleed_at(13499) is None
    assert tl.full_bleed_at(4499) is True        # the Veo beat itself is still attributable


def test_a_clip_dropped_by_the_strobe_guard_shows_the_one_before_it():
    """Code critic #2, repro A. `coalesce_strobe` drops the photo that starts 1000 ms after the
    diagram, and the diagram holds 9-13 s, so the frame at 10.5 s is a card."""
    clips = [clip(0, "scene_image", 0, duration_ms=9000), clip(9000, "diagram", 1, duration_ms=1000),
             clip(10000, "scene_image", 2), clip(13000, "scene_image", 3)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=16000)
    assert "diagram" in mods(tl.candidates_at(10499))
    assert tl.full_bleed_at(10499) is False


def test_a_collapsed_card_may_hold_the_long_window_after_it():
    """Code critic #2, repro B: the diagram's 300 ms window is collapsed. Depending on the pass
    that wins, it is funded into the photo before it OR holds 12-20 s after the strobe guard
    drops the photo at 12.3 s. Frames 4, 5 and 6 may all be the card."""
    clips = [clip(0, "scene_image", 0, duration_ms=12000), clip(12000, "diagram", 1, duration_ms=300),
             clip(12300, "scene_image", 2, duration_ms=7700), clip(20000, "scene_image", 3)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=23000)
    for t in (13499, 16499, 19499):
        assert tl.full_bleed_at(t) is False, t
    assert tl.full_bleed_at(22499) is True


def test_a_collapsed_run_is_funded_from_the_anchor_before_the_whole_run():
    """Renderer-oracle find: four collapsed rows in a row (7000-7800) are funded from the photo at
    0, and the oracle painted the motion clip at 3.2 s. The previous distinct start (7000) is
    itself collapsed, so funding reaches back to the anchor before the run."""
    clips = [clip(0, "scene_image", 0, duration_ms=7000), clip(7000, "video_hero", 1, **VEO),
             clip(7400, "motion", 2), clip(7800, "video_hero", 3, **VEO),
             clip(7800, "video_hero", 4, **VEO), clip(8000, "motion", 5),
             clip(12000, "video_hero", 6, **VEO)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=30000)
    assert "motion" in mods(tl.candidates_at(4499))


def test_a_funded_member_can_land_on_the_runs_right_edge_and_hold_from_there():
    """Renderer-oracle find: the second diagram of the pile at 6900 collapses to the run's right
    edge (7300), wins that start, and the strobe guard keeps it until 12.6 s."""
    clips = [clip(0, "scene_image", 0), clip(2900, "motion", 1, duration_ms=1000),
             clip(6900, "diagram", 2), clip(6900, "diagram", 3, duration_ms=6000),
             clip(7300, "video_hero", 4, duration_ms=1000, **VEO),
             clip(8600, "video_hero", 5, **VEO),
             clip(12600, "motion", 6, status="failed", duration_ms=6000),
             clip(19600, "scene_image", 7)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=42100)
    assert "diagram" in mods(tl.candidates_at(10499))


def test_the_strobe_fold_cascades_to_the_clip_kept_before():
    """Renderer-oracle find (seed 7, trial 2648, exact). The last clip dwells 1000 ms, so the strobe
    guard's final-kept fold pops it, and with it the video_hero kept 200 ms before. The diagram at
    7 s then holds to the end of the master."""
    gsap = {"render_mode": "video", "motion_render": "gsap"}
    clips = [clip(0, "motion", 0, status="failed", duration_ms=0, **gsap),
             clip(7000, "diagram", 1, duration_ms=6000),
             clip(14000, "video_hero", 2, duration_ms=6000, **VEO),
             clip(14200, "video_hero", 3, duration_ms=1000, **VEO)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=17200)
    assert "diagram" in mods(tl.candidates_at(16499))


def test_the_strobe_fold_reads_the_master_span():
    """Renderer-oracle find (seed 31, trial 2286, exact). The last clip has no persisted duration,
    but once the refloor re-lays this timeline against the 11 s master the last window is 1000 ms.
    It is folded, and the motion clip holds to the end."""
    gsap = {"render_mode": "video", "motion_render": "gsap"}
    clips = [clip(0, "video_hero", 0, duration_ms=6000, **VEO),
             clip(400, "video_hero", 1, status="failed", duration_ms=3000, **VEO),
             clip(800, "diagram", 2, duration_ms=0), clip(800, "diagram", 3, duration_ms=3000),
             clip(4800, "scene_image", 4, duration_ms=0), clip(5000, "diagram", 5, duration_ms=0),
             clip(7500, "motion", 6, duration_ms=3000, **gsap),
             clip(10000, "video_hero", 7, duration_ms=0, **VEO)]
    assert "motion" in mods(DeliveredTimeline.from_clips(clips, master_ms=11000).candidates_at(10499))
    # The same claims under a long master leave a full dwell after the last start: no fold.
    long_master = DeliveredTimeline.from_clips(clips, master_ms=40000)
    assert mods(long_master.candidates_at(13499)) == ["video_hero"]


# ── trust: what the persisted starts can and cannot say ────────────────────────────────────────

def test_a_legacy_timeline_is_untrusted():
    """Code critic #3: with no `master_segment_timeline` the worker passes `real_offsets=False`.
    The master then lays clips from 0 and drops the first start, so the claims do not hold."""
    doc = {"visual": {"clips": [clip(5000, "scene_image", 0), clip(9000, "scene_image", 1)]}}
    tl = DeliveredTimeline.from_job(doc, master_ms=20000)
    assert tl.diagnosis == "legacy" and tl.candidates_at(10499) == []
    doc["master_segment_timeline"] = [{"index": 0, "start_ms": 5000, "end_ms": 20000}]
    assert DeliveredTimeline.from_job(doc, master_ms=20000).full_bleed_at(10499) is True


def test_monotonicity_is_judged_in_the_renderers_sort_order():
    """Code critic #4: the renderer sorts by (beat_index, start_ms) before `resolve_bounds`. These
    starts never decrease in array order, but beat 1 claims 6 s and beat 2 claims 3 s."""
    clips = [clip(0, "scene_image", 0), clip(3000, "scene_image", 2), clip(6000, "scene_image", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "untrusted_decreasing" and tl.candidates_at(7499) == []


def test_mixed_anchoring_keeps_the_anchored_clips_attributable():
    """Design #3 and claims #1: `resolve_bounds`' mixed-anchoring branch keeps every anchored start
    and puts an unanchored clip zero-width at the NEXT anchored start. It does not re-lay the clips
    by duration. So the anchored photo is still attributable, and the unanchored card is a
    candidate wherever the floor may fund it, which is the photo window before 6 s."""
    clips = [clip(0, "scene_image", 0, duration_ms=6000), clip(None, "diagram", 1),
             clip(6000, "scene_image", 2), clip(12000, "scene_image", 3),
             clip(18000, "scene_image", 4)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=30000)
    assert tl.diagnosis == "mixed_anchored"
    assert "diagram" in mods(tl.candidates_at(4499))
    # The card may be funded anywhere from 0, may collapse onto the run's right edge (12 s) and
    # hold one dwell from there, so it is ruled out only once the strobe guard keeps 18 s.
    assert "diagram" in mods(tl.candidates_at(16499))
    assert tl.full_bleed_at(22499) is True


def test_no_numeric_start_at_all_is_untrusted():
    clips = [clip(None, "scene_image", 0), clip(None, "scene_image", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "untrusted_unanchored" and tl.candidates_at(4499) == []


def test_a_boolean_start_is_untrusted_not_read_as_one_millisecond():
    clips = [clip(True, "scene_image", 0), clip(3000, "scene_image", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "untrusted_bool_start" and tl.candidates_at(4499) == []


def test_no_renderable_clip_is_its_own_diagnosis():
    """Code critic #5: every row failed. Assembly degrades to audio-only or last-resort cards,
    which is a different finding from an untrusted timeline."""
    clips = [clip(0, "scene_image", 0, status="failed"), clip(3000, "scene_image", 1, asset_uri="")]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "no_renderable" and tl.candidates_at(4499) == []


def test_no_clips_at_all_is_absent_not_untrusted():
    for clips in ([], None):
        tl = DeliveredTimeline.from_clips(clips)
        assert tl.diagnosis == "absent" and tl.candidates_at(4499) == []


def test_a_hand_built_row_without_an_asset_key_is_read_by_its_modality():
    """Every persisted clip carries `asset_uri` (`VisualClip` defaults it to ""), so only a
    hand-built record lacks the key. An EMPTY value means not rendered and is unknown."""
    keyless = [{"start_ms": 0, "modality": "scene_image"}, {"start_ms": 3000, "modality": "scene_image"}]
    assert DeliveredTimeline.from_clips(keyless, master_ms=20000).full_bleed_at(4499) is True
    empty = [dict(c, asset_uri="") for c in keyless]
    assert DeliveredTimeline.from_clips(empty, master_ms=20000).diagnosis == "no_renderable"


# ── the bands around a cut ─────────────────────────────────────────────────────────────────────

def test_a_frame_just_before_a_cut_may_already_show_the_next_clip():
    """Cuts land up to 367 ms early (17 cuts, two 16:9 masters). A frame 401 ms before a
    photo -> card cut is judged by both sides."""
    clips = [clip(0, "video_hero", 0, **VEO), clip(7900, "diagram", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert mods(tl.candidates_at(7499)) == ["diagram", "video_hero"]
    assert mods(tl.candidates_at(4499)) == ["video_hero"]


def test_a_frame_just_after_a_cut_may_still_show_the_clip_before():
    """Code critic #5: `CUT_LATE_MS = 0` left every test green. A frame 99 ms after a card ->
    photo cut keeps the card as a candidate; 101 ms after, it does not."""
    clips = [clip(0, "diagram", 0, duration_ms=7400), clip(7400, "scene_image", 1)]
    late = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert "diagram" in mods(late.candidates_at(7499))
    clips[1]["start_ms"] = 7398
    assert mods(DeliveredTimeline.from_clips(clips, master_ms=20000).candidates_at(7499)) == [
        "scene_image"]


def test_the_intro_lead_stays_unknown_into_the_late_band():
    """Code critic #5: dropping `+ CUT_LATE_MS` from the intro guard left every test green. The
    lead ends at exactly the first start; a frame 99 ms after it is still unknown."""
    clips = [clip(7400, "scene_image", 0), clip(20000, "scene_image", 1)]
    assert DeliveredTimeline.from_clips(clips, master_ms=30000).candidates_at(7499) == []
    clips[0]["start_ms"] = 7398
    assert DeliveredTimeline.from_clips(clips, master_ms=30000).full_bleed_at(7499) is True


# ── what counts as full-bleed ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row, expected", [
    ({"modality": "scene_image"}, True),
    ({"modality": " Scene_Image "}, True),
    ({"modality": "video_hero", "render_mode": "still", "asset_uri": "gs://b/j/title_band_3.png"},
     False),
    ({"modality": "video_hero"}, False),
    ({"modality": "video_hero", "render_mode": "video"}, True),
    ({"modality": "video_hero", "motion_render": "veo"}, True),
    ({"modality": "video_hero", "asset_uri": "gs://b/veo_0/clip.MP4?x=1"}, True),
    ({"modality": "video_hero", "asset_kind": "video"}, True),
    ({"modality": "diagram", "render_mode": "video", "motion_render": "veo"}, False),
    ({"modality": "motion"}, False),
    ({"modality": None}, False),
    (None, False),
])
def test_full_bleed_is_scene_image_or_video_hero_with_veo_evidence(row, expected):
    """Code critic #1: a reclaimed still stamped `video_hero` (a flowchart, a title band) is drawn
    text, so `video_hero` is exempt only with evidence that video was painted."""
    assert bleeds_by_design(row) is expected


# ── a full-bleed modality that carries DRAWN TEXT is checked ────────────────────────────────────

PHOTO = {"modality": "scene_image", "asset_uri": "gs://b/p.mp4", "status": "done"}


@pytest.mark.parametrize("row, expected", [
    (dict(PHOTO), True),
    (dict(PHOTO, diagram_debug={"kind": "photo_statement"}), False),
    (dict(PHOTO, modality_reasons=["demote→photo_statement:cc_by($0)"]), False),
    (dict(PHOTO, diagram_debug={"kind": "relimage"}), False),
    (dict(PHOTO, modality_reasons=["demote→library_image:embed_only($0)"]), False),
    (dict(PHOTO, diagram_debug={"kind": "library_photo"},
          modality_reasons=["demote→library_image:cc0($0)"]), True),
    (dict(PHOTO, card_spec={"title": "x"}), False),
    (dict(PHOTO, diagram_debug={"kind": "key_term_highlight"}), True),
    (dict(PHOTO, modality="video_hero", diagram_debug={"kind": "relimage"}, **VEO), False),
])
def test_a_picture_with_drawn_text_is_not_full_bleed(row, expected):
    """`animatable.is_animatable_still` is the renderer's own answer to "a picture with no burned-in
    text": a photo statement (its kind or its demote reason), a relimage band, a library photo whose
    licence burns a credit (`embed_only`), a card or figure spec. Found on real pixels: 072c32c4's
    last clip is a `scene_image` photo statement whose line drifts to 12 px from the left edge."""
    assert bleeds_by_design(row) is expected


def test_a_reuse_or_crop_of_a_burned_still_carries_its_text():
    burned_still = dict(PHOTO, content_hash="h1", diagram_debug={"kind": "photo_statement"})
    reuse = dict(PHOTO, content_hash="h1")
    crop = dict(PHOTO, content_hash="h2", modality_reasons=["reframed_from:h1"])
    crop_of_crop = dict(PHOTO, content_hash="h3", modality_reasons=["reframed_from:h2"])
    burned = dt.burned_text_stills([burned_still, reuse, crop, crop_of_crop])
    assert burned == {"h1", "h2", "h3"}
    for row in (reuse, crop, crop_of_crop):
        assert bleeds_by_design(row, burned) is False
    assert bleeds_by_design(dict(PHOTO, content_hash="h9"), burned) is True


def test_a_photo_statement_holding_the_tail_is_checked():
    """072c32c4, shape exact: the last clip is a parallax `scene_image` of kind `photo_statement`, and
    the master holds it 8 s past its claimed end. Every frame there is judged as text."""
    clips = [clip(78329, "diagram", 12, duration_ms=6228, render_mode="video"),
             clip(84557, "scene_image", 13, duration_ms=3720, render_mode="parallax_2_5d",
                  diagram_debug={"kind": "photo_statement"}, ai_generated=False)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=95500)
    for t in (85499, 94499):
        assert tl.full_bleed_at(t) is False, t
    clips[1]["diagram_debug"] = {"kind": "scene_image"}
    assert DeliveredTimeline.from_clips(clips, master_ms=95500).full_bleed_at(94499) is True


def test_drawn_text_matches_the_renderers_own_predicate():
    """The mirror against `animatable.is_animatable_still` and `burned_text_stills` themselves, over
    every combination of the vocabulary they read. SKIPS without kitesforu-workers."""
    _renderer()
    animatable = importlib.import_module("workers.stages.visuals.animatable")
    kinds = [None, "scene_image", "library_photo", "photo_statement", "relimage",
             "key_term_highlight", "flowchart"]
    reasons = [None, ["demote→photo_statement:cc_by($0)"], ["demote→library_image:embed_only($0)"],
               ["demote→library_image:cc0($0)"], ["reframed_from:h0"], ["reframed_from_beat:3"]]
    rows = []
    for i, (kind, reason, spec, modality) in enumerate(
            (k, r, s, m) for k in kinds for r in reasons for s in (None, "card_spec")
            for m in ("scene_image", "video_hero")):
        row = {"modality": modality, "content_hash": f"h{i}", "asset_uri": "gs://b/x.png"}
        if kind:
            row["diagram_debug"] = {"kind": kind}
        if reason:
            row["modality_reasons"] = list(reason)
        if spec:
            row[spec] = {"title": "x"}
        rows.append(row)
    ours, theirs = dt.burned_text_stills(rows), animatable.burned_text_stills(rows)
    assert ours == theirs and ours, "the burned-still sets differ"
    for row in rows:
        assert dt.carries_drawn_text(row, ours) is (not animatable.is_animatable_still(row, theirs)), row


# ── the views agree with each other and with their old owners ──────────────────────────────────

PARITY_SHAPES = {
    "plain": [clip(0), clip(3000), clip(9000, duration_ms=4000)],
    "pile": [clip(0), clip(5000), clip(5000), clip(5000, duration_ms=0), clip(12000)],
    "failed rows": [clip(0), clip(4000, status="failed", asset_uri=""), clip(8000)],
    "missing start": [clip(0), clip(None), {"start_ms": "7000", "duration_ms": 10}],
    "end_ms": [clip(0), {"start_ms": 4000, "end_ms": 9000}],
    "unsorted": [clip(9000), clip(0), clip(4000, duration_ms=0)],
}


@pytest.mark.parametrize("name", sorted(PARITY_SHAPES))
def test_delivered_spans_is_the_claims_view_of_the_timeline(name):
    """`narration_alignment.delivered_spans` is now this module's claims view. The old body is
    restated here ONLY as the reference arm of a parity check (the import-side function under
    test is the real one); it was moved, not changed."""
    clips = PARITY_SHAPES[name]

    def old_delivered_spans(clips):
        rows = []
        for i, c in enumerate(clips or []):
            if not isinstance(c, dict):
                continue
            try:
                rows.append((int(c.get("start_ms")), i, c))
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda r: r[0])
        out = {}
        for n, (start, idx, c) in enumerate(rows):
            end = next((s for s, _, _ in rows[n + 1:] if s > start), None)
            if end is None:
                s0, e = c.get("start_ms"), c.get("end_ms")
                try:
                    s0 = int(s0)
                    e = int(e) if e is not None else (s0 + int(c["duration_ms"])
                                                      if c.get("duration_ms") is not None else None)
                except (TypeError, ValueError):
                    e = None
                end = e if e is not None and e > s0 else None
            if end is not None and end > start:
                out[idx] = (start, int(end))
        return out

    assert delivered_spans(clips) == old_delivered_spans(clips)


def test_every_best_estimate_window_is_among_the_candidates():
    """`candidates_at` is a superset of `spans_by_clip` by construction, so the two views cannot
    name different clips for one instant. Checked over every 250 ms of several shapes."""
    for clips in (PARITY_SHAPES["plain"], PARITY_SHAPES["pile"],
                  [clip(0, "scene_image"), clip(9000, "diagram", duration_ms=1000),
                   clip(10000, "scene_image"), clip(13000, "scene_image")]):
        tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
        spans = tl.spans_by_clip()
        for t in range(0, 20000, 250):
            cands = tl.candidates_at(t)
            if not cands:
                continue
            for idx, (lo, hi) in spans.items():
                if lo <= t < hi and dt._painted(clips[idx]):
                    assert any(c is clips[idx] for c in cands), (idx, t)


def test_painted_windows_hand_a_pile_to_its_last_claim():
    """`frame_proof` measures one window per painted span. A pile is one window, held by the last
    claim at that start (the raw `resolve_bounds` rule), exactly as its old `_windows` did."""
    clips = [clip(0, render_mode="still"), clip(5000, render_mode="video"),
             clip(5000, render_mode="parallax_2_5d"), clip(9000, render_mode="still")]
    tl = DeliveredTimeline.from_clips(clips, master_ms=15000)
    assert [(w.start_ms, w.end_ms, w.fields["render_mode"]) for w in tl.painted_windows()] == [
        (0, 5000, "still"), (5000, 9000, "parallax_2_5d"), (9000, 15000, "still")]


# ── the renderer as an oracle ──────────────────────────────────────────────────────────────────

_QA_ROOT = Path(__file__).resolve().parents[1]
_EXPLICIT_SRC = os.environ.get("WORKERS_SRC")
_WORKERS_SRC = _EXPLICIT_SRC or str(_QA_ROOT.parent / "kitesforu-workers" / "src")


def _renderer():
    """The renderer's own passes, or SKIP when kitesforu-workers is absent (FAIL when WORKERS_SRC
    names a tree without it): the pattern of `test_qa_mirrors_match_production.py`."""
    if _WORKERS_SRC not in sys.path:
        sys.path.insert(0, _WORKERS_SRC)
    if importlib.util.find_spec("workers") is None:
        if _EXPLICIT_SRC:
            pytest.fail(f"WORKERS_SRC={_EXPLICIT_SRC} holds no `workers` package")
        pytest.skip("kitesforu-workers is not importable here (set WORKERS_SRC). SKIPPED, not passed.")
    cov = importlib.import_module("workers.stages.visuals.coverage_gate")
    pace = importlib.import_module("workers.stages.visuals.pacing.master_span_refloor")
    va = importlib.import_module("workers.stages.visuals.video_assembler")
    return cov.fill_coverage_gaps, pace.plan_pacing, va.resolve_bounds, va._apply_jcut


def _painted_by_renderer(clips, span_ms):
    """`assemble_episode_video`'s own order: coverage fill, renderable filter, the (beat_index,
    start_ms) sort, `plan_pacing`, `resolve_bounds` with real offsets, then the J-cut with its
    zero-width restore. Windows of 1 ms or less are skipped, as the per-scene loop skips them."""
    fill, plan, bounds_of, jcut = _renderer()
    filled, _ = fill([dict(c) for c in clips], enabled=True)
    ren = [c for c in filled if c.get("asset_uri") and c.get("status", "done") != "failed"]
    ren.sort(key=lambda c: (c.get("beat_index", 0) if isinstance(c.get("beat_index"), int) else 0,
                            c.get("start_ms", 0) if isinstance(c.get("start_ms"), int) else 0))
    ren = plan(ren, span_ms)
    bounds = bounds_of(ren, span_ms, real_offsets=True)
    zero = {i for i, b in enumerate(bounds) if (b[1] - b[0]) <= 1.0}
    pre = list(bounds)
    bounds = jcut(bounds)
    for i in zero:
        bounds[i] = pre[i]
        if i > 0:
            bounds[i - 1] = (bounds[i - 1][0], pre[i - 1][1])
        if i + 1 < len(bounds):
            bounds[i + 1] = (pre[i + 1][0], bounds[i + 1][1])
    return [(s, e, c) for (s, e), c in zip(bounds, ren, strict=True) if e - s > 1.0]


def test_the_fallback_never_exempts_a_frame_the_renderer_paints_with_a_card():
    """Seeded random timelines with piles, collapsed and sub-dwell windows, failed and empty-asset
    rows and unanchored clips. Every frame the model exempts must show only full-bleed assets in
    the renderer's own layout. 7 seeds x 3000 timelines (~184k frames) were run while building
    this; 400 timelines keep the suite fast."""
    styles = [("scene_image", {}), ("video_hero", VEO), ("diagram", {}),
              ("motion", {"render_mode": "video", "motion_render": "gsap"})]
    rng = random.Random(20261005)
    frames = exempt = 0
    for trial in range(400):
        t, clips = 0, []
        for i in range(rng.randint(3, 14)):
            if i:
                t += rng.choice([0, 0, 200, 400, 900, 1300, 2500, 4000, 4000, 7000, 7000])
            modality, extra = rng.choice(styles)
            c = {"beat_index": i, "start_ms": t, "duration_ms": rng.choice([0, 1000, 3000, 6000]),
                 "modality": modality, "status": "done",
                 "asset_uri": f"gs://b/{trial}_{i}." + ("mp4" if extra else "png"), **extra}
            roll = rng.random()
            if roll < 0.08:
                c["status"] = "failed"
            elif roll < 0.12:
                c["asset_uri"] = ""
            elif roll < 0.17:
                c["start_ms"] = None
            clips.append(c)
        span = max([c["start_ms"] for c in clips if c["start_ms"] is not None] or [0]) + rng.choice(
            [1000, 3000, 8000, 20000])
        truth = _painted_by_renderer(clips, span)
        model = DeliveredTimeline.from_clips(clips, real_offsets=True, master_ms=span)
        for k in range(span // 3000 + 1):
            ts = 3000 * k + 1499
            on = [c for s, e, c in truth if s <= ts < e]
            if ts >= span or not on:
                continue
            frames += 1
            if model.full_bleed_at(ts) is True:
                exempt += 1
                assert all(bleeds_by_design(c) for c in on), (
                    f"trial {trial} t={ts}: exempt, but the renderer paints "
                    f"{[c.get('modality') for c in on]}; clips={clips}")
    assert frames > 1000 and exempt > 100, (frames, exempt)   # the property was exercised
