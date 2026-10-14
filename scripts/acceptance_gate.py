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
from pathlib import Path
from typing import Any

# The package's ONE readiness rule (stdlib only): this tree's `src`, never an installed copy.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kitesforu_qa.visual_readiness import (  # noqa: E402
    PHASE_READY,
    VisualReadiness,
    is_delivered_hero_clip,
)

_EDU_KEYS = ("explain", "educat", "understand", "how ", "what is", "concept",
            "informational", "tutorial", "guide", "why do", "why does")


class JobNotResolved(LookupError):
    """The id names no single ``podcast_jobs`` document: a LOOKUP failure, never a verdict on content.

    A missing doc used to come back as ``{}`` and grade as "NOT SURFACED: visual.video_url empty", a BLOCKER
    naming the producer, for a job whose artifact was fine (a truncated 8-character id, 2026-09-06). "The job
    was not found" and "the producer never surfaced a video" are opposite diagnoses with opposite fixes."""

    def __init__(self, job_id: str, candidates: list[str]) -> None:
        self.job_id = job_id
        self.candidates = candidates
        super().__init__(f"{job_id!r} matches {len(candidates)} podcast_jobs document(s)")


def _fetch_job(job_id: str) -> dict[str, Any]:
    """The job's doc, with ``job_id`` stamped. An exact id wins; otherwise the id is read as a PREFIX (jobs are
    quoted by their first 8 characters) and must match exactly one doc. Raises ``JobNotResolved`` otherwise."""
    from google.cloud import firestore  # lazy: keeps the CLI importable without creds
    db = firestore.Client(project=os.environ.get("GCP_PROJECT", "kitesforu-dev"))
    jobs = db.collection("podcast_jobs")
    if not job_id.strip():
        raise JobNotResolved(job_id, [])
    snap = jobs.document(job_id).get()
    if snap.exists:
        doc = snap.to_dict() or {}
        doc.setdefault("job_id", job_id)
        return doc
    hits = [s.id for s in jobs.where(filter=firestore.FieldFilter("__name__", ">=", jobs.document(job_id)))
            .where(filter=firestore.FieldFilter("__name__", "<", jobs.document(job_id + "\uf8ff")))
            .limit(3).select([]).stream()]
    if len(hits) != 1:
        raise JobNotResolved(job_id, hits)
    doc = jobs.document(hits[0]).get().to_dict() or {}
    doc["job_id"] = hits[0]
    return doc


def _probe_dims(path: str) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.strip()
    parts = out.split(",")
    w = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
    h = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    try:
        dur = float(parts[2])
    except (IndexError, ValueError):
        dur = 0.0
    return w, h, dur


def _extract_frames(mp4: str, out_dir: str) -> list[str]:
    """OBSERVE: one frame every ~3s across the FULL duration (never one hero frame)."""
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", mp4, "-vf", "fps=1/3,scale=540:-1",
                    os.path.join(out_dir, "f_%05d.png")],
                   capture_output=True)
    return _frames_in_order(out_dir)


def _frames_in_order(out_dir: str) -> list[str]:
    """Frame files in TIME order. A string sort puts f_1000 before f_101 (past ~50 minutes at one
    frame per 3 s), which scrambles every timestamp after it; the frame number decides."""
    def _n(name: str) -> int:
        try:
            return int(name[2:-4])
        except ValueError:
            return 10 ** 9
    names = [f for f in os.listdir(out_dir) if f.startswith("f_") and f.endswith(".png")]
    return [os.path.join(out_dir, f) for f in sorted(names, key=_n)]


#: `_extract_frames` runs ffmpeg at `fps=1/3`: output frame i (0-based) is taken from inside the
#: video's [3i, 3i+3) s window. Named rather than inlined because the frame->clip mapping below is
#: only as correct as this constant, and the two must be changed together.
_FRAME_INTERVAL_MS = 3000


def _frame_time_ms(i: int) -> int:
    """Where frame i sits in the video: the MIDDLE of its window. ffmpeg's fps filter takes the
    frame at about 3i+1.3 s (measured by the #187 review from the luma clock), not 3i; using 3i
    mapped 33 of the 850 census frames to the wrong clip (a photo checked as a diagram)."""
    return i * _FRAME_INTERVAL_MS + _FRAME_INTERVAL_MS // 2


