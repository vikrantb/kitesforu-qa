"""Is a job's VISUAL compartment final, and is its video coming? ONE rule for every qa reader.

WHY THIS FILE EXISTS (kitesforu-qa #175 round 5: design D1, latency L1/L2, code critic F1/F2/F5).
This repo held four answers to "are the visuals done", and they disagreed:

* ``harness/artifact.py`` ``VisualReadiness``: ``visual.status == done``, no ``pending`` clip, no empty
  ``asset_uri``. It moved here, unchanged, and ``Artifact.visual_readiness`` builds it from here;
* ``settled_clips``: a content fingerprint of ``visual.clips`` that must hold across 120 s. Its
  fingerprint and window are what :func:`VisualReadiness.from_visual` and the wait loop read;
* ``scripts/capture_starved_measurements.py`` ``settle_stamp_state``: the ``visual.clips_settled_at``
  stamp. It moved here, and that script imports it;
* ``scripts/create_verification_job.sh``: "a ``video_url`` means the visuals are final". Deleted. It ignored
  the video ladder, so a failed assembly and a by-design audio-only skip both waited 90 minutes for a video
  that was never coming, and it counted purchased clips from one unsettled read.

The frontend's answer, ``kitesforu-frontend lib/media/videoReadiness.ts`` ``resolveVideoDeliverable``, is
the fifth. :meth:`VisualReadiness.video_phase` mirrors it, with one deviation stated where it happens.

WHAT ``visual.clips_settled_at`` MEANS. Two statements in this repo contradicted each other: "PREFER THE STAMP.
It is authoritative" (``capture_starved_measurements``), and "it does not mean that: it is re-stamped by every
terminal persist" (``verification_job.py``, round 4). Both were half right. Read from workers ``66f5c4f1d``:

* ``_persist_story_visuals`` (``stages/visuals/worker.py`` ~:12516-12524) stamps it on EVERY terminal persist,
  unconditionally; the incremental writer ``_on_clip_rendered`` (~:7942) clears it to ``None`` on every
  partial write. So a SET stamp means: the array on the doc is the one a terminal persist wrote, and no pass is
  writing it now. That part is authoritative.
* It does NOT mean no later pass will run. The terminal persist runs before ``_maybe_assemble_episode_video``
  and is not gated on ``veo_pending`` or ``motion_pending`` (~:15940, :16187), so a pass whose purchased Veo op
  is still rendering stamps the array too, and the hot-swap pass rewrites it later.

So: the stamp answers "is a pass mid-write?"; the video ladder answers "is the deliverable assembled?"; and a
clip COUNT is trustworthy only from a read where the ladder says ``ready``, the stamp is not cleared, and the
array has stopped changing (the ``settled_clips`` fingerprint). That conjunction is the wait loop's rule.

THE HERO PREDICATE is the producer's own, ``veo_hero.is_hot_swapped``: ``modality == "video_hero"`` AND
``render_mode == "video"``. ``degrade_ladder`` stamps ``modality="video_hero"`` on a reclaimed STILL, so the
modality alone counts a failed purchase as a delivered clip (#175 round-5 code critic F2).

Standard library only (beside :mod:`kitesforu_qa.settled_clips`, also stdlib-only), so a shell script can import
it with any ``python3``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settled_clips import DEFAULT_STABLE_SECONDS, clips_fingerprint

__all__ = [
    "DEFAULT_STABLE_SECONDS",
    "VIDEO_PENDING_SKIP_REASONS",
    "VisualReadiness",
    "is_delivered_hero_clip",
    "settle_stamp_state",
]

# ---- the settle stamp ------------------------------------------------------------------------------

#: Three states, and the ABSENT-vs-NULL distinction is load-bearing (moved verbatim in meaning from
#: ``scripts/capture_starved_measurements.py``):
#:
#:   non-empty str -> a terminal persist wrote this array and no pass is writing it now;
#:   None          -> present but cleared: a pass is IN FLIGHT. Keep waiting;
#:   absent        -> the job predates the stamp. Fall back to a quiet period / a stable fingerprint.
#:
#: Reading None as "no stamp" would hang on every in-flight job until the deadline; reading absent as
#: "in flight" would hang on every job already in Firestore. Opposite diagnoses, one ``in`` check apart.
SETTLE_STAMP_KEY = "clips_settled_at"
STAMP_SETTLED = "settled"
STAMP_IN_FLIGHT = "in_flight"
STAMP_ABSENT = "absent"


def _stamp_state_of(visual: Any) -> str:
    if not isinstance(visual, dict) or SETTLE_STAMP_KEY not in visual:
        return STAMP_ABSENT
    value = visual.get(SETTLE_STAMP_KEY)
    if isinstance(value, str) and value.strip():
        return STAMP_SETTLED
    return STAMP_IN_FLIGHT


def settle_stamp_state(job: Any) -> str:
    """Which of the three states the stamp reports for this JOB doc (``job["visual"]``)."""
    return _stamp_state_of(job.get("visual") if isinstance(job, dict) else None)


# ---- the hero predicate ----------------------------------------------------------------------------

def is_delivered_hero_clip(clip: Any) -> bool:
    """A delivered hero VIDEO clip, by the producer's predicate (workers ``veo_hero.is_hot_swapped``)."""
    return (isinstance(clip, dict) and clip.get("modality") == "video_hero"
            and clip.get("render_mode") == "video")


