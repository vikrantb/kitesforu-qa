"""The acceptance gate must score each sampled frame as the clip ACTUALLY on screen when taken.

THE DEFECT (witness job f7df77bf, 2026-10-05). The gate extracts one frame per 3 s with ffmpeg's
``fps`` filter and assumed frame k shows the master at ``3k`` seconds. It does not: the filter
rounds every source timestamp to the NEAREST output slot, so frame k is the last source frame
before ``(k + 1/2) * 3`` seconds. On the witness all 28 frames are pixel-identical to source frame
``90k + 44`` (``3k + 1.4667 s`` at 30 fps). Every frame was therefore attributed to the clip on
screen ~1.5 s earlier, and the two frames past the authored clip timeline (69.24 s of an 85.03 s
master) were attributed to no clip at all. With video_hero not exempt either, the gate raised
``MAJOR EDGE-CLIP: 4/4 checked frames`` on four full-bleed photographic frames.

Three things are pinned here:

* the TIME — on real ffmpeg output, with synthetic frames that carry their own frame number, so the
  true instant of every extracted frame is known rather than assumed;
* the ATTRIBUTION — which clip(s) the master may be showing at that instant, including the tail,
  the intro lead, a cut that lands early, a pile of clips claiming one start, and a timeline the
  persisted starts cannot describe;
* the TEETH — a real edge clip on a graphic beat must still raise MAJOR beside full-bleed beats,
  and every modality outside {scene_image, video_hero} must still be checked, so an exemption
  widened by one modality goes red here.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
from fractions import Fraction

import pytest

np = pytest.importorskip("numpy")
PIL_Image = pytest.importorskip("PIL.Image")
PIL_ImageDraw = pytest.importorskip("PIL.ImageDraw")

_GATE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "acceptance_gate.py"
_W, _H = 540, 304  # the size `_extract_frames` emits for a 16:9 master

_HAS_FFMPEG = bool(shutil.which("ffmpeg"))


def _load_gate():
    spec = importlib.util.spec_from_file_location("acceptance_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _edge_issues(issues):
    return [i for i in issues if "EDGE-CLIP" in i["msg"]]


# ── frame fixtures ────────────────────────────────────────────────────────────────────────────

def _photo(path):
    """Full-bleed, busy to every edge — what a photo or a Veo frame looks like to the edge rule.
    It TRIPS the per-frame rule on purpose: only the authored label may exempt it."""
    rng = np.random.default_rng(7)
    PIL_Image.fromarray(rng.integers(0, 256, (_H, _W), dtype=np.uint8)).save(path)


def _clipped_card(path):
    """A card and its text rows running off the LEFT edge — the canary 29355571 shape."""
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
    nothing exempts them — otherwise "no EDGE-CLIP" would pass for the wrong reason."""
    gate = _load_gate()
    for name in ("photo", "clipped"):
        assert _edge_issues(gate._pixel_invariants([frames[name]] * 6)), (
            f"the {name} fixture does not trip the edge rule unlabelled"
        )


