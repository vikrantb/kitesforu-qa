"""The acceptance gate grades the captioned MP4 a creator posts, not only the video the player streams.

The gate fetched ``visual.video_url or visual.video_burned_url``: the uncaptioned render the Studio player plays.
Creators download and post the captioned MP4 (the frontend's export reads ``visual.video_burned_url``; a born-short's
shareable clip is ``outputs.clips[0].clip_url``), so a defect that lives only in the captioned file (an uncaptioned
final line, a caption over a card's label, two caption layers) passed the gate on every job it was found on.

These pins run the real ``run_gate`` on synthetic docs and synthetic videos (ffmpeg ``lavfi``). This repository is
public, so no job, user or content here is real. The defective video holds a caption bar on the bottom edge for its
whole length, which the gate's own edge probe flags; the clean one keeps everything inside the margins.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_QA = Path(__file__).resolve().parents[1]
_GATE = _QA / "scripts" / "acceptance_gate.py"

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe required to synthesize the videos",
)

_WATCH = "https://synthetic.invalid/visuals/synthetic/episode_video.mp4"
_BURNED = "https://synthetic.invalid/visuals/synthetic/episode_video_captioned.mp4"
_CLIP = "https://synthetic.invalid/visuals/synthetic/episode_video_captioned_clip.mp4"
_VTT = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nA synthetic caption.\n"
_SECONDS = 12  # fps=1/3 extraction: four frames, enough for the gate's run rule

#: A dark canvas with one card in the middle and, for the captioned variants, a caption bar. Gray rather than
#: white keeps the clean frames clear of the gate's letterbox rule (middle vs top/bottom band brightness).
_CANVAS = "color=c=0x303030:s=540x960:d={d}"
_CARD = "drawbox=x=190:y=420:w=160:h=120:color=0xb0b0b0:t=fill"
_CAPTION_INSET = "drawbox=x=120:y=800:w=300:h=24:color=0xb0b0b0:t=fill"
_CAPTION_ON_EDGE = "drawbox=x=60:y=940:w=420:h=20:color=0xb0b0b0:t=fill"


def _video(path: Path, *filters: str) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", _CANVAS.format(d=_SECONDS), "-vf", ",".join(filters),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    return path


@pytest.fixture(scope="module")
def videos(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("videos")
    return {
        "clean": _video(d / "clean.mp4", _CARD),
        "captioned_clean": _video(d / "captioned_clean.mp4", _CARD, _CAPTION_INSET),
        "captioned_on_edge": _video(d / "captioned_on_edge.mp4", _CARD, _CAPTION_ON_EDGE),
    }


@pytest.fixture()
def gate(monkeypatch, tmp_path):
    """A fresh gate whose fetched masters land in this test's own directory."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    spec = importlib.util.spec_from_file_location("acceptance_gate_captioned", _GATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _doc(*, outputs: dict | None = None, **visual) -> dict:
    vis = {"status": "done", "video_status": "ready",
           "clips": [{"start_ms": 0, "duration_ms": _SECONDS * 1000, "modality": "diagram", "aspect_ratio": "9:16"}],
           **visual}
    return {"topic": "a synthetic topic", "visual": vis, "outputs": outputs or {}}


def _run(gate, monkeypatch, tmp_path, doc: dict, served: dict[str, Path]):
    """``run_gate`` on ``doc``, the fetch serving ``served`` (url -> local file). Returns (out, fetched urls)."""
    fetched: list[str] = []

    def fetch(url: str, path: str) -> bool:
        fetched.append(url)
        if url not in served:
            return False
        shutil.copyfile(served[url], path)
        return True

    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: dict(doc))
    monkeypatch.setattr(gate, "_fetch_master", fetch)
    out = gate.run_gate(f"synthetic-{uuid.uuid4().hex}", frames_dir=str(tmp_path / "frames"))
    return out, fetched


def _pngs(d: Path) -> list[str]:
    return sorted(p for p in os.listdir(d) if p.endswith(".png")) if d.is_dir() else []


# ---- the defect: the captioned file was never fetched ---------------------------------------------------------

