"""Pin LONG-FORM CORRECTNESS Part D, Layer 2 — the OCR visual-fit check in
``harness/checks/visual.py`` (``visual.text_not_edge_cropped`` critical,
``visual.text_in_safe_area`` advisory).

Strategy (mirrors ``tests/test_check_batteries.py``'s established pattern): synthesize a real
1920x1080 H.264 mp4 with ``ffmpeg drawtext`` burning in a label at a controlled pixel position, wrap
it in a doc with ONE structural-diagram ``visual_clips`` entry spanning the whole clip, and run the
REAL check through ``run_dimension`` — no mocking of ffmpeg/pytesseract, so this exercises the actual
production code path. Skips cleanly when ffmpeg/tesseract-ocr aren't available locally.

Three positions prove the two-tier design:
  * x=-40 (drawn PARTLY off-canvas, so the VISIBLE remainder starts at x=0 — the literal
    "chopped at the frame edge" bug) -> BOTH checks fail.
  * x=50  (>3px but <8% safe margin)    -> only the ADVISORY check fails (crowding, not clipped).
  * x=900 (well inside the safe area)   -> both checks pass.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from kitesforu_qa.harness import Artifact, run_dimension

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("tesseract")),
    reason="ffmpeg + tesseract-ocr required to synthesize/OCR the fixture video",
)

_W, _H = 1920, 1080
_MARGIN_PX = int(_W * 0.08)  # 153px — matches visual.py's _TEXT_SAFE_MARGIN_FRAC at this width


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True, capture_output=True, text=True, timeout=60,
    )


def _diagram_video_with_text(path: str, *, x: int, y: int = 500, seconds: float = 3.0,
                              text: str = "EDGE LABEL") -> str:
    """A navy 1920x1080 clip with ONE burned-in label at a controlled pixel position, held for the
    WHOLE clip (so sampling the last ~500ms sees the same text regardless of exact seek timing)."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=0x0b1020:s={_W}x{_H}:d={seconds}",
        "-vf", f"drawtext=text='{text}':x={x}:y={y}:fontsize=54:fontcolor=white",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", path,
    )
    return path


def _diagram_doc(*, structural: bool = True) -> dict:
    """One beat spanning the whole 3s clip, tagged as a structural diagram (or a scene, for the
    N/A-skip case) — the shape ``visual.text_not_edge_cropped``/``text_in_safe_area`` read."""
    modality = "diagram" if structural else "scene"
    return {
        "job_id": "synthetic-safe-area",
        "status": "completed",
        "episode_profile": {"genre": "explainer"},
        "visual_clips": [
            {"beat_index": 0, "start_ms": 0, "end_ms": 3000, "modality": modality,
             "render_mode": "image", "asset_uri": "gs://x/0.png"},
        ],
    }


def _by_check(art) -> tuple:
    sr = run_dimension(art, "visual-images", genre=art.genre)
    return sr, {c["check_id"]: c for c in sr.data["checks"]}


#: What each check says when it failed ON A WORD, as opposed to any other failure.
_FAILED_ON = {"visual.text_not_edge_cropped": "touches the frame edge",
              "visual.text_in_safe_area": "safe area over"}


def _failed_on_a_word(check: dict) -> None:
    """The check read frames and failed on text. A check that RAISES is recorded as failed
    (``check raised: …``, harness/check.py), so a bare ``not passed`` went green with no frame
    read at all: in a venv without Pillow every "must fail" pin here passed (#179 round-1 claims
    SF). The evidence must name the failure the pin is about."""
    assert not check["skipped"], check["evidence"]
    assert not check["passed"], check["evidence"]
    assert _FAILED_ON[check["check_id"]] in str(check["evidence"]), check["evidence"]


def test_edge_clipped_text_fails_both_checks_and_gates_the_dimension(tmp_path):
    # x=-40 draws the label PARTLY off-canvas — the VISIBLE remainder is truncated at x=0, the
    # literal pixel-level "chopped at the frame edge" bug (not merely "close to the edge").
    video = _diagram_video_with_text(str(tmp_path / "edge.mp4"), x=-40)
    art = Artifact.from_doc(_diagram_doc(), video_path=video)
    sr, by = _by_check(art)

    crit = by["visual.text_not_edge_cropped"]
    _failed_on_a_word(crit)  # edge-clipped text must FAIL on the word

    adv = by["visual.text_in_safe_area"]
    _failed_on_a_word(adv)  # edge-clipped text is also outside the safe area

    # a CRITICAL check failing must gate the whole dimension (battery.py's _GATING contract)
    assert not sr.passed, "critical text_not_edge_cropped failure must fail the dimension gate"


