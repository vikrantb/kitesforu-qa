"""Which clip a DELIVERED master shows at a given instant. One definition for every reader.

Before this module there were four answers to that question: the acceptance gate's private
mirror, :func:`narration_alignment.delivered_spans`, ``scripts/frame_proof.py``'s ``_windows``
and ``full_artifact_checker.sh``'s edge arm (which asked no question at all). They disagreed on the
tail, on piles of clips claiming one start and on sort order. Each was a hand copy of renderer
logic that keeps moving.

THE PRODUCER STAMPS IT. Workers #3257 writes the painted timeline: the windows actually rendered,
after coverage fill, pacing and the J-cut, each with the painted asset's modality, render mode and
kind. It is a sidecar next to the master, named on the doc as ``visual.painted_timeline_uri``, and
it is read through workers' own ``parse_v1`` (:mod:`.painted_timeline_sidecar`). When that read
succeeds and the stamp describes the video being judged (``version == 1``), every reader uses it,
and nothing below the stamp reader is consulted.

THE ESTIMATE IS CONSERVATIVE, NOT A MIRROR. Every other master is read from the persisted
``visual.clips``: one assembled before the sidecar, one whose sidecar could not be read, and every
master read where ``parse_v1`` cannot be imported. Such a timeline says so (``source ==
"estimated"``, and ``stamp_rejected`` names the failed step). Assembly re-times those clips in ways
the clips cannot express, so the estimate does not try to reproduce the renderer. Instead it
OVER-APPROXIMATES: for each clip it computes every span the clip MAY be on screen, and
:meth:`DeliveredTimeline.candidates_at` returns the union. A reader that needs certainty, such as an exemption, must treat any ``None`` candidate,
or an empty answer, as "unknown". The gate checks such frames and never exempts them.

The renderer passes the estimate has to over-approximate, all in kitesforu-workers
``stages/visuals`` (origin/main 17bb3ce, 2026-10-05):

* ``video_assembler.assemble_episode_video`` runs ``coverage_gate.fill_coverage_gaps`` BEFORE its
  renderable filter. Every failed or empty-asset row is re-pointed at the nearest rendered
  neighbour, which may be the NEXT one, so its window shows a neighbour's picture. Here such a row
  keeps its window and its content is UNKNOWN.
* The renderable rows are sorted by ``(beat_index, start_ms)``, with a non-int field counted as 0,
  before ``resolve_bounds``. Here the same key is used, and a timeline that decreases after that sort
  is untrusted. The renderer re-lays such a timeline by scaled durations.
* ``resolve_bounds`` keeps the persisted starts only on the all-numeric monotone branch, or on the
  MIXED-ANCHORING branch (``real_offsets``). On the mixed branch an unanchored clip gets a zero-width
  window at the next anchored start. Here it becomes a pile member at that start. A timeline with no
  numeric start is re-laid by scaled durations and is untrusted.
* ``beat_timeline.floor_windows`` funds a RUN of collapsed windows (each narrower than
  ``beat_timeline.COLLAPSE_FLOOR_MS`` = 500 ms, which includes every pile member) from the tail of
  the anchored window before the whole run. Every member of the run is therefore a candidate from
  that anchor's start onward.
* ``destrobe.coalesce_strobe`` (inside ``master_span_refloor.plan_pacing``) drops every clip that
  starts less than ``destrobe.DEFAULT_MIN_DWELL_MS`` = 1600 ms after the last kept clip, and the kept
  clip holds the screen over the dropped clip's window. So a clip stays a candidate until the first
  start at least 1600 ms after its own. Its final-kept guard can also fold a short last clip into
  the one before, so that clip stays a candidate to the end.
* ``_apply_jcut`` leads every internal cut by 120 ms, and segment rounding adds more. Measured with a
  frame-difference cut finder: 17 cuts on two 16:9 masters (7 on f7df77bf, 10 on 820a8a23) landed
  133-367 ms early and none late. A frame within ``CUT_EARLY_MS`` before a cut or ``CUT_LATE_MS``
  after it may show either side. This band is fitted to 2 masters. No 9:16 short is in that
  population, and shorts are where pacing moves windows most.
* Before the first clip, a master with real offsets opens on an intro lead: a title card, the held
  first frame or a black plate. That is UNKNOWN.
* With no ``master_segment_timeline`` (legacy), the worker passes ``real_offsets=False``. Clips are
  then laid out from t=0, dropping the first start, and the spread heuristic can re-lay them. Such a
  timeline is untrusted. 0 of 461 delivered masters in the 2026-10-05 census are legacy.

Every reader that needs ONE window per clip uses :meth:`DeliveredTimeline.spans_by_clip`. That is the
best estimate: the stamp, or the persisted claims. :meth:`candidates_at` is a superset of it by
construction, so the two views cannot contradict each other on which clip is on screen. They differ
only in how much they are willing to rule out.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .painted_timeline_sidecar import PARSER_UNAVAILABLE, FetchedMaster, SidecarRead, read_sidecar

#: The one stamp version this reader understands.
STAMP_VERSION = 1

#: Where a timeline came from: the producer's stamp, or qa's conservative estimate.
SOURCE_STAMP = "stamp"
SOURCE_ESTIMATED = "estimated"

#: A stamp describes the video it was written for. Its windows run to the end of that video (the
#: tail hold stretches the last one, and an overrun trim cuts it), so if they end further than this
#: from the video stream actually probed, the stamp describes a different render and is not used.
#: The stamp's own ``master_ms`` is the AUDIO span. It is not compared, because a video can end
#: short of its master: the producer then refuses the close with ``video_short_of_master``, and the
#: stamp still describes every frame that video has.
STAMP_MASTER_TOLERANCE_MS = 1000

#: The stamp names a different master object than the one fetched. Every identity field that BOTH
#: sides carry is compared: the stamp's ``master_generation`` with the fetched master's GCS
#: generation, and its ``master_size`` with the bytes on disk. Any mismatch is stale, a size mismatch
#: on its own included. A re-assembly over the same audio keeps the same length, which the length
#: check above cannot see; this can. A generation that is not the fetched one's decimal text, a
#: non-numeric string included, is a mismatch.
STALE_MASTER = "stale_master"
#: Neither a probed length nor a compared identity ties the stamp to this video, so it is not used.
#: A probe that returned 0 s is an unknown length, never a pass.
UNTIED = "untied: no probed video length and no master identity compared"

#: ``DeliveredTimeline.master_identity``: whether the fetched master's identity was compared with
#: the stamp's and matched (``verified``), or could not be compared (``unchecked``).
IDENTITY_VERIFIED = "verified"
IDENTITY_UNCHECKED = "unchecked"


def stamp_note(stamp_rejected: str | None) -> str | None:
    """The line every reader prints in its headline output when the job named the producer's timeline
    and it was not used, so a verdict that rests on qa's estimate says so. None when the stamp was
    used, and when the doc named none. A missing parser is the loudest case, because it is a setup
    fault, not a fact about the job, and the line says how to fix it. The gate, the step-by-step
    checker's 9b and ``frame_proof`` all print this one line."""
    if not stamp_rejected:
        return None
    why = stamp_rejected[:300]
    if stamp_rejected.startswith(PARSER_UNAVAILABLE):
        return (f"NO PARSER: this job names the producer's painted timeline, but workers' parse_v1 "
                f"could not be loaded ({why}), so qa's estimate attributed every frame. Set WORKERS_SRC "
                f"to a workers src tree that has it, or WORKERS_REPO / WORKERS_REF to a workers repo "
                f"and ref that do.")
    return f"the producer's painted timeline was not used ({why}); qa's estimate attributed every frame"

