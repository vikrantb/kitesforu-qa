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
from pathlib import Path

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
    """A real-render-quality test video: 1920×1080 H.264/yuv420p @30fps + an AAC audio stream, muxed
    with +faststart (moov before mdat). The long-tail video_sync battery probes resolution, fps,
    pix_fmt, codec, stream count, faststart, and the >10KB byte floor — so the GOOD fixture must
    model an actual hero render, not a 320×180 stub. Duration is the only var across calls."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=navy:s=1920x1080:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", path,
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


def _diagram_card_image(path: str, *, w: int = 1920, h: int = 1080) -> str:
    """A diagram/card render: a navy (11,16,32) background that FILLS the frame with a large bright
    painted region (>20% non-background) -> diagram_fills_frame + diagram_not_blank PASS. Named only
    by the caller (content-hash basename in the F3 test) so it carries no filename token."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (11, 16, 32))  # navy diagram background
    draw = ImageDraw.Draw(img)
    # paint a centered light panel covering ~36% of the frame (well over the 20% fill floor)
    draw.rectangle([w * 0.2, h * 0.2, w * 0.8, h * 0.8], fill=(230, 235, 245))
    img.save(path)
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


def _visual_clips_varied() -> list[dict]:
    """SIX evenly-spread assets across the same 20 s — an artifact that is GOOD by the
    CURRENT monotony rule, not by the one in force when these tests were written.

    ``_visual_clips()`` gives beat 0 a single scene image held for 10 of 20 s = 50% of
    runtime. ``_MONOTONY_TOP1_MAX`` is 0.25, so that fixture asserts a GOOD artifact
    contains exactly the "same image dancing" defect the anti-dullness work exists to
    catch. The gate is right; the fixture is stale.

    This is ADDITIVE on purpose. ``_visual_clips()`` is shared by seven tests, and
    ``test_visual_count_reasonable_uses_nested_clips`` asserts literally "4 expected" —
    editing the shared fixture to fix the monotony tests breaks that one, which is
    currently green. (Tried it; broke it; reverted.) So the tests that need a
    current-standards GOOD artifact get their own fixture and the shared one is untouched.

    Arithmetic, against the real thresholds in harness/checks/visual.py:
        6 assets x ~3.33 s over 20 s
        top-1  16.7%  (max 25%, strict >)      top-3  50%  (max 60%)
        18.0 distinct/min — per_min has a MINIMUM only, no upper cap
    """
    return [
        # beat 0 — the pictorial half, THREE shots instead of one 10 s hold. Beats stay
        # {0, 1} to match _base_doc()'s segment_beat_map {"0":0,"1":0,"2":1,"3":1}; a beat
        # index outside that map is an ORPHAN clip and trips visual.no_orphan_clip.
        {"beat_index": 0, "start_ms": 0, "duration_ms": 3333, "modality": "scene",
         "render_mode": "image", "aspect_ratio": "16:9", "motion_preset": "push_in",
         "asset_uri": "gs://x/v0.png"},
        {"beat_index": 0, "start_ms": 3333, "duration_ms": 3333, "modality": "scene",
         "render_mode": "image", "aspect_ratio": "16:9", "motion_preset": "pan_left",
         "asset_uri": "gs://x/v1.png"},
        {"beat_index": 0, "start_ms": 6666, "duration_ms": 3334, "modality": "scene",
         "render_mode": "image", "aspect_ratio": "16:9", "motion_preset": "push_out",
         "asset_uri": "gs://x/v2.png"},
        # beat 1 — a diagram that BUILDS over 3 reveal frames (node-by-node)
        {"beat_index": 1, "start_ms": 10000, "duration_ms": 3333, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 0, "_reveal_total": 3, "asset_uri": "gs://x/v3a.png"},
        {"beat_index": 1, "start_ms": 13333, "duration_ms": 3333, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 1, "_reveal_total": 3, "asset_uri": "gs://x/v3b.png"},
        {"beat_index": 1, "start_ms": 16666, "duration_ms": 3334, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 2, "_reveal_total": 3, "asset_uri": "gs://x/v3c.png"},
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


def test_audio_master_padded_passes(tmp_path):
    """F1 fidelity: a HEALTHY master is legitimately LONGER than Σ(segment duration_ms) because the
    assembler inserts real inter-segment pause padding (the breaths). The one-sided floor must PASS
    this — a symmetric ~5% tolerance falsely failed every healthy episode (btree 160 vs 145, etc.).

    Σsegments = 4x5s = 20s; render a 24s master (+20% padding) -> master/Σ=1.20 must pass."""
    doc = _base_doc()
    art = Artifact.from_doc(doc, audio_path=_good_audio(str(tmp_path / "padded.wav"), seconds=24.0))
    _sr, by = _gating_results(art, "audio-mix")
    c = by["audio.master_matches_segment_sum"]
    assert c["passed"], f"padded master (+20%) must pass the one-sided floor: {c['evidence']}"
    assert not c["skipped"], c["evidence"]


def test_audio_master_collapsed_fails(tmp_path):
    """F1: the real bug this guards — a WAV mis-decoded as MP3 collapses the master far BELOW
    Σsegments (the 2026-06-19 incident: ~10s master vs ~290s of segments). master/Σ << floor -> fail.

    Σsegments = 4x5s = 20s; render a 2s master -> master/Σ=0.10, well under the 0.85 floor."""
    doc = _base_doc()
    art = Artifact.from_doc(doc, audio_path=_truncated_audio(str(tmp_path / "collapse.wav"), seconds=2.0))
    _sr, by = _gating_results(art, "audio-mix")
    c = by["audio.master_matches_segment_sum"]
    assert not c["passed"], f"collapsed master (master/Σ≈0.10) must fail the floor: {c['evidence']}"


def test_audio_na_when_no_audio():
    """No audio file -> every audio-mix check skips (a podcast doc with no downloaded master)."""
    _assert_all_skipped(Artifact.from_doc(_base_doc()), "audio-mix")


# ════════════════════════════════ VISUAL-IMAGES ══════════════════════════════


def test_visual_good_passes(tmp_path):
    doc = _base_doc()
    # the CURRENT-standards fixture: _visual_clips() holds one image for 50% of runtime,
    # which the monotony rule (top-1 max 25%) correctly rejects. See _visual_clips_varied.
    doc["visual_clips"] = _visual_clips_varied()
    imgs = [
        _good_image(str(tmp_path / "v0.png")),
        _good_image(str(tmp_path / "v1.png")),
        _good_image(str(tmp_path / "v2.png")),
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


def test_visual_missing_images_skips_offline(tmp_path):
    """FP-FIX (harness_asset_artifact): visual_clips declare assets but NONE were downloaded — the
    offline scorecard NEVER fills image_paths from asset_uri, so this is NOT a silent drop. The check
    must SKIP cleanly (doc-side coverage asserted, real drop owned by visual.clip_per_beat), not
    false-fail. Was the 4d1700ee false positive: "0 image files (visual_clips=0)"."""
    doc = _base_doc()
    doc["visual_clips"] = _visual_clips()
    art = Artifact.from_doc(doc, image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.images_present"]
    assert c["skipped"], f"offline (no images downloaded) must SKIP, not fail: {c['evidence']}"
    assert "doc-side coverage" in c["evidence"], c["evidence"]


def test_visual_count_reasonable_uses_nested_clips(tmp_path):
    """F2 fidelity: real jobs nest clips at doc['visual']['clips'] (top-level visual_clips is None),
    so _expected_image_count must read through _clips() (nested fallback) — not fall back to
    len(beat_map). Here beat_map has only 2 beats but there are 4 asset-carrying clips; 4 images
    must count as a MATCH (got==expected==4), which fails iff the old top-level-only read regressed."""
    doc = _base_doc()
    doc["visual"] = {"clips": _visual_clips()}  # nested (real) shape; no top-level visual_clips
    imgs = [_good_image(str(tmp_path / f"n{i}.png")) for i in range(4)]
    art = Artifact.from_doc(doc, image_paths=imgs)
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.count_reasonable"]
    assert not c["skipped"], c["evidence"]
    assert c["passed"], (
        f"4 images vs 4 nested asset-clips must match; a beat_map fallback (2) would mis-expect: "
        f"{c['evidence']}"
    )
    assert "4 expected" in c["evidence"], c["evidence"]


def test_visual_diagram_checks_run_on_content_hash_names(tmp_path):
    """F3 fidelity: real diagram renders are content-hash-named (d97b2026.png), so the old
    filename-token heuristic ('diagram'/'card'/...) never matched them and the 4 pixel checks
    (incl. critical fill/blank) silently skipped on every real job. The modality mapping must
    correlate a structural clip's asset_uri basename to the downloaded file so the checks RUN."""
    doc = _base_doc()
    # one scene clip + one diagram clip, both asset_uri'd with content-hash basenames (no tokens)
    doc["visual"] = {"clips": [
        {"beat_index": 0, "modality": "scene", "render_mode": "image",
         "aspect_ratio": "16:9", "asset_uri": "gs://x/a1b2c3d4e5f6a7b8.png"},
        {"beat_index": 1, "modality": "diagram", "render_mode": "image",
         "aspect_ratio": "16:9", "asset_uri": "gs://x/d97b2026deadbeef.png"},
    ]}
    # the diagram file is a real navy-bg card that FILLS the frame; named by content hash only.
    diagram = _diagram_card_image(str(tmp_path / "d97b2026deadbeef.png"))
    scene = _good_image(str(tmp_path / "a1b2c3d4e5f6a7b8.png"))
    art = Artifact.from_doc(doc, image_paths=[scene, diagram])
    _sr, by = _gating_results(art, "visual-images")
    fills = by["visual.diagram_fills_frame"]
    blank = by["visual.diagram_not_blank"]
    assert not fills["skipped"], f"diagram_fills_frame must RUN via modality mapping: {fills['evidence']}"
    assert not blank["skipped"], f"diagram_not_blank must RUN via modality mapping: {blank['evidence']}"
    assert fills["passed"], fills["evidence"]
    assert blank["passed"], blank["evidence"]


def test_visual_diagram_checks_skip_clearly_when_uncorrelatable(tmp_path):
    """F3: when a diagram clip's asset can't be correlated to any downloaded file and no filename
    token matches, the pixel checks must skip with the CLEAR note (not the old N/A-looking wording)."""
    doc = _base_doc()
    doc["visual"] = {"clips": [
        {"beat_index": 1, "modality": "diagram", "render_mode": "image",
         "aspect_ratio": "16:9", "asset_uri": "gs://x/uncorrelated_hash.png"},
    ]}
    # a downloaded scene image whose basename matches NO clip and carries no diagram token
    art = Artifact.from_doc(doc, image_paths=[_good_image(str(tmp_path / "scene_only.png"))])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.diagram_fills_frame"]
    assert c["skipped"], c["evidence"]
    assert "no modality-tagged diagram image available" in c["evidence"], c["evidence"]


def test_visual_na_when_no_visuals():
    """A doc with no visual_clips and no images -> the whole visual battery skips."""
    _assert_all_skipped(Artifact.from_doc(_base_doc()), "visual-images")


# ── REGRESSION PINS: the 4d1700ee false-positive fires (FIDELITY-AUDIT 2026-06-22) ───────────────
# Job 4d1700ee (educational, created 2026-06-22) ran the offline scorecard with NO downloaded images
# (image_paths empty) over a HEALTHY doc: 18 nested clips at doc['visual']['clips'], every clip a real
# gs:// asset_uri + status=done. Three visual checks contradicted each other on these same 18 clips:
#   images_present  -> FALSE-FAILED "0 image files (visual_clips=0)"   (wrong field path + offline)
#   count_reasonable-> FALSE-FAILED "0 images vs 18 expected"          (mixed local-file vs doc plane)
#   no_adjacent_diagrams -> FALSE-FAILED on beats 4-5 (a 2-long run that is ON-SPEC for educational)
# The pins below prove all three now AGREE (the 18 clips exist) and stop false-firing, while a
# genuinely-bad doc still FAILS so the fixes didn't gut the checks.


def _job_4d1700ee_clips() -> list[dict]:
    """Exactly 18 nested clips like the real 4d1700ee: 8 distinct beats (0-7); structural diagram
    beats at 4 and 5 (adjacent run of 2 — the on-spec educational weave the old check false-failed)
    plus an isolated diagram at 7. Scene beats are 1 clip; the 3 diagram beats build over reveal
    sub-clips. 5 scene beats (1 each) + diagram beats of 6+4+3 reveals = 5 + 13 = 18 clips total."""
    diagram_reveals = {4: 6, 5: 4, 7: 3}  # beats 4,5 adjacent + isolated 7; 6+4+3 = 13 sub-clips
    clips: list[dict] = []
    for beat in range(8):
        is_diag = beat in diagram_reveals
        modality = "diagram" if is_diag else "scene"
        reveals = diagram_reveals.get(beat, 1)  # scene beats = a single clip
        for r in range(reveals):
            clips.append({
                "beat_index": beat, "modality": modality, "render_mode": "image",
                "aspect_ratio": "16:9", "status": "done",
                "asset_uri": f"gs://kfu/4d1700ee/{beat}_{r}.png",
                # diagrams are authored (no model_id); scenes are AI (record the model)
                **({} if is_diag else {"model_id": "gemini-2.5-flash-image"}),
                "content_hash": f"{beat:02x}{r:02x}deadbeefcafe00",
            })
    return clips  # 5 scene + 13 diagram-reveal = 18 clips


def _job_4d1700ee_doc() -> dict:
    """The 4d1700ee educational doc shape: nested clips, segment_beat_map planning 13 beats (0-12)
    but clips only covering beats 0-7 (the REAL tail-drop visual.clip_per_beat must still catch)."""
    doc = _base_doc()
    doc["job_id"] = "4d1700ee-00f8-467b-ba46-413550b9c743"
    doc["episode_profile"] = {"genre": "educational", "topic": "How a B-tree database index works"}
    doc["inputs"] = {"duration_min": 3, "genre": "educational"}
    doc["visual"] = {"clips": _job_4d1700ee_clips(), "status": "done"}
    # 21 segments -> 13 distinct planned beats (0-12). Clips cover only 0-7 (beats 8-12 dropped).
    doc["segment_beat_map"] = {str(i): min(i * 13 // 21, 12) for i in range(21)}
    return doc


def test_pin_4d1700ee_images_present_skips_offline():
    """PIN (fix 1): images_present must SKIP on the 4d1700ee offline run (18 doc clips with assets,
    no downloaded files) — not false-fail "0 image files (visual_clips=0)"."""
    art = Artifact.from_doc(_job_4d1700ee_doc(), image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.images_present"]
    assert c["skipped"], f"4d1700ee images_present must skip offline, not fail: {c['evidence']}"
    # evidence must report the REAL nested clip count, not the old top-level visual_clips=0
    assert "18 clips" in c["evidence"], f"evidence must report 18 nested clips: {c['evidence']}"


def test_pin_4d1700ee_count_reasonable_skips_offline():
    """PIN (fix 2): count_reasonable must SKIP on the 4d1700ee offline run — not false-fail
    "0 images vs 18 expected" (mixing local-file plane vs doc-clip plane)."""
    art = Artifact.from_doc(_job_4d1700ee_doc(), image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.count_reasonable"]
    assert c["skipped"], f"4d1700ee count_reasonable must skip offline, not fail: {c['evidence']}"
    assert "18 clips carry assets" in c["evidence"], c["evidence"]


def test_pin_4d1700ee_no_adjacent_diagrams_passes_educational():
    """PIN (fix 3): the educational run-of-2 diagram beats (4,5) is ON-SPEC (workers allow ≤2 for
    non-fiction). no_adjacent_diagrams must PASS, not false-fail."""
    art = Artifact.from_doc(_job_4d1700ee_doc(), image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.no_adjacent_diagrams"]
    assert not c["skipped"], c["evidence"]
    assert c["passed"], f"educational run-of-2 diagrams is on-spec (cap 2); must pass: {c['evidence']}"
    assert "longest structural-diagram run 2 beats" in c["evidence"], c["evidence"]


def test_pin_4d1700ee_three_visual_checks_agree():
    """PIN (the 4d1700ee contradiction smoking-gun): images_present + count_reasonable must no longer
    CONTRADICT clip_per_beat. The two asset-plane checks skip; clip_per_beat is the ONLY one that
    fires — and it fires for the REAL reason (beats 8-12 uncovered tail-drop). All three now agree the
    18 clips exist."""
    art = Artifact.from_doc(_job_4d1700ee_doc(), image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    assert by["visual.images_present"]["skipped"], by["visual.images_present"]["evidence"]
    assert by["visual.count_reasonable"]["skipped"], by["visual.count_reasonable"]["evidence"]
    # clip_per_beat is the KEEP check — it must STILL FAIL on the genuine tail-drop (beats 8-12).
    cpb = by["visual.clip_per_beat"]
    assert not cpb["skipped"], cpb["evidence"]
    assert not cpb["passed"], f"clip_per_beat must still catch the real beats 8-12 drop: {cpb['evidence']}"
    assert "missing beats" in cpb["evidence"], cpb["evidence"]


def test_pin_no_adjacent_diagrams_still_fails_wall_of_charts(tmp_path):
    """GENUINELY-BAD pin (fix 3 didn't gut the check): 3 consecutive diagram beats (a real wall of
    charts) must STILL FAIL even for non-fiction (run 3 > cap 2)."""
    doc = _base_doc()
    doc["episode_profile"] = {"genre": "educational", "topic": "x"}
    doc["visual"] = {"clips": [
        {"beat_index": 0, "modality": "diagram", "render_mode": "image", "asset_uri": "gs://x/0.png"},
        {"beat_index": 1, "modality": "diagram", "render_mode": "image", "asset_uri": "gs://x/1.png"},
        {"beat_index": 2, "modality": "diagram", "render_mode": "image", "asset_uri": "gs://x/2.png"},
        {"beat_index": 3, "modality": "scene", "render_mode": "image", "asset_uri": "gs://x/3.png"},
    ]}
    art = Artifact.from_doc(doc, image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.no_adjacent_diagrams"]
    assert not c["passed"], f"3-long diagram run must fail even non-fiction (cap 2): {c['evidence']}"


def test_pin_no_adjacent_diagrams_fiction_fails_run_of_2(tmp_path):
    """GENUINELY-BAD pin (fix 3, fiction side): for fiction the cap is 1, so even a run of 2 diagram
    beats must FAIL (narrative scenes carry the story)."""
    doc = _base_doc()
    doc["episode_profile"] = {"genre": "thriller", "topic": "x"}
    doc["visual"] = {"clips": [
        {"beat_index": 0, "modality": "diagram", "render_mode": "image", "asset_uri": "gs://x/0.png"},
        {"beat_index": 1, "modality": "diagram", "render_mode": "image", "asset_uri": "gs://x/1.png"},
        {"beat_index": 2, "modality": "scene", "render_mode": "image", "asset_uri": "gs://x/2.png"},
    ]}
    art = Artifact.from_doc(doc, image_paths=[])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.no_adjacent_diagrams"]
    assert not c["passed"], f"fiction run-of-2 diagrams must fail (cap 1): {c['evidence']}"


def test_pin_images_present_still_fails_done_clip_no_asset(tmp_path):
    """GENUINELY-BAD pin (fix 1 didn't gut the check): when images ARE downloaded but NOT ONE clip
    carries an asset_uri (a real all-empty render), images_present must still FAIL."""
    doc = _base_doc()
    doc["visual"] = {"clips": [
        {"beat_index": 0, "modality": "scene", "render_mode": "image", "status": "done", "asset_uri": ""},
        {"beat_index": 1, "modality": "scene", "render_mode": "image", "status": "done", "asset_uri": ""},
    ]}
    # an image WAS downloaded (so we don't take the offline skip) but no clip declares an asset
    art = Artifact.from_doc(doc, image_paths=[_good_image(str(tmp_path / "orphan.png"))])
    _sr, by = _gating_results(art, "visual-images")
    c = by["visual.images_present"]
    assert not c["skipped"], c["evidence"]
    assert not c["passed"], f"no clip carries an asset_uri -> must fail: {c['evidence']}"


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


# ── intro-offset (the +12s false-positive) ────────────────────────────────────
# The delivered master prepends ~12s of intro music, so the REAL spoken offsets (and the visual
# clips stamped against them) sit +12s into the master while segments_ready clocks 0-based. The
# checks must compare clips to the SAME post-intro axis (master_segment_timeline) — a healthy
# intro-offset job must PASS, a genuinely mis-beated clip must still FAIL.

_INTRO_MS = 12000  # intro-music prepend (matches the ec1620a1 +12s class)


def _intro_offset_clips() -> list[dict]:
    """The _visual_clips beats SHIFTED onto the delivered (post-intro) axis: beat 0 starts at the
    intro offset, beat 1's 3 reveal sub-clips clock across its 10s span (seg2+seg3) right after."""
    b0 = _INTRO_MS               # 12000 — beat 0 (segs 0,1 → 0..10s of speech) starts here
    b1 = _INTRO_MS + 10000       # 22000 — beat 1 (segs 2,3 → 10..20s of speech) starts here
    return [
        {"beat_index": 0, "start_ms": b0, "duration_ms": 10000, "modality": "scene",
         "render_mode": "image", "aspect_ratio": "16:9", "asset_uri": "gs://x/0.png"},
        {"beat_index": 1, "start_ms": b1, "duration_ms": 3333, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 0, "_reveal_total": 3, "asset_uri": "gs://x/1a.png"},
        {"beat_index": 1, "start_ms": b1 + 3333, "duration_ms": 3333, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 1, "_reveal_total": 3, "asset_uri": "gs://x/1b.png"},
        {"beat_index": 1, "start_ms": b1 + 6666, "duration_ms": 3334, "modality": "diagram",
         "render_mode": "image", "_reveal_index": 2, "_reveal_total": 3, "asset_uri": "gs://x/1c.png"},
    ]


def _master_segment_timeline(intro_ms: int = _INTRO_MS) -> list[dict]:
    """The audio stage's canonical delivered-master timeline: each 5s segment shifted +intro_ms
    ({index, start_ms, end_ms}), so the reference window lands on the SAME axis as the clips."""
    return [
        {"index": i, "start_ms": intro_ms + i * 5000, "end_ms": intro_ms + (i + 1) * 5000}
        for i in range(4)
    ]


def _intro_offset_doc() -> dict:
    """A healthy intro-offset doc: segments_ready 0-based (as always), clips on the +12s delivered
    axis, and the master_segment_timeline that anchors them there."""
    doc = _base_doc()
    doc["visual_clips"] = _intro_offset_clips()
    doc["master_segment_timeline"] = _master_segment_timeline()
    return doc


def test_video_intro_offset_passes(tmp_path):
    """A healthy job whose visuals sit +12s in (intro-music prepend) must PASS — clips compared on
    the SAME post-intro axis (master_segment_timeline), no false +12s lag / leading gap."""
    audio_s = (_INTRO_MS + 20000) / 1000.0  # 12s intro + 20s speech = 32s delivered master
    doc = _intro_offset_doc()
    doc["captions"] = [
        {"start_ms": _INTRO_MS + i * 4000, "end_ms": _INTRO_MS + (i + 1) * 4000, "text": f"cue {i}"}
        for i in range(5)
    ]
    art = Artifact.from_doc(
        doc,
        audio_path=_good_audio(str(tmp_path / "intro.wav"), seconds=audio_s),
        video_path=_video(str(tmp_path / "intro.mp4"), seconds=audio_s),
    )
    by = _assert_gating_pass(art, "video-sync")
    for cid in (
        "video_sync.clips_beat_aligned",
        "video_sync.no_gap_or_overrun",
        "video_sync.beat_map_covers_all_segments",
    ):
        assert by[cid]["passed"], (cid, by[cid]["evidence"])
        assert not by[cid]["skipped"], (cid, by[cid]["evidence"])


def test_video_intro_offset_passes_legacy_no_timeline(tmp_path):
    """Same +12s job but a LEGACY doc with NO master_segment_timeline: the intro offset is DETECTED
    from the clips' shared front offset and the 0-based segment walk is shifted by it — still PASS."""
    audio_s = (_INTRO_MS + 20000) / 1000.0
    doc = _intro_offset_doc()
    doc.pop("master_segment_timeline", None)  # legacy: no canonical timeline persisted
    art = Artifact.from_doc(
        doc,
        audio_path=_good_audio(str(tmp_path / "legacy.wav"), seconds=audio_s),
        video_path=_video(str(tmp_path / "legacy.mp4"), seconds=audio_s),
    )
    _sr, by = _gating_results(art, "video-sync")
    for cid in ("video_sync.clips_beat_aligned", "video_sync.no_gap_or_overrun"):
        assert by[cid]["passed"], (cid, by[cid]["evidence"])
        assert not by[cid]["skipped"], (cid, by[cid]["evidence"])


def test_video_intro_offset_misaligned_clip_still_fails(tmp_path):
    """The fix must NOT blind the check: a clip claiming beat_index 1 but stamped deep in beat 0's
    window (a genuine off-beat stamp, NOT the intro offset) must still FAIL clips_beat_aligned."""
    audio_s = (_INTRO_MS + 20000) / 1000.0
    doc = _intro_offset_doc()
    clips = _intro_offset_clips()
    # Mis-stamp ALL of beat 1's clips back into beat 0's narration window (12-22s) while they still
    # CLAIM beat_index 1 (whose real window is 22-32s) — a real talks-one/shows-other desync.
    for c in clips:
        if c["beat_index"] == 1:
            c["start_ms"] = _INTRO_MS  # 12000: inside beat 0, far outside beat 1 ±slack
    doc["visual_clips"] = clips
    art = Artifact.from_doc(
        doc,
        audio_path=_good_audio(str(tmp_path / "bad.wav"), seconds=audio_s),
        video_path=_video(str(tmp_path / "bad.mp4"), seconds=audio_s),
    )
    _sr, by = _gating_results(art, "video-sync")
    c = by["video_sync.clips_beat_aligned"]
    assert not c["passed"], f"genuinely off-beat clip must fail: {c['evidence']}"
    assert not c["skipped"], c["evidence"]


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


# ── REGRESSION PINS: the c21da616 quiet_floor false-positive fires (FIDELITY-AUDIT 2026-06-22) ────
# Job c21da616 (interview, 2026-06-15): the workers' listening verdict is method=quiet_floor,
# detected=False, observed_margin_db=0.71, floor_margin_db=6.0, fail=FALSE, enforced=FALSE,
# expected=True. The bytes DIFFER from speech-only (a bed WAS mixed); it just didn't clear the
# UN-CALIBRATED, NON-ENFORCED 6.0 dB floor (workers deliberately set fail=False). Two checks wrongly
# escalated that placeholder to CRITICAL/HIGH hard failures:
#   not_byte_identical_speech_only -> over-reached via `or detected is False`
#   present_when_expected          -> over-reached via `or detected is False`
# The pins prove both now respect the workers' enforcement contract (skip/reduced-pass), while
# music.below_speech (the KEEP check) STILL fires on the real 0.71 dB margin.


def _quiet_floor_verdict(*, expected=True, detected=False, margin=0.71, fail=False,
                         enforced=False) -> dict:
    """The c21da616 listening-QA shape: an UN-ENFORCED quiet_floor verdict (bytes differ, a bed was
    mixed, but the margin didn't clear the uncalibrated 6.0 dB floor)."""
    return {
        "job-audio": {"qa": {"listening": {"music": {
            "expected": expected, "detected": detected, "method": "quiet_floor",
            "observed_margin_db": margin, "floor_margin_db": 6.0, "fail": fail,
            "enforced": enforced,
        }}}, "result": {"intro_duration_ms": 12000}},
    }


def _c21da616_doc() -> dict:
    """The c21da616 interview doc: music expected (inputs._music_enabled) + the un-enforced
    quiet_floor verdict that false-fired two music checks."""
    doc = _base_doc()
    doc["job_id"] = "c21da616-8ed9-4ead-a3e8-7e5bb160753c"
    doc["episode_profile"] = {"genre": "interview", "topic": "x"}
    doc["inputs"] = {"genre": "interview", "_music_enabled": True}
    doc["stages"] = _quiet_floor_verdict()
    return doc


def test_pin_c21da616_not_byte_identical_passes():
    """PIN (fix 5): not_byte_identical_speech_only must NOT fire on the un-enforced quiet_floor
    detected=False (bytes differ → a bed was mixed). It only fails the CERTAIN byte-identical drop."""
    _sr, by = _gating_results(Artifact.from_doc(_c21da616_doc()), "music-sfx")
    c = by["music.not_byte_identical_speech_only"]
    assert not c["skipped"], c["evidence"]
    assert c["passed"], f"quiet_floor (bytes differ) must NOT trip the byte-identity gate: {c['evidence']}"


def test_pin_c21da616_present_when_expected_reduced_pass():
    """PIN (fix 4): present_when_expected must honor enforced=False — a reduced-confidence PASS on the
    un-enforced quiet_floor detected=False, NOT a hard HIGH failure."""
    _sr, by = _gating_results(Artifact.from_doc(_c21da616_doc()), "music-sfx")
    c = by["music.present_when_expected"]
    assert c["passed"], f"un-enforced quiet_floor detected=False must be a reduced-pass: {c['evidence']}"
    assert c["score"] == 0.5, c
    assert "un-enforced" in c["evidence"], c["evidence"]


def test_pin_c21da616_below_speech_still_fires():
    """KEEP pin (the fixes did NOT gut the real signal): music.below_speech must STILL FAIL on the
    real 0.71 dB margin (well under the 6.0 dB floor) — the genuine inaudible-bed weakness."""
    _sr, by = _gating_results(Artifact.from_doc(_c21da616_doc()), "music-sfx")
    c = by["music.below_speech"]
    assert not c["skipped"], c["evidence"]
    assert not c["passed"], f"0.71 dB margin must still fail below_speech: {c['evidence']}"
    assert "0.7 dB" in c["evidence"], c["evidence"]


def test_pin_byte_identical_still_fails():
    """GENUINELY-BAD pin: the CERTAIN byte-identical drop (method=byte_identical, detected=False,
    fail=True — job 6e507451) must STILL FAIL both fixed checks (the fix didn't blind them)."""
    doc = _base_doc()
    doc["inputs"] = {"_music_enabled": True}
    doc["stages"] = {"job-audio": {"qa": {"listening": {"music": {
        "expected": True, "detected": False, "method": "byte_identical", "fail": True,
    }}}, "result": {"intro_duration_ms": 12000}}}
    _sr, by = _gating_results(Artifact.from_doc(doc), "music-sfx")
    nbi = by["music.not_byte_identical_speech_only"]
    pwe = by["music.present_when_expected"]
    assert not nbi["passed"], f"byte_identical drop must fail not_byte_identical: {nbi['evidence']}"
    assert not pwe["passed"], f"byte_identical drop must fail present_when_expected: {pwe['evidence']}"


def test_pin_enforced_quiet_floor_still_fails():
    """GENUINELY-BAD pin (fix 4): when the workers DID enforce the floor (enforced=True, fail=True),
    present_when_expected must hard-fail — honoring enforcement cuts both ways."""
    doc = _base_doc()
    doc["inputs"] = {"_music_enabled": True}
    doc["stages"] = _quiet_floor_verdict(detected=False, fail=True, enforced=True, margin=0.71)
    _sr, by = _gating_results(Artifact.from_doc(doc), "music-sfx")
    c = by["music.present_when_expected"]
    assert not c["passed"], f"enforced+fail quiet_floor drop must hard-fail: {c['evidence']}"


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
    doc["visual_clips"] = _visual_clips_varied()
    doc["captions"] = _captions()
    doc["stages"] = _music_stage()
    imgs = [_good_image(str(tmp_path / f"s{i}.png")) for i in range(6)]
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


# ── visual.pictorial_share (stroke-based) ────────────────────────────────────────
# The FIRST version scored COLOUR VARIETY and was inverted for our renderers (#80 ->
# reverted in #81): its top-scoring "depiction" was narration set in type, its
# bottom-scoring "typography" was a real flowchart. These fixtures are the ACTUAL
# renders from job 38e591f2 (downscaled to what the check measures anyway), each one
# viewed and hand-labelled — synthetic fixtures are what let v1 ship broken.
_FIXTURES = Path(__file__).parent / "fixtures" / "pictorial"
REAL_TEXT_CARD = str(_FIXTURES / "real_text_card.png")        # narration set in type
REAL_HEADLINE_CARD = str(_FIXTURES / "real_headline_card.png")  # bold full-width headline
REAL_FLOWCHART = str(_FIXTURES / "real_flowchart.png")        # Prompt -> AI -> Output
# A 16:9 EPISODE diagram — the case a horizontal-only signal misgraded (see below).
REAL_SEQ_16X9 = str(_FIXTURES / "real_sequence_diagram_16x9.png")


def _pictorial_doc(cards: int, figs: int) -> dict:
    doc = _base_doc()
    clips = [
        {"beat_index": i, "modality": "diagram", "render_mode": "image",
         "aspect_ratio": "9:16", "asset_uri": f"gs://x/card{i}.png"}
        for i in range(cards)
    ] + [
        {"beat_index": cards + j, "modality": "diagram", "render_mode": "image",
         "aspect_ratio": "9:16", "asset_uri": f"gs://x/fig{j}.png"}
        for j in range(figs)
    ]
    doc["visual"] = {"clips": clips}
    return doc


def _stage(tmp_path, cards: int, figs: int) -> list[str]:
    out = []
    for i in range(cards):
        p = tmp_path / f"card{i}.png"
        shutil.copy(REAL_TEXT_CARD if i % 2 == 0 else REAL_HEADLINE_CARD, p)
        out.append(str(p))
    for j in range(figs):
        p = tmp_path / f"fig{j}.png"
        shutil.copy(REAL_FLOWCHART, p)
        out.append(str(p))
    return out


def _pictorial_result(art):
    _sr, by = _gating_results(art, "visual-images")
    return by["visual.pictorial_share"]


def test_pictorial_signal_separates_real_card_from_real_flowchart():
    """PREMISE PIN — the reason v1 shipped broken. If a renderer restyle collapses this
    separation, fail HERE instead of silently mis-grading every job."""
    from PIL import Image

    from kitesforu_qa.harness.checks.visual import (
        _PICTORIAL_MIN_STROKE,
        _max_ink_run_frac,
    )
    scores = {}
    for name, path in (("text", REAL_TEXT_CARD), ("headline", REAL_HEADLINE_CARD),
                       ("flow", REAL_FLOWCHART)):
        with Image.open(path) as im:
            scores[name] = _max_ink_run_frac(im)
    assert scores["text"] < _PICTORIAL_MIN_STROKE, scores
    assert scores["headline"] < _PICTORIAL_MIN_STROKE, (
        f"a bold full-width headline card must still read as typography: {scores}"
    )
    assert scores["flow"] >= _PICTORIAL_MIN_STROKE, scores


def test_pictorial_not_fooled_by_the_gradient_that_broke_v1():
    """v1 scored this exact card HIGHEST of all and called it a depiction."""
    from PIL import Image

    from kitesforu_qa.harness.checks.visual import _max_ink_run_frac
    with Image.open(REAL_TEXT_CARD) as im:
        assert _max_ink_run_frac(im) < 0.35


def test_pictorial_share_fails_a_wall_of_text_cards(tmp_path):
    r = _pictorial_result(Artifact.from_doc(
        _pictorial_doc(8, 1), image_paths=_stage(tmp_path, 8, 1)))
    assert not r["skipped"], r["evidence"]
    assert not r["passed"], f"8 real cards + 1 flowchart must FAIL: {r['evidence']}"


def test_pictorial_share_passes_when_a_majority_depict(tmp_path):
    r = _pictorial_result(Artifact.from_doc(
        _pictorial_doc(2, 6), image_paths=_stage(tmp_path, 2, 6)))
    assert not r["skipped"], r["evidence"]
    assert r["passed"], f"6 flowcharts vs 2 cards must PASS: {r['evidence']}"


def test_pictorial_share_skips_rather_than_lies_when_no_assets():
    r = _pictorial_result(Artifact.from_doc(_pictorial_doc(2, 2), image_paths=[]))
    assert r["skipped"], f"no assets must SKIP, got: {r['evidence']}"


def test_pictorial_scores_both_axes_not_just_width():
    """REGRESSION (2026-07-28): the first stroke version scored HORIZONTAL runs only.
    It was calibrated on 9:16 born-shorts, where a box border spans most of the width.
    On a 16:9 EPISODE the same diagram is proportionally narrower, so this real sequence
    diagram (tele -> model -> alert -> play -> contain, labelled arrows) scored 0.178
    horizontally and was called TYPOGRAPHY — while its lifelines ran 0.517 of the HEIGHT.
    Scoring max(h, v) fixes it; caught by LOOKING at a frame the check had misgraded."""
    from PIL import Image

    from kitesforu_qa.harness.checks.visual import (
        _PICTORIAL_MIN_STROKE,
        _max_ink_run_frac,
    )
    with Image.open(REAL_SEQ_16X9) as im:
        score = _max_ink_run_frac(im)
    assert score >= _PICTORIAL_MIN_STROKE, (
        f"a real 16:9 sequence diagram must read as a depiction; got {score:.3f}. "
        f"A width-only signal scores it 0.178 and misgrades every landscape diagram."
    )


def test_landscape_fix_does_not_disturb_portrait_calibration():
    """max(h, v) must not turn a portrait text card into a depiction."""
    from PIL import Image

    from kitesforu_qa.harness.checks.visual import (
        _PICTORIAL_MIN_STROKE,
        _max_ink_run_frac,
    )
    for path in (REAL_TEXT_CARD, REAL_HEADLINE_CARD):
        with Image.open(path) as im:
            assert _max_ink_run_frac(im) < _PICTORIAL_MIN_STROKE, path


# ── visual.cadence + visual.motion_not_silently_dead ─────────────────────────────
# Founder, 2026-07-28 on job eaf99101: "the visuals were horrible, a boring screen and
# same image dancing ... i need to see a new fresh beautiful image every 2 seconds or
# zoom to some other part. there has to be always something happening."
# Followed by: "dont keep repeating same mistakes again and again. everytime you
# encounter an issue create a recurring mechanism so that next time the validation and
# also the fix can be learnt."
#
# That job passed EVERY existing visual check while holding one still frame for 67
# SECONDS with zero motion clips. These two checks are the mechanism that ends it, and
# these tests use ITS REAL NUMBERS so the alarm can never silently stop working.

def _cadence_doc(durations_ms: list[int], *, moving: bool = False) -> dict:
    doc = _base_doc()
    doc["visual"] = {"clips": [
        {"beat_index": i, "modality": "diagram", "render_mode": "image",
         "aspect_ratio": "16:9", "asset_uri": f"gs://x/a{i}.png",
         "duration_ms": d,
         **({"motion_preset": "push_in"} if moving else {})}
        for i, d in enumerate(durations_ms)
    ]}
    return doc


def _res(doc, name):
    _sr, by = _gating_results(Artifact.from_doc(doc, image_paths=[]), "visual-images")
    return by[name]


def test_cadence_fails_the_eaf99101_dead_frame():
    """THE WITNESS: a 67-second held frame must fail. It previously passed everything."""
    r = _res(_cadence_doc([3000, 4000, 67390, 5000, 3000, 4000], moving=True),
             "visual.cadence")
    assert not r["skipped"], r["evidence"]
    assert not r["passed"], f"a 67s dead frame must FAIL cadence: {r['evidence']}"
    assert "dead frame" in r["evidence"]


def test_cadence_fails_sustained_slowness_even_with_no_single_dead_frame():
    r = _res(_cadence_doc([10000] * 8, moving=True), "visual.cadence")
    assert not r["passed"], r["evidence"]
    assert "mean hold" in r["evidence"]


def test_cadence_passes_a_lively_video():
    r = _res(_cadence_doc([2000, 2500, 3000, 2000, 2500, 3000], moving=True),
             "visual.cadence")
    assert not r["skipped"], r["evidence"]
    assert r["passed"], f"a ~2-3s cadence must PASS: {r['evidence']}"


def test_motion_death_fires_when_nothing_moves():
    """THE WITNESS: eaf99101 had motion flags ON and rendered_motion_clips=0 with every
    clip a still. Flags-on + zero-output is the silent-feature-death class."""
    r = _res(_cadence_doc([4000] * 8, moving=False), "visual.motion_not_silently_dead")
    assert not r["skipped"], r["evidence"]
    assert not r["passed"], f"zero moving clips must FAIL: {r['evidence']}"
    assert "ZERO" in r["evidence"]


def test_motion_death_passes_when_something_moves():
    r = _res(_cadence_doc([4000] * 8, moving=True), "visual.motion_not_silently_dead")
    assert r["passed"], r["evidence"]


def test_motion_death_skips_a_short_artifact_rather_than_crying_wolf():
    """A still-by-design artifact with few clips is not a dead feature."""
    r = _res(_cadence_doc([4000, 4000], moving=False), "visual.motion_not_silently_dead")
    assert r["skipped"], f"2 clips must SKIP, got: {r['evidence']}"
