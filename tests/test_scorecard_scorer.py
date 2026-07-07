"""Integration tests for kitesforu_qa.scorecard.scorer.score_short — proves the 8 axes wire
together into one coherent verdict, and that SHIP=True is genuinely achievable (not just a
theoretical rubric constant) when every axis is honestly measurable and clears its floor.

The pure-logic path (test_score_short_ship_true_...) monkeypatches ``video_duration_s`` so it needs
no real media. A SEPARATE, ffmpeg-gated test proves the real ffprobe/audio_quality_judge plumbing
end-to-end with a synthesized clip (skipped if ffmpeg/ffprobe are not installed) — mirroring the
media-synthesis pattern in test_check_batteries.py.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from kitesforu_qa.harness.artifact import Artifact
from kitesforu_qa.scorecard import axes
from kitesforu_qa.scorecard.config import ScorecardConfig
from kitesforu_qa.scorecard.rubric import RUBRIC, SHIP_TOTAL_MIN
from kitesforu_qa.scorecard.scorer import score_axes, score_short


def _perfect_doc() -> dict:
    """A synthetic job doc engineered to clear every axis's floor. Zero beats are labeled
    photoreal (all diagram/kinetic_text with motion render modes) so visual_truth is vacuously
    satisfied WITHOUT needing a VLM — the only paid axis left is substance_novelty, exercised via
    an injected fake judge_fn (still $0 in a test)."""
    return {
        "job_id": "perfect-1",
        "format": "short_video",
        "inputs": {"duration_min": 0.5},
        "visual": {
            # One beat per canonical bucket (diagram / kinetic_text / scene / other) for a
            # perfectly balanced 4-way modality-mix entropy — deliberately NONE labeled
            # ai_photo/photo/scene_image, so visual_truth is vacuously satisfied without a VLM.
            "clips": [
                {"beat_index": 0, "modality": "diagram", "render_mode": "video", "start_ms": 0, "duration_ms": 3000},
                {"beat_index": 1, "modality": "kinetic_text", "render_mode": "parallax_2_5d", "start_ms": 3000, "duration_ms": 3000},
                {"beat_index": 2, "modality": "scene", "render_mode": "still", "start_ms": 6000, "duration_ms": 2000},
                {"beat_index": 3, "modality": None, "render_mode": "still", "start_ms": 8000, "duration_ms": 3000},
            ],
            "rendered_motion_clips": 2,
            "karaoke_word_timestamps": [
                {"word": "Your", "start_ms": 20}, {"word": "brain", "start_ms": 320},
                {"word": "deletes", "start_ms": 620}, {"word": "memories", "start_ms": 1000},
            ],
        },
        "master_segment_timeline": [{"index": 0, "start_ms": 0, "end_ms": 3000}],
        "outputs": {"script": {"dialogue": [
            {"speaker": "Narrator", "text": "Your brain deletes memories on purpose."},
        ]}},
        "stages": {
            "job-research-planner": {"route": {"research_mode": "llm"}, "result": {"research_skipped": False}},
            "music_director": {"cue_count": 2, "music_recommended": True},
            "job-audio": {"qa": {"listening": {"music": {"detected": True}}}},
        },
        "word_timestamps": [
            {"word": "Your", "start_ms": 0}, {"word": "brain", "start_ms": 300},
            {"word": "deletes", "start_ms": 600}, {"word": "memories", "start_ms": 980},
        ],
        "costs": {"total_usd_estimate": 0.05},
        "safety": {"passed": True},
    }


class _FakeMusicJudge:
    def judge(self, path):
        return {"verdict": "likely_real_music (moves)"}


def test_score_short_returns_all_8_axis_keys() -> None:
    result = score_short(Artifact.from_doc({}), ScorecardConfig())
    assert set(result["axes"].keys()) == {spec.name for spec in RUBRIC}
    assert "weighted_total" in result
    assert "ship" in result
    assert "missing_instrumentation" in result


def test_score_short_job_id_passthrough() -> None:
    result = score_short(Artifact.from_doc({"job_id": "abc123"}), ScorecardConfig())
    assert result["job_id"] == "abc123"


def test_score_short_ship_true_when_every_axis_is_honestly_measurable(monkeypatch) -> None:
    art = Artifact.from_doc(_perfect_doc(), audio_path="/fake/audio.mp3")
    monkeypatch.setattr(axes.signals, "video_duration_s", lambda a: 10.0)
    monkeypatch.setattr(axes, "_audio_quality_judge", _FakeMusicJudge())

    def fake_judge(a):
        return 90.0, "clear, well-sourced non-obvious claim"

    cfg = ScorecardConfig(enable_judge=True, judge_fn=fake_judge)
    result = score_short(art, cfg)

    for name, axis in result["axes"].items():
        assert axis["score"] is not None, f"{name} unexpectedly unmeasurable: {axis['evidence']}"
        assert axis["pass"] is True, f"{name} scored {axis['score']} below its floor {axis['floor']}: {axis['evidence']}"
    assert result["missing_instrumentation"] == []
    assert result["provisional_axes"] == []
    assert result["weighted_total"] is not None and result["weighted_total"] >= SHIP_TOTAL_MIN
    assert result["ship"] is True


def test_score_short_baseline_bare_doc_is_not_ship_and_lists_missing_axes() -> None:
    result = score_short(Artifact.from_doc({}), ScorecardConfig())
    assert result["ship"] is False
    missing_names = {m["axis"] for m in result["missing_instrumentation"]}
    # On a bare doc, sync_exactness (no word timestamps) and cost_safety (no costs/safety) are
    # always unmeasurable; visual_truth is vacuously satisfied (no clips at all -> no photoreal beats).
    assert "sync_exactness" in missing_names
    assert "cost_safety" in missing_names


def test_score_axes_dict_keys_match_rubric() -> None:
    result = score_axes(Artifact.from_doc({}), ScorecardConfig())
    assert set(result.keys()) == {spec.name for spec in RUBRIC}


# ── real-media integration test (ffmpeg-gated, mirrors test_check_batteries.py) ────


pytestmark_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required to synthesize a real clip for the integration check",
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, timeout=60)


@pytestmark_ffmpeg
def test_audio_feel_and_motion_density_against_a_real_synthesized_clip(tmp_path) -> None:
    """Proves the REAL ffprobe duration probe + the REAL audio_quality_judge subprocess pipeline
    both function end-to-end (no mocks) — a 9:16, 6s clip with a sine-tone audio bed."""
    video_path = str(tmp_path / "clip.mp4")
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=navy:s=1080x1920:d=6",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
        "-shortest", video_path,
    )
    doc = {"visual": {"clips": [{"beat_index": 0, "render_mode": "parallax_2_5d"}]}}
    art = Artifact.from_doc(doc, video_path=video_path)

    motion = axes.score_motion_density(art, ScorecardConfig())
    assert motion.score is not None  # real ffprobe duration resolved; provenance path taken

    audio = axes.score_audio_feel(art, ScorecardConfig())
    assert audio.score is not None  # real audio_quality_judge ran against the real clip
    assert audio.sub_signals["judge"]["verdict"]