#: The cut band (see the module docstring for its population). For a stamp the band also covers
#: the overlap of a dissolve: ``video_assembler._TRANSITION_S["dissolve"]`` is 0.5 s, and it blends
#: BEFORE the nominal boundary.
CUT_EARLY_MS = 500
CUT_LATE_MS = 100

#: ``beat_timeline.COLLAPSE_FLOOR_MS``: a window this narrow is funded from the one before it.
COLLAPSE_MS = 500

#: ``destrobe.DEFAULT_MIN_DWELL_MS``: a clip starting closer than this to the last kept clip is
#: dropped and the kept clip holds its window.
STROBE_DWELL_MS = 1600

#: The modalities whose pixels bleed to every edge by design. This is the crop-fill side of the
#: renderer's ``video_assembler._STATIC_MODALITIES`` (``{"diagram", "chart"}``, contain-fit), and
#: the same set as ``animatable.ANIMATABLE_MODALITIES``. Any modality not listed here stays
#: checked. That is deliberate: a new pictorial modality has to be added here as a decision. A row
#: under one of these modalities is still checked when its still carries drawn text
#: (:func:`carries_drawn_text`).
FULL_BLEED_MODALITIES = frozenset({"scene_image", "video_hero"})

#: What makes a picture carry DRAWN TEXT, mirrored from kitesforu-workers and pinned against it by
#: ``tests/test_delivered_timeline.py`` when ``WORKERS_SRC`` is importable:
#: ``animatable.TEXT_BEARING_KINDS``; ``render_contract.PHOTO_STATEMENT_KIND`` and the
#: ``modality_reasons`` spellings ``render_contract`` writes (``DEMOTE_*``, ``REFRAMED_FROM``); and
#: ``image_library._ATTRIBUTION_REQUIRED``, the licence classes whose credit is burned into the bytes.
TEXT_BEARING_KINDS = frozenset({"relimage"})
PHOTO_STATEMENT_KIND = "photo_statement"
_DEMOTE_LIBRARY_IMAGE = "demote→library_image"
_DEMOTE_PHOTO_STATEMENT = "demote→photo_statement"
_REFRAMED_FROM = "reframed_from"
_ATTRIBUTION_REQUIRED = frozenset({"embed_only"})
#: ``render_contract.PICTURE_KINDS`` / ``PICTURE_MODALITIES``: the kinds (or, with no kind, the
#: modalities) whose stored pixels are a picture. Every other kind is a render an engine drew.
PICTURE_KINDS = frozenset({"scene_image", "image", "library_photo"})
PICTURE_MODALITIES = frozenset({"scene_image", "image"})
#: The degrade ladder's reframe crop: ``<why>→reframe`` decision reasons, ``reframed_from:<hash>``,
#: and ``reframed_from_beat:<n>``, which only a crop cut under the whole-root rule (2026-09-23)
#: carries. A crop without it "names a source nobody vouched for" (``render_contract``), and some were
#: cut from labelled diagrams (workers names 200227db; qa #184 opened five more).
_REFRAME_RUNG = "→reframe"
_REFRAMED_FROM_BEAT = "reframed_from_beat"

