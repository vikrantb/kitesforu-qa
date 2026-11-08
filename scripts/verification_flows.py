#!/usr/bin/env python3
"""Every flow the verification job creates beside a podcast or a course, from its request to its read-back.

``create_verification_job.sh --class``, ``--writeup`` and ``--course-video-export <course_id>`` each make the smallest
request their api door accepts. Per flow, this module owns the body, the :class:`Doors` it is POSTed to and read back
on, the estimate and the ACK decision computed from the serialized body, and the comparison of what the api stored
with what was sent. The shell owns the arguments, auth, the POSTs and the exit codes, as it does for a podcast and a
course. Two commands:

``plan``          the body, its doors, its estimate and its ACK decision, as one JSON object;
``check-stored``  every way the stored request differs from the one sent, one per line.

THE THREE DOORS (kitesforu-api origin/main, kitesforu-schemas 2.85.0, the version the api locks)
-----------------------------------------------------------------------------------------------
* **A class** is two POSTs. ``POST /v1/classes`` (``CreateClassRequest``) writes a $0 draft and is not behind the
  creation gate; ``POST /v1/classes/{id}/curriculum/generate`` (``services/classes/generation.py``
  ``start_curriculum_generation``) is gated and publishes class-initiate. In ``autopilot``, the default this body
  pins, the syllabus worker publishes class-orchestrate itself once the curriculum is saved (kitesforu-course-workers
  ``class_syllabus/worker.py``), so that one trigger runs the whole class: ``POST /v1/classes/{id}/generate`` would
  start the lessons a second time.
* **A writeup** is two POSTs. ``POST /v1/writeups`` (``CreateWriteupRequest``) writes a $0 draft;
  ``POST /v1/writeups/{id}/generate`` charges and publishes writeup-research.
* **A course's continuous-video export** is one POST with no body, ``POST /v1/courses/{id}/continuous-video``:
  owner-only, no creation gate, zero credits (``routes/courses/export.py``). It queues the media-concat worker, which
  stitches the lesson masters the course already rendered.

Offline and $0: ``plan`` reads a local workers tree (a class's lessons may buy paid stills) and ``check-stored``
reads text handed to it. No command here makes a network call.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import verification_job as vj

FLOWS = ("class", "writeup", "video-export")

#: The reason a flow whose estimate holds an unpriced term gives the ACK gate: an approval must never price an
#: unknown as $0, so the price shown says what it leaves out and the run needs the ACK.
UNPRICED_REASON = "est=unknown"


@dataclass(frozen=True)
class Doors:
    """Where a flow is POSTed and read back. ``{id}`` stands for the id the create's reply names (``id_key``)."""
    noun: str            # what the create makes, in the script's messages
    create: str          # the route POSTed first
    id_key: str          # the reply key naming what the create made
    read: str            # the route its stored request is read back on, and the status URL printed
    trigger: str = ""    # the route POSTed second, when the create makes only a draft
    started: str = ""    # the phrase the trigger's reply carries when generation started (lower case)


# ---------------------------------------------------------------------------------------------
# A class: the smallest one POST /v1/classes accepts, run by its one curriculum trigger
# ---------------------------------------------------------------------------------------------

#: The fewest lessons and quiz questions ``CreateClassRequest`` accepts: ``total_lessons`` and
#: ``questions_per_lesson`` are both ``Field(ge=3)``. Its ``lesson_duration_min`` floor is 10 s, the script's default.
CLASS_LESSONS = 3
CLASS_QUESTIONS_PER_LESSON = 3
#: ``subject`` is required (``min_length=1``) and reaches the curriculum prompt. Synthetic, like the default topic.
CLASS_SUBJECT = "General"

CLASS_DOORS = Doors(noun="class", create="/v1/classes", id_key="class_id", read="/v1/classes/{id}",
                    trigger="/v1/classes/{id}/curriculum/generate", started="curriculum generation started")

#: The tier every lesson runs at. ``CreateClassRequest`` has no ``quality_tier``, so the class doc stores none, the
#: orchestrator creates each lesson job naming none (course-workers ``class_orchestrator/worker.py``), and
#: ``podcast_services.create_job`` stores and runs a job that names none at medium: the course mode's
#: ``COURSE_TIER_WHEN_UNSTORED``, the same fact.
LESSON_TIER = vj.COURSE_TIER_WHEN_UNSTORED

