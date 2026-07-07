"""Unit tests for kitesforu_qa.scorecard.signals — the $0 deterministic signal extractors that
back every axis. Pure dict-in/dict-out (no ffmpeg, no network) except where noted."""
from __future__ import annotations

from kitesforu_qa.harness.artifact import Artifact
from kitesforu_qa.scorecard import signals


def _art(doc: dict, **kwargs) -> Artifact:
    return Artifact.from_doc(doc, **kwargs)


# ── is_short ───────────────────────────────────────────────────────────────────


def test_is_short_by_format() -> None:
    assert signals.is_short(_art({"format": "short_video"})) is True


def test_is_short_by_duration() -> None:
    assert signals.is_short(_art({"inputs": {"duration_min": 0.5}})) is True


def test_is_short_false_for_long_podcast() -> None:
    assert signals.is_short(_art({"format": "podcast", "inputs": {"duration_min": 12}})) is False


# ── hook line / word count ──────────────────────────────────────────────────────


def test_hook_line_prefers_dialogue() -> None:
    doc = {"outputs": {"script": {"dialogue": [{"speaker": "Narrator", "text": "Hello world."}]}}}
    assert signals.hook_line(_art(doc)) == "Hello world."


def test_hook_line_falls_back_to_segments() -> None:
    doc = {"segments_ready": [{"text_preview": "Fallback hook."}]}
    assert signals.hook_line(_art(doc)) == "Fallback hook."


def test_hook_line_empty_when_no_dialogue_or_segments() -> None:
    assert signals.hook_line(_art({})) == ""


def test_word_count() -> None:
    assert signals.word_count("Your brain deletes memories on purpose.") == 6
    assert signals.word_count("") == 0
    assert signals.word_count("   ") == 0


# ── first speech timing ─────────────────────────────────────────────────────────


def test_first_speech_ms_reads_master_segment_timeline() -> None:
    doc = {"master_segment_timeline": [{"index": 0, "start_ms": 120, "end_ms": 900}, {"index": 1, "start_ms": 900, "end_ms": 2000}]}
    assert signals.first_speech_ms(_art(doc)) == 120.0


def test_first_speech_ms_none_when_no_timeline() -> None:
    assert signals.first_speech_ms(_art({})) is None


# ── first beat / photo-first-frame ──────────────────────────────────────────────


def test_first_beat_clip_picks_lowest_beat_index() -> None:
    doc = {"visual": {"clips": [
        {"beat_index": 2, "start_ms": 5000},
        {"beat_index": 0, "start_ms": 0},
        {"beat_index": 1, "start_ms": 2000},
    ]}}
    clip = signals.first_beat_clip(_art(doc))
    assert clip is not None
    assert clip["beat_index"] == 0


def test_first_beat_clip_none_when_no_clips() -> None:
    assert signals.first_beat_clip(_art({})) is None


def test_is_photo_first_frame_true_for_scene_image_modality() -> None:
    assert signals.is_photo_first_frame({"modality": "scene_image", "render_mode": "still"}) is True


def test_is_photo_first_frame_true_for_motion_render_mode_regardless_of_modality() -> None:
    assert signals.is_photo_first_frame({"modality": "diagram", "render_mode": "parallax_2_5d"}) is True


def test_is_photo_first_frame_false_for_static_text_card() -> None:
    assert signals.is_photo_first_frame({"modality": "kinetic_text", "render_mode": "still"}) is False


def test_is_photo_first_frame_false_for_none_clip() -> None:
    assert signals.is_photo_first_frame(None) is False


# ── modality mix ────────────────────────────────────────────────────────────────


def test_modality_bucket_counts_maps_known_modalities() -> None:
    doc = {"visual": {"clips": [
        {"beat_index": 0, "modality": "scene_image"},
        {"beat_index": 1, "modality": "diagram"},
        {"beat_index": 2, "modality": "kinetic_text"},
    ]}}
    counts = signals.modality_bucket_counts(_art(doc))
    assert counts["ai_photo"] == 1
    assert counts["diagram"] == 1
    assert counts["kinetic_text"] == 1


def test_modality_bucket_counts_infers_scene_from_motion_render_mode_when_modality_unset() -> None:
    doc = {"visual": {"clips": [{"beat_index": 0, "modality": None, "render_mode": "video"}]}}
    counts = signals.modality_bucket_counts(_art(doc))
    assert counts["scene"] == 1


def test_modality_bucket_counts_counts_distinct_beats_once() -> None:
    # Two clips on the SAME beat (a multi-reveal build) must count once, not twice.
    doc = {"visual": {"clips": [
        {"beat_index": 0, "modality": "diagram", "start_ms": 0},
        {"beat_index": 0, "modality": "diagram", "start_ms": 500},
    ]}}
    counts = signals.modality_bucket_counts(_art(doc))
    assert sum(counts.values()) == 1


