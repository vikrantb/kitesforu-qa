#!/usr/bin/env python3
"""Acceptance Gate — "Open It Like the Founder" (founder escalation 2026-07-10).

The forcing function for `.claude/rules/acceptance-gate.md`: no user-facing "done" until the REAL
produced artifact is OBSERVED and an independent skeptic fails to refute "ship-ready". This module
is the deterministic + frame-extraction core (PRODUCE → PROBE → OBSERVE); the ADVERSARY step is a
fresh-context vision agent run on the emitted frames + manifest.

Proven: on the real broken job 85ec6fc9 (topic "systematic discrimination", rendered 1080x1920 with
16:9-authored clips) this FAILS on exactly the two defects the founder caught in 30s.

Usage:
  python acceptance_gate.py --job-id <id> [--frames-dir DIR]
Exit 0 = deterministic PASS (still run the vision/adversary step); non-zero = deterministic FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

# Reading Firestore and then forking a subprocess (ffprobe, ffmpeg, curl, git) stalls ~62.7 s per run
# while gRPC fork support is on (measured by the #184 round-2 latency lens: 68 s vs 7 s). The gate
# makes no gRPC call in a child, so fork support is off before anything imports firestore.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")

# The repo's OWN package first: an editable install elsewhere (e.g. the shared checkout) must never
# answer for this checkout's attribution model.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from kitesforu_qa.harness.delivered_timeline import DeliveredTimeline, stamp_note  # noqa: E402
from kitesforu_qa.harness.painted_timeline_sidecar import (  # noqa: E402
    FetchedMaster,
    fetched_master,
)

_EDU_KEYS = ("explain", "educat", "understand", "how ", "what is", "concept",
            "informational", "tutorial", "guide", "why do", "why does")


def _fetch_job(job_id: str) -> dict[str, Any]:
    from google.cloud import firestore  # lazy: keeps the CLI importable without creds
    db = firestore.Client(project=os.environ.get("GCP_PROJECT", "kitesforu-dev"))
    return (db.collection("podcast_jobs").document(job_id).get().to_dict()) or {}


#: Seconds for each gate subprocess: the GET, ffprobe, the frame extraction.
_FETCH_TIMEOUT_S = 900
_PROBE_TIMEOUT_S = 60
_EXTRACT_TIMEOUT_S = 900


def _probe_dims(path: str) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S).stdout.strip()
    parts = out.split(",")
    w = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    h = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    try:
        dur = float(parts[2])
    except (IndexError, ValueError):
        dur = 0.0
    return w, h, dur


#: One extracted frame per interval. The frame->time mapping (`_frame_time_ms`) depends on this
#: AND on the fps filter's rounding mode, so the filter is BUILT from it rather than typed beside
#: it: `fps=1000/3000` is ffmpeg's rational 1/3, and `round=near` is its default spelled out, so a
#: changed default cannot silently move every frame. Byte-identical to the old `fps=1/3` filter:
#: all 28 PNGs of the witness master f7df77bf compare equal with `cmp`.
_FRAME_INTERVAL_MS = 3000

#: The step-by-step checker's interval (``full_artifact_checker.sh`` 9b): one frame per SECOND.
#: Its instants, ``k + 0.5`` s, include every instant the gate reads (``3k + 1.5`` s) and every
#: instant the arm 9b replaced read (step 9's ``fps=1/5``: ``5k + 2.5`` s), so no frame either of
#: them judged goes unjudged.
CHECKER_FRAME_INTERVAL_MS = 1000


def _fps_filter(interval_ms: int) -> str:
    return f"fps=1000/{interval_ms}:round=near"


_FPS_FILTER = _fps_filter(_FRAME_INTERVAL_MS)


def _extract_frames(mp4: str, out_dir: str, interval_ms: int = _FRAME_INTERVAL_MS) -> list[str]:
    """OBSERVE: one frame per ``interval_ms`` across the FULL duration (never one hero frame).

    Frames an earlier run left in ``out_dir`` are removed first: a longer earlier master would
    otherwise leave its tail frames to be scored as this one's."""
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.startswith("f_") and name.endswith(".png"):
            os.remove(os.path.join(out_dir, name))
    subprocess.run(["ffmpeg", "-y", "-i", mp4, "-vf", f"{_fps_filter(interval_ms)},scale=540:-1",
                    os.path.join(out_dir, "f_%03d.png")],
                   capture_output=True, timeout=_EXTRACT_TIMEOUT_S)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".png"))