#: Diagnoses, so a reader can say WHY an instant is unknown.
STAMP = "stamp"
TRUSTED = "trusted"
MIXED_ANCHORED = "mixed_anchored"
UNTRUSTED_DECREASING = "untrusted_decreasing"
UNTRUSTED_UNANCHORED = "untrusted_unanchored"
UNTRUSTED_BOOL_START = "untrusted_bool_start"
LEGACY = "legacy"
NO_RENDERABLE = "no_renderable"
ABSENT = "absent"
_ATTRIBUTABLE = frozenset({STAMP, TRUSTED, MIXED_ANCHORED})


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _asset_path(row: Mapping[str, Any]) -> str:
    return _lower(row.get("asset_uri")).split("?", 1)[0]


def has_veo_evidence(row: Mapping[str, Any]) -> bool:
    """Whether a ``video_hero`` row shows video, not a reclaimed still.

    ``degrade_ladder`` stamps a whole reclaim of a still with ``modality="video_hero"``. Its own
    docstring counts 3 of 39 seed descriptors with that shape in the 600 newest jobs (round-5 code
    critic census, 2026-09-24): 1 flowchart and 2 title-band renders, all drawn text.
    ``veo_hero._apply_hotswap`` stamps ``render_mode="video"`` and ``motion_render="veo"`` together
    with the modality. Any one of these counts as evidence: those two fields, a ``.mp4``
    asset, or a stamp's ``asset_kind == "video"``.

    KNOWN RESIDUAL: a reclaimed text still that is later motion-upgraded (a Ken Burns MP4, or a Veo
    image-to-video of the still) would carry the same evidence. Of the 49 ``video_hero`` rows in the
    2026-10-05 census, every one points at a ``veo_N/`` asset, so none has that shape today.
    """
    return (_lower(row.get("render_mode")) == "video"
            or _lower(row.get("motion_render")) == "veo"
            or _lower(row.get("asset_kind")) == "video"
            or _asset_path(row).endswith(".mp4"))


def _clip_kind(row: Mapping[str, Any]) -> str:
    debug = row.get("diagram_debug")
    return _lower(debug.get("kind") if isinstance(debug, Mapping) else None)


def _demote(reason: Any) -> tuple[str, str] | None:
    """``render_contract.parse_demote_reason``: ``(target, licence)`` or None."""
    r = str(reason or "")
    for target in (_DEMOTE_LIBRARY_IMAGE, _DEMOTE_PHOTO_STATEMENT):
        if r.startswith(target + ":"):
            return target, r[len(target) + 1:].split("(", 1)[0].strip().lower()
    return None


def _reasons(row: Mapping[str, Any]) -> tuple[Any, ...]:
    reasons = row.get("modality_reasons")
    return tuple(reasons) if isinstance(reasons, (list, tuple)) else ()


def _reframe_source(row: Mapping[str, Any]) -> str | None:
    """``render_contract.reframe_source_of``: the ``content_hash`` a crop was cut from."""
    for reason in _reasons(row):
        r = str(reason or "")
        if r.startswith(_REFRAMED_FROM + ":"):
            return r[len(_REFRAMED_FROM) + 1:].strip() or None
    return None


def _is_photo_statement(row: Mapping[str, Any]) -> bool:
    """``render_contract.is_photo_statement``: by kind, or by its declared demote reason."""
    if _clip_kind(row) == PHOTO_STATEMENT_KIND:
        return True
    return any((p := _demote(r)) is not None and p[0] == _DEMOTE_PHOTO_STATEMENT for r in _reasons(row))