#: Modalities whose pixels are photographed or filmed: they bleed to every edge by design, so the
#: edge rule never inspects them. `ai_generated` (any diffusion image or Veo clip) counts too: the
#: first version exempted `scene_image` alone, and Veo footage (`video_hero`) then made 19 of the
#: 151 run frames in the census.
_FOOTAGE_MODALITIES = frozenset({"scene_image", "video_hero"})


def _is_footage(clip: dict | None) -> bool:
    """True when the clip's pixels are a picture. A picture reframed into a card slot keeps
    `modality: diagram` and `ai_generated: False`, but its `visual_register` says "photo": 11 of
    the 393 census clips are diagram/photo, and the one RUN on them (33ae4811 123-129 s, a
    full-bleed office scene) was a false positive. No real cut or margin touch in the census sits
    on a photo-register clip. "cartoon" is NOT exempt: diagram/cartoon clips exist and none was
    examined."""
    if not clip:
        return False
    return (clip.get("modality") in _FOOTAGE_MODALITIES or clip.get("ai_generated") is True
            or clip.get("visual_register") == "photo")


def _clip_at(clips: list[dict] | None, ts_ms: int) -> dict | None:
    """The clip covering ``ts_ms``, or None when unknown.

    None is returned for BOTH "no clips supplied" and "no clip covers this timestamp", and both
    mean the same thing to the caller: keep checking. Absence must never be read as permission
    to skip a check — that is how a gate silently turns itself off.
    """
    for c in clips or []:
        try:
            start = int(c.get("start_ms") or 0)
            dur = int(c.get("duration_ms") or 0)
        except (TypeError, ValueError):
            continue
        if dur > 0 and start <= ts_ms < start + dur:
            return c
    return None


