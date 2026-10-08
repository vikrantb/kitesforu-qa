"""What a delivered master shows when: the stamp first, then a fallback that only ever over-checks.

Every rule in ``kitesforu_qa.harness.delivered_timeline`` has a test here that goes red when the
rule is removed. Each fallback repro is one that the qa #184 round-1 code critic, or the renderer
oracle at the bottom of this file, found exempting a frame that shows a card. The oracle runs the
renderer's own coverage fill, sort, pacing, bounds and J-cut from kitesforu-workers.

Frame instants are written out as numbers: frame k shows the master at ``3000k + 1499`` ms (see
``tests/test_the_gate_reads_each_frame_at_its_true_time.py``). Offline and $0.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

from kitesforu_qa.harness import delivered_timeline as dt
from kitesforu_qa.harness import painted_timeline_sidecar as sidecar_mod
from kitesforu_qa.harness.delivered_timeline import DeliveredTimeline, bleeds_by_design
from kitesforu_qa.harness.narration_alignment import delivered_spans
from kitesforu_qa.harness.painted_timeline_sidecar import FetchedMaster, SidecarRead, read_sidecar

VEO = {"render_mode": "video", "motion_render": "veo"}


def clip(start, modality="diagram", beat=None, **extra):
    """A persisted-shape clip: it carries ``asset_uri``, as every persisted clip does."""
    c = {"start_ms": start, "modality": modality, "asset_uri": f"gs://b/{start}.png",
         "status": "done", "duration_ms": 3000}
    if beat is not None:
        c["beat_index"] = beat
    c.update(extra)
    return c


def mods(candidates):
    return sorted({"UNKNOWN" if c is None else c.get("modality") for c in candidates})


# ── the stamp is read first ────────────────────────────────────────────────────────────────────

def stamp(windows, master_ms=20000, version=1):
    return {"version": version, "master_ms": master_ms, "windows": windows,
            "close": {"applied": False, "fade_start_ms": None, "fade_ms": None, "reason": "test"}}


def window(clip_idx, start, end, modality, *, source=None, **painted):
    w = {"clip": clip_idx, "source_clip": clip_idx if source is None else source,
         "start_ms": start, "end_ms": end, "modality": modality,
         "render_mode": None, "motion_render": None, "asset_kind": None}
    w.update(painted)
    return w


def test_a_valid_stamp_wins_over_the_persisted_claims():
    """The clips claim a diagram at 6-12 s. The stamp says a photo was painted there (coverage
    fill), so the frame at 7.5 s is the photo."""
    clips = [clip(0, "scene_image"), clip(6000, "diagram", status="failed", asset_uri=""),
             clip(12000, "scene_image")]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp([
        window(0, 0, 6000, "scene_image", asset_kind="image"),
        window(1, 6000, 12000, "scene_image", source=0, asset_kind="image"),
        window(2, 12000, 20000, "scene_image", asset_kind="image")]), master_ms=20000)
    assert (tl.source, tl.diagnosis) == ("stamp", "stamp")
    assert tl.full_bleed_at(7499) is True
    assert tl.spans_by_clip() == {0: (0, 6000), 1: (6000, 12000), 2: (12000, 20000)}


def test_a_stamped_video_hero_needs_video_evidence_in_the_stamp():
    clips = [clip(0, "video_hero")]
    painted_still = stamp([window(0, 0, 20000, "video_hero", asset_kind="image",
                                  render_mode="still")])
    painted_video = stamp([window(0, 0, 20000, "video_hero", asset_kind="video")])
    assert DeliveredTimeline.from_clips(clips, stamp=painted_still,
                                        master_ms=20000).full_bleed_at(4499) is False
    assert DeliveredTimeline.from_clips(clips, stamp=painted_video,
                                        master_ms=20000).full_bleed_at(4499) is True


@pytest.mark.parametrize("bad, reason", [
    (stamp([window(0, 0, 20000, "scene_image")], version=2), "version=2"),
    (stamp([window(0, 0, 30000, "scene_image")], master_ms=30000), "windows end 30000 != probed video 20000"),
    (stamp([window(0, 0, 18500, "scene_image")], master_ms=18500), "windows end 18500 != probed video 20000"),
    (stamp([window(5, 0, 20000, "scene_image")]), "malformed_window"),
    (stamp([window(0, 9000, 9000, "scene_image")]), "malformed_window"),
    (stamp([7]), "malformed_window"),
    (stamp([]), "no_windows"),
    ("not a stamp", "version=None"),
])
def test_a_stamp_that_cannot_be_trusted_is_replaced_by_the_estimate_and_says_why(bad, reason):
    clips = [clip(0, "diagram")]
    tl = DeliveredTimeline.from_clips(clips, stamp=bad, master_ms=20000)
    assert tl.source == "estimated" and reason in (tl.stamp_rejected or ""), tl.stamp_rejected
    assert tl.full_bleed_at(4499) is False      # the estimate read the diagram claim


def test_a_video_short_of_its_master_keeps_its_stamp():
    """The producer refuses the close with `video_short_of_master` when the video ends before the
    audio. The stamp's `master_ms` is the AUDIO span, so it is never compared with the probed video:
    the windows end where the video ends, and the gate probes the video stream."""
    clips = [clip(0, "scene_image"), clip(1, "diagram")]
    short = stamp([window(0, 0, 9000, "scene_image"), window(1, 9000, 20000, "diagram")],
                  master_ms=27000)
    short["close"] = {"applied": False, "fade_start_ms": None, "fade_ms": None,
                      "reason": "video_short_of_master"}
    tl = DeliveredTimeline.from_clips(clips, stamp=short, master_ms=20000)
    assert (tl.source, tl.stamp_rejected) == ("stamp", None)
    assert tl.full_bleed_at(4499) is True and tl.full_bleed_at(13499) is False


# ── the master the stamp names ─────────────────────────────────────────────────────────────────

def named(generation=111, size=2048, *, windows=None, **fields):
    """A stamp naming its master: the uploaded blob's generation and size (#3257's optional v1 fields)."""
    st = stamp(windows or [window(0, 0, 9000, "scene_image"), window(1, 9000, 20000, "diagram")])
    st.update(master_generation=generation, master_size=size, **fields)
    return st


TWO_CLIPS = [clip(0, "scene_image", 0), clip(9000, "diagram", 1)]


@pytest.mark.parametrize("generation", [111, "111", " 111 "])
def test_a_stamp_that_names_the_fetched_master_is_read(generation):
    """``master_generation`` is ``int | str | None`` in the contract (#3257 keeps a string as it got
    one), and ``x-goog-generation`` is the same number in decimal."""
    tl = DeliveredTimeline.from_clips(TWO_CLIPS, stamp=named(generation), master_ms=20000,
                                      master=FetchedMaster(111, 2048))
    assert (tl.source, tl.stamp_rejected) == ("stamp", None)


@pytest.mark.parametrize("st, fetched", [
    (named(), FetchedMaster(112, 2048)),
    (named(), FetchedMaster(111, 2049)),
    (named("112"), FetchedMaster(111, 2048)),
    (named("not-a-generation"), FetchedMaster(111, 2048)),
    # Only ONE field on a side is still compared: a size that differs is another object.
    (named(), FetchedMaster(None, 4096)),           # a GET without x-goog-generation, wrong size
    (named(generation=None), FetchedMaster(111, 4096)),
    (named(size=None), FetchedMaster(112, None)),   # a size not known, a generation that differs
])
def test_a_stamp_that_names_another_master_is_stale(st, fetched):
    """A pass that uploads a new master and dies before its sidecar leaves the old sidecar at the same
    URL. Over the same audio the new master has the same length, so only the object can tell. Each
    identity field that BOTH sides carry is compared on its own (round-2 code critic: a 4096-byte
    file against a stamp naming 9999 bytes was accepted because no generation was known)."""
    tl = DeliveredTimeline.from_clips(TWO_CLIPS, stamp=st, master_ms=20000, master=fetched)
    assert (tl.source, tl.stamp_rejected) == ("estimated", "stale_master")
    assert tl.full_bleed_at(4499) is True and tl.full_bleed_at(10499) is False   # the estimate


@pytest.mark.parametrize("st, fetched, identity", [
    (stamp([window(0, 0, 9000, "scene_image"), window(1, 9000, 20000, "diagram")]),
     FetchedMaster(999, 1), "unchecked"),                      # a sidecar written before the fields
    (named(), None, "unchecked"),                              # nothing fetched to compare with
    (named(generation=None, size=None), FetchedMaster(111, 2048), "unchecked"),
    (named(generation="  ", size=None), FetchedMaster(999, 1), "unchecked"),  # blank is no generation
    (named(size=None), FetchedMaster(None, 2048), "unchecked"),  # no field on BOTH sides
    (named(), FetchedMaster(None, 2048), "verified"),          # the size agrees, no generation known
    (named(size=None), FetchedMaster(111, 1), "verified"),     # the generation agrees
    (named(), FetchedMaster(111, 2048), "verified"),
])
def test_with_nothing_to_contradict_the_length_check_decides(st, fetched, identity):
    """No field disagrees, so the windows' end against the probed video decides, and the timeline
    says whether the object itself was compared (``master_identity``)."""
    tl = DeliveredTimeline.from_clips(TWO_CLIPS, stamp=st, master_ms=20000, master=fetched)
    assert (tl.source, tl.stamp_rejected, tl.master_identity) == ("stamp", None, identity)
    stale = DeliveredTimeline.from_clips(TWO_CLIPS, stamp=st, master_ms=30000, master=fetched)
    assert stale.stamp_rejected == "windows end 20000 != probed video 30000"


@pytest.mark.parametrize("master_ms", [None, 0, 0.0])
def test_a_stamp_tied_to_nothing_is_not_used(master_ms):
    """No probed length (a probe that returned 0 s is no length) and no identity compared: nothing
    ties the stamp to this video, so it is not used, and the timeline says why (round-2 code critic:
    a falsy `master_ms` skipped the length check and trusted the stamp unchecked)."""
    for st, fetched in ((named(), None), (named(), FetchedMaster(None, None)),
                        (named(generation=None, size=None), FetchedMaster(111, 2048))):
        tl = DeliveredTimeline.from_clips(TWO_CLIPS, stamp=st, master_ms=master_ms, master=fetched)
        assert (tl.source, tl.stamp_rejected) == ("estimated", dt.UNTIED), (st, fetched)


@pytest.mark.parametrize("master_ms", [None, 0])
def test_a_verified_identity_ties_a_stamp_whose_length_is_unknown(master_ms):
    tl = DeliveredTimeline.from_clips(TWO_CLIPS, stamp=named(), master_ms=master_ms,
                                      master=FetchedMaster(111, 2048))
    assert (tl.source, tl.stamp_rejected, tl.master_identity) == ("stamp", None, "verified")


def test_the_named_master_does_not_excuse_the_length_check():
    """The object matches, but the windows end 10 s short of the probed video: the stamp is wrong
    about this video either way."""
    tl = DeliveredTimeline.from_clips(TWO_CLIPS, stamp=named(), master_ms=30000,
                                      master=FetchedMaster(111, 2048))
    assert tl.stamp_rejected == "windows end 20000 != probed video 30000"
def test_a_gap_in_the_stamp_and_the_intro_before_it_are_unknown():
    clips = [clip(0, "scene_image"), clip(1, "scene_image")]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp([
        window(0, 3000, 6000, "scene_image"), window(1, 9000, 20000, "scene_image")]), master_ms=20000)
    assert tl.candidates_at(1499) == []         # before the first painted window: the lead
    assert tl.candidates_at(7499) == []         # 1.5 s clear of both windows
    assert tl.full_bleed_at(10499) is True


def test_a_stamp_reports_one_envelope_per_clip_and_one_window_per_painted_span():
    clips = [clip(0, "scene_image"), clip(1, "diagram")]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp([
        window(0, 0, 4000, "scene_image"), window(1, 4000, 9000, "diagram"),
        window(0, 9000, 20000, "scene_image")]), master_ms=20000)
    assert tl.spans_by_clip() == {0: (0, 20000), 1: (4000, 9000)}
    assert [(w.start_ms, w.end_ms) for w in tl.painted_windows()] == [
        (0, 4000), (4000, 9000), (9000, 20000)]


# Two stamps in the producer's shape: built OFFLINE by kitesforu-workers #3257 (022ffae383a3)
# `painted_timeline.build_painted_timeline` from a clip table (the witness f7df77bf's own, for
# `witness`), and saved under tests/fixtures/. Neither is the stamp of a real render: the witness
# master predates the producer by three hours. `master_generation` / `master_size` were added by
# hand, the witness's from a GET of its master.
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def produced(name):
    return json.loads((FIXTURES / f"painted_timeline_v1_{name}.json").read_text())


URI = "gs://kitesforu-dev-podcasts/visuals/f7df77bf-5f6e-4862-ae2e-5446270d5f1d/painted_timeline.json"


def sidecar_of(name):
    """A vendored producer stamp, read as the sidecar a doc names. ``json.loads`` stands in for
    workers' ``parse_v1`` here (both take the body bytes); the golden-sidecar tests at the bottom of
    this file run the real one."""
    body = (FIXTURES / f"painted_timeline_v1_{name}.json").read_bytes()
    return read_sidecar({"visual": {"painted_timeline_uri": URI}}, fetch=lambda url: body,
                        parse=json.loads)


def test_the_producers_witness_stamp_exempts_every_full_bleed_frame():
    """f7df77bf as the assembler stamps it: 11 traced windows, the J-cut-led boundaries, the tail hold
    to 85033 ms and an applied close. Every sampled frame is a picture with video evidence."""
    clips = [clip(s, m, i, duration_ms=d, asset_uri=f"gs://b/{i}.mp4", render_mode="video",
                  motion_render=mr)
             for i, (s, d, m, mr) in enumerate(
                 [(0, 6412, "video_hero", "veo"), (6412, 6412, "scene_image", "kenburns"),
                  (12824, 6517, "video_hero", "veo"), (19341, 6517, "scene_image", "kenburns"),
                  (25858, 6666, "scene_image", "kenburns"), (32524, 6666, "scene_image", "kenburns"),
                  (39190, 5846, "scene_image", "kenburns"), (45036, 5845, "scene_image", "kenburns"),
                  (50881, 6576, "scene_image", "kenburns"), (57457, 6575, "scene_image", "kenburns"),
                  (64032, 5208, "scene_image", "kenburns")])]
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": clips, "painted_timeline_uri": URI}}
    read = sidecar_of("witness")
    tl = DeliveredTimeline.from_job(doc, master_ms=85033, sidecar=read)
    assert (tl.source, tl.diagnosis, tl.stamp_rejected) == ("stamp", "stamp", None)
    assert tl.sidecar_bytes == read.bytes_read == (FIXTURES / "painted_timeline_v1_witness.json").stat().st_size
    for k in (0, 2, 4, 7, 9, 12, 14, 17, 19, 22, 24, 27):     # `_sample_indices(28)`
        assert tl.full_bleed_at(3000 * k + 1499) is True, k
    assert tl.spans_by_clip()[10] == (63912, 85033)


def test_the_producers_stamp_with_a_lead_a_fill_and_an_untraced_row():
    """A title-card lead (no window), a coverage-filled row painting its donor's picture, a window the
    producer could not trace (`clip` -1), a Veo clip, a reclaimed `video_hero` still, and a refused
    close (`portrait_canvas`)."""
    stamp = produced("coverage_untraced")
    clips = [clip(0, "scene_image", 0), clip(4000, "diagram", 1),
             clip(9000, "scene_image", 2, status="failed", asset_uri=""),
             clip(16000, "video_hero", 3, asset_uri="gs://b/veo_0/clip.mp4", **VEO),
             clip(20000, "video_hero", 4, render_mode="still", asset_uri="gs://b/title_band.png"),
             clip(24000, "scene_image", 5)]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp, master_ms=27000)
    assert tl.source == "stamp"
    assert tl.candidates_at(1499) == []                      # the title card: no clip
    assert tl.full_bleed_at(4499) is True                    # clip 0's picture
    assert tl.full_bleed_at(13499) is True                   # clip 2, painted with clip 0's picture
    assert tl.candidates_at(17499) == [None]                 # untraced: whose pixels is unknown
    assert tl.full_bleed_at(20499) is True                   # Veo
    assert tl.full_bleed_at(25499) is False                  # a reclaimed still: no video evidence
    assert -1 not in tl.spans_by_clip() and tl.spans_by_clip()[2] == (12000, 16000)
    assert [w.clip for w in tl.painted_windows()] == [0, 1, 2, -1, 3, 4]


def test_a_stale_source_index_makes_the_window_unknown():
    """`clip` indexes the array the assembler was handed; a racing pass can re-persist `visual.clips`
    after the mux. When the source row no longer has the window's modality, its pixels are unknown."""
    stamp = produced("coverage_untraced")
    clips = [clip(0, "diagram", 0), clip(4000, "diagram", 1),
             clip(9000, "scene_image", 2, status="failed", asset_uri=""),
             clip(16000, "video_hero", 3, **VEO), clip(20000, "video_hero", 4), clip(24000, "scene_image", 5)]
    tl = DeliveredTimeline.from_clips(clips, stamp=stamp, master_ms=27000)
    assert tl.candidates_at(4499) == [None] and tl.candidates_at(13499) == [None]