def test_crowded_but_not_clipped_text_fails_only_the_advisory_check(tmp_path):
    # 3px < x=50 < 153px (8% of 1920) — inside the frame, but crowding the safe-area margin.
    video = _diagram_video_with_text(str(tmp_path / "crowded.mp4"), x=50)
    art = Artifact.from_doc(_diagram_doc(), video_path=video)
    sr, by = _by_check(art)

    crit = by["visual.text_not_edge_cropped"]
    assert not crit["skipped"], crit["evidence"]
    assert crit["passed"], f"x=50 is >3px from the edge — must NOT be flagged as clipped: {crit['evidence']}"

    adv = by["visual.text_in_safe_area"]
    _failed_on_a_word(adv)  # x=50 < the safe-area margin: flagged advisory

    # advisory (low severity) never gates the dimension on its own.
    assert sr.passed, "an advisory-only failure must not fail the gate"


def test_well_inside_text_passes_both_checks(tmp_path):
    video = _diagram_video_with_text(str(tmp_path / "safe.mp4"), x=900)
    art = Artifact.from_doc(_diagram_doc(), video_path=video)
    sr, by = _by_check(art)

    crit = by["visual.text_not_edge_cropped"]
    adv = by["visual.text_in_safe_area"]
    assert not crit["skipped"] and crit["passed"], crit["evidence"]
    assert not adv["skipped"] and adv["passed"], adv["evidence"]
    assert sr.passed


def test_no_structural_diagram_clips_skips_cleanly(tmp_path):
    # a scene-only job (no diagram/chart beats) has nothing for this gate to sample.
    video = _diagram_video_with_text(str(tmp_path / "scene.mp4"), x=900)
    art = Artifact.from_doc(_diagram_doc(structural=False), video_path=video)
    sr, by = _by_check(art)
    assert by["visual.text_not_edge_cropped"]["skipped"]
    assert by["visual.text_in_safe_area"]["skipped"]
    assert sr.passed, "an all-skip dimension must not fail the gate"


def test_no_video_skips_cleanly():
    art = Artifact.from_doc(_diagram_doc())  # no video_path at all
    sr, by = _by_check(art)
    assert by["visual.text_not_edge_cropped"]["skipped"]
    assert by["visual.text_in_safe_area"]["skipped"]
    assert sr.passed


def test_a_cut_word_mid_beat_is_caught_though_the_beat_ends_clean(tmp_path):
    """The canary's shape (job 29355571, "Air molecu"): an engine tour cuts a neighbour mid-dwell and
    ends on its widest view, so the last ~500ms is clean. The edge-cut label shows only from 1 s to 3 s
    of a 6 s beat; a centred label holds throughout. Sampled only at its tail the beat passed; sampled
    across its window it fails."""
    video = str(tmp_path / "tour.mp4")
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=0x0b1020:s={_W}x{_H}:d=6",
        "-vf", "drawtext=text='EDGE LABEL':x=-40:y=500:fontsize=54:fontcolor=white:enable='between(t,1,3)',"
               "drawtext=text='CENTRE LABEL':x=800:y=700:fontsize=54:fontcolor=white",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", video,
    )
    doc = _diagram_doc()
    doc["visual_clips"][0]["end_ms"] = 6000
    art = Artifact.from_doc(doc, video_path=video)
    sr, by = _by_check(art)
    crit = by["visual.text_not_edge_cropped"]
    _failed_on_a_word(crit)  # a word cut mid-beat must fail
    assert not sr.passed


def test_the_beat_is_sampled_across_its_window_and_at_its_tail():
    from kitesforu_qa.harness.checks.visual import _beat_sample_times

    assert _beat_sample_times(0.0, 6.0) == [0.5, 1.5, 2.5, 3.5, 5.5]   # 4 spread + the tail
    assert _beat_sample_times(10.0, 10.4) == [10.0]                      # a sliver: its start
    assert _beat_sample_times(0.0, 2.0) == [0.5, 1.5]                    # the tail is 1.5 itself