#: Each class LLM call's output cap: ``max_tokens=4000`` on the syllabus call (course-workers ``class_syllabus``) and
#: on each quiz call (``class_orchestrator/quiz_generator.py``).
CLASS_CALL_MAX_TOKENS = 4000


def build_class_body(*, topic: str, duration: str, style: str, language: str) -> Dict[str, Any]:
    """The ``POST /v1/classes`` body. Every key is a declared ``CreateClassRequest`` field: the model drops an
    undeclared key without an error."""
    return {
        "topic": topic,
        "subject": CLASS_SUBJECT,
        "total_lessons": CLASS_LESSONS,
        "lesson_duration_min": float(duration),
        "questions_per_lesson": CLASS_QUESTIONS_PER_LESSON,
        "style": style,
        "language": language,
        "generation_mode": "autopilot",
    }


def class_quiz_calls(body: Dict[str, Any]) -> int:
    """One quiz per audio lesson and a cumulative one: the syllabus worker interleaves them (``_interleave_quiz_slots``)
    whatever ``quiz_enabled`` says, which no course-workers code reads."""
    return int(body["total_lessons"]) + 1


def class_lesson_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """One lesson job, in the keys :func:`verification_job.estimate` reads: an orchestrated episode at
    :data:`LESSON_TIER` with the paid stills a real owner's never-asked episode may buy."""
    return vj.implied_episode_body(float(body["lesson_duration_min"]), LESSON_TIER)


def class_estimate(body: Dict[str, Any], *, images: Optional[vj.ImageQuote]) -> vj.Estimate:
    """The lessons are priced as the episodes the orchestrator creates; the syllabus and quiz calls are NOT, and say
    so: no class is stored to measure them."""
    n = int(body["total_lessons"])
    lesson = vj.estimate(class_lesson_body(body), images=images)
    if lesson.low is None or lesson.high is None:
        raise vj.PricingUnavailable(f"a lesson at {LESSON_TIER} has no priced band: {lesson.text}")
    lo, hi = n * lesson.low, n * lesson.high
    quizzes = class_quiz_calls(body)
    text = (f"unknown = {n} lesson episode(s) at {LESSON_TIER} ~{vj._span(lo, hi)} (each {lesson.text}) + NOT PRICED: "
            f"the class syllabus (1 LLM call) and {quizzes} quiz calls (one per lesson and a cumulative one)")
    detail = [
        f"lessons: the class door stores no tier, so each lesson job names none and the api runs it at {LESSON_TIER}",
        "not priced: no class is stored, so its LLM calls have no measurement (read-only Firestore, "
        "2026-11-07: no `classes` collection, against 237 `courses` read the same way). Each call is capped at "
        f"{CLASS_CALL_MAX_TOKENS} output tokens. Not added either: the cover's concept call (covercore, one small-model "
        "call capped at 200 output tokens)",
    ] + list(lesson.detail)
    return vj.Estimate(text=text, low=None, high=None, detail=detail)


def class_ack_decision(body: Dict[str, Any]) -> Tuple[bool, str]:
    """:func:`verification_job.ack_decision` of one lesson job, and :data:`UNPRICED_REASON` for the LLM calls."""
    _, why = vj.ack_decision(class_lesson_body(body))
    return True, "".join(f"{r} " for r in why.split() + [UNPRICED_REASON])


# ---------------------------------------------------------------------------------------------
# A writeup: one format, the smallest, run by its generate trigger
# ---------------------------------------------------------------------------------------------

#: The format with the smallest target: 250 words (course-workers ``writeup_generate/worker.py`` FORMAT_CONFIGS).
#: ``CreateWriteupRequest.formats_requested`` takes at least one; every format is its own paid generate call.
WRITEUP_FORMAT = "linkedin_post"

WRITEUP_DOORS = Doors(noun="writeup", create="/v1/writeups", id_key="writeup_id", read="/v1/writeups/{id}",
                      trigger="/v1/writeups/{id}/generate", started="writeup generation started")

#: The tier a writeup runs at. ``CreateWriteupRequest`` has no ``quality_tier``, so ``/generate`` reads none off the
#: doc and publishes none, and the course-workers stages resolve an absent tier to medium (``tier_models``).
WRITEUP_TIER = "medium"


def build_writeup_body(*, topic: str) -> Dict[str, Any]:
    """The ``POST /v1/writeups`` body: the topic and one format, both declared ``CreateWriteupRequest`` fields."""
    return {"topic": topic, "formats_requested": [WRITEUP_FORMAT]}