def test_the_captioned_video_is_graded_beside_the_watch_video(gate, monkeypatch, tmp_path, videos):
    """The witness's shape: a clean watch video and a captioned twin whose caption sits on the frame edge."""
    out, fetched = _run(gate, monkeypatch, tmp_path, _doc(video_url=_WATCH, video_burned_url=_BURNED),
                        {_WATCH: videos["clean"], _BURNED: videos["captioned_on_edge"]})
    assert fetched == [_WATCH, _BURNED], f"the gate did not fetch both surfaces: {fetched}"
    watch, captioned = out["surfaces"]["watch"], out["surfaces"]["captioned"]
    assert watch["verdict"] == "PASS_DETERMINISTIC" and watch["issues"] == [], watch
    assert captioned["verdict"] == "REVIEW", captioned
    assert captioned["source"] == "visual.video_burned_url" and captioned["url"] == _BURNED
    assert out["verdict"] == "REVIEW", "the overall verdict is not the worse of the two surfaces"
    edge = [i for i in out["issues"] if "EDGE-CLIP" in i["msg"]]
    assert edge and all(i["surface"] == "captioned" for i in out["issues"]), out["issues"]
    assert any("(bottom)" in i["msg"] for i in edge), edge
    # Each surface's frames in its own directory, and none loose beside them.
    frames = tmp_path / "frames"
    assert _pngs(frames / "watch") and _pngs(frames / "captioned") and not _pngs(frames), os.listdir(frames)
    assert watch["frames_dir"] == str(frames / "watch") and captioned["frames_dir"] == str(frames / "captioned")
    assert out["num_frames"] == watch["num_frames"] + captioned["num_frames"] == 2 * len(_pngs(frames / "watch"))
    assert out["dims"] == [540, 960] == watch["dims"] == captioned["dims"]
    assert "captioned/" in out["next"], "the adversary is not told where the captioned frames are"


def test_the_shareable_clip_is_the_captioned_video_when_no_burned_url_is_set(gate, monkeypatch, tmp_path, videos):
    clip = {"clip_url": _CLIP, "source": "episode_video_short"}
    out, fetched = _run(gate, monkeypatch, tmp_path, _doc(video_url=_WATCH, outputs={"clips": [clip]}),
                        {_WATCH: videos["clean"], _CLIP: videos["captioned_on_edge"]})
    assert fetched == [_WATCH, _CLIP], fetched
    captioned = out["surfaces"]["captioned"]
    assert captioned["source"] == "outputs.clips[0].clip_url" and captioned["verdict"] == "REVIEW", captioned
    assert out["verdict"] == "REVIEW"


@pytest.mark.parametrize("watch_file,captioned_file,expect_watch,expect_captioned,expect_overall", [
    ("clean", "captioned_clean", "PASS_DETERMINISTIC", "PASS_DETERMINISTIC", "PASS_DETERMINISTIC"),  # control
    ("captioned_on_edge", "captioned_clean", "REVIEW", "PASS_DETERMINISTIC", "REVIEW"),
    (None, "captioned_clean", "FAIL", "PASS_DETERMINISTIC", "FAIL"),  # the watch video is not fetchable
    ("clean", None, "PASS_DETERMINISTIC", "FAIL", "FAIL"),  # the captioned video is not fetchable
])
def test_the_verdict_is_the_worse_of_the_two_surfaces(gate, monkeypatch, tmp_path, videos, watch_file,
                                                      captioned_file, expect_watch, expect_captioned, expect_overall):
    served = {url: videos[name] for url, name in ((_WATCH, watch_file), (_BURNED, captioned_file)) if name}
    out, fetched = _run(gate, monkeypatch, tmp_path, _doc(video_url=_WATCH, video_burned_url=_BURNED), served)
    assert fetched == [_WATCH, _BURNED], fetched
    surfaces = out["surfaces"]
    assert (surfaces["watch"]["verdict"], surfaces["captioned"]["verdict"], out["verdict"]) == (
        expect_watch, expect_captioned, expect_overall), out["issues"]
    for name in ("watch", "captioned"):
        assert all(i["surface"] == name for i in surfaces[name]["issues"]), surfaces[name]
    assert {i["surface"] for i in out["issues"]} <= {"watch", "captioned"}
    assert len(out["issues"]) == len(surfaces["watch"]["issues"]) + len(surfaces["captioned"]["issues"])