def _untraced_clips():
    return [clip(0, "scene_image", 0), clip(4000, "diagram", 1),
            clip(9000, "scene_image", 2, status="failed", asset_uri=""),
            clip(16000, "video_hero", 3, asset_uri="gs://b/veo_0/clip.mp4", **VEO),
            clip(20000, "video_hero", 4, render_mode="still", asset_uri="gs://b/title_band.png"),
            clip(24000, "scene_image", 5)]


def test_parse_v1s_typed_model_reads_exactly_like_its_json():
    """`parse_v1` returns a typed model, not a dict, so every contract field is also read as an
    attribute. The same stamp as attributes yields the same windows."""
    from types import SimpleNamespace as Model

    raw = produced("coverage_untraced")
    typed = Model(**{**raw, "windows": tuple(Model(**w) for w in raw["windows"]),
                     "close": Model(**raw["close"])})
    as_json = DeliveredTimeline.from_clips(_untraced_clips(), stamp=raw, master_ms=27000)
    as_model = DeliveredTimeline.from_clips(_untraced_clips(), stamp=typed, master_ms=27000)
    assert (as_model.source, as_model.stamp_rejected) == ("stamp", None)
    assert as_model.windows == as_json.windows and len(as_model.windows) == 6


# ── the sidecar is the only channel, and an unread one is an estimate that says why ───────────