def writeup_estimate(body: Dict[str, Any]) -> vj.Estimate:
    formats = list(body.get("formats_requested") or [])
    text = (f"unknown = NOT PRICED: research (1 LLM call) + generate ({len(formats)} format(s): {', '.join(formats)}; "
            "how many passes each runs is the serving worker's flags)")
    detail = [
        f"tier: the writeup door stores none, so its stages run at {WRITEUP_TIER}",
        "not priced: no writeup is stored, so its calls have no measurement (read-only Firestore, "
        "2026-11-07: no `writeups` collection). The one recorded figure, kitesforu-course-workers COST_CHANGELOG "
        "2026-07-27: a medium blog_post (1500 words) booked $0.011 single-shot to $0.029 planner-v2 for research + "
        "generate, every token at the input rate, before the passes added since; this format targets 250 words",
        "not added: the cover's concept call (covercore, one small-model call capped at 200 output tokens)",
    ]
    return vj.Estimate(text=text, low=None, high=None, detail=detail)


# ---------------------------------------------------------------------------------------------
# A course's continuous-video export: compute over masters the course already rendered
# ---------------------------------------------------------------------------------------------

#: A course id is one path segment that is not a flag. Anything else would change the route the POST reaches.
COURSE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def export_doors(course_id: str) -> Doors:
    route = f"/v1/courses/{course_id}/continuous-video"
    return Doors(noun="video export", create=route, id_key="export_id", read=route)


def export_estimate() -> vj.Estimate:
    text = ("unknown = compute only, NOT PRICED: the media-concat worker stitches the course's lesson masters "
            "(ffmpeg on Cloud Run); it calls no paid provider and charges no credits")
    detail = [
        "not priced: this script carries no Cloud Run rate. The two exports on record took 23 s (2 lessons, stream "
        "copy) and 968 s (6 lessons, re-encode) of wall time (read-only Firestore, 2026-11-07, 2 of 237 courses)",
        "the api refuses it with 409 not_enough_ready unless 2 or more of the course's lessons have a video master: "
        "a --course at the 10 s default is expected to render none (a 10 s run authors no blueprint, so its visuals "
        "skip); expected from the code, not observed on a course",
    ]
    return vj.Estimate(text=text, low=None, high=None, detail=detail)


# ---------------------------------------------------------------------------------------------
# The plan: body, doors, estimate and ACK decision, from the bytes sent
# ---------------------------------------------------------------------------------------------

def plan(flow: str, *, topic: str, duration: str, style: str, language: str, course_id: str,
         workers_src: str, catalog_label: str) -> Dict[str, Any]:
    """The flow's plan in the shape ``verification_job.py plan`` emits, plus its doors. As there, the estimate and
    the ACK decision read the serialized payload, the same string curl sends, never the dict it came from."""
    if flow == "class":
        payload = json.dumps(build_class_body(topic=topic, duration=duration, style=style, language=language))
        sent = json.loads(payload)
        est = class_estimate(sent, images=vj.episode_stills_quote(workers_src))
        needs_ack, reason = class_ack_decision(sent)
        doors = CLASS_DOORS
        summary = (f"{sent['total_lessons']} audio lessons x {sent['lesson_duration_min']:g}min at {LESSON_TIER}, "
                   f"{class_quiz_calls(sent)} quizzes")
    elif flow == "writeup":
        payload = json.dumps(build_writeup_body(topic=topic))
        sent = json.loads(payload)
        est = writeup_estimate(sent)
        needs_ack, reason = True, f"tier={WRITEUP_TIER} {UNPRICED_REASON} "
        doors = WRITEUP_DOORS
        summary = f"{len(sent['formats_requested'])} format ({', '.join(sent['formats_requested'])}) at {WRITEUP_TIER}"
    elif flow == "video-export":
        if not COURSE_ID.match(course_id or ""):
            raise ValueError(f"--course-video-export takes a course id (letters, digits, _ and -), got {course_id!r}")
        payload = ""   # the export route takes no body
        est = export_estimate()
        needs_ack, reason = True, f"{UNPRICED_REASON} "
        doors = export_doors(course_id)
        summary = f"one video stitched from course {course_id}'s lesson masters"
    else:
        raise ValueError(f"no flow {flow!r}: one of {', '.join(FLOWS)}")
    detail = list(est.detail)
    if flow == "class" and catalog_label:
        detail.insert(0, f"priced from {catalog_label}")
    return {"payload": payload, "est": est.text, "est_low": est.low, "est_high": est.high, "est_detail": detail,
            "needs_ack": needs_ack, "ack_reason": reason, "warnings": [], "summary": summary, "noun": doors.noun,
            "create_path": doors.create, "id_key": doors.id_key, "trigger_path": doors.trigger,
            "started": doors.started, "read_path": doors.read}