def _frame_time_ms(index: int, interval_ms: int = _FRAME_INTERVAL_MS) -> int:
    """The instant of the master that extracted frame ``index`` (0-based) actually shows, for an
    extraction at one frame per ``interval_ms``.

    NOT ``index * interval``, which is what this gate assumed until 2026-10-05. ffmpeg's ``fps``
    filter rounds every source timestamp to the NEAREST output slot and emits, for each slot, the
    last source frame rounded into it — so output frame k holds the last source frame BEFORE
    ``(k + 1/2) * interval``: the MIDDLE of its 3 s slot, not the start.

    MEASURED on the witness master f7df77bf (30 fps): every one of its 28 extracted frames is
    pixel-identical (mean |diff| 0.000) to source frame ``90k + 44`` = ``3k + 1.4667 s``, against
    1.6-60.1 for the frame at ``3k``. Under the old mapping f_003 (7.47 s, a scene_image) was read
    as 6.0 s (a video_hero) — every frame was scored as the clip on screen ~1.5 s earlier.

    The value is 1 ms before the slot's midpoint: the instant at which the screen shows exactly
    that source frame, at any frame rate below 1000 fps (the midpoint itself already shows the
    NEXT frame whenever a frame boundary falls on it — at 24, 25 and 30 fps it does).
    """
    return index * interval_ms + interval_ms // 2 - 1