# ---- the video ladder ------------------------------------------------------------------------------

#: ``visual.video_skip_reason`` values that mean the video IS still coming: the worker parks, NACKs and
#: self-heals. The frontend's ``VIDEO_PENDING_SKIP_REASONS``, same two members. Every OTHER reason is
#: terminal: ``audio_only_no_clips``, ``opted_out_no_visuals``, ``video_feature_disabled``,
#: ``final_audio_missing``, ``assembly_failed``, ``fiction_or_undetermined_no_opt_in`` (workers
#: ``stages/visuals/worker.py`` ``VIDEO_SKIP_*``, 66f5c4f1d).
VIDEO_PENDING_SKIP_REASONS = frozenset({"final_audio_pending", "clip_timing_pending"})

#: The phases :meth:`VisualReadiness.video_phase` returns. ``failed`` and ``no_video`` are TERMINAL: no video
#: is coming. ``rendering``, ``assembling`` and ``pending`` are not.
PHASE_NOT_EXPECTED = "not_expected"
PHASE_READY = "ready"
PHASE_FAILED = "failed"
PHASE_NO_VIDEO = "no_video"
PHASE_RENDERING = "rendering"
PHASE_ASSEMBLING = "assembling"
PHASE_PENDING = "pending"
TERMINAL_PHASES = frozenset({PHASE_FAILED, PHASE_NO_VIDEO})