def _carries_burned_credit(row: Mapping[str, Any]) -> bool:
    """``render_contract.carries_burned_credit``: a library serve whose licence burns a credit."""
    return any((p := _demote(r)) is not None and p[1] in _ATTRIBUTION_REQUIRED for r in _reasons(row))


def burns_text_itself(row: Mapping[str, Any]) -> bool:
    """``animatable._burns_text_itself``: the row's own record says its still carries drawn words: a
    text-bearing kind (a ``relimage`` band), a photo statement, or a burned licence credit."""
    return (_clip_kind(row) in TEXT_BEARING_KINDS or _is_photo_statement(row)
            or _carries_burned_credit(row))


def burned_text_stills(clips: Sequence[Any] | None) -> frozenset[str]:
    """``animatable.burned_text_stills``: every ``content_hash`` on the job that carries drawn text,
    plus every reframed crop of one, followed to a fixpoint, so a reuse or a crop is caught too."""
    rows = [c for c in clips or () if isinstance(c, Mapping)]
    burned = {str(c["content_hash"]) for c in rows if c.get("content_hash") and burns_text_itself(c)}
    grew = True
    while grew:
        grew = False
        for c in rows:
            h = str(c.get("content_hash") or "")
            if h and h not in burned and _reframe_source(c) in burned:
                burned.add(h)
                grew = True
    return frozenset(burned)


def has_picture_pixels(row: Mapping[str, Any]) -> bool:
    """``render_contract.has_picture_pixels``: are the stored pixels a picture with no engine-drawn
    words? Not a photo statement or a burned credit; a library serve is; then the KIND decides
    (``PICTURE_KINDS``), and only a row with no kind falls back to its modality. Fail closed: the
    renderer's own rule, because the other direction delivers a torn label."""
    if _is_photo_statement(row) or _carries_burned_credit(row):
        return False
    for reason in _reasons(row):
        parsed = _demote(reason)
        if parsed and parsed[0] == _DEMOTE_LIBRARY_IMAGE:
            return True
    kind = _clip_kind(row)
    if kind:
        return kind in PICTURE_KINDS
    return _lower(row.get("modality")) in PICTURE_MODALITIES


def is_unvouched_crop(row: Mapping[str, Any]) -> bool:
    """A reframe crop that does not carry ``reframed_from_beat``: its source was never vouched for as a
    root picture, so its pixels may be a crop of a labelled figure, whatever the row's own kind says
    (a reframe is always stamped ``scene_image``)."""
    reasons = [str(r) for r in _reasons(row)]
    crop = any(_REFRAME_RUNG in r or r.startswith(_REFRAMED_FROM + ":") for r in reasons)
    return crop and not any(r.startswith(_REFRAMED_FROM_BEAT + ":") for r in reasons)


def carries_drawn_text(row: Mapping[str, Any], burned: frozenset[str] = frozenset()) -> bool:
    """The negation of ``animatable.is_animatable_still`` for a picture modality: a card or figure
    spec, drawn words in its own still, or a reuse or crop of a still that has them."""
    if row.get("card_spec") or row.get("diagram_spec") or burns_text_itself(row):
        return True
    return bool(row.get("content_hash")) and str(row.get("content_hash")) in burned


def bleeds_by_design(row: Mapping[str, Any] | None, burned: frozenset[str] = frozenset()) -> bool:
    """Whether the painted asset legitimately fills every edge AND carries no drawn text: a
    ``scene_image`` whose stored pixels are a picture (``has_picture_pixels``), or a ``video_hero`` with
    Veo evidence; in both cases no burned text (``animatable``) and no unvouched reframe crop. A photo
    statement, a ``relimage`` band, a burned credit, a diagram kind under a picture modality or a crop
    of an unknown source is text the edge rule must see. ``None`` never bleeds."""
    if not row:
        return False
    modality = _lower(row.get("modality"))
    if (modality not in FULL_BLEED_MODALITIES or carries_drawn_text(row, burned)
            or is_unvouched_crop(row)):
        return False
    if modality == "video_hero":
        # The pixels are Veo's, not the still's, so the still's (often stale) kind says nothing.
        return has_veo_evidence(row)
    return has_picture_pixels(row)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _painted(row: Mapping[str, Any]) -> bool:
    """``video_assembler``'s renderable filter. A row failing it is coverage-filled or dropped.

    A row with NO ``asset_uri`` key is a hand-built record, not a persisted clip: every persisted
    clip carries the key, because ``VisualClip.asset_uri`` defaults to "". Such a record is read by
    its modality, as the gate on main read every row. An empty value is never read that way."""
    if row.get("status", "done") == "failed":
        return False
    return "asset_uri" not in row or bool(row.get("asset_uri"))