def _clip(start_ms, modality, **extra):
    c = {"start_ms": start_ms, "modality": modality, "asset_uri": f"gs://x/{start_ms}.mp4",
         "status": "done"}
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
@pytest.mark.parametrize("rate", ["24", "25", "30", "30000/1001"])
def test_every_extracted_frame_shows_the_instant_the_gate_reads_it_at(tmp_path, rate):
    """Run the REAL extraction on a video whose frames name themselves, and require the instant
    `_frame_time_ms` assigns to every extracted frame to fall inside that frame's own display
    interval ``[n/fps, (n+1)/fps)``. Goes red on the old ``k * 3000`` mapping at every frame."""
    gate = _load_gate()
    fps = Fraction(rate)
    mp4 = tmp_path / "numbered.mp4"
    _numbered_video(mp4, rate, seconds=22)
    out = gate._extract_frames(str(mp4), str(tmp_path / "frames"))
    assert len(out) == 7, f"22 s at one frame per 3 s should give 7 frames, got {len(out)}"
    for k, png in enumerate(out):
        n = _frame_number(png)
        shows_from, shows_until = n / fps, (n + 1) / fps
        # PREMISE: the frame really sits mid-slot, so this case can tell the two mappings apart.
        assert shows_from - 3 * k > 1, (
            f"frame {k} is source frame {n} ({float(shows_from):.4f}s) — not mid-slot, so this "
            f"case cannot discriminate the mappings"
        )
        at = Fraction(gate._frame_time_ms(k), 1000)
        assert shows_from <= at < shows_until, (
            f"@{rate}fps frame {k} is source frame {n}, on screen {float(shows_from):.4f}-"
            f"{float(shows_until):.4f}s, but the gate reads it at {float(at):.3f}s"
        )


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed")
def test_every_extracted_frame_is_attributed_to_the_clip_on_screen(tmp_path):
    """End to end: a master whose four clips paint four gray levels, cut at 7, 13 and 19 s — each
    between a slot start and the frame actually taken in that slot, which is exactly where the old
    mapping read the PREVIOUS clip (frames 2, 4 and 6). Ground truth comes from the pixels."""
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
    out = gate._extract_frames(str(mp4), str(tmp_path / "frames"))
    assert len(out) == 8
    misread_by_slot_start = 0
    for k, png in enumerate(out):
        level = np.asarray(PIL_Image.open(png).convert("L"), dtype=float).mean()
        truth = min(range(4), key=lambda i: abs(levels[i] - level))
        on = gate._clips_on_screen(clips, gate._frame_time_ms(k))
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


# ── THE WITNESS ──────────────────────────────────────────────────────────────────────────────

#: job f7df77bf `visual.clips` (start_ms, duration_ms, modality), read from Firestore 2026-10-05.
#: 11 clips end at 69.24 s; the delivered master runs 85.03 s and extracts to 28 frames.
_WITNESS = [(0, 6412, "video_hero"), (6412, 6412, "scene_image"), (12824, 6517, "video_hero"),
            (19341, 6517, "scene_image"), (25858, 6666, "scene_image"),
            (32524, 6666, "scene_image"), (39190, 5846, "scene_image"),
            (45036, 5845, "scene_image"), (50881, 6576, "scene_image"),
            (57457, 6575, "scene_image"), (64032, 5208, "scene_image")]


def _witness_clips():
    return [_clip(s, m, duration_ms=d) for s, d, m in _WITNESS]


def test_the_witness_no_longer_raises_edge_clip_on_full_bleed_frames(frames):
    """Every sampled frame of the witness is full-bleed footage. Before the fix this raised
    `MAJOR EDGE-CLIP: 4/4 checked frames` (f_001, f_003, f_025, f_028)."""
    gate = _load_gate()
    issues = gate._pixel_invariants([frames["photo"]] * 28, _witness_clips())
    assert not _edge_issues(issues), issues


def test_the_witness_frames_named_in_the_report_are_read_as_the_right_clips():
    gate = _load_gate()
    clips = _witness_clips()
    # f_003 (index 2) is 7.47 s into the master: the scene_image at 6.41-12.82 s, not the
    # video_hero the slot start (6.0 s) pointed at.
    assert gate._clips_on_screen(clips, gate._frame_time_ms(2)) == [clips[1]]
    # f_025 / f_028 sit past the authored timeline (69.24 s): the master holds the last scene.
    for idx in (24, 27):
        assert gate._clips_on_screen(clips, gate._frame_time_ms(idx)) == [clips[-1]]


def test_coverage_says_the_witness_pass_is_a_pass_by_exemption(frames):
    """12 of 12 sampled frames exempt means the edge rule observed nothing on this job. The
    result has to say so, or a PASS reads as "edges verified"."""
    gate = _load_gate()
    cov: dict = {}
    gate._pixel_invariants([frames["photo"]] * 28, _witness_clips(), coverage=cov)
    assert cov == {"sampled": 12, "checked": 0, "exempt_full_bleed": 12, "unattributed": 0,
                   "flagged": 0, "timeline": "trusted"}


# ── THE EXEMPTION, and exactly the exemption ─────────────────────────────────────────────────

def _uniform(modality, n=12):
    """One clip per 3 s slot WITH durations, so the pre-fix gate reads them too — a test here
    fails on main for its attribution, never for a fixture it could not read."""
    return [_clip(i * 3000, modality, duration_ms=3000) for i in range(n)]


