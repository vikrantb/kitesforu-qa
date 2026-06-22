"""Self-test for the Claude-parallel deterministic check batteries (audio / visual / video_sync /
music) on branch feat/qh-check-batteries.

Strategy: build a GOOD synthetic Artifact and a DEGRADED one and prove, per dimension, that
  * GOOD  -> the gating (critical+high) checks PASS,
  * BAD   -> the relevant degraded check FAILS,
  * N/A   -> the dimension's checks SKIP (no audio / no visuals / no video / music not expected).

Media is synthesized at test time with ffmpeg (audio + video) and PIL (images) — all $0, no cloud,
no network. The audio/video checks shell ffprobe/ffmpeg exactly as production does, so this exercises
the REAL code path, not a mock.

These tests import the worktree's checks via the registry that ``kitesforu_qa.harness.checks`` builds
on import. Run with the worktree src on PYTHONPATH so the EDITABLE install (which points at the
Claude-main checkout, lacking these 4 files) does not shadow them:

    PYTHONPATH=$PWD/src python -m pytest tests/test_check_batteries.py
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from kitesforu_qa.harness import Artifact, run_dimension
from kitesforu_qa.harness.check import checks_for

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required to synthesize audio/video fixtures",
)

_GATING = {"critical", "high"}


# ── media synthesis helpers (ffmpeg / PIL) ───────────────────────────────────


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True, capture_output=True, text=True, timeout=120,
    )


def _good_audio(path: str, *, seconds: float = 20.0) -> str:
    """Stereo 48 kHz tone normalized to ~-16 LUFS: lands in the explainer LUFS band, peak well under
    0 dBFS (no clipping), no silence, full-length. loudnorm hits the genre loudness window the
    audio.loudness_in_target_range check asks for, so the GOOD master is genuinely in-spec."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "2", "-ar", "48000",
        "-c:a", "pcm_s16le", path,
    )
    return path


def _clipped_audio(path: str, *, seconds: float = 20.0) -> str:
    """Tone driven hard into the rails: sample peak clamps at 0.0 dBFS -> no_clipping must fail."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
        # +40 dB then clamp at 0 dBFS via alimiter-less hard drive: ffmpeg integer codec clips at full.
        "-af", "volume=40dB", "-ac", "2", "-ar", "48000",
        "-c:a", "pcm_s16le", path,
    )
    return path


def _truncated_audio(path: str, *, seconds: float = 1.0) -> str:
    """A tiny stub far shorter than the script implies -> not_truncated must fail."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
        "-af", "volume=-12dB", "-ac", "2", "-ar", "48000",
        "-c:a", "pcm_s16le", path,
    )
    return path


def _low_sample_rate_audio(path: str, *, seconds: float = 20.0) -> str:
    """8 kHz telephone-grade encode -> sample_rate_ok must fail."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
        "-af", "volume=-12dB", "-ac", "2", "-ar", "8000",
        "-c:a", "pcm_s16le", path,
    )
    return path


def _video(path: str, *, seconds: float) -> str:
    """A solid-color test video of the given length (duration is all video_sync probes)."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=navy:s=320x180:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "12", path,
    )
    return path


def _good_image(path: str, *, w: int = 1280, h: int = 720) -> str:
    """A 16:9 pictorial image with pixel variance (a soft gradient) -> not_blank passes."""
    from PIL import Image
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(0, w, 4):  # coarse stride keeps it fast; still plenty of variance
            v = (x + y) % 256
            for dx in range(4):
                if x + dx < w:
                    px[x + dx, y] = (v, (v * 2) % 256, (v * 3) % 256)
    img.save(path)
    return path


def _blank_image(path: str, *, w: int = 1280, h: int = 720) -> str:
    """A flat grey frame (zero variance) -> not_blank must fail (the 16:9-blank-pad regression)."""
    from PIL import Image
    Image.new("RGB", (w, h), (128, 128, 128)).save(path)
    return path


# ── synthetic docs ───────────────────────────────────────────────────────────