# ---------------------------------------------------------------------------------------------
# The read-back: what the api stored against what was sent
# ---------------------------------------------------------------------------------------------

class UnreadableSnapshot(ValueError):
    """The read is not the flow's stored doc: an error page, an auth failure, an empty transport."""


#: The class settings the body sets, compared one by one with ``settings.*`` of ``GET /v1/classes/{id}``.
_CLASS_SETTINGS = ("total_lessons", "lesson_duration_min", "questions_per_lesson", "generation_mode", "style",
                   "language")


def _differs(label: str, want: Any, got: Any) -> List[str]:
    return [] if want == got else [f"{label}: sent {json.dumps(want)}, stored {json.dumps(got)}"]


def check_stored(flow: str, sent: Dict[str, Any], snapshot: Any, *, created_id: str) -> List[str]:
    """Every way the stored request differs from the one sent. ``[]`` means it stored what was sent. The trigger's
    reply said generation started, so a class or writeup still stored as a draft is a difference too, and so is an
    export whose stored id is not the one the POST answered with."""
    if not isinstance(snapshot, dict):
        raise UnreadableSnapshot("the read is not a JSON object")
    out: List[str] = []
    if flow == "class":
        if snapshot.get("class_id") != created_id or not isinstance(snapshot.get("settings"), dict):
            raise UnreadableSnapshot("the read carries no settings for this class")
        for key in ("topic", "subject"):
            out += _differs(key, sent[key], snapshot.get(key))
        for key in _CLASS_SETTINGS:
            out += _differs(f"settings.{key}", sent[key], snapshot["settings"].get(key))
        if snapshot.get("status") == "draft":
            out.append("status: the trigger said curriculum generation started, and the class is stored as draft")
    elif flow == "writeup":
        if snapshot.get("writeup_id") != created_id:
            raise UnreadableSnapshot("the read is not this writeup")
        out += _differs("topic", sent["topic"], snapshot.get("topic"))
        out += _differs("formats_requested", sent["formats_requested"], snapshot.get("formats_requested"))
        if snapshot.get("status") == "draft":
            out.append("status: the trigger said writeup generation started, and the writeup is stored as draft")
    elif flow == "video-export":
        if "status" not in snapshot:
            raise UnreadableSnapshot("the read carries no export status")
        out += _differs("export_id", created_id, snapshot.get("export_id"))
        if snapshot.get("status") == "absent":
            out.append("status: the POST queued an export, and the course reports none")
    else:
        raise ValueError(f"no flow {flow!r}: one of {', '.join(FLOWS)}")
    return out


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="body + doors + estimate + ACK decision, as one JSON object")
    p.add_argument("--flow", required=True, choices=FLOWS)
    for name in ("--topic", "--duration", "--style", "--language"):
        p.add_argument(name, required=True)
    p.add_argument("--course-id", default="")
    p.add_argument("--workers-src", default="")
    p.add_argument("--catalog-label", default="")
    c = sub.add_parser("check-stored", help="every way the stored request differs from the one sent, one per line")
    c.add_argument("--flow", required=True, choices=FLOWS)
    c.add_argument("--requested", required=True, help="the exact body POSTed first ('' for an export)")
    c.add_argument("--created-id", required=True, help="the id the create's reply named")
    c.add_argument("--snapshot", required=True, help="the read-back GET's JSON, or - for stdin")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        try:
            out = plan(args.flow, topic=args.topic, duration=args.duration, style=args.style,
                       language=args.language, course_id=args.course_id, workers_src=args.workers_src,
                       catalog_label=args.catalog_label)
        except (vj.PricingUnavailable, ValueError) as exc:
            print(f"cannot plan this {args.flow}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(out))
        return 0
    try:
        lines = check_stored(args.flow, json.loads(args.requested) if args.requested else {},
                             vj._read_snapshot(args.snapshot), created_id=args.created_id)
    except UnreadableSnapshot as exc:
        print(f"the read-back is unreadable: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