# ── motion beats ────────────────────────────────────────────────────────────────


def test_motion_beats_counts_render_mode_and_preset() -> None:
    doc = {"visual": {"clips": [
        {"beat_index": 0, "render_mode": "parallax_2_5d"},
        {"beat_index": 1, "render_mode": "still", "motion_preset": "pan"},
        {"beat_index": 2, "render_mode": "still"},
    ]}}
    n_motion, n_total, records = signals.motion_beats(_art(doc))
    assert n_motion == 2
    assert n_total == 3
    assert len(records) == 2


def test_rendered_motion_clips_counter_reads_visual_field() -> None:
    assert signals.rendered_motion_clips_counter(_art({"visual": {"rendered_motion_clips": 3}})) == 3
    assert signals.rendered_motion_clips_counter(_art({})) is None


# ── word-timestamp tracks (sync exactness) ──────────────────────────────────────


def test_word_timestamp_track_absent_by_default() -> None:
    assert signals.word_timestamp_track(_art({})) is None


def test_word_timestamp_track_rejects_segment_level_captions() -> None:
    # captions_vtt / segment text_preview must NOT be accepted as a word-level track.
    doc = {"segments_ready": [{"text_preview": "not a word track"}]}
    assert signals.word_timestamp_track(_art(doc)) is None


def test_word_timestamp_track_extracts_pairs_when_present() -> None:
    doc = {"word_timestamps": [
        {"word": "Your", "start_ms": 0}, {"word": "brain", "start_ms": 300},
    ]}
    track = signals.word_timestamp_track(_art(doc))
    assert track is not None
    assert track["words"] == 2
    assert track["pairs"] == [("Your", 0.0), ("brain", 300.0)]


def test_word_timestamp_track_converts_seconds_to_ms() -> None:
    doc = {"word_timestamps": [{"word": "Hi", "start": 0.5}]}
    track = signals.word_timestamp_track(_art(doc))
    assert track is not None
    assert track["pairs"] == [("Hi", 500.0)]


def test_karaoke_word_track_absent_by_default() -> None:
    assert signals.karaoke_word_track(_art({})) is None


def test_karaoke_word_track_extracts_pairs_when_present() -> None:
    doc = {"visual": {"karaoke_word_timestamps": [{"word": "Hi", "start_ms": 10}]}}
    track = signals.karaoke_word_track(_art(doc))
    assert track is not None
    assert track["pairs"] == [("Hi", 10.0)]


# ── research grounding ───────────────────────────────────────────────────────────


def test_research_grounding_llm_route_but_skipped_is_not_grounded() -> None:
    doc = {"stages": {"job-research-planner": {
        "route": {"research_mode": "llm"},
        "result": {"research_skipped": True, "reason": "evergreen_topic"},
    }}}
    g = signals.research_grounding(_art(doc))
    assert g["research_mode"] == "llm"
    assert g["research_skipped"] is True
    assert g["grounded"] is False


def test_research_grounding_llm_route_and_not_skipped_is_grounded() -> None:
    doc = {"stages": {"job-research-planner": {
        "route": {"research_mode": "llm"},
        "result": {"research_skipped": False},
    }}}
    assert signals.research_grounding(_art(doc))["grounded"] is True


def test_research_grounding_none_route_is_not_grounded() -> None:
    doc = {"stages": {"job-research-planner": {"route": {"research_mode": "none"}, "result": {}}}}
    assert signals.research_grounding(_art(doc))["grounded"] is False


def test_research_grounding_defaults_when_stage_absent() -> None:
    g = signals.research_grounding(_art({}))
    assert g["grounded"] is False
    assert g["research_mode"] is None


# ── music / cost / safety ────────────────────────────────────────────────────────


def test_music_signals_reads_planner_and_listening_qa() -> None:
    doc = {
        "stages": {
            "music_director": {"cue_count": 4, "music_recommended": True, "coverage_pct": 100.0},
            "job-audio": {"qa": {"listening": {"music": {"expected": True, "detected": True, "observed_margin_db": 13.3}}}},
        },
    }
    m = signals.music_signals(_art(doc))
    assert m["planned_cue_count"] == 4
    assert m["music_recommended"] is True
    assert m["detected"] is True
    assert m["observed_margin_db"] == 13.3


def test_cost_total_usd_reads_costs_block() -> None:
    assert signals.cost_total_usd(_art({"costs": {"total_usd_estimate": 0.05}})) == 0.05
    assert signals.cost_total_usd(_art({})) is None


def test_safety_verdict_absent_returns_none() -> None:
    assert signals.safety_verdict(_art({})) is None


def test_safety_verdict_reads_top_level_safety_block() -> None:
    doc = {"safety": {"passed": True}}
    assert signals.safety_verdict(_art(doc)) == {"passed": True}