def _base_doc() -> dict:
    """A completed explainer doc with a short script (~40 words -> ~16 s implied at 150 wpm) so a 20 s
    GOOD master is NOT flagged as truncated, while the 1 s stub fixture IS. Segments clock 4x5 s = 20 s
    to match the GOOD audio length for the video-sync beat windows."""
    text = (
        "A B-tree is a balanced index. For example, imagine a library catalog: you look up a "
        "letter and jump to the right shelf. It works by keeping keys sorted so a lookup only "
        "touches a few pages."
    )
    seg_count = 4
    seg_ms = 5000
    return {
        "job_id": "synthetic-good",
        "status": "completed",
        "episode_profile": {"genre": "explainer", "topic": "How a B-tree index works"},
        "inputs": {"duration_min": 2, "genre": "explainer"},
        "script": {"dialogue": [
            {"speaker": "Maya", "text": text[: len(text) // 2]},
            {"speaker": "Theo", "text": text[len(text) // 2:]},
        ]},
        "segments_ready": [
            {"index": i, "text": f"segment {i} narration content here", "duration_ms": seg_ms}
            for i in range(seg_count)
        ],
        # segment->beat: 4 segments collapse into 2 beats (the producer's dict shape).
        "segment_beat_map": {"0": 0, "1": 0, "2": 1, "3": 1},
    }


def _visual_clips() -> list[dict]:
    """Two beats: beat 0 a pictorial scene, beat 1 a diagram that BUILDS over 3 reveal frames.
    Spans clock gaplessly across the 20 s audio (segments are 4x5 s)."""
    return [
        {"beat_index": 0, "start_ms": 0, "duration_ms": 10000, "modality": "scene",
         "render_mode": "image", "aspect_ratio": "16:9", "asset_uri": "gs://x/0.png"},
        # diagram sub-clips share beat_index 1 and carry reveal indices (node-by-node build)
        {"beat_index": 1, "start_ms": 10000, "duration_ms": 3333, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 0, "_reveal_total": 3, "asset_uri": "gs://x/1a.png"},
        {"beat_index": 1, "start_ms": 13333, "duration_ms": 3333, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 1, "_reveal_total": 3, "asset_uri": "gs://x/1b.png"},
        {"beat_index": 1, "start_ms": 16666, "duration_ms": 3334, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 2, "_reveal_total": 3, "asset_uri": "gs://x/1c.png"},
    ]


def _captions(audio_ms: int = 20000) -> list[dict]:
    """Monotonic cues covering the full audio span."""
    n = 5
    step = audio_ms // n
    return [{"start_ms": i * step, "end_ms": (i + 1) * step, "text": f"cue {i}"} for i in range(n)]


def _music_stage(*, expected: bool = True, detected: bool = True, margin: float = 10.0,
                 intro_ms: int | None = 12000, fail: bool = False) -> dict:
    """A job-audio stage with a listening-QA music verdict. ``intro_ms=None`` omits the intro field
    (a genuinely no-music job, so even music.intro_outro_bounded skips)."""
    result: dict = {}
    if intro_ms is not None:
        result["intro_duration_ms"] = intro_ms
    return {
        "job-audio": {
            "qa": {"listening": {"music": {
                "expected": expected, "detected": detected, "method": "energy",
                "observed_margin_db": margin, "floor_margin_db": 6.0, "fail": fail,
            }}},
            "result": result,
        }
    }


# ── helpers to assert per-dimension ──────────────────────────────────────────


def _gating_results(art: Artifact, dimension: str):
    sr = run_dimension(art, dimension, genre=art.genre)
    checks = sr.data["checks"]
    return sr, {c["check_id"]: c for c in checks}


def _assert_gating_pass(art: Artifact, dimension: str):
    sr, by = _gating_results(art, dimension)
    assert sr.passed, f"{dimension} GOOD should pass gating; issues={sr.issues}"
    # at least one non-skipped check actually ran (the dimension isn't entirely N/A)
    assert any(not c["skipped"] for c in by.values()), f"{dimension}: nothing ran on GOOD artifact"
    return by


def _assert_all_skipped(art: Artifact, dimension: str):
    sr, by = _gating_results(art, dimension)
    ran = [c for c in by.values() if not c["skipped"]]
    assert not ran, f"{dimension} N/A: expected all skips, but these ran: {[c['check_id'] for c in ran]}"
    assert sr.passed, f"{dimension} N/A: all-skip must not fail the gate"


# ════════════════════════════════ AUDIO-MIX ══════════════════════════════════


def test_audio_good_passes(tmp_path):
    doc = _base_doc()
    art = Artifact.from_doc(doc, audio_path=_good_audio(str(tmp_path / "good.wav")))
    by = _assert_gating_pass(art, "audio-mix")
    assert by["audio.no_clipping"]["passed"], by["audio.no_clipping"]["evidence"]
    assert by["audio.not_truncated"]["passed"], by["audio.not_truncated"]["evidence"]
    assert by["audio.sample_rate_ok"]["passed"], by["audio.sample_rate_ok"]["evidence"]


def test_audio_clipping_fails(tmp_path):
    art = Artifact.from_doc(_base_doc(), audio_path=_clipped_audio(str(tmp_path / "clip.wav")))
    _sr, by = _gating_results(art, "audio-mix")
    assert not by["audio.no_clipping"]["passed"], (
        f"clipped audio should fail no_clipping: {by['audio.no_clipping']['evidence']}"
    )


def test_audio_truncated_fails(tmp_path):
    art = Artifact.from_doc(_base_doc(), audio_path=_truncated_audio(str(tmp_path / "stub.wav")))
    _sr, by = _gating_results(art, "audio-mix")
    assert not by["audio.not_truncated"]["passed"], by["audio.not_truncated"]["evidence"]


def test_audio_low_sample_rate_fails(tmp_path):
    art = Artifact.from_doc(_base_doc(), audio_path=_low_sample_rate_audio(str(tmp_path / "lo.wav")))
    _sr, by = _gating_results(art, "audio-mix")
    assert not by["audio.sample_rate_ok"]["passed"], by["audio.sample_rate_ok"]["evidence"]


def test_audio_na_when_no_audio():
    """No audio file -> every audio-mix check skips (a podcast doc with no downloaded master)."""
    _assert_all_skipped(Artifact.from_doc(_base_doc()), "audio-mix")


# ════════════════════════════════ VISUAL-IMAGES ══════════════════════════════


def test_visual_good_passes(tmp_path):
    doc = _base_doc()
    doc["visual_clips"] = _visual_clips()
    imgs = [
        _good_image(str(tmp_path / "v0.png")),
        _good_image(str(tmp_path / "v1a.png")),
        _good_image(str(tmp_path / "v1b.png")),
        _good_image(str(tmp_path / "v1c.png")),
    ]
    art = Artifact.from_doc(doc, image_paths=imgs)
    by = _assert_gating_pass(art, "visual-images")
    assert by["visual.images_present"]["passed"]
    assert by["visual.not_corrupt"]["passed"]
    assert by["visual.not_blank"]["passed"], by["visual.not_blank"]["evidence"]


def test_visual_blank_fails(tmp_path):
    doc = _base_doc()
    doc["visual_clips"] = _visual_clips()[:1]
    art = Artifact.from_doc(doc, image_paths=[_blank_image(str(tmp_path / "blank.png"))])
    _sr, by = _gating_results(art, "visual-images")
    assert not by["visual.not_blank"]["passed"], by["visual.not_blank"]["evidence"]


def test_visual_missing_images_fails(tmp_path):
    """visual_clips declare assets but none were downloaded -> images_present fails (silent drop)."""
    doc = _base_doc()
    doc["visual_clips"] = _visual_clips()
    art = Artifact.from_doc(doc, image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    assert not by["visual.images_present"]["passed"], by["visual.images_present"]["evidence"]


def test_visual_na_when_no_visuals():
    """A doc with no visual_clips and no images -> the whole visual battery skips."""
    _assert_all_skipped(Artifact.from_doc(_base_doc()), "visual-images")


# ════════════════════════════════ VIDEO-SYNC ═════════════════════════════════


def _video_doc_art(tmp_path, *, video_seconds: float, audio_seconds: float = 20.0):
    doc = _base_doc()
    doc["visual_clips"] = _visual_clips()
    doc["captions"] = _captions(int(audio_seconds * 1000))
    audio = _good_audio(str(tmp_path / "va.wav"), seconds=audio_seconds)
    video = _video(str(tmp_path / "v.mp4"), seconds=video_seconds)
    return Artifact.from_doc(doc, audio_path=audio, video_path=video)


def test_video_good_passes(tmp_path):
    art = _video_doc_art(tmp_path, video_seconds=20.0, audio_seconds=20.0)
    by = _assert_gating_pass(art, "video-sync")
    assert by["video_sync.duration_matches_master"]["passed"], by["video_sync.duration_matches_master"]["evidence"]
    assert by["video_sync.captions_align"]["passed"], by["video_sync.captions_align"]["evidence"]
    assert by["video_sync.clips_beat_aligned"]["passed"], by["video_sync.clips_beat_aligned"]["evidence"]


def test_video_duration_mismatch_fails(tmp_path):
    """Video far shorter than master audio (the WAV-as-mp3 10s/292s class)."""
    art = _video_doc_art(tmp_path, video_seconds=4.0, audio_seconds=20.0)
    _sr, by = _gating_results(art, "video-sync")
    assert not by["video_sync.duration_matches_master"]["passed"], (
        by["video_sync.duration_matches_master"]["evidence"]
    )


def test_video_na_when_no_video(tmp_path):
    """No video_path -> every video-sync check skips (audio-only episode)."""
    doc = _base_doc()
    doc["visual_clips"] = _visual_clips()
    doc["captions"] = _captions()
    art = Artifact.from_doc(doc, audio_path=_good_audio(str(tmp_path / "a.wav")))
    _assert_all_skipped(art, "video-sync")


# ════════════════════════════════ MUSIC-SFX ══════════════════════════════════


def test_music_good_passes():
    doc = _base_doc()
    doc["stages"] = _music_stage(expected=True, detected=True, margin=10.0, intro_ms=12000)
    by = _assert_gating_pass(Artifact.from_doc(doc), "music-sfx")
    assert by["music.present_when_expected"]["passed"], by["music.present_when_expected"]["evidence"]


def test_music_silent_drop_fails():
    """Music planned (expected) but the listening verdict says it's absent — job 6e507451 class."""
    doc = _base_doc()
    doc["stages"] = _music_stage(expected=True, detected=False, fail=True)
    _sr, by = _gating_results(doc_art := Artifact.from_doc(doc), "music-sfx")
    assert doc_art is not None
    assert not by["music.present_when_expected"]["passed"], by["music.present_when_expected"]["evidence"]


def test_music_na_when_not_expected():
    """Genre with no music (expected=False, no intro clip) -> the music battery skips."""
    doc = _base_doc()
    doc["stages"] = _music_stage(expected=False, detected=False, intro_ms=None)
    _assert_all_skipped(Artifact.from_doc(doc), "music-sfx")


def test_music_na_when_no_verdict():
    """No listening-QA stage at all -> the music battery skips (nothing to pin)."""
    _assert_all_skipped(Artifact.from_doc(_base_doc()), "music-sfx")


# ════════════════════════════════ SCORECARD ══════════════════════════════════


def test_scorecard_registers_all_four_dimensions():
    """The four parallel-lane dimensions must be registered (the __init__ add-a-file imports work)."""
    dims = {c.dimension for c in checks_for()}
    for d in ("audio-mix", "visual-images", "video-sync", "music-sfx"):
        assert d in dims, f"dimension {d!r} not registered — checks/__init__ import missing?"


def test_scorecard_fully_loaded_artifact_passes(tmp_path):
    """An artifact with audio + visuals + video + music passes the gate across ALL four dimensions."""
    from kitesforu_qa.harness import run_scorecard, scorecard_summary
    doc = _base_doc()
    doc["visual_clips"] = _visual_clips()
    doc["captions"] = _captions()
    doc["stages"] = _music_stage()
    imgs = [_good_image(str(tmp_path / f"s{i}.png")) for i in range(4)]
    art = Artifact.from_doc(
        doc,
        audio_path=_good_audio(str(tmp_path / "full.wav")),
        image_paths=imgs,
        video_path=_video(str(tmp_path / "full.mp4"), seconds=20.0),
    )
    summary = scorecard_summary(run_scorecard(art))
    for d in ("audio-mix", "visual-images", "video-sync", "music-sfx"):
        assert summary["dimensions"][d]["passed"], (d, summary["all_issues"])
    assert summary["passed"], summary["all_issues"]