def _sample_indices(n: int, want: int = 12) -> list[int]:
    """The frame indices `_pixel_invariants` inspects — EXTRACTED so a test can exercise the real
    arithmetic instead of restating it.

    That distinction is the reason this function exists. The first test written for this fix
    recomputed the stride itself, so reverting the source left it green: it tested a copy of the
    rule, not the rule. Anything that re-derives the logic under test is not testing it.

    THE ORIGINAL DEFECT. Plain `n // want` FLOORS to 1 for any n in 13..23 and `[::1][:12]` then
    takes the FIRST twelve frames, so a 22-frame video was scored on its first 36 seconds.
    Measured 2026-09-01 with `(max(f)-min(f)+1)/n` over `_sample_indices`: 18 frames -> 67%,
    22 -> 55%, 23 -> 52%.

    WHY NOT A CEILING STRIDE. The first fix used `step = max(1, -(-n // want))`, which spans the
    array but returns `ceil(n/step)` frames — BELOW the 12 it asks for on 55 of the 229 counts in
    12..240, bottoming out at 7 of 13. The #172 code-critic lens caught it; re-derived here with
    `len([n for n in range(12,241) if len(_sample_indices(n)) < min(n,12)]) == 55`. That also drags
    both verdict thresholds down with it, since `max(2, len(samp)//2)` and `max(2, edge//3)` are
    derived from the sample size — the gate would have become thinner AND more trigger-happy.

    Even spacing wins both axes at once: exactly `min(n, want)` indices, first and last frame
    always included (`i*(n-1)//(want-1)` lands on `n-1` at `i == want-1`, which a stride misses at
    n=14,16,18,20,22,...), so coverage is 100% at every n. Verified 2026-09-01 across n in 2..240:
    never short, never clustered, last index always `n-1`.
    """
    if n <= 0:
        return []
    if n <= want:
        return list(range(n))
    return [i * (n - 1) // (want - 1) for i in range(want)]


def _pixel_invariants(frames: list[str], clips: list[dict] | None = None, *,
                      timeline: DeliveredTimeline | None = None, every_frame: bool = False,
                      interval_ms: int = _FRAME_INTERVAL_MS) -> tuple[list[dict], dict[str, Any]]:
    """PROBE invariants B (persistent letterbox band) + C (content clipped at frame edge),
    measured on the REAL extracted frames. Deterministic, $0 — catches the classes the vision
    layer would flag, without an LLM call. Rough heuristics, biased toward flagging.

    Returns ``(issues, edge_clip_coverage)``. The coverage says what the EDGE-CLIP rule actually
    ran on: a pass with every frame exempt is a pass by exemption, and the caller must be able to
    say so. ``timeline`` is the delivered timeline (``DeliveredTimeline.from_job`` in production);
    without one it is built from ``clips`` alone, assuming real offsets.

    ``every_frame`` judges every extracted frame instead of the 12 sampled ones, and
    ``interval_ms`` is the extraction's interval (it decides the instant each frame shows). The
    gate's MAJOR stays an aggregate over its sample; ``full_artifact_checker.sh`` extracts one frame
    per second, judges every one, and FAILs on any single flagged frame
    (``coverage["flagged_frames"]``), as the arm it replaced did.

    ── EDGE-CLIP IS SKIPPED ON FULL-BLEED BEATS, AND ONLY ON FULL-BLEED BEATS ────────────────
    A frame is exempt only when EVERY asset the master may show at the frame's true instant
    (``_frame_time_ms``) bleeds by design (``DeliveredTimeline.full_bleed_at``: a scene_image, or a
    video_hero with Veo evidence, whose still carries no drawn text — a photo statement, a relimage
    band or a burned licence credit stays checked). Anything the timeline cannot attribute is
    checked. On the witness
    f7df77bf the 4 frames the old code "checked" were all full-bleed, and each fell to a different
    cause: f_001 is a Veo frame (video_hero was not exempt), f_003 is a scene_image read 1.5 s early
    as the Veo clip before it, and f_025/f_028 re-show the last scene past the authored timeline,
    where the old duration lookup returned None.

    The gate runs on frames from the DELIVERED MASTER, which interleaves diagram beats with
    generated photography. The pipeline's OWN edge checker (`log_unsafe_bbox`) is called only
    from `diagram/render.py` — it never inspects a photo, because a photo legitimately bleeds to
    every edge. Running the diagram rule over photo frames made this gate flag 6 of 12 frames I
    had labelled clean by eye.

    MEASURED against a labelled set of four jobs (`visual.clips[].modality`):

        6cae642d  REAL defects   diagram 13 · scene_image  1
        c533260d  REAL defects   diagram 14 · scene_image  3
        131546af  FALSE positive scene_image 24 · diagram  1
        db02c066  FALSE positive scene_image 17 · diagram  3

    The false-positive jobs are photo-dominated and the true-defect jobs diagram-dominated. This
    is the "never ask a model to PERCEIVE what you can COMPUTE" case: the pipeline already LABELS
    every beat at authoring time, so no pixel heuristic is needed. Two were tried and neither
    separated the classes — brightness scored a smooth backdrop 61560, and a horizontal-gradient
    variant scored the clipped control 0.

    LETTERBOX is unaffected: a persistent black band is a defect on a photo too.

    `modality is None` occurs on real clips. It is treated as UNKNOWN and still checked — reading
    absence as "photo" would silently disable the gate on exactly the jobs whose records are
    incomplete.
    """
    issues: list[dict] = []
    if timeline is None:
        timeline = DeliveredTimeline.from_clips(clips or [], real_offsets=True)
    coverage: dict[str, Any] = {"sampled": 0, "checked": 0, "exempt_full_bleed": 0, "unknown": 0,
                                "flagged": 0, "timeline": timeline.diagnosis,
                                "source": timeline.source, "sidecar_bytes": timeline.sidecar_bytes,
                                "flagged_frames": []}
    if timeline.stamp_rejected:
        coverage["stamp_rejected"] = timeline.stamp_rejected
    if timeline.master_identity:
        coverage["master_identity"] = timeline.master_identity
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return issues, coverage
    if not frames:
        return issues, coverage
    # SPAN THE WHOLE VIDEO. `len(frames) // 12` FLOORS to 1 for any count in 13..23, and
    # `[::1][:12]` then takes the FIRST twelve frames — so a 22-frame video (about 66s at the
    # fps=1/3 extraction above) was scored on its first 36 seconds and the rest was never looked
    # at. Measured 2026-09-02::
    #
    #     frames  old step  old window   coverage
    #        18       1       0..11         67%
    #        22       1       0..11         55%
    #        23       1       0..11         52%
    #        24       2       0..22         96%   <- only once the floor reaches 2
    #
    # Found by correlating a real job: `0082f988` carries `maps_sequence` at beats 6-7, which
    # occupy 57.9-66.6s = frames 19..22 — entirely outside the sampled window, so a MAJOR
    # edge-clip verdict on that job said nothing whatever about its map frames.
    #
    # This is the gate `.claude/rules/02-done.md` relies on precisely because it "scores every
    # frame across the full duration rather than one hero frame". For a whole band of realistic
    # durations it scored the first half. `-(-n // 12)` is ceiling division, so the stride always
    # spans the array.
    # Keep the ORIGINAL index alongside the path — it is the only thing that maps a frame back to
    # a timestamp, and therefore to the clip that authored it.
    samp = [(i, frames[i]) for i in (range(len(frames)) if every_frame
                                     else _sample_indices(len(frames)))]
    letterbox = edge_clip = 0
    edge_checked = 0          # frames the EDGE-CLIP rule actually ran on (full-bleed is skipped)
    edge_skipped_full_bleed = 0
    edge_unknown = 0          # checked because the timeline could not say what was on screen
    for idx, fp in samp:
        try:
            im = np.asarray(Image.open(fp).convert("L"), dtype=float)
        except Exception:  # noqa: BLE001 — a bad frame is skipped, never fatal
            continue
        h = im.shape[0]
        band = max(1, int(h * 0.10))
        top, bot, mid = im[:band], im[-band:], im[band:-band]
        # B: uniform dark band top AND bottom, clearly darker than the middle => letterbox.
        if mid.size and (mid.mean() - max(top.mean(), bot.mean())) > 16 \
                and top.std() < 9 and bot.std() < 9:
            letterbox += 1
        # C: content CUT OFF by the frame edge.
        #
        # This used to count BRIGHT pixels in the edge columns (`im[:, :3] >= 200`). Brightness
        # cannot tell a clipped box from a bright FULL-BLEED BACKDROP, and a backdrop legitimately
        # fills the margin — so the check both cried wolf and, worse, produced uninterpretable
        # numbers that made a fleet scope impossible ("13 of 40 frames carry margin pixels" says
        # nothing about clipping). Measured on a labelled control set: a smooth bright backdrop
        # scores 61560 bright pixels in the left margin and is perfectly fine.
        #
        # What actually distinguishes them is VERTICAL STRUCTURE. A backdrop — however bright,
        # however it ramps — changes smoothly, so no two vertically adjacent pixels differ much.
        # Content that is cut by the frame has hard horizontal boundaries where the box or the
        # text row starts and stops. Counting steps >28 in the vertical direction inside the
        # margin therefore reads ~0 for any backdrop and high for anything clipped.
        #
        # Validated on a 3-case labelled set (`tests/test_edge_clip_probe.py`, which regenerates
        # the fixtures deterministically):
        #     bright smooth backdrop -> 0    PASS   (brightness test said 61560: false positive)
        #     clean, content inset   -> 0    PASS
        #     box+text cut at x=0    -> 798  FLAG
        # and against the human-observed defect in job 9725a85c at t=21s -> left margin 660, while
        # its clean frames (t=8,11,14) score 0.
        #
        # NOTE the earlier horizontal-gradient attempt scored the clipped control 0 and was
        # discarded: a box that fills the whole margin is horizontally uniform inside it.
        # A full-bleed beat legitimately bleeds to every edge — the pipeline's own checker never
        # inspects one. Skip the rule, do not merely discount it. The frame is read at the
        # instant it actually shows, and is exempt only if EVERY asset that may be on screen then
        # bleeds by design; an instant the timeline cannot attribute is UNKNOWN, which is checked.
        full_bleed = timeline.full_bleed_at(_frame_time_ms(idx, interval_ms))
        if full_bleed is True:
            edge_skipped_full_bleed += 1
        else:
            edge_checked += 1
            if full_bleed is None:
                edge_unknown += 1
            if _cut_at_edge(im):
                edge_clip += 1
                coverage["flagged_frames"].append(os.path.basename(fp))
    if letterbox >= max(2, len(samp) // 2):
        issues.append({"sev": "MAJOR", "msg": (
            f"LETTERBOX: {letterbox}/{len(samp)} frames show a persistent dark band — content "
            "not filling the vertical frame (authored for the wrong aspect)")})
    coverage.update({"sampled": len(samp), "checked": edge_checked,
                     "exempt_full_bleed": edge_skipped_full_bleed, "unknown": edge_unknown,
                     "flagged": edge_clip})
    # THE DENOMINATOR IS WHAT WAS CHECKED, not what was sampled. Dividing by the full sample
    # after skipping photos would make a photo-heavy job progressively harder to flag — the gate
    # would quietly weaken on exactly the jobs where the skip applies, which is the opposite of
    # what the skip is for.
    if edge_checked and edge_clip >= max(2, edge_checked // 3):
        skipped = (f" ({edge_skipped_full_bleed} full-bleed frame(s) exempt — scene_image and "
                   "video_hero bleed to the edge by design)") if edge_skipped_full_bleed else ""
        issues.append({"sev": "MAJOR", "msg": (
            f"EDGE-CLIP: {edge_clip}/{edge_checked} checked frames have bright/text pixels hugging "
            f"the frame edge — content likely cut off-frame{skipped}")})
    return issues, coverage


def _cut_at_edge(im: Any) -> bool:
    """Invariant C on one grayscale frame: vertical (or, at top/bottom, horizontal) steps > 28 in the
    3% margin, at least 12 of them. See the rationale inside ``_pixel_invariants``."""
    import numpy as np

    mw = max(3, int(im.shape[1] * 0.03))
    mh = max(3, int(im.shape[0] * 0.03))

    def _vsteps(strip: Any) -> int:
        return int((np.abs(np.diff(strip, axis=0)) > 28).sum())

    def _hsteps(strip: Any) -> int:
        return int((np.abs(np.diff(strip, axis=1)) > 28).sum())

    return (_vsteps(im[:, :mw]) >= 12 or _vsteps(im[:, -mw:]) >= 12
            or _hsteps(im[:mh, :]) >= 12 or _hsteps(im[-mh:, :]) >= 12)


def probe_master(doc: dict[str, Any], mp4: str, frames_dir: str, master_ms: float | None = None,
                 master: FetchedMaster | None = None, every_frame: bool = False,
                 interval_ms: int = _FRAME_INTERVAL_MS) -> tuple[list[str], list[dict], dict[str, Any]]:
    """OBSERVE + PROBE B/C on one master, exactly as ``run_gate`` does: extract, attribute every
    frame against the job's delivered timeline, score. ``full_artifact_checker.sh`` calls this
    too, so the step-by-step checker and the gate cannot disagree about an edge clip.

    The timeline is the producer's sidecar when it reads and describes this video, else qa's
    estimate; ``coverage["source"]`` says which. ``master_ms`` is the VIDEO stream's duration
    (``_probe_dims``), the length the sidecar's windows describe. ``master`` is the master object
    fetched (its ``x-goog-generation`` and size); a stamp written for another one is ``stale_master``.
    ``every_frame`` and ``interval_ms`` are :func:`_pixel_invariants`'s; the gate uses neither.
    """
    frames = _extract_frames(mp4, frames_dir, interval_ms)
    timeline = DeliveredTimeline.from_job(doc, master_ms=master_ms, master=master)
    issues, coverage = _pixel_invariants(frames, timeline=timeline, every_frame=every_frame,
                                         interval_ms=interval_ms)
    return frames, issues, coverage


def every_frame_edge_step(doc: dict[str, Any], mp4: str, frames_dir: str,
                          master_ms: float | None = None,
                          master: FetchedMaster | None = None) -> tuple[bool, list[str]]:
    """``full_artifact_checker.sh`` step 9b: ``(failed, lines to print)``.

    One frame per second across the whole master (``CHECKER_FRAME_INTERVAL_MS``), each attributed at
    the instant it shows and judged by probe C, and a FAIL on ANY single flagged frame: the arm 9b
    replaced FAILed on one clipped frame, and so does this one. The gate's own MAJOR stays an
    aggregate over its 12-frame sample. The witnesses this arm was written for, 7171699f f_004
    (17.5 s) and 4d41320d f_001 (2.5 s), are single frames; both are pinned in
    ``tests/test_the_gate_reads_each_frame_at_its_true_time.py``."""
    frames, _, cov = probe_master(doc, mp4, frames_dir, master_ms, master, every_frame=True,
                                  interval_ms=CHECKER_FRAME_INTERVAL_MS)
    index = {os.path.basename(f): i for i, f in enumerate(frames)}
    flagged = [f"{name}@{_frame_time_ms(index[name], CHECKER_FRAME_INTERVAL_MS) / 1000:.1f}s"
               for name in cov["flagged_frames"]]
    lines = [f"[9b edge]     every frame, 1/s: checked={cov['checked']}/{cov['sampled']} "
             f"exempt_full_bleed={cov['exempt_full_bleed']} flagged={cov['flagged']} {flagged[:8]} "
             f"timeline={cov['timeline']} source={cov['source']} "
             f"identity={cov.get('master_identity', '-')} "
             f"stamp_rejected={cov.get('stamp_rejected', '-')} :: {'FAIL' if flagged else 'PASS'}"]
    lines += [f"              NOTE: {note}"
              for note in (stamp_note(cov.get("stamp_rejected")), _edge_clip_note(cov)) if note]
    return bool(flagged), lines


def _edge_clip_note(coverage: dict[str, Any]) -> str | None:
    """One line when the edge rule observed nothing, so a PASS cannot read as "edges verified"."""
    if coverage.get("sampled") and not coverage.get("checked"):
        return (f"EDGE-CLIP checked 0 of {coverage['sampled']} sampled frames "
                f"({coverage.get('exempt_full_bleed', 0)} exempt as full-bleed, timeline "
                f"{coverage.get('timeline')}, {coverage.get('source')}): this verdict carries no "
                f"edge-clip observation")
    return None


def run_gate(job_id: str, frames_dir: str | None = None, persona: str | None = None) -> dict[str, Any]:
    d = _fetch_job(job_id)
    topic = d.get("topic") or d.get("title") or ""
    vis = d.get("visual") or {}
    clips = vis.get("clips") or []
    # PRODUCE: the SURFACED url the watch page reads — never an intermediate render path.
    url = vis.get("video_url") or vis.get("video_burned_url")
    issues: list[dict[str, str]] = []
    if not url:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic,
                "issues": [{"sev": "BLOCKER", "msg": "NOT SURFACED: visual.video_url empty"}]}

    tmp = os.path.join(tempfile.gettempdir(), f"ag_{job_id}.mp4")
    # The master an earlier run left at this path must never be scored as this run's: it is removed
    # before the GET, and the GET must succeed (`curl -f`, its exit status checked). The GET's own
    # headers name the object fetched (x-goog-generation), so the producer's stamp is held to THIS
    # master; they go to a fresh file per run, removed whatever happens.
    if os.path.exists(tmp):
        os.remove(tmp)
    hdr_fd, hdr = tempfile.mkstemp(prefix=f"ag_{job_id}_", suffix=".headers")
    os.close(hdr_fd)
    try:
        got = subprocess.run(["gsutil", "-q", "cp", url, tmp] if url.startswith("gs://")
                             else ["curl", "-sfL", "--max-time", str(_FETCH_TIMEOUT_S),
                                   "-D", hdr, "-o", tmp, url],
                             check=False, capture_output=True, timeout=_FETCH_TIMEOUT_S)
        with open(hdr, encoding="latin-1") as fh:
            headers = fh.read()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic, "issues": [
            {"sev": "BLOCKER", "msg": f"artifact not fetchable: {url}: {type(exc).__name__}: {exc}"}]}
    finally:
        os.remove(hdr)
    if got.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic, "issues": [
            {"sev": "BLOCKER", "msg": f"artifact not fetchable: {url} (exit {got.returncode})"}]}
    try:
        vw, vh, dur = _probe_dims(tmp)
    except subprocess.TimeoutExpired:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic, "issues": [
            {"sev": "BLOCKER", "msg": f"ffprobe timed out after {_PROBE_TIMEOUT_S}s on {url}"}]}
    vertical = bool(vw and vh and vh > vw)

    # PROBE — invariant A: authored clip orientation must match the render target.
    clip_aspects = sorted({str(c.get("aspect_ratio")) for c in clips if c.get("aspect_ratio")})
    if vertical and any(a in ("16:9", "16x9", "1.78") for a in clip_aspects):
        issues.append({"sev": "BLOCKER", "msg": (
            f"ORIENTATION MISMATCH (=> CUT): video {vw}x{vh} (vertical) but clips authored "
            f"{clip_aspects} -> 16:9 visuals crop-filled into 9:16, edges CUT")})
    # off-topic Veo on educational content (planner disqualifier).
    ct = str((d.get("audio_config") or {}).get("content_type") or d.get("content_type") or "").lower()
    genre = str(d.get("genre") or "").lower()
    edu = any(k in (ct + genre + topic.lower()) for k in _EDU_KEYS)
    veo = [c for c in clips if c.get("modality") == "video_hero"]
    if edu and veo:
        issues.append({"sev": "MAJOR", "msg": (
            f"OFF-TOPIC RISK: {len(veo)} abstract Veo video_hero clips on EDUCATIONAL content "
            f"('{topic[:48]}') -> prefer meaningful diagrams")})

    # OBSERVE: emit frames for the independent vision/adversary step, then invariants B + C.
    fdir = frames_dir or os.path.join(tempfile.gettempdir(), f"ag_frames_{job_id}")
    # Only the master itself (video_url) is the object the stamp names; the captioned copy is not.
    fetched = fetched_master(headers, tmp) if url == vis.get("video_url") else None
    try:
        frames, pixel_issues, edge_coverage = probe_master(d, tmp, fdir, dur * 1000 if dur else None,
                                                           master=fetched)
    except subprocess.TimeoutExpired:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic, "issues": issues + [
            {"sev": "BLOCKER", "msg": f"frame extraction timed out after {_EXTRACT_TIMEOUT_S}s"}]}
    issues.extend(pixel_issues)

    verdict = "FAIL" if any(i["sev"] == "BLOCKER" for i in issues) else \
              ("REVIEW" if issues else "PASS_DETERMINISTIC")
    return {"job_id": job_id, "topic": topic, "dims": [vw, vh], "duration": dur,
            "clip_aspects": clip_aspects, "verdict": verdict, "issues": issues,
            "edge_clip_coverage": edge_coverage, "edge_clip_note": _edge_clip_note(edge_coverage),
            "timeline_note": stamp_note(edge_coverage.get("stamp_rejected")),
            "frames_dir": fdir, "num_frames": len(frames),
            "persona": persona or None,
            "next": _adversary_brief(persona)}


