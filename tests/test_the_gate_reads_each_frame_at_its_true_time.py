"""The acceptance gate must score each sampled frame as the asset ACTUALLY on screen when taken.

THE DEFECT (witness job f7df77bf, 2026-10-05). The gate extracts one frame per 3 s with ffmpeg's
``fps`` filter and assumed frame k shows the master at ``3k`` seconds. It does not: the filter
rounds every source timestamp to the NEAREST output slot, so frame k is the last source frame
before ``(k + 1/2) * 3`` seconds. On the witness all 28 frames are pixel-identical to source frame
``90k + 44`` (``3k + 1.4667 s`` at 30 fps). Every frame was therefore attributed to the clip on
screen ~1.5 s earlier, and the two frames past the authored clip timeline (69.24 s of an 85.03 s
master) were attributed to no clip at all. With video_hero not exempt either, the gate raised
``MAJOR EDGE-CLIP: 4/4 checked frames`` on four full-bleed photographic frames.

This file pins the GATE: the instant each frame shows, on real ffmpeg output with frames that
carry their own number; that the gate reads the delivered timeline at that instant; and that it
keeps its TEETH, i.e. a real edge clip on a graphic beat still raises MAJOR beside full-bleed beats,
and an exemption widened by one modality goes red. What is on screen at an instant is
``kitesforu_qa.harness.delivered_timeline``, pinned rule by rule in ``tests/test_delivered_timeline.py``.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
from fractions import Fraction

import pytest

from kitesforu_qa.harness.delivered_timeline import DeliveredTimeline

np = pytest.importorskip("numpy")
PIL_Image = pytest.importorskip("PIL.Image")
PIL_ImageDraw = pytest.importorskip("PIL.ImageDraw")

_GATE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "acceptance_gate.py"
_W, _H = 540, 304  # the size `_extract_frames` emits for a 16:9 master

_HAS_FFMPEG = bool(shutil.which("ffmpeg"))
VEO = {"render_mode": "video", "motion_render": "veo"}


def _load_gate():
    spec = importlib.util.spec_from_file_location("acceptance_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _edge_issues(issues):
    return [i for i in issues if "EDGE-CLIP" in i["msg"]]


# ── frame fixtures ────────────────────────────────────────────────────────────────────────────

def _photo(path):
    """Full-bleed, busy to every edge: what a photo or a Veo frame looks like to the edge rule.
    It TRIPS the per-frame rule on purpose; only the authored label may exempt it."""
    rng = np.random.default_rng(7)
    PIL_Image.fromarray(rng.integers(0, 256, (_H, _W), dtype=np.uint8)).save(path)


def _clipped_card(path):
    """A card and its text rows running off the LEFT edge: the canary 29355571 shape."""
    im = PIL_Image.new("L", (_W, _H), 18)
    dr = PIL_ImageDraw.Draw(im)
    dr.rectangle([-40, 90, 120, 210], fill=235)
    for i in range(5):
        dr.rectangle([-30, 105 + i * 20, 100, 113 + i * 20], fill=30)
    im.save(path)


@pytest.fixture()
def frames(tmp_path):
    made = {}
    for name, fn in (("photo", _photo), ("clipped", _clipped_card)):
        p = tmp_path / f"{name}.png"
        fn(p)
        made[name] = str(p)
    return made


def test_the_fixtures_trip_the_per_frame_rule(frames):
    """PREMISE. Every exemption test below is meaningless unless BOTH fixtures trip the rule when
    nothing exempts them; otherwise "no EDGE-CLIP" would pass for the wrong reason."""
    gate = _load_gate()
    for name in ("photo", "clipped"):
        issues, _ = gate._pixel_invariants([frames[name]] * 6)
        assert _edge_issues(issues), f"the {name} fixture does not trip the edge rule unlabelled"


def _clip(start_ms, modality, *, asset=".png", **extra):
    """A persisted-shape clip (it carries `asset_uri`, as every persisted clip does)."""
    c = {"start_ms": start_ms, "modality": modality, "asset_uri": f"gs://x/{start_ms}{asset}",
         "status": "done", "duration_ms": 3000}
    c.update(extra)
    return c


# ── THE TIME: real ffmpeg, synthetic frames that carry their own frame number ───────────────────

_BITS = 10       # 2**10 frames: 34 s at 30 fps
_STRIPE = 16     # source pixels per bit


def _numbered_video(path, rate: str, seconds: int) -> None:
    """A video whose frame N shows N in binary, one black/white stripe per bit (bit 0 leftmost).
    Binary stripes survive any encoder; libx264 is preferred because real masters are H.264."""
    src = f"color=c=black:s={_BITS * _STRIPE}x16:r={rate}:d={seconds}"
    vf = f"format=gray,geq=lum='255*mod(floor(N/pow(2\\,floor(X/{_STRIPE})))\\,2)'"
    for codec in (["-c:v", "libx264", "-qp", "0"], ["-c:v", "mpeg4", "-q:v", "1"]):
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf", vf,
                            *codec, "-pix_fmt", "yuv420p", str(path)], capture_output=True)
        if r.returncode == 0:
            return
    pytest.fail(f"could not encode the synthetic video: {r.stderr.decode()[-300:]}")


def _frame_number(png: str) -> int:
    im = np.asarray(PIL_Image.open(png).convert("L"), dtype=float)
    scale = im.shape[1] / (_BITS * _STRIPE)
    n = 0
    for b in range(_BITS):
        lo, hi = int((b * _STRIPE + 4) * scale), int((b * _STRIPE + 12) * scale)
        if im[:, lo:hi].mean() > 128:
            n |= 1 << b
    return n


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed")
@pytest.mark.parametrize("interval_ms", [3000, 1000])        # the gate's, and the checker's 9b
@pytest.mark.parametrize("rate", ["24", "25", "30", "30000/1001"])
def test_every_extracted_frame_shows_the_instant_the_gate_reads_it_at(tmp_path, rate, interval_ms):
    """Run the REAL extraction on a video whose frames name themselves, and require the instant
    `_frame_time_ms` assigns to every extracted frame to fall inside that frame's own display
    interval ``[n/fps, (n+1)/fps)``. Goes red on the old ``k * 3000`` mapping at every frame."""
    gate = _load_gate()
    fps = Fraction(rate)
    slot = Fraction(interval_ms, 1000)
    mp4 = tmp_path / "numbered.mp4"
    _numbered_video(mp4, rate, seconds=22)
    out = gate._extract_frames(str(mp4), str(tmp_path / "frames"), interval_ms)
    assert len(out) == {3000: 7, 1000: 22}[interval_ms], len(out)
    for k, png in enumerate(out):
        n = _frame_number(png)
        shows_from, shows_until = n / fps, (n + 1) / fps
        # PREMISE: the frame really sits mid-slot, so this case can tell the two mappings apart.
        assert shows_from - slot * k > slot / 3, (
            f"frame {k} is source frame {n} ({float(shows_from):.4f}s) — not mid-slot, so this "
            f"case cannot discriminate the mappings"
        )
        at = Fraction(gate._frame_time_ms(k, interval_ms), 1000)
        assert shows_from <= at < shows_until, (
            f"@{rate}fps frame {k} is source frame {n}, on screen {float(shows_from):.4f}-"
            f"{float(shows_until):.4f}s, but the gate reads it at {float(at):.3f}s"
        )


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed")
def test_every_extracted_frame_is_attributed_to_the_clip_on_screen(tmp_path):
    """End to end: a master whose four clips paint four gray levels, cut at 7, 13 and 19 s, each
    between a slot start and the frame actually taken in that slot, which is exactly where the old
    mapping read the PREVIOUS clip (frames 2, 4 and 6). Ground truth comes from the pixels; the
    attribution is the delivered timeline the gate reads, at the instant the gate reads it."""
    gate = _load_gate()
    cuts_s, levels = [0, 7, 13, 19], [40, 100, 160, 220]
    expr = "+".join(f"{levels[i] - (levels[i - 1] if i else 0)}*gte(T\\,{c})"
                    for i, c in enumerate(cuts_s))
    mp4 = tmp_path / "clips.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=black:s=64x36:r=30:d=25",
                        "-vf", f"format=gray,geq=lum='{expr}'", "-pix_fmt", "yuv420p", str(mp4)],
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-300:]
    clips = [_clip(c * 1000, "diagram", duration_ms=(n - c) * 1000)
             for c, n in zip(cuts_s, cuts_s[1:] + [25], strict=True)]
    timeline = DeliveredTimeline.from_clips(clips, master_ms=25000)
    out = gate._extract_frames(str(mp4), str(tmp_path / "frames"))
    assert len(out) == 8
    misread_by_slot_start = 0
    for k, png in enumerate(out):
        level = np.asarray(PIL_Image.open(png).convert("L"), dtype=float).mean()
        truth = min(range(4), key=lambda i: abs(levels[i] - level))
        on = timeline.candidates_at(gate._frame_time_ms(k))
        assert on == [clips[truth]], (
            f"frame {k} shows clip {truth} (gray {level:.0f}) but the gate attributes it to "
            f"{[clips.index(c) for c in on]}"
        )
        misread_by_slot_start += truth != max(i for i, c in enumerate(cuts_s) if c <= 3 * k)
    assert misread_by_slot_start == 3, "premise: the slot-start reading misattributes 3 frames"


def test_the_instants_are_mid_slot():
    """The documented values, written out rather than recomputed from the formula."""
    gate = _load_gate()
    assert [gate._frame_time_ms(k) for k in (0, 1, 2, 24, 27)] == [1499, 4499, 7499, 73499, 82499]
    assert [gate._frame_time_ms(k, 1000) for k in (0, 1, 2, 17)] == [499, 1499, 2499, 17499]


def test_the_checker_reads_every_instant_the_gate_and_the_arm_it_replaced_read():
    """9b's one frame per second holds every instant the gate reads (one per 3 s) and every instant
    the bright-pixel arm it replaced read (step 9's ``fps=1/5``), so no frame either judged goes
    unjudged. The two witnesses that arm was written for sit at 5 s slots 0 and 3."""
    gate = _load_gate()
    checker = {gate._frame_time_ms(j, gate.CHECKER_FRAME_INTERVAL_MS) for j in range(700)}
    assert {gate._frame_time_ms(k) for k in range(200)} <= checker
    assert {gate._frame_time_ms(k, 5000) for k in range(120)} <= checker
    assert gate._frame_time_ms(3, 5000) == gate._frame_time_ms(17, 1000) == 17499    # 7171699f f_004
    assert gate._frame_time_ms(0, 5000) == gate._frame_time_ms(2, 1000) == 2499      # 4d41320d f_001


# ── THE WITNESS ──────────────────────────────────────────────────────────────────────────────

#: job f7df77bf `visual.clips` (start_ms, duration_ms, modality, motion_render), read from
#: Firestore 2026-10-05. Every clip has `render_mode="video"` and an MP4 asset: the two video_hero
#: rows are Veo (`veo_N/clip.mp4`) and the scene images are Ken Burns renders. 11 clips end at
#: 69.24 s; the delivered master runs 85.03 s and extracts to 28 frames.
_WITNESS = [(0, 6412, "video_hero", "veo"), (6412, 6412, "scene_image", "kenburns"),
            (12824, 6517, "video_hero", "veo"), (19341, 6517, "scene_image", "kenburns"),
            (25858, 6666, "scene_image", "kenburns"), (32524, 6666, "scene_image", "kenburns"),
            (39190, 5846, "scene_image", "kenburns"), (45036, 5845, "scene_image", "kenburns"),
            (50881, 6576, "scene_image", "kenburns"), (57457, 6575, "scene_image", "kenburns"),
            (64032, 5208, "scene_image", "kenburns")]
_WITNESS_MASTER_MS = 85033


def _witness_clips():
    return [_clip(s, m, asset=".mp4", duration_ms=d, beat_index=i, render_mode="video",
                  motion_render=mr) for i, (s, d, m, mr) in enumerate(_WITNESS)]


def _witness_timeline(clips=None):
    return DeliveredTimeline.from_clips(clips or _witness_clips(), master_ms=_WITNESS_MASTER_MS)


def test_the_witness_no_longer_raises_edge_clip_on_full_bleed_frames(frames):
    """Every sampled frame of the witness is full-bleed footage. Before the fix this raised
    `MAJOR EDGE-CLIP: 4/4 checked frames` (f_001, f_003, f_025, f_028)."""
    gate = _load_gate()
    issues, _ = gate._pixel_invariants([frames["photo"]] * 28, timeline=_witness_timeline())
    assert not _edge_issues(issues), issues


def test_the_witness_frames_named_in_the_report_are_read_as_the_right_clips():
    gate = _load_gate()
    clips = _witness_clips()
    tl = _witness_timeline(clips)
    # f_003 (index 2) is 7.47 s into the master: the scene_image at 6.41-12.82 s, not the
    # video_hero the slot start (6.0 s) pointed at.
    assert tl.candidates_at(gate._frame_time_ms(2)) == [clips[1]]
    # f_025 / f_028 sit past the authored timeline (69.24 s): the master holds the last scene.
    for idx in (24, 27):
        assert tl.candidates_at(gate._frame_time_ms(idx)) == [clips[-1]]


def test_the_coverage_says_the_witness_pass_is_a_pass_by_exemption(frames):
    """12 of 12 sampled frames exempt means the edge rule observed nothing on this job. The
    result has to say so, or a PASS reads as "edges verified"."""
    gate = _load_gate()
    _, cov = gate._pixel_invariants([frames["photo"]] * 28, timeline=_witness_timeline())
    assert cov == {"sampled": 12, "checked": 0, "exempt_full_bleed": 12, "unknown": 0,
                   "flagged": 0, "timeline": "trusted", "source": "estimated", "sidecar_bytes": 0,
                   "flagged_frames": []}
    assert "checked 0 of 12" in gate._edge_clip_note(cov)
    assert "timeline trusted, estimated)" in gate._edge_clip_note(cov)


def test_probe_master_reads_the_producers_sidecar_and_says_so(frames, tmp_path, monkeypatch):
    """The gate's own path (`probe_master`, which `full_artifact_checker.sh` 9b also calls) takes
    the timeline from the sidecar the doc names, and its coverage says the attribution is the
    producer's and what the read cost. The read is stubbed with the producer's witness stamp."""
    from kitesforu_qa.harness import delivered_timeline as dt
    from kitesforu_qa.harness.painted_timeline_sidecar import read_sidecar

    body = (pathlib.Path(__file__).parent / "fixtures" / "painted_timeline_v1_witness.json").read_bytes()
    uri = "gs://kitesforu-dev-podcasts/visuals/f7df77bf/painted_timeline.json"
    monkeypatch.setattr(dt, "read_sidecar", lambda doc: read_sidecar(
        doc, fetch=lambda url: body, parse=json.loads))
    gate = _load_gate()
    monkeypatch.setattr(gate, "_extract_frames", lambda mp4, out, *_: [frames["photo"]] * 28)
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": _witness_clips(), "painted_timeline_uri": uri}}
    _, issues, cov = gate.probe_master(doc, "unused.mp4", str(tmp_path), 85033)
    assert (cov["source"], cov["timeline"], cov["sidecar_bytes"]) == ("stamp", "stamp", len(body)), cov
    assert cov["exempt_full_bleed"] == 12 and not _edge_issues(issues), cov
    # The same job with the video 3 s longer than the stamp's windows: a different render, so the
    # stamp is refused, the coverage says why, and the estimate decides.
    _, _, cov = gate.probe_master(doc, "unused.mp4", str(tmp_path), 88033)
    assert cov["source"] == "estimated" and "windows end 85033" in cov["stamp_rejected"], cov


# ── THE EXEMPTION, and exactly the exemption ─────────────────────────────────────────────────

def _uniform(modality, n=12, **extra):
    """One clip per 3 s slot WITH durations, so the pre-fix gate reads them too: a test here
    fails on main for its attribution, never for a fixture it could not read."""
    return [_clip(i * 3000, modality, duration_ms=3000, beat_index=i, **extra) for i in range(n)]


@pytest.mark.parametrize("modality, extra", [
    ("scene_image", {}),
    ("video_hero", VEO),
    (" Video_Hero ", {"asset": ".mp4"}),
])
def test_full_bleed_beats_are_exempt(frames, modality, extra):
    gate = _load_gate()
    issues, _ = gate._pixel_invariants([frames["clipped"]] * 6, _uniform(modality, **extra))
    assert not _edge_issues(issues)


def test_a_video_hero_png_still_stays_checked(frames):
    """Code critic #1: `degrade_ladder` stamps a reclaimed still (a flowchart, a title band) with
    `modality="video_hero"`. Without Veo evidence it is drawn text, so the edge rule runs on it."""
    gate = _load_gate()
    still = _uniform("video_hero", render_mode="still")
    issues, cov = gate._pixel_invariants([frames["clipped"]] * 6, still)
    assert _edge_issues(issues) and cov["exempt_full_bleed"] == 0, cov


@pytest.mark.parametrize("modality", ["diagram", "chart", "motion", "physics", "card",
                                      "relimage", "video", None, ""])
def test_every_other_modality_is_still_checked(frames, modality):
    """Widening the exemption by ONE modality goes red here. `motion` is GSAP / Living Stage
    rendered text; a `relimage` picture carries a moved title and disclosure label; None and ""
    are UNKNOWN, which is never permission to skip."""
    gate = _load_gate()
    issues, _ = gate._pixel_invariants([frames["clipped"]] * 6, _uniform(modality))
    assert _edge_issues(issues), f"modality={modality!r} was exempted from the edge rule"


def test_a_clipped_card_between_full_bleed_beats_still_raises_major(frames):
    """THE POSITIVE CONTROL. A Veo/photo-heavy job, the witness's own shape, with two graphic beats
    whose card is cut at the edge, like canary 29355571. The two cards must still be checked and
    must still raise MAJOR, with the denominator naming only what was checked."""
    gate = _load_gate()
    clips = _witness_clips()
    clips[4].update(modality="diagram", motion_render=None)   # 25.86-32.52 s holds frame 9 (28.5 s)
    clips[6].update(modality="motion", motion_render="gsap")  # 39.19-45.04 s holds frame 14 (43.5 s)
    paths = [frames["photo"]] * 28
    paths[9] = paths[14] = frames["clipped"]
    issues, _ = gate._pixel_invariants(paths, timeline=_witness_timeline(clips))
    edge = _edge_issues(issues)
    assert edge and edge[0]["sev"] == "MAJOR", "a real edge clip on a graphic beat was not flagged"
    assert edge[0]["msg"].startswith("EDGE-CLIP: 2/2 checked frames"), edge[0]["msg"]
    assert "10 full-bleed frame(s) exempt" in edge[0]["msg"], edge[0]["msg"]


def test_a_tail_frame_is_judged_as_the_last_clip(frames):
    """The tail re-shows the last clip, so it is judged as the last clip: exempt when that clip is
    full-bleed, CHECKED when it is a card."""
    gate = _load_gate()
    for last, expect_flag in (("scene_image", False), ("diagram", True)):
        clips = [_clip(0, "scene_image", beat_index=0), _clip(3000, last, beat_index=1)]
        timeline = DeliveredTimeline.from_clips(clips, master_ms=36000)
        issues, _ = gate._pixel_invariants([frames["clipped"]] * 12, timeline=timeline)
        assert bool(_edge_issues(issues)) is expect_flag, (last, issues)


def test_an_untrusted_timeline_is_checked_on_every_frame(frames):
    gate = _load_gate()
    clips = [_clip(s, "scene_image", beat_index=i) for i, s in enumerate((0, 9000, 6000))]
    issues, cov = gate._pixel_invariants([frames["clipped"]] * 12, clips)
    assert cov["timeline"] == "untrusted_decreasing" and cov["unknown"] == cov["checked"] == 12, cov
    assert _edge_issues(issues)


# ── what run_gate returns ────────────────────────────────────────────────────────────────────

def test_run_gate_reports_coverage_and_a_note_when_nothing_was_checked(frames, tmp_path,
                                                                    monkeypatch, capsys):
    """Design #4: the coverage is `edge_clip_coverage`, returned (not an out-parameter), and a pass
    by exemption prints one line. I/O is stubbed: the doc, the download, the probe, the frames."""
    gate = _load_gate()
    doc = {"topic": "a storm", "master_segment_timeline": [{"index": 0, "start_ms": 0,
                                                             "end_ms": 85000}],
           "visual": {"clips": _witness_clips(), "video_url": "https://example.invalid/m.mp4"}}
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: doc)
    monkeypatch.setattr(gate.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_download(cmd, **_kw):
        pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(gate.subprocess, "run", fake_download)
    monkeypatch.setattr(gate, "_probe_dims", lambda path: (1920, 1080, 85.033))
    monkeypatch.setattr(gate, "_extract_frames", lambda mp4, out, *_: [frames["photo"]] * 28)
    res = gate.run_gate("f7df77bf-witness")
    assert "edge_clip" not in res
    assert res["edge_clip_coverage"]["checked"] == 0 and res["verdict"] == "PASS_DETERMINISTIC"
    assert res["edge_clip_note"].startswith("EDGE-CLIP checked 0 of 12 sampled frames")
    monkeypatch.setattr(gate, "run_gate", lambda *a, **k: res)
    monkeypatch.setattr(sys, "argv", ["acceptance_gate.py", "--job-id", "f7df77bf-witness"])
    assert gate.main() == 0
    assert capsys.readouterr().err.strip() == f"NOTE: {res['edge_clip_note']}"
    # A job whose edge rule did run carries no note.
    _, cov = gate._pixel_invariants([frames["clipped"]] * 6, _uniform("diagram"))
    assert gate._edge_clip_note(cov) is None


def _gate_with_a_stamped_job(monkeypatch, tmp_path, frames, *, stamped_generation, fetched_generation,
                             fetch_burned=False):
    """`run_gate` on the witness, its sidecar stubbed with the producer's witness stamp naming master
    `stamped_generation`, its download stubbed to answer with `fetched_generation`."""
    from kitesforu_qa.harness import delivered_timeline as dt
    from kitesforu_qa.harness.painted_timeline_sidecar import read_sidecar

    body = b"\x00" * 4096
    st = json.loads((pathlib.Path(__file__).parent / "fixtures"
                     / "painted_timeline_v1_witness.json").read_bytes())
    st.update(master_generation=stamped_generation, master_size=len(body))
    monkeypatch.setattr(dt, "read_sidecar", lambda doc: read_sidecar(
        doc, fetch=lambda url: json.dumps(st).encode(), parse=json.loads))
    gate = _load_gate()
    urls = {"video_burned_url": "https://storage.googleapis.com/b/visuals/w/episode_video_captioned.mp4"}
    if not fetch_burned:
        urls["video_url"] = "https://storage.googleapis.com/b/visuals/w/episode_video.mp4"
    doc = {"topic": "a storm", "master_segment_timeline": [{"index": 0}],
           "visual": {"clips": _witness_clips(), "painted_timeline_uri": "https://x.invalid/pt.json",
                      **urls}}
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: doc)
    monkeypatch.setattr(gate.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = []

    def fake_get(cmd, **_kw):
        calls.append(cmd)
        pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(body)
        pathlib.Path(cmd[cmd.index("-D") + 1]).write_text(
            f"HTTP/1.1 200 OK\r\nx-goog-generation: {fetched_generation}\r\n\r\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(gate.subprocess, "run", fake_get)
    monkeypatch.setattr(gate, "_probe_dims", lambda path: (1920, 1080, 85.033))
    monkeypatch.setattr(gate, "_extract_frames", lambda mp4, out, *_: [frames["photo"]] * 28)
    return gate.run_gate("f7df77bf-witness"), calls


def test_run_gate_holds_the_stamp_to_the_master_it_fetched(frames, tmp_path, monkeypatch):
    """The GET's own `x-goog-generation` and the bytes on disk are the master the stamp must name."""
    res, calls = _gate_with_a_stamped_job(monkeypatch, tmp_path, frames,
                                          stamped_generation=1759660800123456,
                                          fetched_generation=1759660800123456)
    assert calls[0][:2] == ["curl", "-sfL"] and "-D" in calls[0]
    cov = res["edge_clip_coverage"]
    assert (cov["source"], cov.get("stamp_rejected")) == ("stamp", None), cov
    # The master was re-assembled after the stamp was written: same length, another object.
    res, _ = _gate_with_a_stamped_job(monkeypatch, tmp_path, frames,
                                      stamped_generation=1759660800123456,
                                      fetched_generation=1759661999000001)
    cov = res["edge_clip_coverage"]
    assert (cov["source"], cov["stamp_rejected"]) == ("estimated", "stale_master"), cov
    assert sorted(p.suffix for p in tmp_path.iterdir() if p.suffix == ".headers") == []


def test_the_captioned_copy_is_not_held_to_the_masters_generation(frames, tmp_path, monkeypatch):
    """With no `video_url` the gate fetches the captioned copy, a different object from the master
    the stamp names, so its generation proves nothing: the length check decides."""
    res, _ = _gate_with_a_stamped_job(monkeypatch, tmp_path, frames,
                                      stamped_generation=1759660800123456,
                                      fetched_generation=1759661999000001, fetch_burned=True)
    cov = res["edge_clip_coverage"]
    assert (cov["source"], cov.get("stamp_rejected")) == ("stamp", None), cov


# ── a GET that fails is a FAIL, never the last run's master re-scored ──────────────────────────

def _gate_on_a_failing_get(monkeypatch, tmp_path, frames, fail):
    """`run_gate` on the witness with an earlier run's master left at the gate's temp path, and a GET
    that fails as ``fail`` says. Returns the result and the header files left behind."""
    gate = _load_gate()
    doc = {"topic": "a storm", "master_segment_timeline": [{"index": 0}],
           "visual": {"clips": _witness_clips(), "video_url": "https://example.invalid/m.mp4"}}
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: doc)
    monkeypatch.setattr(gate.tempfile, "gettempdir", lambda: str(tmp_path))
    stale = tmp_path / "ag_f7df77bf-witness.mp4"
    stale.write_bytes(b"\x00" * 4096)                         # what an earlier run downloaded

    def failing_get(cmd, **_kw):
        if fail == "exit":
            return subprocess.CompletedProcess(cmd, 22)        # curl -f on a 404, nothing written
        if fail == "partial":                                   # the connection dropped mid-body
            pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(b"\x00" * 100)
            return subprocess.CompletedProcess(cmd, 18)
        if fail == "missing":
            raise FileNotFoundError(2, "No such file or directory", cmd[0])
        raise subprocess.TimeoutExpired(cmd, 900)

    monkeypatch.setattr(gate.subprocess, "run", failing_get)
    monkeypatch.setattr(gate, "_probe_dims", lambda path: (1920, 1080, 85.033))
    monkeypatch.setattr(gate, "_extract_frames", lambda mp4, out, *_: [frames["photo"]] * 28)
    res = gate.run_gate("f7df77bf-witness")
    return res, sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".headers")