def _renderer_sort_key(row: Mapping[str, Any]) -> tuple[int, int]:
    """``video_assembler``'s own key, quirks included: a non-int field counts as 0."""
    beat, start = row.get("beat_index"), row.get("start_ms")
    return (beat if isinstance(beat, int) else 0, start if isinstance(start, int) else 0)


@dataclass(frozen=True)
class Window:
    """One clip's best-estimate window on the master. ``end_ms`` may be ``math.inf``.

    ``fields`` describe what is painted there: the stamp's window (plus the source clip's asset
    and ``diagram_debug``), or the claiming row itself. ``known`` is False for a claim whose row
    was never rendered, because coverage fill paints a neighbour's asset there."""

    clip: int
    start_ms: float
    end_ms: float
    fields: Mapping[str, Any]
    known: bool


@dataclass(frozen=True)
class DeliveredTimeline:
    """The answer to "what is on screen at t", built once and queried per frame."""

    source: str                                   # SOURCE_STAMP | SOURCE_ESTIMATED
    diagnosis: str
    windows: tuple[Window, ...] = ()
    stamp_rejected: str | None = None             # why a named or given stamp was not used
    burned: frozenset[str] = frozenset()          # the job's stills that carry drawn text
    sidecar_bytes: int = 0                        # body bytes fetched for the stamp (egress)
    master_identity: str | None = None            # IDENTITY_VERIFIED | IDENTITY_UNCHECKED, stamps only
    _first_ms: float = math.inf                   # before this instant: unknown (intro lead)
    _spans: tuple[tuple[float, float, Mapping[str, Any] | None], ...] = field(default=(),
                                                                            repr=False)
    _span_starts: tuple[float, ...] = field(default=(), repr=False)

    # ── constructors ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def from_job(cls, doc: Mapping[str, Any] | None, master_ms: float | None = None, *,
                 sidecar: SidecarRead | None = None,
                 master: FetchedMaster | None = None) -> DeliveredTimeline:
        """From a ``podcast_jobs`` doc: the producer's sidecar if it reads and describes this video,
        else the conservative estimate.

        ``master_ms`` is the probed duration of the master's VIDEO stream. It is used to reject a
        stamp written for a different render, and to end the last best-estimate window. ``sidecar``
        is the doc's sidecar, already read; by default it is read here (a few KB over HTTPS, and
        nothing at all when the doc names none). ``master`` is the master object actually fetched
        (``FetchedMaster``, from what ``integrations.download`` reports); a stamp that names
        another one is stale.
        """
        doc = doc or {}
        visual = doc.get("visual") or {}
        if sidecar is None:
            sidecar = read_sidecar(doc)
        timeline = cls.from_clips(visual.get("clips") or [],
                                  real_offsets=bool(doc.get("master_segment_timeline")),
                                  stamp=sidecar.parsed, master_ms=master_ms, master=master)
        return replace(timeline, stamp_rejected=sidecar.why_unread or timeline.stamp_rejected,
                       sidecar_bytes=sidecar.bytes_read)

    @classmethod
    def from_clips(cls, clips: Sequence[Any] | None, *, real_offsets: bool = True,
                   stamp: Any = None, master_ms: float | None = None,
                   master: FetchedMaster | None = None) -> DeliveredTimeline:
        """From bare clips. ``real_offsets`` defaults to True, which holds on 461/461 delivered
        masters in the census. A caller holding the job doc should use :meth:`from_job`, which
        reads the real flag, detects a legacy timeline and reads the sidecar. ``stamp`` is the
        producer's timeline, as its JSON or as ``parse_v1``'s typed model."""
        clips = list(clips or [])
        burned = burned_text_stills(clips)
        rejected = None
        if stamp is not None:
            built, rejected = _from_stamp(stamp, clips, master_ms, master)
            if built is not None:
                return replace(built, burned=burned)
        timeline = _estimate(clips, real_offsets=real_offsets, master_ms=master_ms)
        return replace(timeline, stamp_rejected=rejected, burned=burned)

    # ── queries ──────────────────────────────────────────────────────────────────────────────

    @property
    def attributable(self) -> bool:
        return self.diagnosis in _ATTRIBUTABLE

    def candidates_at(self, ts_ms: float) -> list[Mapping[str, Any] | None]:
        """Every painted asset the master MAY show at ``ts_ms``. ``None`` marks an unknown one.

        An empty list means nothing is known (untrusted timeline, or the intro lead). A reader
        deciding an exemption must require a non-empty list with no ``None`` in it."""
        if not self.attributable or ts_ms < self._first_ms + CUT_LATE_MS:
            return []
        hi_idx = bisect.bisect_right(self._span_starts, ts_ms)
        return [painted for lo, hi, painted in self._spans[:hi_idx] if lo <= ts_ms < hi]

    def full_bleed_at(self, ts_ms: float) -> bool | None:
        """True when every candidate bleeds by design; False when one does not; None if unknown."""
        cands = self.candidates_at(ts_ms)
        if not cands or any(c is None for c in cands):
            return None
        return all(bleeds_by_design(c, self.burned) for c in cands)

    def painted_windows(self) -> list[Window]:
        """One window per distinct painted span, in time order: what a per-window pixel
        measurement iterates. Clips stacked at one start are one window, held by the last claim
        there, which is the clip ``resolve_bounds``' raw rule hands that window."""
        held: dict[tuple[float, float], Window] = {}
        for w in self.windows:
            held[(w.start_ms, w.end_ms)] = w
        return sorted(held.values(), key=lambda w: (w.start_ms, w.end_ms))

    def spans_by_clip(self) -> dict[int, tuple[int, int]]:
        """One best-estimate window per clip, keyed by its index in ``visual.clips``.

        These are the stamped windows, or the persisted CLAIMS: the gap to the next distinct
        start. A last window with no known end is bounded by the clip's own claimed end. Several
        windows for one clip, possible on a stamp, are reported as their envelope."""
        out: dict[int, tuple[int, int]] = {}
        for w in self.windows:
            if w.clip < 0 or not math.isfinite(w.end_ms) or w.end_ms <= w.start_ms:
                continue
            lo, hi = int(w.start_ms), int(w.end_ms)
            if w.clip in out:
                lo, hi = min(lo, out[w.clip][0]), max(hi, out[w.clip][1])
            out[w.clip] = (lo, hi)
        return out