@pytest.mark.parametrize("modality", ["scene_image", "video_hero", " Video_Hero "])
def test_full_bleed_modalities_are_exempt(frames, modality):
    gate = _load_gate()
    assert not _edge_issues(gate._pixel_invariants([frames["clipped"]] * 6, _uniform(modality)))


@pytest.mark.parametrize("modality", ["diagram", "chart", "motion", "physics", "card",
                                      "relimage", "video", None, ""])
def test_every_other_modality_is_still_checked(frames, modality):
    """Widening the exemption by ONE modality goes red here. `motion` is GSAP / Living Stage
    rendered text; a `relimage` picture carries a moved title and disclosure label; None and ""
    are UNKNOWN, which is never permission to skip."""
    gate = _load_gate()
    issues = _edge_issues(gate._pixel_invariants([frames["clipped"]] * 6, _uniform(modality)))
    assert issues, f"modality={modality!r} was exempted from the edge rule"


def test_a_clipped_card_between_full_bleed_beats_still_raises_major(frames):
    """THE POSITIVE CONTROL. A Veo/photo-heavy job — the witness's own shape — with two graphic
    beats whose card is cut at the edge, like canary 29355571. The two cards must still be checked
    and must still raise MAJOR, with the denominator naming only what was checked."""
    gate = _load_gate()
    clips = _witness_clips()
    clips[4]["modality"] = "diagram"    # 25.86-32.52 s holds frame 9 (28.5 s)
    clips[6]["modality"] = "motion"     # 39.19-45.04 s holds frame 14 (43.5 s)
    paths = [frames["photo"]] * 28
    paths[9] = paths[14] = frames["clipped"]
    edge = _edge_issues(gate._pixel_invariants(paths, clips))
    assert edge and edge[0]["sev"] == "MAJOR", "a real edge clip on a graphic beat was not flagged"
    assert edge[0]["msg"].startswith("EDGE-CLIP: 2/2 checked frames"), edge[0]["msg"]
    assert "10 full-bleed frame(s) exempt" in edge[0]["msg"], edge[0]["msg"]


# ── THE ATTRIBUTION RULES ────────────────────────────────────────────────────────────────────

def test_a_frame_past_the_timeline_belongs_to_the_last_clip_and_keeps_its_rule(frames):
    """The tail re-shows the last clip, so it is judged as the last clip: exempt when that clip is
    full-bleed, CHECKED when it is a card. Mapping the tail to None hid nothing; mapping it to the
    last clip must not hide a held card either."""
    gate = _load_gate()
    for last, expect_flag in (("scene_image", False), ("diagram", True)):
        clips = [_clip(0, "scene_image", duration_ms=3000), _clip(3000, last, duration_ms=3000)]
        issues = _edge_issues(gate._pixel_invariants([frames["clipped"]] * 12, clips))
        assert bool(issues) is expect_flag, (last, issues)


def test_the_intro_lead_before_the_first_clip_is_unknown(frames):
    """When the first clip starts late the master opens on a title card (text) or a black plate,
    never the first clip — so those frames are unattributed and checked."""
    gate = _load_gate()
    clips = [_clip(9000, "scene_image"), _clip(20000, "scene_image")]
    assert gate._clips_on_screen(clips, gate._frame_time_ms(2)) == []      # 7.5 s
    assert gate._clips_on_screen(clips, gate._frame_time_ms(3)) == [clips[0]]
    cov: dict = {}
    issues = gate._pixel_invariants([frames["clipped"]] * 12, clips, coverage=cov)
    assert cov["unattributed"] == 3 and cov["checked"] == 3, cov
    assert _edge_issues(issues), "the clipped lead frames were not flagged"
    # The lead ends exactly at the first start — the J-cut moves only cuts BETWEEN clips — so a
    # frame 301 ms before it is still the title card, even inside the band a cut would get.
    late_first = [_clip(7800, "video_hero"), _clip(20000, "scene_image")]
    assert gate._clips_on_screen(late_first, gate._frame_time_ms(2)) == []    # 7.5 s