@pytest.mark.parametrize("rejected, expect", [
    (None, None),
    ("parser_unavailable: FileNotFoundError: /x/kitesforu-workers has no origin/main:src/w.py",
     "NO PARSER: this job names the producer's painted timeline"),
    ("stale_master", "was not used (stale_master)"),
    (dt.UNTIED, f"was not used ({dt.UNTIED})"),
    ("fetch_failed: HTTPError: 404", "was not used (fetch_failed: HTTPError: 404)"),
])
def test_every_reader_prints_one_line_for_a_stamp_it_could_not_use(rejected, expect):
    note = dt.stamp_note(rejected)
    assert (note is None) if expect is None else (expect in note), note
    if rejected and rejected.startswith("parser_unavailable"):
        assert "WORKERS_SRC" in note and "WORKERS_REPO" in note      # it says how to fix the setup


def test_from_job_reads_the_sidecar_the_doc_names(monkeypatch):
    """No caller passes the sidecar in production: `from_job` reads it. Pinned with the read
    stubbed, so the test spends nothing."""
    seen = []

    def read(doc):
        seen.append(doc["visual"]["painted_timeline_uri"])
        return sidecar_of("coverage_untraced")

    monkeypatch.setattr(dt, "read_sidecar", read)
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": _untraced_clips(), "painted_timeline_uri": URI}}
    tl = DeliveredTimeline.from_job(doc, master_ms=27000)
    assert seen == [URI] and (tl.source, tl.stamp_rejected) == ("stamp", None)
    assert tl.candidates_at(17499) == [None]                 # the untraced window, from the stamp


def test_an_inline_timeline_on_the_doc_is_not_read():
    """`visual.painted_timeline` went away with the sidecar. A copy left on a doc is not a second
    channel: with no sidecar named, the clips are estimated."""
    clips = [clip(0, "scene_image", 0), clip(9000, "diagram", 1)]
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": clips,
                      "painted_timeline": stamp([window(0, 0, 20000, "scene_image")])}}
    tl = DeliveredTimeline.from_job(doc, master_ms=20000)
    assert (tl.source, tl.stamp_rejected, tl.sidecar_bytes) == ("estimated", None, 0)
    assert tl.full_bleed_at(10499) is False                  # the diagram claim


@pytest.mark.parametrize("read, why", [
    (SidecarRead("parser_unavailable", URI, detail="ModuleNotFoundError: No module named 'workers'"),
     "parser_unavailable: ModuleNotFoundError"),
    (SidecarRead("uri_unresolvable", "visuals/j/painted_timeline.json",
                 detail="visuals/j/painted_timeline.json"), "uri_unresolvable"),
    (SidecarRead("fetch_failed", URI, detail="HTTPError: 404"), "fetch_failed: HTTPError: 404"),
    (SidecarRead("parse_failed", URI, bytes_read=812, detail="ValueError: v2"),
     "parse_failed: ValueError: v2"),
])
def test_an_unread_sidecar_leaves_an_estimate_that_names_the_failed_step(read, why):
    clips = [clip(0, "scene_image", 0), clip(9000, "diagram", 1)]
    doc = {"master_segment_timeline": [{"index": 0}],
           "visual": {"clips": clips, "painted_timeline_uri": URI}}
    tl = DeliveredTimeline.from_job(doc, master_ms=20000, sidecar=read)
    assert tl.source == "estimated" and (tl.stamp_rejected or "").startswith(why), tl.stamp_rejected
    assert tl.sidecar_bytes == read.bytes_read
    assert tl.full_bleed_at(4499) is True and tl.full_bleed_at(10499) is False