# ── the stamp ──────────────────────────────────────────────────────────────────────────────────

#: ``painted_timeline.UNTRACED``: the producer could not trace a window back to a clip row.
UNTRACED_INDEX = -1


def _index(value: Any, n: int) -> int | None:
    """A window's ``clip`` / ``source_clip``: an index into ``visual.clips``, or ``UNTRACED_INDEX``.
    None when it is neither (a malformed or stale stamp)."""
    if isinstance(value, int) and not isinstance(value, bool) and UNTRACED_INDEX <= value < n:
        return value
    return None


def _field(obj: Any, key: str) -> Any:
    """A contract field, from a mapping (the JSON) or from an attribute (``parse_v1``'s typed model)."""
    return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _generation(value: Any) -> str | None:
    """A GCS generation as its decimal text. The contract types ``master_generation`` as
    ``int | str | None`` (#3257 keeps a string as it was handed one: the GCS JSON API encodes the
    int64 as a string), and ``x-goog-generation`` is the same number in decimal."""
    if _is_int(value):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _from_stamp(stamp: Any, clips: list[Any], master_ms: float | None,
                master: FetchedMaster | None = None) -> tuple[DeliveredTimeline | None, str | None]:
    """The stamped timeline, or ``(None, why)`` when the stamp cannot be trusted.

    A window the producer could not trace (``clip`` or ``source_clip`` is -1) is still a painted span,
    but nothing says whose pixels it holds, so its content is UNKNOWN. So is a window whose source row
    no longer has the window's modality: ``clip`` indexes the array the assembler was handed, and a
    racing pass can re-persist ``visual.clips`` after the mux (workers ``painted_timeline.py``)."""
    version = _field(stamp, "version")
    if version != STAMP_VERSION:
        return None, f"version={version!r}"
    identity = _compare_identity(stamp, master)
    if identity is False:
        return None, STALE_MASTER
    raw = _field(stamp, "windows")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None, "no_windows"
    windows: list[Window] = []
    for w in raw:
        clip = _index(_field(w, "clip"), len(clips))
        source = _index(_field(w, "source_clip"), len(clips))
        start, end = _field(w, "start_ms"), _field(w, "end_ms")
        if clip is None or source is None or not (_is_number(start) and _is_number(end) and end > start):
            return None, "malformed_window"
        painted: dict[str, Any] = {k: _field(w, k) for k in ("modality", "render_mode",
                                                             "motion_render", "asset_kind")}
        painted.update(clip=clip, source_clip=source)
        known = clip != UNTRACED_INDEX and source != UNTRACED_INDEX
        if known:
            src = clips[source] if isinstance(clips[source], Mapping) else {}
            known = _lower(src.get("modality")) == _lower(painted.get("modality"))
            for key in ("asset_uri", "diagram_debug", "modality_reasons", "content_hash",
                        "card_spec", "diagram_spec"):
                painted[key] = src.get(key)
        windows.append(Window(clip, float(start), float(end), painted, known))
    video_end = max(w.end_ms for w in windows)
    length_known = _is_number(master_ms) and float(master_ms) > 0
    if length_known and abs(video_end - float(master_ms)) > STAMP_MASTER_TOLERANCE_MS:
        return None, f"windows end {round(video_end)} != probed video {round(master_ms)}"
    if not length_known and identity is not True:
        return None, UNTIED
    windows.sort(key=lambda w: (w.start_ms, w.end_ms))
    spans = tuple((w.start_ms - CUT_EARLY_MS, w.end_ms + CUT_LATE_MS, w.fields if w.known else None)
                  for w in windows)
    return DeliveredTimeline(SOURCE_STAMP, STAMP, tuple(windows), _first_ms=windows[0].start_ms,
                             _spans=spans, _span_starts=tuple(s[0] for s in spans),
                             master_identity=IDENTITY_VERIFIED if identity else IDENTITY_UNCHECKED), None


