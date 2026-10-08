"""``scripts/frame_proof.py`` measures the picture, so its span is the VIDEO stream's, not the container's.

The span ends the last estimated window, and it is the length a producer's painted timeline is
checked against (``DeliveredTimeline.from_job``). The container's duration is the longer of the two
streams: on the witness master f7df77bf it is 85.109 s against the video's 85.033 s, and a video can
end well short of its audio (the producer's ``video_short_of_master``). Real ffmpeg, offline, $0.

It also says what it did not check: it compares no master object with the stamp's, and a stamp the
job named but it could not use is named in the same line every reader prints.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess

import pytest

_FRAME_PROOF = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "frame_proof.py"

_needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                   reason="needs ffmpeg and ffprobe")


def _frame_proof():
    spec = importlib.util.spec_from_file_location("frame_proof", _FRAME_PROOF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _encode(path: pathlib.Path, video_s: float, audio_s: float) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size=64x36:rate=10:duration={video_s}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_s}",
                    "-c:v", "mpeg4", "-c:a", "aac", str(path)], check=True)


@_needs_ffmpeg
def test_the_span_is_the_video_streams_when_the_audio_runs_longer(tmp_path):
    mp4 = tmp_path / "video_short_of_master.mp4"
    _encode(mp4, 2, 3)
    assert _frame_proof()._probe_duration(str(mp4)) == pytest.approx(2.0, abs=0.05)


@_needs_ffmpeg
def test_a_container_without_a_stream_duration_falls_back_to_its_own(tmp_path):
    """Matroska records no per-stream duration, so ffprobe reports N/A for the stream."""
    mkv = tmp_path / "no_stream_duration.mkv"
    _encode(mkv, 2, 2)
    assert _frame_proof()._probe_duration(str(mkv)) == pytest.approx(2.0, abs=0.1)


def _stamped(master_ms):
    from kitesforu_qa.harness.delivered_timeline import DeliveredTimeline

    clips = [{"start_ms": 0, "modality": "scene_image", "asset_uri": "gs://b/0.png", "status": "done"}]
    stamp = {"version": 1, "master_ms": 9000, "master_generation": 7, "master_size": 10,
             "windows": [{"clip": 0, "source_clip": 0, "start_ms": 0, "end_ms": 9000,
                          "modality": "scene_image", "render_mode": None, "motion_render": None,
                          "asset_kind": None}],
             "close": {"applied": False, "fade_start_ms": None, "fade_ms": None, "reason": "t"}}
    return DeliveredTimeline.from_clips(clips, stamp=stamp, master_ms=master_ms)


def test_a_used_stamp_says_its_master_identity_was_not_checked():
    """frame_proof passes no master object, so a stamp it reads is held to the video's LENGTH only,
    and its timeline line says so (round-2 code critic #3)."""
    lines = _frame_proof()._timeline_lines(_stamped(9000))
    assert lines[0].strip().startswith("timeline: stamp (stamp; master identity unchecked"), lines
    assert len(lines) == 1, lines


def test_a_probe_of_zero_seconds_does_not_vouch_for_the_stamp():
    """``_probe_duration`` returns 0.0 when ffprobe reads nothing. That is an unknown length, so the
    stamp is not used, and the line names why."""
    lines = _frame_proof()._timeline_lines(_stamped(0.0))
    assert lines[0].strip().startswith("timeline: estimated (") and "untied" in lines[0], lines
    assert lines[1].strip().startswith("NOTE: the producer's painted timeline was not used"), lines