@pytest.mark.parametrize("fail", ["exit", "partial", "missing", "timeout"])
def test_a_failed_get_fails_the_gate_and_leaves_no_headers(frames, tmp_path, monkeypatch, fail):
    """Round-2 code critic #2 and #5: the master path is fixed per job, so a GET that failed used to
    leave the PREVIOUS run's bytes to be scored as this run's, with empty headers. The old file is
    removed first, the GET's exit status counts (a body cut short is on disk, and curl says so), and
    the header file goes whatever happens."""
    res, headers = _gate_on_a_failing_get(monkeypatch, tmp_path, frames, fail)
    assert res["verdict"] == "FAIL" and res["issues"][0]["sev"] == "BLOCKER", res
    assert res["issues"][0]["msg"].startswith("artifact not fetchable: https://example.invalid/m.mp4")
    if fail != "partial":
        assert not (tmp_path / "ag_f7df77bf-witness.mp4").exists()
    assert headers == []


@pytest.mark.parametrize("where", ["probe", "extract"])
def test_a_subprocess_that_times_out_is_a_fail_not_a_hang(frames, tmp_path, monkeypatch, where):
    """Round-1 latency #6: every gate subprocess has a timeout, and running into it FAILs the gate."""
    gate = _load_gate()
    doc = {"topic": "a storm", "master_segment_timeline": [{"index": 0}],
           "visual": {"clips": _witness_clips(), "video_url": "https://example.invalid/m.mp4"}}
    monkeypatch.setattr(gate, "_fetch_job", lambda job_id: doc)
    monkeypatch.setattr(gate.tempfile, "gettempdir", lambda: str(tmp_path))

    def get(cmd, **_kw):
        pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0)

    def times_out(*_a, **_k):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(gate.subprocess, "run", get)
    monkeypatch.setattr(gate, "_probe_dims", times_out if where == "probe"
                        else (lambda path: (1920, 1080, 85.033)))
    monkeypatch.setattr(gate, "_extract_frames", times_out if where == "extract"
                        else (lambda mp4, out, *_: [frames["photo"]] * 28))
    res = gate.run_gate("f7df77bf-witness")
    assert res["verdict"] == "FAIL" and "timed out" in res["issues"][-1]["msg"], res


