"""Unit tests for kitesforu_qa.scorecard.axes — the 8 SHORT SCORECARD axis-scoring functions.

Each axis is tested for: (1) its PASS case, (2) at least one FAIL case, and (3) — for axes 3/5/6/8,
which can degrade — the GRACEFUL-DEGRADE case: score MUST be ``None`` (never a fabricated pass) with
a ``needs`` string. Video/ffmpeg-dependent internals (``video_duration_s``, ``ffmpeg_scene_change_count``)
and the paid judge/VLM callables are exercised via dependency injection (monkeypatch / ``ScorecardConfig``
callables) so these tests are pure-Python, $0, and fast — no real media, no network.
"""
from __future__ import annotations

from kitesforu_qa.harness.artifact import Artifact
from kitesforu_qa.scorecard import axes
from kitesforu_qa.scorecard.config import ScorecardConfig


def _art(doc: dict, **kwargs) -> Artifact:
    return Artifact.from_doc(doc, **kwargs)


def _cfg(**kwargs) -> ScorecardConfig:
    return ScorecardConfig(**kwargs)


# ── axis 1 — hook stop-power ────────────────────────────────────────────────────


def test_hook_stop_power_full_pass() -> None:
    doc = {
        "visual": {"clips": [{"beat_index": 0, "modality": "scene_image", "render_mode": "parallax_2_5d"}]},
        "outputs": {"script": {"dialogue": [{"speaker": "Narrator", "text": "Your brain deletes dreams on purpose."}]}},
        "master_segment_timeline": [{"index": 0, "start_ms": 0, "end_ms": 2000}],
    }
    r = axes.score_hook_stop_power(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.passed is True
    assert r.tier == "T1"


def test_hook_stop_power_banned_opener_fails_partial_credit() -> None:
    doc = {
        "visual": {"clips": [{"beat_index": 0, "modality": "scene_image", "render_mode": "parallax_2_5d"}]},
        "outputs": {"script": {"dialogue": [{"speaker": "Narrator", "text": "Imagine this: your brain deletes dreams."}]}},
        "master_segment_timeline": [{"index": 0, "start_ms": 0, "end_ms": 2000}],
    }
    r = axes.score_hook_stop_power(_art(doc), _cfg())
    assert r.sub_signals["opener_banned_phrase"] == "imagine this"
    assert r.score is not None and r.score < 100.0


def test_hook_stop_power_flat_text_card_fails_modality() -> None:
    doc = {
        "visual": {"clips": [{"beat_index": 0, "modality": "kinetic_text", "render_mode": "still"}]},
        "outputs": {"script": {"dialogue": [{"speaker": "Narrator", "text": "Your brain deletes dreams."}]}},
    }
    r = axes.score_hook_stop_power(_art(doc), _cfg())
    assert r.sub_signals["modality_ok"] is False
    assert r.score is not None and r.score < 80.0
    assert r.passed is False


def test_hook_stop_power_timing_unmeasured_renormalizes_rather_than_nulling() -> None:
    # No master_segment_timeline on this (legacy) job — timing can't be checked, but the axis must
    # still produce a real score from the 3 signals it CAN measure, never a fabricated None.
    doc = {
        "visual": {"clips": [{"beat_index": 0, "modality": "scene_image", "render_mode": "parallax_2_5d"}]},
        "outputs": {"script": {"dialogue": [{"speaker": "Narrator", "text": "Your brain deletes dreams."}]}},
    }
    r = axes.score_hook_stop_power(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.sub_signals["first_speech_ok"] is None


def test_hook_stop_power_empty_hook_fails_word_count_and_opener() -> None:
    r = axes.score_hook_stop_power(_art({}), _cfg())
    assert r.sub_signals["hook_word_count"] == 0
    assert r.sub_signals["opener_ok"] is False


# ── axis 2 — substance novelty ──────────────────────────────────────────────────


def test_substance_novelty_proxy_when_judge_disabled() -> None:
    doc = {"stages": {"job-research-planner": {"route": {"research_mode": "llm"}, "result": {"research_skipped": False}}}}
    r = axes.score_substance_novelty(_art(doc), _cfg(enable_judge=False))
    assert r.proxy is True
    assert r.score == 50.0  # grounded only; no angle brief on this synthetic doc
    assert r.needs is not None


def test_substance_novelty_ungrounded_skipped_research_scores_zero_proxy() -> None:
    doc = {"stages": {"job-research-planner": {
        "route": {"research_mode": "llm"},
        "result": {"research_skipped": True, "reason": "evergreen_topic"},
    }}}
    r = axes.score_substance_novelty(_art(doc), _cfg(enable_judge=False))
    assert r.proxy is True
    assert r.score == 0.0
    assert r.passed is False


def test_substance_novelty_enabled_without_judge_fn_is_honest_null() -> None:
    r = axes.score_substance_novelty(_art({}), _cfg(enable_judge=True, judge_fn=None))
    assert r.score is None
    assert r.proxy is False
    assert "not wired" in r.how_measured


def test_substance_novelty_enabled_with_judge_fn_is_authoritative() -> None:
    def fake_judge(art):
        return 88.0, "clear non-obvious claim, well-sourced"

    r = axes.score_substance_novelty(_art({}), _cfg(enable_judge=True, judge_fn=fake_judge))
    assert r.score == 88.0
    assert r.proxy is False
    assert r.passed is True


def test_substance_novelty_judge_fn_exception_degrades_to_null() -> None:
    def broken_judge(art):
        raise RuntimeError("provider down")

    r = axes.score_substance_novelty(_art({}), _cfg(enable_judge=True, judge_fn=broken_judge))
    assert r.score is None
    assert r.needs is not None


# ── axis 3 — visual truth ────────────────────────────────────────────────────────


def test_visual_truth_vacuous_pass_when_no_photoreal_beats() -> None:
    doc = {"visual": {"clips": [{"beat_index": 0, "modality": "diagram"}]}}
    r = axes.score_visual_truth(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.passed is True


def test_visual_truth_disabled_with_photoreal_beats_is_honest_null() -> None:
    doc = {"visual": {"clips": [{"beat_index": 0, "modality": "scene_image", "asset_uri": "gs://x/y.png"}]}}
    r = axes.score_visual_truth(_art(doc), _cfg(enable_vlm=False))
    assert r.score is None
    assert r.needs is not None
    assert "enable_vlm=True" in r.needs


def test_visual_truth_enabled_without_vlm_fn_is_honest_null() -> None:
    doc = {"visual": {"clips": [{"beat_index": 0, "modality": "ai_photo", "asset_uri": "gs://x/y.png"}]}}
    r = axes.score_visual_truth(_art(doc), _cfg(enable_vlm=True, vlm_fn=None))
    assert r.score is None
    assert "not wired" in r.how_measured


def test_visual_truth_enabled_with_vlm_fn_is_authoritative() -> None:
    def fake_vlm(image_uris, context):
        assert len(image_uris) == 1
        return 40.0, "one beat is an illustration mislabeled as photoreal"

    doc = {"visual": {"clips": [{"beat_index": 0, "modality": "scene_image", "asset_uri": "gs://x/y.png"}]}}
    r = axes.score_visual_truth(_art(doc), _cfg(enable_vlm=True, vlm_fn=fake_vlm))
    assert r.score == 40.0
    assert r.passed is False  # below the floor of 90 — catches the Pixar-labeled-photoreal lie


def test_visual_truth_context_carries_video_path_and_beats_for_a_real_vlm_fn() -> None:
    """A REAL vlm_fn (kitesforu_qa.scorecard.vlm) needs the video_path + per-beat start_ms/asset_uri to
    actually extract a frame — pin that axes.py passes them, not just the resolved image_uris strings."""
    captured: dict = {}

    def fake_vlm(image_uris, context):
        captured.update(context)
        return 100.0, "ok"

    doc = {
        "visual": {"clips": [
            {"beat_index": 2, "modality": "ai_photo", "render_mode": "still", "start_ms": 4500,
             "asset_uri": "gs://x/y.png"},
        ]},
    }
    axes.score_visual_truth(_art(doc, video_path="/tmp/episode.mp4"), _cfg(enable_vlm=True, vlm_fn=fake_vlm))
    assert captured["video_path"] == "/tmp/episode.mp4"
    assert captured["beats"] == [{
        "beat_index": 2, "start_ms": 4500, "asset_uri": "gs://x/y.png",
        "modality": "ai_photo", "render_mode": "still",
    }]


# ── axis 4 — modality mix ────────────────────────────────────────────────────────


def test_modality_mix_no_clips_is_honest_null() -> None:
    r = axes.score_modality_mix(_art({}), _cfg())
    assert r.score is None
    assert r.needs is not None


def test_modality_mix_single_modality_reel_scores_zero() -> None:
    doc = {"visual": {"clips": [
        {"beat_index": i, "modality": "diagram"} for i in range(5)
    ]}}
    r = axes.score_modality_mix(_art(doc), _cfg())
    assert r.score == 0.0
    assert r.passed is False


def test_modality_mix_balanced_four_way_split_scores_100() -> None:
    doc = {"visual": {"clips": [
        {"beat_index": 0, "modality": "scene_image"},
        {"beat_index": 1, "modality": "diagram"},
        {"beat_index": 2, "modality": "kinetic_text"},
        {"beat_index": 3, "modality": "scene", "render_mode": "video"},
    ]}}
    r = axes.score_modality_mix(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.passed is True


# ── axis 5 — motion density ──────────────────────────────────────────────────────


def test_motion_density_from_renderer_provenance(monkeypatch) -> None:
    doc = {"visual": {"clips": [
        {"beat_index": 0, "render_mode": "parallax_2_5d"},
        {"beat_index": 1, "render_mode": "still"},
        {"beat_index": 2, "render_mode": "still"},
        {"beat_index": 3, "render_mode": "still"},
    ]}}
    art = _art(doc)
    monkeypatch.setattr(axes.signals, "video_duration_s", lambda a: 4.0)  # 1 motion beat / 4s = target rate
    r = axes.score_motion_density(art, _cfg())
    assert r.score == 100.0
    assert r.proxy is False


def test_motion_density_below_target_rate_fails(monkeypatch) -> None:
    doc = {"visual": {"clips": [{"beat_index": i, "render_mode": "still"} for i in range(10)]}}
    art = _art(doc)
    monkeypatch.setattr(axes.signals, "video_duration_s", lambda a: 40.0)
    r = axes.score_motion_density(art, _cfg())
    assert r.score == 0.0
    assert r.passed is False


def test_motion_density_no_duration_is_honest_null() -> None:
    r = axes.score_motion_density(_art({}), _cfg())
    assert r.score is None
    assert r.needs is not None


def test_motion_density_ffmpeg_fallback_when_no_provenance(monkeypatch) -> None:
    art = _art({}, video_path="/fake/video.mp4")
    monkeypatch.setattr(axes.signals, "video_duration_s", lambda a: 10.0)
    monkeypatch.setattr(axes.signals, "ffmpeg_scene_change_count", lambda path: 3)
    r = axes.score_motion_density(art, _cfg(ffmpeg_motion_fallback=True))
    assert r.proxy is True
    assert r.score is not None
    assert r.sub_signals["ffmpeg_scene_changes"] == 3


def test_motion_density_no_provenance_no_fallback_no_video_is_honest_null(monkeypatch) -> None:
    monkeypatch.setattr(axes.signals, "video_duration_s", lambda a: 10.0)
    r = axes.score_motion_density(_art({}), _cfg(ffmpeg_motion_fallback=True))
    assert r.score is None
    assert "no local video file" in r.evidence


def test_motion_density_surfaces_provenance_contradiction_as_evidence(monkeypatch) -> None:
    doc = {"visual": {"clips": [{"beat_index": 0, "render_mode": "video"}], "rendered_motion_clips": 0}}
    art = _art(doc)
    monkeypatch.setattr(axes.signals, "video_duration_s", lambda a: 4.0)
    r = axes.score_motion_density(art, _cfg())
    assert "contradicts" in r.evidence
    # 2026-07 calibration fix: a contradiction between the renderer's OWN rendered_motion_clips
    # counter and the render_mode-derived beat count means the signal this score is computed FROM
    # is one the pipeline's own telemetry disagrees with — so the score must be flagged non-
    # authoritative (proxy=True), not silently trusted (previously only a text note, invisible to
    # the aggregate ranking in harness/quality_matrix.py).
    assert r.proxy is True
    assert r.needs is not None
    assert r.sub_signals["provenance_contradiction"] is True


def test_motion_density_no_contradiction_is_not_proxy(monkeypatch) -> None:
    # Sanity check: agreement between the two provenance signals must NOT be flagged proxy.
    doc = {"visual": {"clips": [{"beat_index": 0, "render_mode": "video"}], "rendered_motion_clips": 1}}
    art = _art(doc)
    monkeypatch.setattr(axes.signals, "video_duration_s", lambda a: 4.0)
    r = axes.score_motion_density(art, _cfg())
    assert r.proxy is False
    assert r.sub_signals["provenance_contradiction"] is False


# ── axis 6 — sync exactness ──────────────────────────────────────────────────────


def test_sync_exactness_no_ground_truth_track_is_honest_null() -> None:
    r = axes.score_sync_exactness(_art({}), _cfg())
    assert r.score is None
    assert "Phase 1a" in r.needs


def test_sync_exactness_truth_present_but_no_karaoke_track_is_honest_null() -> None:
    doc = {"word_timestamps": [{"word": "hi", "start_ms": 0}]}
    r = axes.score_sync_exactness(_art(doc), _cfg())
    assert r.score is None
    assert "rendered" in r.needs.lower() or "karaoke" in r.needs.lower()


def test_sync_exactness_zero_drift_scores_100() -> None:
    doc = {
        "word_timestamps": [{"word": "Your", "start_ms": 0}, {"word": "brain", "start_ms": 300}],
        "visual": {"karaoke_word_timestamps": [{"word": "Your", "start_ms": 0}, {"word": "brain", "start_ms": 300}]},
    }
    r = axes.score_sync_exactness(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.passed is True


def test_sync_exactness_drift_at_floor_ms_scores_exactly_the_floor() -> None:
    # word_sync_floor_ms defaults to 60.0 — per config's documented contract, a median drift EQUAL
    # to that constant must land the score exactly on the axis floor (85).
    cfg = _cfg(word_sync_floor_ms=60.0)
    doc = {
        "word_timestamps": [{"word": "Your", "start_ms": 0}],
        "visual": {"karaoke_word_timestamps": [{"word": "Your", "start_ms": 60}]},
    }
    r = axes.score_sync_exactness(_art(doc), cfg)
    assert r.score == 85.0
    assert r.passed is True  # exactly at the floor -> passes (>=)


def test_sync_exactness_large_drift_fails() -> None:
    doc = {
        "word_timestamps": [{"word": "Your", "start_ms": 0}],
        "visual": {"karaoke_word_timestamps": [{"word": "Your", "start_ms": 600}]},
    }
    r = axes.score_sync_exactness(_art(doc), _cfg())
    assert r.score is not None and r.score < 85.0
    assert r.passed is False


# ── axis 7 — audio feel ──────────────────────────────────────────────────────────


class _FakeJudgeModule:
    def __init__(self, result):
        self._result = result

    def judge(self, path):
        return dict(self._result)


def test_audio_feel_no_media_is_honest_null() -> None:
    r = axes.score_audio_feel(_art({}), _cfg())
    assert r.score is None
    assert r.needs is not None


def test_audio_feel_real_music_with_detected_cues_scores_high(monkeypatch) -> None:
    monkeypatch.setattr(axes, "_audio_quality_judge", _FakeJudgeModule({"verdict": "likely_real_music (moves)"}))
    doc = {"stages": {
        "music_director": {"cue_count": 4, "music_recommended": True},
        "job-audio": {"qa": {"listening": {"music": {"detected": True}}}},
    }}
    r = axes.score_audio_feel(_art(doc, audio_path="/fake/audio.mp3"), _cfg())
    assert r.score == 93.0
    assert r.passed is True


def test_audio_feel_drone_with_music_expected_scores_low(monkeypatch) -> None:
    monkeypatch.setattr(axes, "_audio_quality_judge", _FakeJudgeModule({"verdict": "DRONE/HUM (tuneless)"}))
    doc = {"stages": {
        "music_director": {"cue_count": 4, "music_recommended": True},
        "job-audio": {"qa": {"listening": {"music": {"detected": False}}}},
    }}
    r = axes.score_audio_feel(_art(doc, audio_path="/fake/audio.mp3"), _cfg())
    assert r.score is not None and r.score < 70.0
    assert r.passed is False


def test_audio_feel_no_music_bed_on_dry_content_is_not_penalized(monkeypatch) -> None:
    # No music planned/expected -> a no_music_bed verdict is CORRECT, not a defect.
    monkeypatch.setattr(axes, "_audio_quality_judge", _FakeJudgeModule({"verdict": "no_music_bed (speech-only)"}))
    r = axes.score_audio_feel(_art({}, audio_path="/fake/audio.mp3"), _cfg())
    assert r.score is not None and r.score >= 70.0


def test_audio_feel_judge_exception_degrades_to_null(monkeypatch) -> None:
    class _Raising:
        def judge(self, path):
            raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(axes, "_audio_quality_judge", _Raising())
    r = axes.score_audio_feel(_art({}, audio_path="/fake/audio.mp3"), _cfg())
    assert r.score is None


def test_audio_feel_falls_back_to_video_when_no_audio_path(monkeypatch) -> None:
    seen = {}

    class _Recording:
        def judge(self, path):
            seen["path"] = path
            return {"verdict": "ambiguous (inspect)"}

    monkeypatch.setattr(axes, "_audio_quality_judge", _Recording())
    r = axes.score_audio_feel(_art({}, video_path="/fake/video.mp4"), _cfg())
    assert seen["path"] == "/fake/video.mp4"
    assert r.score is not None


# ── axis 8 — cost + safety ────────────────────────────────────────────────────────


def test_cost_safety_both_pass_scores_100() -> None:
    doc = {"costs": {"total_usd_estimate": 0.05}, "safety": {"passed": True}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.passed is True


def test_cost_safety_over_cap_fails_even_with_unknown_safety() -> None:
    doc = {"costs": {"total_usd_estimate": 0.18}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score == 0.0
    assert r.passed is False


def test_cost_safety_flagged_safety_fails_even_with_cost_under_cap() -> None:
    doc = {"costs": {"total_usd_estimate": 0.02}, "safety": {"flagged": True}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score == 0.0


def test_cost_safety_both_missing_is_honest_null() -> None:
    r = axes.score_cost_safety(_art({}), _cfg())
    assert r.score is None
    assert "cost rollup" in r.needs
    assert "safety verdict" in r.needs


def test_cost_safety_cost_under_cap_but_safety_unknown_is_honest_null() -> None:
    doc = {"costs": {"total_usd_estimate": 0.02}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score is None
    assert r.needs == "safety verdict (no safety/moderation block persisted)"


# ── axis 8 — cost + safety: TIER-AWARE cap calibration (2026-07 fix) ──────────────
#
# QUALITY_BACKLOG.md's first baseline found cost_safety at 0/100 on 100% of scored cells. Root
# cause: a single $0.10 cap applied regardless of quality_tier, when the tier system itself targets
# low ~$0.025, medium ~$0.15, high ~$1.0-1.3, ultra "flagship headroom" — so medium/high/ultra jobs
# were GUARANTEED to fail by construction, not because they overspent. These pin the tier-aware cap.


def test_cost_safety_medium_tier_passes_above_the_old_flat_cap() -> None:
    # $0.15 blew the old flat $0.10 cap but is exactly medium tier's documented target cost.
    doc = {"quality_tier": "medium", "costs": {"total_usd_estimate": 0.15}, "safety": {"passed": True}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.sub_signals["cost_cap_usd"] == 0.30
    assert r.sub_signals["quality_tier"] == "medium"


def test_cost_safety_high_tier_passes_above_the_old_flat_cap() -> None:
    doc = {"quality_tier": "high", "costs": {"total_usd_estimate": 1.3}, "safety": {"passed": True}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.sub_signals["cost_cap_usd"] == 2.00


def test_cost_safety_ultra_tier_passes_above_the_old_flat_cap() -> None:
    doc = {"quality_tier": "ultra", "costs": {"total_usd_estimate": 1.55}, "safety": {"passed": True}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score == 100.0
    assert r.sub_signals["cost_cap_usd"] == 5.00


def test_cost_safety_medium_tier_still_fails_a_genuine_overrun() -> None:
    # A tier-aware cap must still CATCH real overspend, not rubber-stamp every tier.
    doc = {"quality_tier": "medium", "costs": {"total_usd_estimate": 5.0}, "safety": {"passed": True}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.score == 0.0
    assert r.passed is False


def test_cost_safety_unknown_tier_falls_back_to_the_strict_low_cap() -> None:
    doc = {"costs": {"total_usd_estimate": 0.18}, "safety": {"passed": True}}  # no quality_tier field
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.sub_signals["quality_tier"] == "unknown"
    assert r.sub_signals["cost_cap_usd"] == 0.10
    assert r.score == 0.0  # unknown tier never gets a MORE lenient cap than the low-tier default


def test_cost_safety_low_tier_cap_unchanged_at_ten_cents() -> None:
    doc = {"quality_tier": "low", "costs": {"total_usd_estimate": 0.11}, "safety": {"passed": True}}
    r = axes.score_cost_safety(_art(doc), _cfg())
    assert r.sub_signals["cost_cap_usd"] == 0.10
    assert r.score == 0.0