#: The surface each persona was written for, read from its own YAML rather than re-listed here.
#: `load_persona` renders 10 of the 17 declared fields and drops `surface`, so a persona could be
#: pointed at an artifact it was never designed to judge with nothing to notice — an AUDIO-ONLY
#: reviewer handed a directory of video frames, for one. The #172 design lens found it; the typo
#: path raises loudly and this path was silent, which is the same defect one axis over. Rather
#: than restrict the flag (a general `--persona` is genuinely useful for cross-checking), the
#: brief now STATES the surface so the reviewer can flag the mismatch itself.
def _surface_note(persona: str | None) -> str:
    """One line naming the surface the persona was authored for, or "" when it declares none."""
    if not persona:
        return ""
    import yaml

    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "hero_users", "personas")
    try:
        with open(os.path.join(d, f"{persona}.yaml")) as fh:
            raw = yaml.safe_load(fh.read()) or {}
        surface = str(raw.get("surface") or "").strip() if isinstance(raw, dict) else ""
    except Exception:  # noqa: BLE001 — a brief must never fail to render over a missing field
        return ""
    if not surface:
        return ""
    return (
        f"THE SURFACE YOU WERE WRITTEN FOR: {surface}. You are being shown frames from a rendered "
        f"video. If that is not your surface, say so FIRST and judge only what you can legitimately "
        f"judge from these frames — do not stretch your lens to cover an artifact it does not fit.\n\n"
    )


