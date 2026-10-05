"""``scripts/frame_proof.py`` measures the picture, so its span is the VIDEO stream's, not the container's.

The span ends the last estimated window, and it is the length a producer's painted timeline is
checked against (``DeliveredTimeline.from_job``). The container's duration is the longer of the two
streams: on the witness master f7df77bf it is 85.109 s against the video's 85.033 s, and a video can
end well short of its audio (the producer's ``video_short_of_master``). Real ffmpeg, offline, $0.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess

import pytest

_FRAME_PROOF = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "frame_proof.py"

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
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


def test_the_span_is_the_video_streams_when_the_audio_runs_longer(tmp_path):
    mp4 = tmp_path / "video_short_of_master.mp4"
    _encode(mp4, 2, 3)
    assert _frame_proof()._probe_duration(str(mp4)) == pytest.approx(2.0, abs=0.05)


def test_a_container_without_a_stream_duration_falls_back_to_its_own(tmp_path):
    """Matroska records no per-stream duration, so ffprobe reports N/A for the stream."""
    mkv = tmp_path / "no_stream_duration.mkv"
    _encode(mkv, 2, 2)
    assert _frame_proof()._probe_duration(str(mkv)) == pytest.approx(2.0, abs=0.1)