def test_every_gate_subprocess_carries_a_timeout(tmp_path, monkeypatch):
    seen = []
    gate = _load_gate()
    monkeypatch.setattr(gate.subprocess, "run",
                        lambda cmd, **kw: seen.append((cmd[0], kw.get("timeout")))
                        or subprocess.CompletedProcess(cmd, 0, stdout=""))
    gate._probe_dims("x.mp4")
    gate._extract_frames("x.mp4", str(tmp_path / "f"))
    assert seen == [("ffprobe", gate._PROBE_TIMEOUT_S), ("ffmpeg", gate._EXTRACT_TIMEOUT_S)]


def test_frames_an_earlier_run_left_are_not_scored(tmp_path, monkeypatch):
    """A longer earlier master leaves tail frames in the same frames dir; they are removed first."""
    gate = _load_gate()
    out = tmp_path / "frames"
    out.mkdir()
    for k in range(1, 30):
        (out / f"f_{k:03d}.png").write_bytes(b"old")
    (out / "notes.txt").write_text("kept")

    def extract(cmd, **_kw):
        for k in range(1, 4):
            (out / f"f_{k:03d}.png").write_bytes(b"new")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(gate.subprocess, "run", extract)
    got = gate._extract_frames("x.mp4", str(out))
    assert [pathlib.Path(f).name for f in got] == ["f_001.png", "f_002.png", "f_003.png"]
    assert (out / "notes.txt").exists()