# ── the fallback: each repro that once exempted a card ─────────────────────────────────────────

def test_a_failed_row_shows_a_neighbours_asset_so_it_is_unknown():
    """Code critic #2 (coverage carry-forward): `fill_coverage_gaps` re-points failed rows at the
    nearest rendered neighbour, which may be the NEXT one. The renderer paints the diagram from
    beat 5 at 13.5 s; the old gate exempted that frame as the video_hero at 3 s."""
    clips = [clip(0, "diagram", 0), clip(3000, "video_hero", 1, **VEO),
             clip(6000, "diagram", 2, status="failed", asset_uri=""),
             clip(9000, "diagram", 3, status="failed", asset_uri=""),
             clip(12000, "diagram", 4, status="failed", asset_uri=""), clip(15000, "diagram", 5)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=18000)
    assert None in tl.candidates_at(13499)
    assert tl.full_bleed_at(13499) is None
    assert tl.full_bleed_at(4499) is True        # the Veo beat itself is still attributable


def test_a_clip_dropped_by_the_strobe_guard_shows_the_one_before_it():
    """Code critic #2, repro A. `coalesce_strobe` drops the photo that starts 1000 ms after the
    diagram, and the diagram holds 9-13 s, so the frame at 10.5 s is a card."""
    clips = [clip(0, "scene_image", 0, duration_ms=9000), clip(9000, "diagram", 1, duration_ms=1000),
             clip(10000, "scene_image", 2), clip(13000, "scene_image", 3)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=16000)
    assert "diagram" in mods(tl.candidates_at(10499))
    assert tl.full_bleed_at(10499) is False


def test_a_collapsed_card_may_hold_the_long_window_after_it():
    """Code critic #2, repro B: the diagram's 300 ms window is collapsed. Depending on the pass
    that wins, it is funded into the photo before it OR holds 12-20 s after the strobe guard
    drops the photo at 12.3 s. Frames 4, 5 and 6 may all be the card."""
    clips = [clip(0, "scene_image", 0, duration_ms=12000), clip(12000, "diagram", 1, duration_ms=300),
             clip(12300, "scene_image", 2, duration_ms=7700), clip(20000, "scene_image", 3)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=23000)
    for t in (13499, 16499, 19499):
        assert tl.full_bleed_at(t) is False, t
    assert tl.full_bleed_at(22499) is True


def test_a_collapsed_run_is_funded_from_the_anchor_before_the_whole_run():
    """Renderer-oracle find: four collapsed rows in a row (7000-7800) are funded from the photo at
    0, and the oracle painted the motion clip at 3.2 s. The previous distinct start (7000) is
    itself collapsed, so funding reaches back to the anchor before the run."""
    clips = [clip(0, "scene_image", 0, duration_ms=7000), clip(7000, "video_hero", 1, **VEO),
             clip(7400, "motion", 2), clip(7800, "video_hero", 3, **VEO),
             clip(7800, "video_hero", 4, **VEO), clip(8000, "motion", 5),
             clip(12000, "video_hero", 6, **VEO)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=30000)
    assert "motion" in mods(tl.candidates_at(4499))


def test_a_funded_member_can_land_on_the_runs_right_edge_and_hold_from_there():
    """Renderer-oracle find: the second diagram of the pile at 6900 collapses to the run's right
    edge (7300), wins that start, and the strobe guard keeps it until 12.6 s."""
    clips = [clip(0, "scene_image", 0), clip(2900, "motion", 1, duration_ms=1000),
             clip(6900, "diagram", 2), clip(6900, "diagram", 3, duration_ms=6000),
             clip(7300, "video_hero", 4, duration_ms=1000, **VEO),
             clip(8600, "video_hero", 5, **VEO),
             clip(12600, "motion", 6, status="failed", duration_ms=6000),
             clip(19600, "scene_image", 7)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=42100)
    assert "diagram" in mods(tl.candidates_at(10499))


def test_the_strobe_fold_cascades_to_the_clip_kept_before():
    """Renderer-oracle find (seed 7, trial 2648, exact). The last clip dwells 1000 ms, so the strobe
    guard's final-kept fold pops it, and with it the video_hero kept 200 ms before. The diagram at
    7 s then holds to the end of the master."""
    gsap = {"render_mode": "video", "motion_render": "gsap"}
    clips = [clip(0, "motion", 0, status="failed", duration_ms=0, **gsap),
             clip(7000, "diagram", 1, duration_ms=6000),
             clip(14000, "video_hero", 2, duration_ms=6000, **VEO),
             clip(14200, "video_hero", 3, duration_ms=1000, **VEO)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=17200)
    assert "diagram" in mods(tl.candidates_at(16499))


def test_the_strobe_fold_reads_the_master_span():
    """Renderer-oracle find (seed 31, trial 2286, exact). The last clip has no persisted duration,
    but once the refloor re-lays this timeline against the 11 s master the last window is 1000 ms.
    It is folded, and the motion clip holds to the end."""
    gsap = {"render_mode": "video", "motion_render": "gsap"}
    clips = [clip(0, "video_hero", 0, duration_ms=6000, **VEO),
             clip(400, "video_hero", 1, status="failed", duration_ms=3000, **VEO),
             clip(800, "diagram", 2, duration_ms=0), clip(800, "diagram", 3, duration_ms=3000),
             clip(4800, "scene_image", 4, duration_ms=0), clip(5000, "diagram", 5, duration_ms=0),
             clip(7500, "motion", 6, duration_ms=3000, **gsap),
             clip(10000, "video_hero", 7, duration_ms=0, **VEO)]
    assert "motion" in mods(DeliveredTimeline.from_clips(clips, master_ms=11000).candidates_at(10499))
    # The same claims under a long master leave a full dwell after the last start: no fold.
    long_master = DeliveredTimeline.from_clips(clips, master_ms=40000)
    assert mods(long_master.candidates_at(13499)) == ["video_hero"]


# ── trust: what the persisted starts can and cannot say ────────────────────────────────────────

def test_a_legacy_timeline_is_untrusted():
    """Code critic #3: with no `master_segment_timeline` the worker passes `real_offsets=False`.
    The master then lays clips from 0 and drops the first start, so the claims do not hold."""
    doc = {"visual": {"clips": [clip(5000, "scene_image", 0), clip(9000, "scene_image", 1)]}}
    tl = DeliveredTimeline.from_job(doc, master_ms=20000)
    assert tl.diagnosis == "legacy" and tl.candidates_at(10499) == []
    doc["master_segment_timeline"] = [{"index": 0, "start_ms": 5000, "end_ms": 20000}]
    assert DeliveredTimeline.from_job(doc, master_ms=20000).full_bleed_at(10499) is True