def _compare_identity(stamp: Any, master: FetchedMaster | None) -> bool | None:
    """True when every identity field both sides carry matches (and at least one is compared), False
    on any mismatch, None when nothing can be compared."""
    if master is None:
        return None
    compared = False
    stamped_generation = _generation(_field(stamp, "master_generation"))
    if stamped_generation is not None and master.generation is not None:
        if stamped_generation != str(master.generation):
            return False
        compared = True
    stamped_size = _field(stamp, "master_size")
    if _is_int(stamped_size) and master.size is not None:
        if stamped_size != master.size:
            return False
        compared = True
    return True if compared else None


# ── the estimate ──────────────────────────────────────────────────────────────────────────────

def _estimate(clips: list[Any], *, real_offsets: bool,
              master_ms: float | None) -> DeliveredTimeline:
    rows = [(i, c) for i, c in enumerate(clips) if isinstance(c, Mapping)]
    claims = _claimed_windows(rows, master_ms)
    if not rows:
        return DeliveredTimeline(SOURCE_ESTIMATED, ABSENT, claims)
    if not any(_painted(c) for _, c in rows):
        return DeliveredTimeline(SOURCE_ESTIMATED, NO_RENDERABLE, claims)
    if not real_offsets:
        return DeliveredTimeline(SOURCE_ESTIMATED, LEGACY, claims)
    ordered = sorted(rows, key=lambda r: _renderer_sort_key(r[1]))
    starts = [c.get("start_ms") for _, c in ordered]
    if any(isinstance(s, bool) for s in starts):
        return DeliveredTimeline(SOURCE_ESTIMATED, UNTRUSTED_BOOL_START, claims)
    anchored = [float(s) for s in starts if _is_number(s)]
    if not anchored:
        return DeliveredTimeline(SOURCE_ESTIMATED, UNTRUSTED_UNANCHORED, claims)
    if any(a > b for a, b in zip(anchored, anchored[1:], strict=False)):
        return DeliveredTimeline(SOURCE_ESTIMATED, UNTRUSTED_DECREASING, claims)
    diagnosis = TRUSTED if len(anchored) == len(starts) else MIXED_ANCHORED
    # Mixed anchoring: an unanchored clip sits zero-width at the NEXT anchored start (inf: at
    # the end of the master), exactly as `resolve_bounds` lays it before the floor funds it.
    effective: list[float] = []
    upcoming = math.inf
    for s in reversed(starts):
        if _is_number(s):
            upcoming = float(s)
        effective.append(float(s) if _is_number(s) else upcoming)
    effective.reverse()
    spans, span_starts = _may_spans(ordered, effective, master_ms)
    return DeliveredTimeline(SOURCE_ESTIMATED, diagnosis, claims, _first_ms=anchored[0], _spans=spans,
                             _span_starts=span_starts)