def _pixel_invariants(frames: list[str], clips: list[dict] | None = None) -> list[dict]:
    """PROBE invariants B (persistent letterbox band) + C (content clipped at frame edge),
    measured on the REAL extracted frames. Deterministic, $0 — catches the classes the vision
    layer would flag, without an LLM call. Rough heuristics, biased toward flagging.

    ── EDGE-CLIP IS SKIPPED ON PHOTO BEATS, AND ONLY ON PHOTO BEATS ──────────────────────────
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
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        # LOUD, never an empty list. Returning no issues here made the gate say PASS_DETERMINISTIC
        # without looking at a single pixel: the 2026-10-11 frame census ran in a venv that had
        # drifted from the declared `Pillow>=10`, and all 8 "passes" were this branch.
        return [{"sev": "BLOCKER", "msg": (
            f"PIXEL INVARIANTS DID NOT RUN: {exc}. Install the declared dependencies "
            "(pip install -e . in kitesforu-qa); a gate that cannot look must not pass.")}]
    if not frames:
        # Same rule as the missing dependency: no frames means nothing was inspected, and that is
        # never a pass (the #187 review: a 403 body saved as the .mp4 extracted 0 frames and
        # the job PASSED).
        return [{"sev": "BLOCKER", "msg": "NO FRAMES INSPECTED: the extraction produced no frames"}]
    # EVERY FRAME, not a sample. The gate used to inspect 12 evenly spaced frames and flag edge-clip
    # only when a third of them clipped, so a defect lasting a few seconds could not register. A frame
    # costs milliseconds here, so there is nothing to save by sampling. On the 2026-10-11 frame census
    # this newly flags 2 of 9 episodes; the census's PASS verdicts themselves came from the missing
    # Pillow (the ImportError branch above). The ORIGINAL index stays with each path: it is the only
    # thing that maps a frame back to a time, and so to the clip that authored it.
    inspected = list(enumerate(frames))
    letterbox = edge_clip = 0
    readable = 0
    edge_checked = 0          # frames the EDGE-CLIP rule actually ran on (footage is skipped)
    edge_skipped_photo = 0
    clipped: list[tuple[int, frozenset]] = []  # (frame index, clipped edges), in order, for the RUN rule
    for idx, fp in inspected:
        try:
            im = np.asarray(Image.open(fp).convert("L"), dtype=float)
        except Exception:  # noqa: BLE001 — a bad frame is skipped, never fatal
            continue
        readable += 1
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
        mw = max(3, int(im.shape[1] * 0.03))
        mh = max(3, int(im.shape[0] * 0.03))
        def _vsteps(strip):
            return int((np.abs(np.diff(strip, axis=0)) > 28).sum())
        def _hsteps(strip):
            return int((np.abs(np.diff(strip, axis=1)) > 28).sum())
        # Footage (a photo, an illustration, Veo) legitimately bleeds to every edge: the pipeline's own
        # checker never inspects one. Skip the rule, do not merely discount it.
        if _is_footage(_clip_at(clips, _frame_time_ms(idx))):
            edge_skipped_photo += 1
        else:
            edge_checked += 1
            edges = frozenset(e for e, hit in (
                ("left", _vsteps(im[:, :mw]) >= 12), ("right", _vsteps(im[:, -mw:]) >= 12),
                ("top", _hsteps(im[:mh, :]) >= 12), ("bottom", _hsteps(im[-mh:, :]) >= 12)) if hit)
            if edges:
                edge_clip += 1
                clipped.append((idx, edges))
    if not readable:
        return [{"sev": "BLOCKER", "msg": (
            f"NO FRAMES INSPECTED: none of the {len(frames)} extracted frame(s) could be read")}]
    if letterbox >= max(2, readable // 2):
        issues.append({"sev": "MAJOR", "msg": (
            f"LETTERBOX: {letterbox}/{readable} frames show a persistent dark band — content "
            "not filling the vertical frame (authored for the wrong aspect)")})
    # THE DENOMINATOR IS WHAT WAS CHECKED, not what was sampled. Dividing by the full sample
    # after skipping photos would make a photo-heavy job progressively harder to flag — the gate
    # would quietly weaken on exactly the jobs where the skip applies, which is the opposite of
    # what the skip is for.
    if edge_checked and edge_clip >= max(2, edge_checked // 3):
        skipped = (f" ({edge_skipped_photo} footage frame(s) exempt — a photo, illustration or Veo "
                   "clip bleeds to the edge by design)") if edge_skipped_photo else ""
        issues.append({"sev": "MAJOR", "msg": (
            f"EDGE-CLIP: {edge_clip}/{edge_checked} checked frames have bright/text pixels hugging "
            f"the frame edge — content likely cut off-frame{skipped}")})
    # RUN rule: content at the SAME edge on 2+ CONSECUTIVE frames (3+ seconds held, not a
    # slide-in passing the edge) is a defect a viewer sees, however clean the rest of the episode
    # is. The rate rule above cannot see it on a long episode; this one names WHEN. "Same edge"
    # matters: the first version chained any edge, and alternating L/R/RB frames (a camera pan)
    # read as one long cut.
    # PRECISION, measured 2026-10-11 by reading every flagged run's frames (44 runs, 6 census
    # episodes, one adversary per episode): 27 cut content off-frame, 15 sat inside the 3% margin
    # without cutting, 2 were pictures. So the message says "cut or inside the margin", and the
    # finding is MAJOR (REVIEW, with the times for the adversary), not a BLOCKER.
    runs = _runs(clipped, min_len=2)
    if runs:
        spans = ", ".join(f"{a * _FRAME_INTERVAL_MS // 1000}-{(b + 1) * _FRAME_INTERVAL_MS // 1000}s "
                          f"({'/'.join(sorted(e))})" for a, b, e in runs)
        issues.append({"sev": "MAJOR", "msg": (
            f"EDGE-CLIP RUN: content held at the same frame edge on consecutive frames at {spans} "
            f"({len(runs)} run(s), {sum(b - a + 1 for a, b, _ in runs)} frame(s)): cut off-frame "
            "or inside the 3% margin")})
    return issues


def _runs(clipped: list[tuple[int, frozenset]], min_len: int = 2) -> list[tuple[int, int, frozenset]]:
    """Maximal runs of consecutive frames that keep at least one clipped edge in common, as
    (first index, last index, the shared edges), keeping runs of `min_len` frames or more."""
    runs: list[tuple[int, int, frozenset]] = []
    start = prev = None
    shared: frozenset = frozenset()
    for i, edges in clipped:
        if prev is not None and i == prev + 1 and shared & edges:
            shared = shared & edges
            prev = i
            continue
        if start is not None and prev - start + 1 >= min_len:
            runs.append((start, prev, shared))
        start = prev = i
        shared = frozenset(edges)
    if start is not None and prev - start + 1 >= min_len:
        runs.append((start, prev, shared))
    return runs


def run_gate(job_id: str, frames_dir: str | None = None, persona: str | None = None) -> dict[str, Any]:
    try:
        d = _fetch_job(job_id)
    except JobNotResolved as exc:
        if exc.candidates:
            why = (f"{len(exc.candidates)} podcast_jobs documents start with {job_id!r} "
                   f"({', '.join(exc.candidates)}); pass more of the id")
        else:
            why = f"no podcast_jobs document has the id {job_id!r} or starts with it"
        return {"job_id": job_id, "verdict": "AMBIGUOUS_ID" if exc.candidates else "NOT_FOUND",
                "candidates": exc.candidates,
                "issues": [{"sev": "BLOCKER", "msg": f"NOT RESOLVED: {why}. A lookup failure, not a content defect."}]}
    requested_id, job_id = job_id, str(d.get("job_id") or job_id)
    topic = d.get("topic") or d.get("title") or ""
    vis = d.get("visual") or {}
    clips = vis.get("clips") or []
    # PRODUCE: the SURFACED url the watch page reads — never an intermediate render path. And only once the
    # ladder says it is the master the watch page shows. The api writes the URL BEFORE the status settles, so
    # a URL beside `assembling` (a re-mux after a hot-swap) is a master about to be replaced, and the frontend
    # shows "rendering", not this file (#175 round-6 design D2: the gate read the URL with no ladder check).
    url = vis.get("video_url") or vis.get("video_burned_url")
    issues: list[dict[str, str]] = []
    phase, why = VisualReadiness.from_visual(vis).video_phase(expected=True)
    if phase != PHASE_READY:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic,
                "issues": [{"sev": "BLOCKER", "msg": f"NOT SURFACED: the video is {phase} ({why}), not the master "
                                                     "the watch page shows"}]}
    if not url:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic,
                "issues": [{"sev": "BLOCKER", "msg": "NOT SURFACED: visual.video_url empty"}]}

    tmp = os.path.join(tempfile.gettempdir(), f"ag_{job_id}.mp4")
    subprocess.run(["gsutil", "-q", "cp", url, tmp] if url.startswith("gs://")
                   else ["curl", "-fsSL", "-o", tmp, url], check=False)
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic,
                "issues": [{"sev": "BLOCKER", "msg": f"artifact not fetchable: {url}"}]}
    vw, vh, dur = _probe_dims(tmp)
    if not (vw and vh):
        # An error page or a truncated download saved under .mp4 has no video stream. Say so; never
        # let it reach extraction and come back as zero frames.
        return {"job_id": job_id, "verdict": "FAIL", "topic": topic,
                "issues": [{"sev": "BLOCKER", "msg": f"NOT A VIDEO: the fetched artifact has no video stream ({url})"}]}
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
    # The producer's hero predicate (`veo_hero.is_hot_swapped`, via the package): a reclaimed STILL stamped
    # `video_hero` by degrade_ladder is not a Veo clip and must not count toward an off-topic finding.
    veo = [c for c in clips if is_delivered_hero_clip(c)]
    if edu and veo:
        issues.append({"sev": "MAJOR", "msg": (
            f"OFF-TOPIC RISK: {len(veo)} abstract Veo video_hero clips on EDUCATIONAL content "
            f"('{topic[:48]}') -> prefer meaningful diagrams")})

    # OBSERVE: emit frames for the independent vision/adversary step.
    fdir = frames_dir or os.path.join(tempfile.gettempdir(), f"ag_frames_{job_id}")
    frames = _extract_frames(tmp, fdir)
    issues.extend(_pixel_invariants(frames, clips))  # invariants B + C on the real frames

    verdict = "FAIL" if any(i["sev"] == "BLOCKER" for i in issues) else \
              ("REVIEW" if issues else "PASS_DETERMINISTIC")
    return {"job_id": job_id, **({"requested_id": requested_id} if requested_id != job_id else {}),
            "topic": topic, "dims": [vw, vh], "duration": dur,
            "clip_aspects": clip_aspects, "verdict": verdict, "issues": issues,
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
    if res["verdict"] in ("NOT_FOUND", "AMBIGUOUS_ID"):
        return 2  # the gate never looked at an artifact: not a FAIL (1) about one
    return 0 if res["verdict"] in ("PASS_DETERMINISTIC", "REVIEW") else 1


if __name__ == "__main__":
    sys.exit(main())