# ── the step-by-step checker's 9b: ANY single clipped frame of all frames FAILs ─────────────────

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

#: The frames the arm 9b replaced was written for, as the gate's extraction holds them (540 px wide,
#: gray: the rule reads luma only). Each is a SINGLE clipped frame on its master. Extracted
#: 2026-10-08 from the delivered masters (generations 1785993296599620 and 1786009381916648, the
#: objects of 2026-08-06): the old arm's step-9 frame and the 1/s frame at the same instant are
#: pixel-identical at this scale (mean |diff| 0.0; the neighbouring frames differ by 33-37).
#:   * 7171699f f_004, 17.5 s: "Summer heat pushes lattice outward" rendered "mmer heat ..." (left
#:     margin 116 vertical steps, threshold 12);
#:   * 4d41320d f_001, 2.5 s: the hook composite cut at the top and the bottom (977 and 395 steps).
WITNESSES = {"7171699f_f004": 17499, "4d41320d_f001": 2499}


def _witness(name):
    return str(FIXTURES / f"edge_witness_{name}.png")


def _flat(path, size):
    PIL_Image.new("L", size, 40).save(path)
    return str(path)


@pytest.mark.parametrize("name", sorted(WITNESSES) + ["a9dc0a4f_331s"])
def test_each_witness_frame_trips_the_rule_at_the_gates_scale(name):
    """PREMISE for the pins below: the rule flags each real witness frame on its own."""
    gate = _load_gate()
    im = np.asarray(PIL_Image.open(_witness(name)).convert("L"), dtype=float)
    assert im.shape[1] == 540 and gate._cut_at_edge(im)