def _may_spans(ordered: list[tuple[int, Mapping[str, Any]]], effective: list[float],
               master_ms: float | None) -> tuple[tuple, tuple]:
    """Every span each clip MAY occupy, under every renderer pass listed in the module docstring."""
    n = len(ordered)
    distinct = sorted({s for s in effective if math.isfinite(s)})

    def first_start_at_or_after(t: float) -> float:
        j = bisect.bisect_left(distinct, t)
        return distinct[j] if j < len(distinct) else math.inf

    # Window widths as `floor_windows` sees them: the gap to the NEXT ROW (a pile member is
    # zero-width), and an unanchored row past the last anchor is zero-width at the end.
    widths = [0.0 if not math.isfinite(s) else
              (effective[r + 1] - s if r + 1 < n else math.inf)
              for r, s in enumerate(effective)]
    collapsed = [not (w >= COLLAPSE_MS) for w in widths]
    # A run of collapsed rows is funded from the anchored window BEFORE THE WHOLE RUN
    # (`beat_timeline._fund_run`), and the pile holder that shares the run's last start may be
    # absorbed into it (`_absorb_pile_holder`). A member may land anywhere from that anchor's start
    # up to the run's right edge (dropped members collapse there). A run at index 0 has no anchor
    # and stays in place.
    funded: list[tuple[float, float] | None] = [None] * n
    r = 0
    while r < n:
        if not collapsed[r]:
            r += 1
            continue
        i = r
        while r < n and collapsed[r]:
            r += 1
        last = r if r < n and effective[r] == effective[r - 1] else r - 1
        if i >= 1:
            right = (first_start_at_or_after(math.nextafter(effective[last], math.inf))
                     if math.isfinite(effective[last]) else math.inf)
            for q in range(i, last + 1):
                funded[q] = (effective[i - 1], right)
    spans: list[tuple[float, float, Mapping[str, Any] | None]] = []
    for q, ((_idx, row), s) in enumerate(zip(ordered, effective, strict=True)):
        content = row if _painted(row) else None
        earliest, latest = funded[q] if funded[q] is not None else (s, s)
        if not math.isfinite(s):
            spans.append((earliest - CUT_EARLY_MS, math.inf, content))
            continue
        # A kept clip holds until the next KEPT start: the first start at least one dwell after
        # its own (`coalesce_strobe`'s greedy rule), counted from the latest start it may have.
        nxt = first_start_at_or_after(math.nextafter(s, math.inf))
        hold = first_start_at_or_after(latest + STROBE_DWELL_MS) if math.isfinite(latest) else math.inf
        spans.append((earliest - CUT_EARLY_MS, max(nxt, hold) + CUT_LATE_MS, content))
    # The strobe guard's final-kept fold. `coalesce_strobe` ends the timeline at the last clip's
    # end and, while the last KEPT clip would dwell under the floor before it, pops that clip, so
    # the clip kept before it holds to the end of the master. The pop cascades, so the clip left
    # holding is any clip whose span runs past `last_end - STROBE_DWELL_MS`. The last end is the
    # last clip's own duration on the plain pass, and the master span after the refloor re-derives
    # it. An unknown span with a zero duration cannot be ruled out.
    if len(distinct) >= 2:
        last_start = distinct[-1]
        tails = [_duration(row) for (_, row), s in zip(ordered, effective, strict=True)
                 if s == last_start]
        windows = [d for d in tails if 0 < d < STROBE_DWELL_MS]
        if master_ms:
            if float(master_ms) - last_start < STROBE_DWELL_MS:
                windows.append(float(master_ms) - last_start)
        elif any(d <= 0 for d in tails):
            windows.append(0.0)
        if windows:
            fold_line = last_start + min(windows) - STROBE_DWELL_MS
            spans = [(lo, math.inf if hi > fold_line else hi, c) for lo, hi, c in spans]
    spans.sort(key=lambda sp: sp[0])
    return tuple(spans), tuple(sp[0] for sp in spans)


def _claimed_windows(rows: list[tuple[int, Mapping[str, Any]]],
                     master_ms: float | None) -> tuple[Window, ...]:
    """The persisted claims: each clip from its ``start_ms`` to the next DISTINCT start.

    Clips stacked at one start do not bound each other, or every one would collapse to zero
    width. The last window runs to ``master_ms`` when it is known, else to the clip's own claimed
    end (``end_ms`` or ``start_ms + duration_ms``). Clips without a usable start claim nothing.
    The coercions (``int()`` on the start, truncating) are ``narration_alignment.delivered_spans``'s
    own, kept so its published metrics do not move. This is the best estimate only. Which part of
    it the master honours is :meth:`DeliveredTimeline.candidates_at`'s business."""
    timed: list[tuple[int, int, Mapping[str, Any]]] = []
    for i, row in rows:
        try:
            timed.append((int(row.get("start_ms")), i, row))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    timed.sort(key=lambda r: r[0])
    out: list[Window] = []
    for n, (start, idx, row) in enumerate(timed):
        end: float | None = next((s for s, _, _ in timed[n + 1:] if s > start), None)
        if end is None:
            end = float(master_ms) if master_ms else _own_end(row)
        if end is not None and end > start:
            out.append(Window(idx, start, end, row, _painted(row)))
    return tuple(out)


def _own_end(row: Mapping[str, Any]) -> int | None:
    """The clip's own claimed end: ``end_ms``, else ``start_ms + duration_ms``. It bounds the last
    claim when the master's length is unknown (``narration_alignment.delivered_spans``)."""
    try:
        start = int(row.get("start_ms"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    end = row.get("end_ms")
    if end is None:
        dur = row.get("duration_ms")
        if dur is None:
            return None
        try:
            end = start + int(dur)
        except (TypeError, ValueError):
            return None
    try:
        end = int(end)
    except (TypeError, ValueError):
        return None
    return end if end > start else None


def _duration(row: Mapping[str, Any]) -> float:
    try:
        return float(row.get("duration_ms") or 0)
    except (TypeError, ValueError):
        return 0.0