def test_a_frame_just_before_a_cut_is_judged_by_both_sides(frames):
    """Cuts land EARLY: 133-200 ms on the witness, 133-367 ms on the 13.6-min 820a8a23. A frame
    401 ms before a photo -> card cut may already show the card, so it is checked."""
    gate = _load_gate()
    photo, card = _clip(0, "video_hero"), _clip(7900, "diagram")
    assert gate._clips_on_screen([photo, card], gate._frame_time_ms(2)) == [photo, card]
    assert gate._clips_on_screen([photo, card], gate._frame_time_ms(1)) == [photo]
    paths = [frames["photo"], frames["photo"], frames["clipped"], frames["clipped"]]
    edge = _edge_issues(gate._pixel_invariants(paths, [photo, card]))
    assert edge and edge[0]["msg"].startswith("EDGE-CLIP: 2/2"), edge


def test_a_pile_of_clips_on_one_start_is_a_candidate_across_the_window_before_it():
    """Several clips claiming one start_ms (690 of 8572 windows in the census) are spread by the
    min-hold floor over the PRECEDING window's tail. A card in the pile may therefore be on screen
    anywhere after the photo before it starts, so that photo's frames are not exempt."""
    gate = _load_gate()
    photo = _clip(0, "scene_image")
    pile = [_clip(12000, "diagram"), _clip(12000, "scene_image")]
    after = _clip(20000, "scene_image")
    clips = [photo, *pile, after]
    on = gate._clips_on_screen(clips, gate._frame_time_ms(2))    # 7.5 s, inside the photo's claim
    assert on == [photo, *pile], on
    on_pile = gate._clips_on_screen(clips, gate._frame_time_ms(5))   # 16.5 s
    assert on_pile == pile, on_pile
    # Without a pile the same photo window is unambiguous.
    assert gate._clips_on_screen([photo, pile[1], after], gate._frame_time_ms(2)) == [photo]


def test_a_photo_window_before_a_pile_holding_a_card_is_checked(frames):
    """The same rule end to end: the card in the pile may be on screen during the photo's
    claimed window, so those frames are judged by the edge rule rather than exempted."""
    gate = _load_gate()
    clips = [_clip(0, "scene_image"), _clip(12000, "diagram"), _clip(12000, "scene_image"),
             _clip(20000, "scene_image")]
    cov: dict = {}
    issues = gate._pixel_invariants([frames["clipped"]] * 12, clips, coverage=cov)
    # frames 0-6 (1.5-19.5 s) may show the pile's card; frames 7-11 sit after the pile.
    assert cov["checked"] == 7 and cov["exempt_full_bleed"] == 5, cov
    assert _edge_issues(issues)


@pytest.mark.parametrize("starts", [[0, 9000, 6000], [0, None, 6000], [None, None, None]])
def test_a_timeline_the_starts_cannot_describe_is_checked_everywhere(frames, starts):
    """Decreasing or missing starts make the renderer re-lay the clips by scaled durations, which
    the persisted starts do not describe (82 of 461 masters in the census). Nothing is exempt."""
    gate = _load_gate()
    clips = [_clip(s, "scene_image") for s in starts]
    cov: dict = {}
    issues = gate._pixel_invariants([frames["clipped"]] * 12, clips, coverage=cov)
    assert cov["timeline"] == "untrusted" and cov["exempt_full_bleed"] == 0, cov
    assert cov["unattributed"] == cov["checked"] == 12, cov
    assert _edge_issues(issues)


def test_a_clip_the_renderer_never_painted_holds_no_screen_time():
    """`video_assembler` drops a failed clip and one without an asset; the clip before it holds
    the screen until the next painted clip, so that is what a frame in the gap shows."""
    gate = _load_gate()
    photo, after = _clip(0, "scene_image"), _clip(9000, "scene_image")
    unpainted_clips = (_clip(3000, "diagram", status="failed"),
                       _clip(3000, "diagram", asset_uri=""))
    for unpainted in unpainted_clips:
        assert gate._clips_on_screen([photo, unpainted, after], gate._frame_time_ms(1)) == [photo]
    # A hand-built record without the key at all is kept (only persisted clips always carry it).
    bare = {"start_ms": 3000, "modality": "diagram"}
    assert gate._clips_on_screen([photo, bare, after], gate._frame_time_ms(1)) == [bare]