@dataclass(frozen=True)
class VisualReadiness:
    """Whether a job's VISUAL stage has actually finished — so a metric read off it means something.

    THE ERROR CLASS THIS EXISTS TO KILL. On 2026-07-30 I misread a still-changing job doc as final
    TEN times in one day. The clip array does not just grow (append-only); a visuals RE-RUN
    REPLACES it, so the same job read 13 -> 19 -> 3 clips within minutes. Each read was internally
    consistent and each supported a different, confident, wrong conclusion:

      * "figures LOST before render"  — receipt paired with a snapshot from a DIFFERENT run
      * "8 pending, no video"         — a mid-run snapshot; the stage finished `done` moments later
      * a real behaviour change (a ceiling lowered 24 -> 12) shipped on that snapshot

    Reasoning was sound every time. The INPUT was provisional. So the harness now refuses to let a
    provisional doc be read silently: ask `readiness.is_final` before believing any clip-derived
    number, or call `assert_final()` and let it raise.

    FINAL means all three, because any one alone has lied here:
      1. `visual.status == "done"` — the stage says it finished;
      2. NO clip left `pending` — every planned picture resolved;
      3. NO clip with an empty `asset_uri` — every resolved picture actually has bytes.

    The fields after ``empty_asset_uris`` were added by #175 round 5 for the video ladder and the settle rule
    (module docstring); they default, so every existing construction stays valid.
    """

    status: str
    video_status: str
    total_clips: int
    pending_clips: int
    empty_asset_uris: int
    video_present: bool = False
    video_skip_reason: str = ""
    settle_stamp: str = STAMP_ABSENT
    hero_clips: int = 0
    clips_fingerprint: str = ""

    @classmethod
    def from_visual(cls, visual: Any, *, top_level_video_url: Any = None) -> VisualReadiness:
        """Classify a ``visual`` compartment: the Firestore doc's, or ``GET /status``'s (re-signed URLs).

        Pure and total: a malformed compartment reports as in-flight (0 clips) rather than raising, because an
        unreadable doc is exactly the case where a confident number is most dangerous."""
        v = visual if isinstance(visual, dict) else {}
        raw_clips = v.get("clips")
        clips = [c for c in (raw_clips or []) if isinstance(c, dict)] if isinstance(raw_clips, list) else []
        pending = sum(1 for c in clips if str(c.get("status") or "").strip().lower() == "pending")
        empty = sum(1 for c in clips if not str(c.get("asset_uri") or "").strip())
        return cls(
            status=str(v.get("status") or "").strip().lower(),
            video_status=str(v.get("video_status") or "").strip().lower(),
            total_clips=len(clips),
            pending_clips=pending,
            empty_asset_uris=empty,
            video_present=bool(v.get("video_url") or v.get("video_burned_url") or top_level_video_url),
            video_skip_reason=str(v.get("video_skip_reason") or "").strip(),
            settle_stamp=_stamp_state_of(visual),
            hero_clips=sum(1 for c in clips if is_delivered_hero_clip(c)),
            clips_fingerprint=clips_fingerprint(raw_clips if isinstance(raw_clips, list) else []),
        )

    @property
    def is_final(self) -> bool:
        return (
            self.status == "done"
            and self.pending_clips == 0
            and self.empty_asset_uris == 0
            and self.total_clips > 0
        )

    @property
    def why_not_final(self) -> list[str]:
        """Ordered reasons, most decisive first — so there is ONE thing to wait on."""
        out: list[str] = []
        if self.total_clips == 0:
            out.append("no clips persisted yet")
        if self.status != "done":
            out.append(f"visual.status={self.status!r} (not 'done')")
        if self.pending_clips:
            out.append(f"{self.pending_clips}/{self.total_clips} clip(s) still 'pending'")
        if self.empty_asset_uris:
            out.append(f"{self.empty_asset_uris} clip(s) with an empty asset_uri")
        return out

    def video_phase(self, *, expected: bool) -> tuple[str, str]:
        """``(phase, reason)``: what is happening to the video a job asked for.

        Mirrors the frontend's ``resolveVideoDeliverable``: a SET ladder (``visual.video_status``) wins over a
        present URL, because the api writes the URL before the status settles (``exports/continuous_video.py``);
        ``failed_assembly`` is terminal; an absent ladder falls back to the URL, then to the skip reason.

        ONE DEVIATION, and why. The frontend lets a set, non-terminal ladder win over the skip reason, so job
        ``e2bda0fa`` (``visual.status=partial``, ``video_status=rendering``, ``video_skip_reason=
        audio_only_no_clips``, no clips, no video) reads as "rendering" forever. Workers write ``rendering`` at
        the stage-lease claim and delete it at the release, so a ``rendering`` that outlives its pass is a pass
        that died, not a render in progress; a terminal skip reason beside it, with no video, is the settled
        answer. Here a terminal skip reason with no video is ``no_video`` whatever a non-``ready`` ladder says.
        A ``visual.status == "failed"`` is ``failed`` too: the frontend never reads it, the old wait loop did."""
        if not expected:
            return PHASE_NOT_EXPECTED, "no video was asked for"
        ladder = self.video_status
        skip = self.video_skip_reason
        if ladder == "ready":
            return PHASE_READY, "video_status=ready"
        if ladder == "failed_assembly":
            return PHASE_FAILED, f"video_status=failed_assembly, video_skip_reason={skip or '(none)'}"
        if self.status == "failed":
            return PHASE_FAILED, "visual.status=failed"
        if not ladder and self.video_present:
            return PHASE_READY, "a video URL and no video_status ladder (a doc older than the ladder)"
        if skip and skip not in VIDEO_PENDING_SKIP_REASONS and not self.video_present:
            return PHASE_NO_VIDEO, f"video_skip_reason={skip}" + (f", video_status={ladder}" if ladder else "")
        if ladder == "rendering":
            return PHASE_RENDERING, "video_status=rendering"
        if ladder == "assembling":
            return PHASE_ASSEMBLING, "video_status=assembling"
        if ladder:
            # A member added upstream that this build does not know yet: fail toward NOT finished.
            return PHASE_PENDING, f"video_status={ladder} (unknown here; treated as not finished)"
        return PHASE_PENDING, "no video yet" + (f" (video_skip_reason={skip}, transient)" if skip else "")

    @property
    def clip_count_trustworthy(self) -> bool:
        """The read a clip count may be taken from, ONE read's half of the rule: the ladder is ``ready`` (or,
        on a doc older than the ladder, a video exists) and no pass is mid-write (the stamp is set, or the doc
        predates it). The other half is time: the fingerprint must hold across ``DEFAULT_STABLE_SECONDS``,
        which only a caller that reads twice can check."""
        phase, _ = self.video_phase(expected=True)
        return phase == PHASE_READY and self.settle_stamp != STAMP_IN_FLIGHT

    def __str__(self) -> str:
        if self.is_final:
            return (
                f"FINAL: visual.status=done, {self.total_clips} clip(s), none pending, "
                f"all with assets (video_status={self.video_status!r})"
            )
        return "IN FLIGHT — do NOT measure: " + "; ".join(self.why_not_final)