@pytest.mark.parametrize("name", sorted(WITNESSES))
def test_the_checker_fails_one_clipped_frame_the_gates_sample_lets_through(name, tmp_path,
                                                                           monkeypatch):
    """Round-2 code critic #1: 9b had become the gate's AGGREGATE (12 sampled frames, MAJOR at 4),
    so a master with one, two or three clipped frames PASSed it, though the arm it replaced FAILed
    on one. Here a master is a diagram for 24 s with ONE real witness frame at its witness instant:
    the checker FAILs and names it, and the gate's own sampled verdict raises nothing."""
    gate = _load_gate()
    size = PIL_Image.open(_witness(name)).size
    flat = _flat(tmp_path / "flat.png", size)
    at = WITNESSES[name]
    per_second = [flat] * 24
    per_second[at // 1000] = _witness(name)
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": [_clip(0, "diagram", duration_ms=24000, beat_index=0)]}}

    def extract(mp4, out, interval_ms=3000):
        assert interval_ms == gate.CHECKER_FRAME_INTERVAL_MS, interval_ms
        return per_second

    monkeypatch.setattr(gate, "_extract_frames", extract)
    failed, lines = gate.every_frame_edge_step(doc, "unused.mp4", str(tmp_path), 24000)
    assert failed and lines[0].endswith(":: FAIL"), lines
    assert f"{pathlib.Path(per_second[at // 1000]).name}@{at / 1000:.1f}s" in lines[0], lines
    # The gate's own verdict, on the same master at its own 3 s frames (which hold the witness only
    # by luck of phase, so it is placed at one): 1 flagged of 8 checked, below max(2, 8 // 3).
    gate_frames = [flat] * 8
    gate_frames[at // 3000] = _witness(name)
    issues, cov = gate._pixel_invariants(gate_frames, timeline=DeliveredTimeline.from_job(doc, 24000))
    assert cov["flagged"] == 1 and not _edge_issues(issues), (cov, issues)


def test_the_checker_passes_a_master_with_no_clipped_frame(tmp_path, monkeypatch):
    gate = _load_gate()
    flat = _flat(tmp_path / "flat.png", (540, 960))
    monkeypatch.setattr(gate, "_extract_frames", lambda mp4, out, *_: [flat] * 24)
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": [_clip(0, "diagram", duration_ms=24000, beat_index=0)]}}
    failed, lines = gate.every_frame_edge_step(doc, "unused.mp4", str(tmp_path), 24000)
    assert not failed and lines[0].endswith(":: PASS") and "flagged=0 []" in lines[0], lines


def test_the_checker_says_when_the_producers_timeline_had_no_parser(tmp_path, monkeypatch):
    """A job that names a sidecar, judged where workers' parse_v1 cannot be loaded: every frame is
    attributed by the estimate, and the step says so in its own output."""
    from kitesforu_qa.harness import delivered_timeline as dt
    from kitesforu_qa.harness.painted_timeline_sidecar import SidecarRead

    gate = _load_gate()
    flat = _flat(tmp_path / "flat.png", (540, 960))
    monkeypatch.setattr(gate, "_extract_frames", lambda mp4, out, *_: [flat] * 6)
    monkeypatch.setattr(dt, "read_sidecar", lambda doc: SidecarRead(
        "parser_unavailable", "https://x.invalid/pt.json",
        detail="FileNotFoundError: /r/kitesforu-workers has no origin/main:src/w.py"))
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": [_clip(0, "diagram", duration_ms=6000)],
                      "painted_timeline_uri": "https://x.invalid/pt.json"}}
    _, lines = gate.every_frame_edge_step(doc, "unused.mp4", str(tmp_path), 6000)
    assert any(line.strip().startswith("NOTE: NO PARSER:") for line in lines), lines