def _adversary_brief(persona: str | None) -> str:
    """The instruction the ADVERSARY step is run under.

    Default: the generic refute-ship-ready brief this gate has always emitted — byte-identical
    when `--persona` is omitted.

    With a persona: the routed HERO USER's own brief, loaded from `hero_users/personas/`. Rule 02
    routes a story to Nadia, a social short to Sofia, any audio to Aarav — and until now there was
    no way to say so. The gate emitted frames and a generic instruction, so the persona system and
    the frame system could not meet: `story_judge.py` can run a persona but reads only the SCRIPT
    (its prompt says voice, music and visuals are graded elsewhere), and this gate has the frames
    but no persona. This is the one line that joins them.

    WHY THIS COEXISTS WITH `.claude/workflows/hero-user-verification.js`, which already routes a
    persona over this gate's frames (#172 design lens). That workflow delegates RENDERING to the
    agent — it tells the agent to read the YAML itself, so its critic sees all 17 declared keys.
    This function is the first actual RENDERER, and it exists because `story_judge --persona` needs
    one and an agent prompt cannot be reused from Python. They are two readers of one config with
    different field coverage (17 keys vs the 10 `load_persona` renders), which is a split brain and
    WILL drift. It is written down rather than fixed here because collapsing them means deciding
    whether the workflow should shell out to this gate — a bigger call than this PR. Filed.

    Reuses `story_judge.load_persona` rather than re-rendering the YAML — one owner for what a
    persona brief looks like. A typo RAISES there with the valid names, deliberately: a silent
    fallback would produce a confident verdict from the wrong reviewer.
    """
    base = ("Run the ADVERSARY: a fresh vision agent Reads every frame in frames_dir, told to "
            "REFUTE 'ship-ready' vs the spec (on-topic? text cut? coherent?). Verdict is final "
            "only after that + the receipt in .claude/acceptance/.")
    if not persona:
        return base
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from story_judge import load_persona

    return (
        f"{load_persona(persona)}\n\n"
        f"{_surface_note(persona)}"
        "YOU ARE REVIEWING THE DELIVERED VIDEO, not a script. Read EVERY frame in `frames_dir` — "
        "they are sampled across the FULL duration at fps=1/3, so judge the whole piece and never "
        "one hero frame. Defects first, verdict last. Refute 'ship-ready' by default; a review "
        "that agrees to be agreeable is a failed review. Name the exact frame and the exact thing "
        "that is wrong with it.\n\n"
        f"{base}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--frames-dir", default=None)
    ap.add_argument(
        "--persona", default="",
        help=("Review as a HERO USER from hero_users/personas/ (nadia-story-listener, "
              "sofia-creator, aarav-audio, elena-ld, maya-student, marcus-technical, "
              "priya-jobseeker). Rule 02 routes story->Nadia, social-short->Sofia, audio->Aarav. "
              "Omitted = the generic adversary brief, byte-identical."),
    )
    a = ap.parse_args()
    for k in ("GRPC_VERBOSITY", "GLOG_minloglevel"):
        os.environ.setdefault(k, "NONE" if "GRPC" in k else "3")
    # Resolve the persona BEFORE `run_gate` pays for the artifact download, ffprobe, the ffmpeg
    # extraction and the numpy probe. `_adversary_brief` is built in run_gate's return dict, so a
    # mistyped name used to raise only AFTER all of that: measured by the #172 latency lens at 1.22s
    # for a 66s local clip, and a 10-minute episode adds the master download plus 5.4s of extraction
    # before the traceback. The names are long and hyphenated, which is the input that gets
    # mistyped. Raising here costs one file stat.
    if a.persona:
        from story_judge import load_persona

        load_persona(a.persona)
    res = run_gate(a.job_id, a.frames_dir, a.persona or None)
    print(json.dumps(res, indent=2))
    for key in ("timeline_note", "edge_clip_note"):
        if res.get(key):
            print(f"NOTE: {res[key]}", file=sys.stderr)
    return 0 if res["verdict"] in ("PASS_DETERMINISTIC", "REVIEW") else 1


if __name__ == "__main__":
    sys.exit(main())