# ---- the captioned file that should exist and does not --------------------------------------------------------

@pytest.mark.parametrize("burn,clip_url", [
    ({"video_caption_burn": "karaoke"}, None),
    ({"captions_vtt": _VTT}, None),  # a burn that failed every arm stamps nothing; the VTT says one ran
    # A born-short's clip promotion falls back to the watch render when no burn landed: uncaptioned.
    ({"captions_vtt": _VTT}, _WATCH),
])
def test_a_missing_captioned_video_is_major_when_the_caption_burn_was_on(gate, monkeypatch, tmp_path, videos,
                                                                       burn, clip_url):
    outputs = {"clips": [{"clip_url": clip_url}]} if clip_url else {}
    out, fetched = _run(gate, monkeypatch, tmp_path, _doc(video_url=_WATCH, outputs=outputs, **burn),
                        {_WATCH: videos["clean"]})
    assert fetched == [_WATCH], fetched
    captioned = out["surfaces"]["captioned"]
    assert captioned["verdict"] == "REVIEW" and captioned["url"] == "", captioned
    assert [i["sev"] for i in out["issues"]] == ["MAJOR"], out["issues"]
    issue = out["issues"][0]
    assert issue["surface"] == "captioned" and issue["msg"].startswith("CAPTIONED VIDEO MISSING"), issue
    assert out["verdict"] == "REVIEW" and out["surfaces"]["watch"]["verdict"] == "PASS_DETERMINISTIC"


def test_no_captioned_video_and_no_burn_is_absent_not_a_defect(gate, monkeypatch, tmp_path, videos):
    """CONTROL for the MAJOR above: nothing on the doc says a burn ran, so there is nothing missing to report."""
    out, fetched = _run(gate, monkeypatch, tmp_path, _doc(video_url=_WATCH), {_WATCH: videos["clean"]})
    assert fetched == [_WATCH]
    assert out["surfaces"]["captioned"]["verdict"] == "ABSENT" and out["issues"] == [], out
    assert out["verdict"] == "PASS_DETERMINISTIC"


# ---- one file, and re-runs ------------------------------------------------------------------------------------

def test_one_file_serving_both_surfaces_is_fetched_once(gate, monkeypatch, tmp_path, videos):
    """No ``video_url``: the player streams the captioned file, so both surfaces are that one file."""
    out, fetched = _run(gate, monkeypatch, tmp_path, _doc(video_burned_url=_BURNED),
                        {_BURNED: videos["captioned_on_edge"]})
    assert fetched == [_BURNED], fetched
    watch, captioned = out["surfaces"]["watch"], out["surfaces"]["captioned"]
    assert watch["source"] == "visual.video_burned_url" and watch["verdict"] == "REVIEW", watch
    assert captioned["same_file_as"] == "watch" and captioned["verdict"] == "REVIEW" and captioned["issues"] == []
    assert out["verdict"] == "REVIEW" and {i["surface"] for i in out["issues"]} == {"watch"}
    assert not (tmp_path / "frames" / "captioned").exists()


def test_a_rerun_never_grades_the_last_runs_frames(gate, monkeypatch, tmp_path, videos):
    """The default frames directory is fixed per job and surface. A longer master's tail frames from an earlier run
    stayed beside a shorter re-mux's and were graded as this run's."""
    from PIL import Image

    stale = tmp_path / "frames" / "captioned"
    stale.mkdir(parents=True)
    Image.new("L", (540, 960), 48).save(stale / "f_00099.png")
    out, _ = _run(gate, monkeypatch, tmp_path, _doc(video_url=_WATCH, video_burned_url=_BURNED),
                  {_WATCH: videos["clean"], _BURNED: videos["captioned_clean"]})
    assert "f_00099.png" not in _pngs(stale), _pngs(stale)
    assert out["surfaces"]["captioned"]["num_frames"] == len(_pngs(stale)) == out["surfaces"]["watch"]["num_frames"]
    assert out["verdict"] == "PASS_DETERMINISTIC", out["issues"]