# ── E1: a diagram whose kind says "picture" stays checked at the gate ──────────────────────────

def test_a_diagram_row_with_a_picture_kind_is_checked_and_its_clipped_frame_flagged(tmp_path):
    """The real a9dc0a4f row 56 (``modality=diagram``, ``diagram_debug.kind=scene_image``, parallax)
    and the frame its master shows at 331.0 s: a four-step diagram whose third card the parallax
    pushes off the right edge (258 vertical steps in the right margin). Sabotage arm E1, widening
    ``FULL_BLEED_MODALITIES`` alone, exempts this row and hides the frame; this goes red then.
    See ``DIAGRAM_ROWS_WITH_A_PICTURE_KIND`` in ``tests/test_delivered_timeline.py`` for the rows."""
    gate = _load_gate()
    row = _clip(0, "diagram", asset=".mp4", duration_ms=6294, beat_index=0,
                diagram_debug={"kind": "scene_image"}, render_mode="parallax_2_5d",
                modality_reasons=["rule3:diagram_spec_present", "weave:adjacent_diagram→scene",
                                  "imagination:director_scene",
                                  "carried_through_render_mode_upgrade:parallax_2_5d"])
    frame = _witness("a9dc0a4f_331s")
    issues, cov = gate._pixel_invariants([frame] * 2, timeline=DeliveredTimeline.from_clips(
        [row], master_ms=6294))
    assert (cov["exempt_full_bleed"], cov["checked"], cov["flagged"]) == (0, 2, 2), cov
    assert _edge_issues(issues)