def test_monotonicity_is_judged_in_the_renderers_sort_order():
    """Code critic #4: the renderer sorts by (beat_index, start_ms) before `resolve_bounds`. These
    starts never decrease in array order, but beat 1 claims 6 s and beat 2 claims 3 s."""
    clips = [clip(0, "scene_image", 0), clip(3000, "scene_image", 2), clip(6000, "scene_image", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "untrusted_decreasing" and tl.candidates_at(7499) == []


def test_mixed_anchoring_keeps_the_anchored_clips_attributable():
    """Design #3 and claims #1: `resolve_bounds`' mixed-anchoring branch keeps every anchored start
    and puts an unanchored clip zero-width at the NEXT anchored start. It does not re-lay the clips
    by duration. So the anchored photo is still attributable, and the unanchored card is a
    candidate wherever the floor may fund it, which is the photo window before 6 s."""
    clips = [clip(0, "scene_image", 0, duration_ms=6000), clip(None, "diagram", 1),
             clip(6000, "scene_image", 2), clip(12000, "scene_image", 3),
             clip(18000, "scene_image", 4)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=30000)
    assert tl.diagnosis == "mixed_anchored"
    assert "diagram" in mods(tl.candidates_at(4499))
    # The card may be funded anywhere from 0, may collapse onto the run's right edge (12 s) and
    # hold one dwell from there, so it is ruled out only once the strobe guard keeps 18 s.
    assert "diagram" in mods(tl.candidates_at(16499))
    assert tl.full_bleed_at(22499) is True


def test_no_numeric_start_at_all_is_untrusted():
    clips = [clip(None, "scene_image", 0), clip(None, "scene_image", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "untrusted_unanchored" and tl.candidates_at(4499) == []


def test_a_boolean_start_is_untrusted_not_read_as_one_millisecond():
    clips = [clip(True, "scene_image", 0), clip(3000, "scene_image", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "untrusted_bool_start" and tl.candidates_at(4499) == []


def test_no_renderable_clip_is_its_own_diagnosis():
    """Code critic #5: every row failed. Assembly degrades to audio-only or last-resort cards,
    which is a different finding from an untrusted timeline."""
    clips = [clip(0, "scene_image", 0, status="failed"), clip(3000, "scene_image", 1, asset_uri="")]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert tl.diagnosis == "no_renderable" and tl.candidates_at(4499) == []


def test_no_clips_at_all_is_absent_not_untrusted():
    for clips in ([], None):
        tl = DeliveredTimeline.from_clips(clips)
        assert tl.diagnosis == "absent" and tl.candidates_at(4499) == []


def test_a_hand_built_row_without_an_asset_key_is_read_by_its_modality():
    """Every persisted clip carries `asset_uri` (`VisualClip` defaults it to ""), so only a
    hand-built record lacks the key. An EMPTY value means not rendered and is unknown."""
    keyless = [{"start_ms": 0, "modality": "scene_image"}, {"start_ms": 3000, "modality": "scene_image"}]
    assert DeliveredTimeline.from_clips(keyless, master_ms=20000).full_bleed_at(4499) is True
    empty = [dict(c, asset_uri="") for c in keyless]
    assert DeliveredTimeline.from_clips(empty, master_ms=20000).diagnosis == "no_renderable"


# ── the bands around a cut ─────────────────────────────────────────────────────────────────────

def test_a_frame_just_before_a_cut_may_already_show_the_next_clip():
    """Cuts land up to 367 ms early (17 cuts, two 16:9 masters). A frame 401 ms before a
    photo -> card cut is judged by both sides."""
    clips = [clip(0, "video_hero", 0, **VEO), clip(7900, "diagram", 1)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert mods(tl.candidates_at(7499)) == ["diagram", "video_hero"]
    assert mods(tl.candidates_at(4499)) == ["video_hero"]


def test_a_frame_just_after_a_cut_may_still_show_the_clip_before():
    """Code critic #5: `CUT_LATE_MS = 0` left every test green. A frame 99 ms after a card ->
    photo cut keeps the card as a candidate; 101 ms after, it does not."""
    clips = [clip(0, "diagram", 0, duration_ms=7400), clip(7400, "scene_image", 1)]
    late = DeliveredTimeline.from_clips(clips, master_ms=20000)
    assert "diagram" in mods(late.candidates_at(7499))
    clips[1]["start_ms"] = 7398
    assert mods(DeliveredTimeline.from_clips(clips, master_ms=20000).candidates_at(7499)) == [
        "scene_image"]


def test_the_intro_lead_stays_unknown_into_the_late_band():
    """Code critic #5: dropping `+ CUT_LATE_MS` from the intro guard left every test green. The
    lead ends at exactly the first start; a frame 99 ms after it is still unknown."""
    clips = [clip(7400, "scene_image", 0), clip(20000, "scene_image", 1)]
    assert DeliveredTimeline.from_clips(clips, master_ms=30000).candidates_at(7499) == []
    clips[0]["start_ms"] = 7398
    assert DeliveredTimeline.from_clips(clips, master_ms=30000).full_bleed_at(7499) is True


# ── what counts as full-bleed ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row, expected", [
    ({"modality": "scene_image"}, True),
    ({"modality": " Scene_Image "}, True),
    ({"modality": "video_hero", "render_mode": "still", "asset_uri": "gs://b/j/title_band_3.png"},
     False),
    ({"modality": "video_hero"}, False),
    ({"modality": "video_hero", "render_mode": "video"}, True),
    ({"modality": "video_hero", "motion_render": "veo"}, True),
    ({"modality": "video_hero", "asset_uri": "gs://b/veo_0/clip.MP4?x=1"}, True),
    ({"modality": "video_hero", "asset_kind": "video"}, True),
    ({"modality": "diagram", "render_mode": "video", "motion_render": "veo"}, False),
    ({"modality": "motion"}, False),
    ({"modality": None}, False),
    (None, False),
])
def test_full_bleed_is_scene_image_or_video_hero_with_veo_evidence(row, expected):
    """Code critic #1: a reclaimed still stamped `video_hero` (a flowchart, a title band) is drawn
    text, so `video_hero` is exempt only with evidence that video was painted."""
    assert bleeds_by_design(row) is expected


#: Real persisted rows whose MODALITY is ``diagram`` while their kind is a picture kind
#: (``diagram_debug.kind == "scene_image"``), read from Firestore 2026-10-08. Widening
#: ``FULL_BLEED_MODALITIES`` alone (sabotage arm E1) exempts every such row, because
#: ``has_picture_pixels`` decides by kind. The round-2 claims lens counted 40 such rows in 9 of 461
#: delivered jobs. One frame from each of five of them (seeked over HTTPS at the row's mid-window)
#: shows 3 photographs and 2 drawn renders, so the kind cannot vouch for the pixels:
#:   * a9dc0a4f row 56 (parallax_2_5d, "weave:adjacent_diagram→scene"): a four-step diagram the
#:     parallax pushes off the right edge, card 3 cut at 331.0 s. Its frame is the gate's fixture
#:     ``tests/fixtures/edge_witness_a9dc0a4f_331s.png``;
#:   * 33ae4811 row 10 (``motion_render`` card_gsap): a text card.
#: The renderer's ``_is_pictorial`` crop-fills these rows (workers ``video_assembler.py``), which is
#: exactly how a labelled figure gets cut, so they stay CHECKED. Exempting them is a decision for
#: the producer's stamp to make, not for a stale kind.
DIAGRAM_ROWS_WITH_A_PICTURE_KIND = {
    "a9dc0a4f_row56": {
        "start_ms": 326057, "duration_ms": 6294, "modality": "diagram",
        "diagram_debug": {"kind": "scene_image"}, "render_mode": "parallax_2_5d",
        "modality_reasons": ["scenario_tailoring.modality_policy=authored_diagrams",
                             "rule3:diagram_spec_present", "weave:adjacent_diagram→scene",
                             "imagination:director_scene", "editorial_director:reveal_inherit(diagram)",
                             "carried_through_render_mode_upgrade:parallax_2_5d"],
        "asset_uri": "gs://b/a9dc0a4f/72a97591ec8add79dca0_63600ms_dav2-small-fp16-v1_parallax.mp4",
        "status": "done"},
    "33ae4811_row10": {
        "start_ms": 39473, "duration_ms": 4877, "modality": "diagram",
        "diagram_debug": {"kind": "scene_image"}, "render_mode": "video", "motion_render": "card_gsap",
        "asset_uri": "gs://b/33ae4811/35513fc2d3617e019aeb8979_5323ms.mp4", "status": "done"},
    "motion_with_a_library_demote": {
        "modality": "motion", "modality_reasons": ["demote→library_image:cc0"],
        "asset_uri": "gs://b/m.png", "status": "done"},
}


@pytest.mark.parametrize("name", sorted(DIAGRAM_ROWS_WITH_A_PICTURE_KIND))
def test_a_drawn_modality_with_a_picture_kind_stays_checked(name):
    """The pin the round-2 claims lens asked for: E1 ("widen FULL_BLEED_MODALITIES alone") was called
    an equivalent mutant, and it is not. It flips these rows from checked to exempt; this goes red."""
    row = DIAGRAM_ROWS_WITH_A_PICTURE_KIND[name]
    assert dt.has_picture_pixels(row) is True        # PREMISE: the kind (or demote) says picture
    assert bleeds_by_design(row) is False
    tl = DeliveredTimeline.from_clips([dict(row, start_ms=0, duration_ms=6000)], master_ms=6000)
    assert tl.full_bleed_at(1499) is False


# ── a full-bleed modality that carries DRAWN TEXT is checked ────────────────────────────────────

PHOTO = {"modality": "scene_image", "asset_uri": "gs://b/p.mp4", "status": "done"}


@pytest.mark.parametrize("row, expected", [
    (dict(PHOTO), True),
    (dict(PHOTO, diagram_debug={"kind": "photo_statement"}), False),
    (dict(PHOTO, modality_reasons=["demote→photo_statement:cc_by($0)"]), False),
    (dict(PHOTO, diagram_debug={"kind": "relimage"}), False),
    (dict(PHOTO, modality_reasons=["demote→library_image:embed_only($0)"]), False),
    (dict(PHOTO, diagram_debug={"kind": "library_photo"},
          modality_reasons=["demote→library_image:cc0($0)"]), True),
    (dict(PHOTO, card_spec={"title": "x"}), False),
    # `animatable` would call this a picture; the crop rule (`has_picture_pixels`) fails closed on a
    # kind that is not a picture kind, and so does the gate: 4f6da264's `mermaid_ext` "scene_image"
    # is a labelled diagram on screen.
    (dict(PHOTO, diagram_debug={"kind": "key_term_highlight"}), False),
    (dict(PHOTO, diagram_debug={"kind": "mermaid_ext"}), False),
    (dict(PHOTO, modality_reasons=["aptness_fail→reframe"]), False),
    (dict(PHOTO, modality_reasons=["reframed_from:h1"]), False),
    (dict(PHOTO, modality_reasons=["reframed_from:h1", "reframed_from_beat:4"]), True),
    (dict(PHOTO, modality="video_hero", diagram_debug={"kind": "relimage"}, **VEO), False),
])
def test_a_picture_with_drawn_text_is_not_full_bleed(row, expected):
    """`animatable.is_animatable_still` is the renderer's own answer to "a picture with no burned-in
    text": a photo statement (its kind or its demote reason), a relimage band, a library photo whose
    licence burns a credit (`embed_only`), a card or figure spec. Found on real pixels: 072c32c4's
    last clip is a `scene_image` photo statement whose line drifts to 12 px from the left edge."""
    assert bleeds_by_design(row) is expected


def test_an_unvouched_reframe_crop_is_checked():
    """Five delivered frames head once exempted show labels torn at the edge: each is a reframe
    crop (`aptness_fail→reframe` / `prompt_blocked→reframe`) stamped `scene_image` with no
    `reframed_from_beat`, i.e. cut before the whole-root rule from a source nobody vouched for
    (038d4717 f_093, 1f6dd462 f_011, 3c3856d4 f_001, 5d0bc058 f_002, 726487d1 f_007)."""
    row = dict(PHOTO, diagram_debug={"kind": "scene_image"}, ai_generated=True,
               render_mode="parallax_2_5d", modality_reasons=["prompt_blocked→reframe"])
    assert dt.is_unvouched_crop(row) and bleeds_by_design(row) is False
    vouched = dict(row, modality_reasons=["reframed_from:abc", "reframed_from_beat:3"])
    assert not dt.is_unvouched_crop(vouched) and bleeds_by_design(vouched) is True


def test_picture_pixels_match_the_renderers_own_predicate():
    """The mirror against `render_contract.has_picture_pixels` itself. SKIPS without workers."""
    _renderer()
    contract = importlib.import_module("workers.stages.visuals.render_contract")
    for kind in (None, "scene_image", "image", "library_photo", "photo_statement", "relimage",
                 "mermaid_ext", "concept_mermaid", "key_term_highlight", "schematic"):
        for reasons in (None, ["demote→library_image:cc0($0)"], ["demote→library_image:embed_only($0)"],
                        ["demote→photo_statement:cc_by($0)"], ["aptness_fail→reframe"]):
            for modality in ("scene_image", "image", "video_hero", "diagram", None):
                row = {"modality": modality}
                if kind:
                    row["diagram_debug"] = {"kind": kind}
                if reasons:
                    row["modality_reasons"] = list(reasons)
                assert dt.has_picture_pixels(row) is contract.has_picture_pixels(row), row


def test_a_reuse_or_crop_of_a_burned_still_carries_its_text():
    burned_still = dict(PHOTO, content_hash="h1", diagram_debug={"kind": "photo_statement"})
    reuse = dict(PHOTO, content_hash="h1")
    crop = dict(PHOTO, content_hash="h2", modality_reasons=["reframed_from:h1"])
    crop_of_crop = dict(PHOTO, content_hash="h3", modality_reasons=["reframed_from:h2"])
    burned = dt.burned_text_stills([burned_still, reuse, crop, crop_of_crop])
    assert burned == {"h1", "h2", "h3"}
    for row in (reuse, crop, crop_of_crop):
        assert bleeds_by_design(row, burned) is False
    assert bleeds_by_design(dict(PHOTO, content_hash="h9"), burned) is True


def test_a_photo_statement_holding_the_tail_is_checked():
    """072c32c4, shape exact: the last clip is a parallax `scene_image` of kind `photo_statement`, and
    the master holds it 8 s past its claimed end. Every frame there is judged as text."""
    clips = [clip(78329, "diagram", 12, duration_ms=6228, render_mode="video"),
             clip(84557, "scene_image", 13, duration_ms=3720, render_mode="parallax_2_5d",
                  diagram_debug={"kind": "photo_statement"}, ai_generated=False)]
    tl = DeliveredTimeline.from_clips(clips, master_ms=95500)
    for t in (85499, 94499):
        assert tl.full_bleed_at(t) is False, t
    clips[1]["diagram_debug"] = {"kind": "scene_image"}
    assert DeliveredTimeline.from_clips(clips, master_ms=95500).full_bleed_at(94499) is True


def test_drawn_text_matches_the_renderers_own_predicate():
    """The mirror against `animatable.is_animatable_still` and `burned_text_stills` themselves, over
    every combination of the vocabulary they read. SKIPS without kitesforu-workers."""
    _renderer()
    animatable = importlib.import_module("workers.stages.visuals.animatable")
    kinds = [None, "scene_image", "library_photo", "photo_statement", "relimage",
             "key_term_highlight", "flowchart"]
    reasons = [None, ["demote→photo_statement:cc_by($0)"], ["demote→library_image:embed_only($0)"],
               ["demote→library_image:cc0($0)"], ["reframed_from:h0"], ["reframed_from_beat:3"]]
    rows = []
    for i, (kind, reason, spec, modality) in enumerate(
            (k, r, s, m) for k in kinds for r in reasons for s in (None, "card_spec")
            for m in ("scene_image", "video_hero")):
        row = {"modality": modality, "content_hash": f"h{i}", "asset_uri": "gs://b/x.png"}
        if kind:
            row["diagram_debug"] = {"kind": kind}
        if reason:
            row["modality_reasons"] = list(reason)
        if spec:
            row[spec] = {"title": "x"}
        rows.append(row)
    ours, theirs = dt.burned_text_stills(rows), animatable.burned_text_stills(rows)
    assert ours == theirs and ours, "the burned-still sets differ"
    for row in rows:
        assert dt.carries_drawn_text(row, ours) is (not animatable.is_animatable_still(row, theirs)), row


# ── the views agree with each other and with their old owners ──────────────────────────────────

PARITY_SHAPES = {
    "plain": [clip(0), clip(3000), clip(9000, duration_ms=4000)],
    "pile": [clip(0), clip(5000), clip(5000), clip(5000, duration_ms=0), clip(12000)],
    "failed rows": [clip(0), clip(4000, status="failed", asset_uri=""), clip(8000)],
    "missing start": [clip(0), clip(None), {"start_ms": "7000", "duration_ms": 10}],
    "end_ms": [clip(0), {"start_ms": 4000, "end_ms": 9000}],
    "unsorted": [clip(9000), clip(0), clip(4000, duration_ms=0)],
}


@pytest.mark.parametrize("name", sorted(PARITY_SHAPES))
def test_delivered_spans_is_the_claims_view_of_the_timeline(name):
    """`narration_alignment.delivered_spans` is now this module's claims view. The old body is
    restated here ONLY as the reference arm of a parity check (the import-side function under
    test is the real one); it was moved, not changed."""
    clips = PARITY_SHAPES[name]

    def old_delivered_spans(clips):
        rows = []
        for i, c in enumerate(clips or []):
            if not isinstance(c, dict):
                continue
            try:
                rows.append((int(c.get("start_ms")), i, c))
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda r: r[0])
        out = {}
        for n, (start, idx, c) in enumerate(rows):
            end = next((s for s, _, _ in rows[n + 1:] if s > start), None)
            if end is None:
                s0, e = c.get("start_ms"), c.get("end_ms")
                try:
                    s0 = int(s0)
                    e = int(e) if e is not None else (s0 + int(c["duration_ms"])
                                                      if c.get("duration_ms") is not None else None)
                except (TypeError, ValueError):
                    e = None
                end = e if e is not None and e > s0 else None
            if end is not None and end > start:
                out[idx] = (start, int(end))
        return out

    assert delivered_spans(clips) == old_delivered_spans(clips)


def test_every_best_estimate_window_is_among_the_candidates():
    """`candidates_at` is a superset of `spans_by_clip` by construction, so the two views cannot
    name different clips for one instant. Checked over every 250 ms of several shapes."""
    for clips in (PARITY_SHAPES["plain"], PARITY_SHAPES["pile"],
                  [clip(0, "scene_image"), clip(9000, "diagram", duration_ms=1000),
                   clip(10000, "scene_image"), clip(13000, "scene_image")]):
        tl = DeliveredTimeline.from_clips(clips, master_ms=20000)
        spans = tl.spans_by_clip()
        for t in range(0, 20000, 250):
            cands = tl.candidates_at(t)
            if not cands:
                continue
            for idx, (lo, hi) in spans.items():
                if lo <= t < hi and dt._painted(clips[idx]):
                    assert any(c is clips[idx] for c in cands), (idx, t)


def test_painted_windows_hand_a_pile_to_its_last_claim():
    """`frame_proof` measures one window per painted span. A pile is one window, held by the last
    claim at that start (the raw `resolve_bounds` rule), exactly as its old `_windows` did."""
    clips = [clip(0, render_mode="still"), clip(5000, render_mode="video"),
             clip(5000, render_mode="parallax_2_5d"), clip(9000, render_mode="still")]
    tl = DeliveredTimeline.from_clips(clips, master_ms=15000)
    assert [(w.start_ms, w.end_ms, w.fields["render_mode"]) for w in tl.painted_windows()] == [
        (0, 5000, "still"), (5000, 9000, "parallax_2_5d"), (9000, 15000, "still")]


# ── the renderer as an oracle ──────────────────────────────────────────────────────────────────

def _renderer():
    """The renderer's own passes, or SKIP when kitesforu-workers is absent (FAIL when WORKERS_SRC
    names a tree without it): the pattern of `test_qa_mirrors_match_production.py`. The tree is the
    loader's own (``workers_tree``): the override, else the workers repo's working tree, which the
    renderer's modules must be importable from."""
    tree, explicit = sidecar_mod.workers_tree(), sidecar_mod.workers_src_override()
    if tree not in sys.path:
        sys.path.insert(0, tree)
    if importlib.util.find_spec("workers") is None:
        if explicit:
            pytest.fail(f"WORKERS_SRC={explicit} holds no `workers` package")
        pytest.skip("kitesforu-workers is not importable here (set WORKERS_SRC). SKIPPED, not passed.")
    cov = importlib.import_module("workers.stages.visuals.coverage_gate")
    pace = importlib.import_module("workers.stages.visuals.pacing.master_span_refloor")
    va = importlib.import_module("workers.stages.visuals.video_assembler")
    return cov.fill_coverage_gaps, pace.plan_pacing, va.resolve_bounds, va._apply_jcut


def _painted_by_renderer(clips, span_ms):
    """`assemble_episode_video`'s own order: coverage fill, renderable filter, the (beat_index,
    start_ms) sort, `plan_pacing`, `resolve_bounds` with real offsets, then the J-cut with its
    zero-width restore. Windows of 1 ms or less are skipped, as the per-scene loop skips them."""
    fill, plan, bounds_of, jcut = _renderer()
    filled, _ = fill([dict(c) for c in clips], enabled=True)
    ren = [c for c in filled if c.get("asset_uri") and c.get("status", "done") != "failed"]
    ren.sort(key=lambda c: (c.get("beat_index", 0) if isinstance(c.get("beat_index"), int) else 0,
                            c.get("start_ms", 0) if isinstance(c.get("start_ms"), int) else 0))
    ren = plan(ren, span_ms)
    bounds = bounds_of(ren, span_ms, real_offsets=True)
    zero = {i for i, b in enumerate(bounds) if (b[1] - b[0]) <= 1.0}
    pre = list(bounds)
    bounds = jcut(bounds)
    for i in zero:
        bounds[i] = pre[i]
        if i > 0:
            bounds[i - 1] = (bounds[i - 1][0], pre[i - 1][1])
        if i + 1 < len(bounds):
            bounds[i + 1] = (pre[i + 1][0], bounds[i + 1][1])
    return [(s, e, c) for (s, e), c in zip(bounds, ren, strict=True) if e - s > 1.0]


def test_the_fallback_never_exempts_a_frame_the_renderer_paints_with_a_card():
    """Seeded random timelines with piles, collapsed and sub-dwell windows, failed and empty-asset
    rows and unanchored clips. Every frame the model exempts must show only full-bleed assets in
    the renderer's own layout. 7 seeds x 3000 timelines (~184k frames) were run while building
    this; 400 timelines keep the suite fast."""
    styles = [("scene_image", {}), ("video_hero", VEO), ("diagram", {}),
              ("motion", {"render_mode": "video", "motion_render": "gsap"})]
    rng = random.Random(20261005)
    frames = exempt = 0
    for trial in range(400):
        t, clips = 0, []
        for i in range(rng.randint(3, 14)):
            if i:
                t += rng.choice([0, 0, 200, 400, 900, 1300, 2500, 4000, 4000, 7000, 7000])
            modality, extra = rng.choice(styles)
            c = {"beat_index": i, "start_ms": t, "duration_ms": rng.choice([0, 1000, 3000, 6000]),
                 "modality": modality, "status": "done",
                 "asset_uri": f"gs://b/{trial}_{i}." + ("mp4" if extra else "png"), **extra}
            roll = rng.random()
            if roll < 0.08:
                c["status"] = "failed"
            elif roll < 0.12:
                c["asset_uri"] = ""
            elif roll < 0.17:
                c["start_ms"] = None
            clips.append(c)
        span = max([c["start_ms"] for c in clips if c["start_ms"] is not None] or [0]) + rng.choice(
            [1000, 3000, 8000, 20000])
        truth = _painted_by_renderer(clips, span)
        model = DeliveredTimeline.from_clips(clips, real_offsets=True, master_ms=span)
        for k in range(span // 3000 + 1):
            ts = 3000 * k + 1499
            on = [c for s, e, c in truth if s <= ts < e]
            if ts >= span or not on:
                continue
            frames += 1
            if model.full_bleed_at(ts) is True:
                exempt += 1
                assert all(bleeds_by_design(c) for c in on), (
                    f"trial {trial} t={ts}: exempt, but the renderer paints "
                    f"{[c.get('modality') for c in on]}; clips={clips}")
    assert frames > 1000 and exempt > 100, (frames, exempt)   # the property was exercised


# ── the producer's golden sidecar, through the producer's parser ───────────────────────────────

_CONTRACT_KEYS = ("modality", "render_mode", "motion_render", "asset_kind")

#: Written by workers' real assembler test (``tests/unit/visuals/test_the_painted_timeline.py`` with
#: ``KFU_RECORD_PAINTED_TIMELINE_GOLDEN=1``), next to ``parse_v1`` since #3257 (merged as 2d707598c).
_GOLDEN = "tests/fixtures/painted_timeline/painted_timeline_v1.golden.json"


def _golden_sidecar() -> bytes:
    """The golden sidecar workers' assembler test writes, read from the SAME source as ``parse_v1``
    (the loader's ``read_workers_file``: the ``WORKERS_SRC`` override's checkout, else the pinned git
    object). SKIP while that source predates the sidecar contract (no ``parse_v1``); FAIL once it has
    ``parse_v1`` but not the golden beside it, which is what a ``WORKERS_SRC`` archive of ``src``
    alone looks like: archive ``tests/fixtures/painted_timeline`` too."""
    parse_v1, why = sidecar_mod.load_parse_v1()
    if parse_v1 is None:
        pytest.skip(f"workers has no painted_timeline.parse_v1 here ({why}): it predates the sidecar "
                    f"contract. SKIPPED, not passed.")
    try:
        return sidecar_mod.read_workers_file(_GOLDEN)[0]
    except FileNotFoundError as exc:
        pytest.fail(f"workers ships parse_v1, so it ships its golden sidecar: {exc}")


def test_the_producers_golden_sidecar_reads_through_parse_v1():
    """The golden, fetched as bytes and parsed by workers' own ``parse_v1``, gives the reader the
    golden's windows exactly: clip, bounds and every painted field."""
    body = _golden_sidecar()
    raw = json.loads(body)
    read = read_sidecar({"visual": {"painted_timeline_uri": URI}}, fetch=lambda url: body)
    assert (read.status, read.bytes_read) == ("read", len(body)), read
    n = 1 + max(max(w["clip"], w["source_clip"]) for w in raw["windows"])
    clips = [clip(0, None) for _ in range(n)]
    for w in raw["windows"]:           # rows that agree with the golden, so no window goes stale
        if w["source_clip"] >= 0:
            clips[w["source_clip"]] = clip(w["start_ms"], w["modality"],
                                           render_mode=w["render_mode"],
                                           motion_render=w["motion_render"])
    doc = {"master_segment_timeline": [{"index": 0}], "visual": {"clips": clips}}
    tl = DeliveredTimeline.from_job(doc, master_ms=max(w["end_ms"] for w in raw["windows"]),
                                    sidecar=read)
    assert (tl.source, tl.stamp_rejected) == ("stamp", None)
    got = sorted((w.clip, w.start_ms, w.end_ms, *(w.fields[k] for k in _CONTRACT_KEYS))
                 for w in tl.windows)
    want = sorted((w["clip"], w["start_ms"], w["end_ms"], *(w[k] for k in _CONTRACT_KEYS))
                  for w in raw["windows"])
    assert got == want


def test_the_vendored_stamps_have_the_goldens_shape():
    """The two stamps under tests/fixtures/ were built offline by the producer's own
    ``build_painted_timeline`` at #3257 022ffae38, and given ``master_generation`` / ``master_size``
    by hand when #3257 added them. A field the sidecar adds or drops makes them stale, and this goes
    red. Their ``_fixture_provenance`` key is documentation, not contract."""
    golden = json.loads(_golden_sidecar())
    for name in ("witness", "coverage_untraced"):
        vendored = produced(name)
        assert {k for k in vendored if not k.startswith("_")} == set(golden), name
        assert {k for w in vendored["windows"] for k in w} == {k for w in golden["windows"] for k in w}
        assert set(vendored["close"]) == set(golden["close"]), name


def test_the_golden_names_the_master_it_was_written_for():
    """#3257 adds ``master_generation`` and ``master_size`` to the sidecar. Read through the real
    ``parse_v1``, the golden is accepted against the master it names and refused, as ``stale_master``,
    against any other."""
    golden = _golden_sidecar()
    raw = json.loads(golden)
    if raw.get("master_generation") is None or raw.get("master_size") is None:
        pytest.skip("the golden names no master yet (no master_generation and master_size): #3257 "
                    "has not added them at this workers source. SKIPPED, not passed.")
    read = read_sidecar({"visual": {"painted_timeline_uri": URI}}, fetch=lambda url: golden)
    n = 1 + max(max(w["clip"], w["source_clip"]) for w in raw["windows"])
    clips = [clip(0, None) for _ in range(n)]
    for w in raw["windows"]:
        if w["source_clip"] >= 0:
            clips[w["source_clip"]] = clip(w["start_ms"], w["modality"],
                                           render_mode=w["render_mode"],
                                           motion_render=w["motion_render"])
    doc = {"master_segment_timeline": [{"index": 0}], "visual": {"clips": clips}}
    end = max(w["end_ms"] for w in raw["windows"])
    gen, size = int(raw["master_generation"]), int(raw["master_size"])
    same = DeliveredTimeline.from_job(doc, master_ms=end, sidecar=read, master=FetchedMaster(gen, size))
    other = DeliveredTimeline.from_job(doc, master_ms=end, sidecar=read,
                                       master=FetchedMaster(gen + 1, size))
    assert (same.source, same.stamp_rejected) == ("stamp", None)
    assert (other.source, other.stamp_rejected) == ("estimated", "stale_master")
