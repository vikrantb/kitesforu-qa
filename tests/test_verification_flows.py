"""create_verification_job.sh --class, --writeup and --course-video-export, end to end, with NOTHING sent.

Every run here is ``--dry-run`` or the sending path with ``curl`` replaced by a stub on PATH, in the harness of
``test_create_verification_job.py``: a scratch copy of the script whose one changed line is ``ACK_FILE``, dead
endpoints, a dummy key. This file's stub answers the create POST, the generation trigger's POST and the read-back GETs
separately, so every exit code can be reached. Every id and topic here is synthetic: this repository is public.

The contract pins read the doors from the producers they describe, at ``origin/main``: kitesforu-api's routes,
kitesforu-schemas' request models (under the workers venv, which carries them), kitesforu-workers' still grant and
kitesforu-course-workers' writeup formats. Each SKIPS, and says so, only when that checkout or interpreter is absent.

Offline and $0.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pytest

QA = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QA / "scripts"))
import verification_flows  # noqa: E402
import verification_job  # noqa: E402
from producer_tree import workers_python as _workers_python  # noqa: E402
from test_create_verification_job import _IMAGES, DEAD, Harness  # noqa: E402

#: The checkouts whose origin/main the contract pins read, beside the workers one (``producer_tree``).
API_REPO = Path(os.environ.get("KFU_API_REPO") or "/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-api")
COURSE_WORKERS_REPO = Path(os.environ.get("KFU_COURSE_WORKERS_REPO")
                           or "/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-course-workers")

#: The stub: the trigger (a POST to ``*/generate``) answers from STUB_TRIGGER_*, every other POST from STUB_POST_*,
#: and each GET from the status sequence, as the harness's own stub does.
FLOW_CURL_STUB = r"""#!/bin/bash
{ printf 'curl'; for a in "$@"; do printf '\x1f%s' "$a"; done; printf '\n'; } >> "$STUB_LOG"
post=""; url=""; w=""
for a in "$@"; do
  [[ "$a" == "POST" ]] && post=1
  [[ "$a" == http* ]] && url="$a"
  [[ "$a" == *"%{http_code}"* ]] && w=1
done
if [[ -n "$post" ]]; then
  p=STUB_POST; [[ "$url" == */generate ]] && p=STUB_TRIGGER
  ex="${p}_EXIT"; dl="${p}_DELAY"; rs="${p}_RESPONSE"; hc="${p}_HTTP"
  [[ -n "${!ex:-}" ]] && exit "${!ex}"
  [[ -n "${!dl:-}" ]] && /bin/sleep "${!dl}"
  printf '%s' "${!rs:-}"
  [[ -n "$w" ]] && printf '\n%s' "${!hc:-200}"
  exit 0
fi
n=$(cat "$STUB_SEQ_POS"); total=$(wc -l < "$STUB_STATUS_SEQ" | tr -d ' ')
(( total == 0 )) && exit 7
idx=$(( n < total ? n + 1 : total ))
echo $(( n + 1 )) > "$STUB_SEQ_POS"
line=$(sed -n "${idx}p" "$STUB_STATUS_SEQ")
if [[ "$line" == EXIT* ]]; then exit "${line#EXIT}"; fi
printf '%s' "$line"
"""


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    h._stub("curl", FLOW_CURL_STUB)
    return h


def posts(h: Harness) -> List[List[str]]:
    return [c for c in h.calls("curl") if "POST" in c]


def url_of(call: List[str]) -> str:
    return next(a for a in call if a.startswith("http"))


def headers(call: List[str]) -> List[str]:
    return [call[i + 1] for i, a in enumerate(call) if a == "-H"]


# ---------------------------------------------------------------------------------------------
# The three flows, as a run sends them and a fake api answers them (synthetic ids)
# ---------------------------------------------------------------------------------------------

OWNER = ("--on-behalf-of", "user_abc123")
COURSE_ID = "course_test_1"

EXPECTED_CLASS_BODY = (
    '{"topic": "pipeline verification", "subject": "General", "total_lessons": 3, "lesson_duration_min": 0.167, '
    '"questions_per_lesson": 3, "style": "Explainer", "language": "en-US", "generation_mode": "autopilot"}'
)
EXPECTED_WRITEUP_BODY = '{"topic": "pipeline verification", "formats_requested": ["linkedin_post"]}'

#: The replies of the api's own routes, in their shapes and with their messages (kitesforu-api routes/classes/crud.py
#: and generation.py, routes/writeups/crud.py and generation.py, routes/courses/export.py).
CLASS_CREATED = json.dumps({"class_id": "cls_test_1", "status": "draft",
                            "message": "Class created successfully. Generate curriculum to proceed."})
CLASS_STARTED = json.dumps({"class_id": "cls_test_1", "status": "syllabus_generation",
                            "message": "Curriculum generation started"})
WRITEUP_CREATED = json.dumps({"writeup_id": "wrt_test_1", "status": "draft",
                              "message": "Writeup created. Call generate to start content creation."})
WRITEUP_STARTED = json.dumps({"writeup_id": "wrt_test_1", "status": "researching",
                              "message": "Writeup generation started"})
EXPORT_ACCEPTED = json.dumps({"status": "pending", "export_id": "course_export_1",
                              "source_fingerprint": "0123456789abcdef", "included_count": 2, "skipped": []})


def class_stored(status: str = "syllabus_generation", **settings) -> str:
    """``GET /v1/classes/<id>``: the class doc's model dump, its settings as the body set them unless overridden."""
    sent = json.loads(EXPECTED_CLASS_BODY)
    base = {k: sent[k] for k in verification_flows._CLASS_SETTINGS}
    return json.dumps({"class_id": "cls_test_1", "topic": sent["topic"], "subject": sent["subject"], "status": status,
                       "settings": {**base, "quiz_enabled": True, "mastery_threshold": 0.7, **settings}})


def writeup_stored(status: str = "researching", **fields) -> str:
    return json.dumps({"writeup_id": "wrt_test_1", "topic": "pipeline verification",
                       "formats_requested": ["linkedin_post"], "status": status, **fields})


def export_stored(status: str = "pending", export_id: Optional[str] = "course_export_1") -> str:
    return json.dumps({"status": status, "export_id": export_id, "included": [{}, {}], "skipped": [],
                       "stale": False})


#: Per flow: the arguments that choose it, the replies of a run that goes well, its id line and its status URL.
FLOWS: Dict[str, dict] = {
    "class": {"args": ("--class", *OWNER), "noun": "class",
              "env": {"STUB_POST_RESPONSE": CLASS_CREATED, "STUB_TRIGGER_RESPONSE": CLASS_STARTED},
              "stored": class_stored, "id_line": "class_id=cls_test_1",
              "create": f"{DEAD}/v1/classes", "trigger": f"{DEAD}/v1/classes/cls_test_1/curriculum/generate",
              "status": f"{DEAD}/v1/classes/cls_test_1"},
    "writeup": {"args": ("--writeup",), "noun": "writeup",
                "env": {"STUB_POST_RESPONSE": WRITEUP_CREATED, "STUB_TRIGGER_RESPONSE": WRITEUP_STARTED},
                "stored": writeup_stored, "id_line": "writeup_id=wrt_test_1",
                "create": f"{DEAD}/v1/writeups", "trigger": f"{DEAD}/v1/writeups/wrt_test_1/generate",
                "status": f"{DEAD}/v1/writeups/wrt_test_1"},
    "video-export": {"args": ("--course-video-export", COURSE_ID), "noun": "video export",
                     "env": {"STUB_POST_RESPONSE": EXPORT_ACCEPTED, "STUB_POST_HTTP": "202"},
                     "stored": export_stored, "id_line": "export_id=course_export_1",
                     "create": f"{DEAD}/v1/courses/{COURSE_ID}/continuous-video", "trigger": None,
                     "status": f"{DEAD}/v1/courses/{COURSE_ID}/continuous-video"},
}
TRIGGERED = ("class", "writeup")


def env_for(flow: str, workers_src: Optional[str], **extra: str) -> Dict[str, str]:
    """A class prices its lessons through the workers tree; a writeup and an export read no repo, and prove it."""
    if flow == "class":
        assert workers_src, "a class is priced through the workers tree"
        tree = {"WORKERS_SRC": workers_src}
    else:
        tree = {"KFU_WORKERS_REPO": "/nonexistent"}
    return {**FLOWS[flow]["env"], **tree, **extra}


@pytest.fixture
def flow_src(request):
    """The workers tree only the class needs: the other flows run without one."""
    return request.getfixturevalue("workers_src") if request.param == "class" else None


def run_flow(harness: Harness, flow: str, src: Optional[str], *extra: str, stored: Optional[List[str]] = None,
             **env: str) -> subprocess.CompletedProcess:
    harness.statuses(stored if stored is not None else [FLOWS[flow]["stored"]()])
    return harness.run(*FLOWS[flow]["args"], *extra, env=env_for(flow, src, **env))


# ---------------------------------------------------------------------------------------------
# The bodies and the doors
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("flow_src,flow,body", [("class", "class", EXPECTED_CLASS_BODY),
                                                ("writeup", "writeup", EXPECTED_WRITEUP_BODY),
                                                ("video-export", "video-export", "")], indirect=["flow_src"])
def test_each_dry_run_prints_its_smallest_request_and_its_doors_and_sends_nothing(harness, flow_src, flow, body):
    r = harness.run(*FLOWS[flow]["args"], "--dry-run", env=env_for(flow, flow_src))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == body
    assert f"POST {FLOWS[flow]['create']}\n" in r.stderr
    assert "-H 'Authorization: Bearer <TEST_API_KEY redacted>'" in r.stderr
    if FLOWS[flow]["trigger"]:
        doors = verification_flows.CLASS_DOORS if flow == "class" else verification_flows.WRITEUP_DOORS
        assert f"then POST {DEAD}{doors.trigger.replace('{id}', '<' + doors.id_key + '>')}" in r.stderr
    else:
        assert "(no body)" in r.stderr and "then POST" not in r.stderr
    assert harness.calls() == [], "a dry run called a network binary"


#: Run under the workers interpreter, which carries kitesforu_schemas (this repo does not depend on it).
_FLOW_MODEL_PROBE = r'''
import json, sys
import kitesforu_schemas
from kitesforu_schemas import CreateClassRequest, CreateWriteupRequest, WriteupFormat
sent = json.loads(sys.stdin.read())
cls, wrt = sent["class"], sent["writeup"]
def floor(name):
    return next(m.ge for m in CreateClassRequest.model_fields[name].metadata if getattr(m, "ge", None) is not None)
def rejected(model, body):
    try:
        model.model_validate(body)
        return False
    except Exception:
        return True
CreateClassRequest.model_validate(cls)
CreateWriteupRequest.model_validate(wrt)
names = ("total_lessons", "questions_per_lesson", "lesson_duration_min")
print(json.dumps({
    "version": kitesforu_schemas.__version__,
    "class_undeclared": sorted(set(cls) - set(CreateClassRequest.model_fields)),
    "writeup_undeclared": sorted(set(wrt) - set(CreateWriteupRequest.model_fields)),
    "class_tier_field": "quality_tier" in CreateClassRequest.model_fields,
    "writeup_tier_field": "quality_tier" in CreateWriteupRequest.model_fields,
    "floors": {n: floor(n) for n in names},
    "below_floor_rejected": {n: rejected(CreateClassRequest, {**cls, n: floor(n) - (0.001 if "duration" in n else 1)})
                             for n in names},
    "no_format_rejected": rejected(CreateWriteupRequest, {**wrt, "formats_requested": []}),
    "formats": [f.value for f in WriteupFormat],
}))
'''


def test_the_bodies_sit_on_the_floors_of_the_apis_own_request_models(harness, workers_src):
    """The api's CreateClassRequest and CreateWriteupRequest accept each body, declare every key it sends (both drop an
    undeclared key without an error) and have no tier field; the class sits on each of its model floors, and one below
    is refused. So "the smallest request the api accepts" is the models' own bounds, read from them."""
    py = _workers_python()
    if not py:
        pytest.skip("no kitesforu-workers venv to import kitesforu_schemas from (set KFU_WORKERS_PYTHON): "
                    "SKIPPED, not passed")
    cls = harness.run("--class", "--dry-run", *OWNER, env={"WORKERS_SRC": workers_src}).stdout
    wrt = harness.run("--writeup", "--dry-run").stdout
    probe = subprocess.run([py, "-I", "-c", _FLOW_MODEL_PROBE], input=json.dumps({"class": json.loads(cls),
                                                                                   "writeup": json.loads(wrt)}),
                           capture_output=True, text=True, timeout=120)
    assert probe.returncode == 0, probe.stderr[-2000:]
    got = json.loads(probe.stdout.strip().splitlines()[-1])
    sent = json.loads(cls)
    assert got["class_undeclared"] == [] and got["writeup_undeclared"] == [], got
    assert got["class_tier_field"] is False and got["writeup_tier_field"] is False, got
    assert {n: sent[n] for n in got["floors"]} == got["floors"], got
    assert set(got["below_floor_rejected"].values()) == {True} and got["no_format_rejected"] is True, got
    assert verification_flows.WRITEUP_FORMAT in got["formats"], got


def _origin_main_file(repo: Path, path: str) -> str:
    """``path`` at ``repo``'s origin/main. SKIPS only when there is no checkout; a file that moved FAILS."""
    if not (repo / ".git").exists():
        pytest.skip(f"no checkout at {repo}: SKIPPED, not passed")
    out = subprocess.run(["git", "-C", str(repo), "show", f"origin/main:{path}"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout, f"{path} is not on {repo.name} origin/main: {out.stderr}"
    return out.stdout


def test_the_doors_and_the_started_phrases_are_the_apis_own():
    """Each route the script POSTs to or reads, and the phrase it reads as "generation started", is in the api's own
    route file. A route that moved, or a reply that stopped saying it started, turns this red rather than every
    trigger into an exit 8."""
    classes = (_origin_main_file(API_REPO, "src/api/routes/classes/crud.py")
               + _origin_main_file(API_REPO, "src/api/routes/classes/generation.py"))
    writeups = (_origin_main_file(API_REPO, "src/api/routes/writeups/crud.py")
                + _origin_main_file(API_REPO, "src/api/routes/writeups/generation.py"))
    export = (_origin_main_file(API_REPO, "src/api/routes/courses/export.py")
              + _origin_main_file(API_REPO, "src/api/services/exports/continuous_video.py"))
    for doors, source in ((verification_flows.CLASS_DOORS, classes), (verification_flows.WRITEUP_DOORS, writeups),
                          (verification_flows.export_doors("{course_id}"), export)):
        param = "{course_id}" if doors.noun == "video export" else "{" + doors.id_key + "}"
        route = lambda verb, path: re.search(rf'@router\.{verb}\("{re.escape(path)}"[,)]', source)
        assert route("post", doors.create), doors.create
        assert route("get", doors.read.replace("{id}", param)), doors.read
        if doors.trigger:
            assert route("post", doors.trigger.replace("{id}", param)), doors.trigger
            assert re.search(rf'"message": "{re.escape(doors.started)}"', source, flags=re.I), doors.started
        assert f'"{doors.id_key}":' in source or f"{doors.id_key}=" in source, doors.id_key


def test_the_writeup_format_is_the_smallest_target():
    """``linkedin_post`` is the format with the fewest target words in course-workers' FORMAT_CONFIGS."""
    source = _origin_main_file(COURSE_WORKERS_REPO, "src/workers/stages/writeup_generate/worker.py")
    targets = dict(re.findall(r'^    "(\w+)": \{\n(?:.*\n){0,3}?\s*"target_words": (\d+)', source, flags=re.M))
    assert len(targets) >= 10, targets   # the probe found the table, not a fragment of it
    assert min(targets, key=lambda f: int(targets[f])) == verification_flows.WRITEUP_FORMAT, targets


# ---------------------------------------------------------------------------------------------
# The estimates: what is priced, what is not, and the ACK
# ---------------------------------------------------------------------------------------------

def test_a_class_is_priced_as_its_lessons_and_names_the_calls_it_cannot_price():
    body = json.loads(EXPECTED_CLASS_BODY)
    lesson = verification_job.estimate(verification_job.implied_episode_body(0.167, "medium"), images=_IMAGES)
    est = verification_flows.class_estimate(body, images=_IMAGES)
    assert est.low is None and est.high is None and est.text.startswith("unknown = "), est.text
    assert f"3 lesson episode(s) at medium ~{verification_job._span(3 * lesson.low, 3 * lesson.high)}" in est.text
    assert "the class syllabus (1 LLM call) and 4 quiz calls" in est.text
    with pytest.raises(verification_job.PricingUnavailable):
        verification_flows.class_estimate(body, images=None)   # a still it cannot price is a refusal, never a guess


def test_the_class_estimate_follows_its_lessons_and_their_length():
    body = {**json.loads(EXPECTED_CLASS_BODY), "total_lessons": 4, "lesson_duration_min": 1.0}
    lesson = verification_job.estimate(verification_job.implied_episode_body(1.0, "medium"), images=_IMAGES)
    est = verification_flows.class_estimate(body, images=_IMAGES)
    assert f"4 lesson episode(s) at medium ~{verification_job._span(4 * lesson.low, 4 * lesson.high)}" in est.text
    assert "5 quiz calls" in est.text
    assert verification_flows.class_ack_decision(body) == (True, "tier=medium duration=1.0min paid_stills=4 "
                                                                 "est=unknown ")


#: Run under the workers interpreter: the producer's grant for one class lesson, the job the orchestrator creates.
_LESSON_GRANT_PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from workers.stages.visuals.policy import policy_for_job, scene_budget_for
lesson = json.loads(sys.argv[2])
def grant(doc):
    return scene_budget_for(doc, policy_for_job(doc), opted_in=False, born_short=False).granted
grants = {str(sub): grant({**lesson, **({"subscription_tier": sub} if sub else {})})
          for sub in ("trial", "free", "enthu", "pro", "payg", "pro_plus", "ultimate", None)}
qa = {**lesson, "user_id": "test_user_e2e", "subscription_tier": "ultimate"}
print(json.dumps({"grants": grants, "qa_control": grant(qa)}))
'''


def test_a_class_lesson_may_buy_the_stills_a_course_episode_may(workers_src):
    """A class lesson is the job the orchestrator creates: a real owner's, at medium, with no visuals key, stamped
    with its parent. The producer grants it at most COURSE_EPISODE_IMPLIED_STILLS paid stills and reaches that bound,
    the share :func:`verification_flows.class_lesson_body` prices."""
    py = _workers_python()
    if not py:
        pytest.skip("no kitesforu-workers venv (set KFU_WORKERS_PYTHON): SKIPPED, not passed")
    lesson = {"user_id": "user_synthetic_owner", "quality_tier": verification_flows.LESSON_TIER,
              "allow_premium": False, "parent_type": "class", "parent_id": "cls_synthetic"}
    probe = subprocess.run([py, "-I", "-c", _LESSON_GRANT_PROBE, workers_src, json.dumps(lesson)],
                           capture_output=True, text=True, timeout=180)
    assert probe.returncode == 0, probe.stderr[-2000:]
    got = json.loads(probe.stdout.strip().splitlines()[-1])
    assert got["qa_control"] == 0, "positive control: a QA identity is granted no paid still"
    assert max(got["grants"].values()) == verification_job.COURSE_EPISODE_IMPLIED_STILLS, got["grants"]
    assert verification_job.ordered_stills(verification_flows.class_lesson_body(json.loads(EXPECTED_CLASS_BODY))) \
        == verification_job.COURSE_EPISODE_IMPLIED_STILLS


@pytest.mark.parametrize("estimate", [verification_flows.writeup_estimate(json.loads(EXPECTED_WRITEUP_BODY)),
                                      verification_flows.export_estimate()])
def test_an_unmeasured_flow_prints_no_number_and_says_why(estimate):
    assert estimate.low is None and estimate.high is None, estimate
    assert estimate.text.startswith("unknown = ") and "NOT PRICED" in estimate.text, estimate.text
    assert estimate.detail and any("not priced" in d for d in estimate.detail), estimate.detail


@pytest.mark.parametrize("flow_src,flow,reason", [("class", "class", "tier=medium paid_stills=4 est=unknown "),
                                                  ("writeup", "writeup", "tier=medium est=unknown "),
                                                  ("video-export", "video-export", "est=unknown ")],
                         indirect=["flow_src"])
def test_every_flow_needs_the_ack_and_says_why(harness, flow_src, flow, reason):
    r = run_flow(harness, flow, flow_src)
    assert r.returncode == 3, (r.returncode, r.stderr)
    assert f"T4 ESCALATION BLOCKED ({reason})" in r.stderr, r.stderr
    assert "est=unknown = " in r.stderr
    assert harness.calls("curl") == [], "a run that needs the ACK reached curl"
    dry = harness.run(*FLOWS[flow]["args"], "--dry-run", env=env_for(flow, flow_src))
    assert f"a real run would need the ACK ({reason})" in dry.stderr, dry.stderr


# ---------------------------------------------------------------------------------------------
# The refusals: before anything is planned or sent
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("flow,flag", [
    ("class", ("--tier", "low")), ("class", ("--wait",)), ("class", ("--visuals",)), ("class", ("--format", "drama")),
    ("writeup", ("--duration", "1.0")), ("writeup", ("--style", "Interview")), ("writeup", ("--language", "fr-FR")),
    ("writeup", ("--tier", "low")), ("video-export", ("--topic", "t")), ("video-export", ("--duration", "1.0")),
    ("video-export", ("--wait",)),
])
def test_a_flag_a_flows_request_cannot_carry_is_refused_before_anything_is_sent(harness, flow, flag):
    harness.fresh_ack()
    r = harness.run(*FLOWS[flow]["args"], *flag, env=FLOWS[flow]["env"])
    mode_flag = FLOWS[flow]["args"][0]
    assert r.returncode == 1 and f"{mode_flag} does not take {flag[0]}:" in r.stderr, (r.returncode, r.stderr)
    assert harness.calls() == [] and r.stdout == ""


@pytest.mark.parametrize("owner", [(), ("--on-behalf-of", "test_synthetic")])
def test_a_class_without_a_real_owner_is_refused_before_the_ack_and_sends_nothing(harness, workers_src, owner):
    for extra in ((), ("--dry-run",)):
        r = harness.run("--class", *extra, *owner, env={"WORKERS_SRC": workers_src, **FLOWS["class"]["env"]})
        assert r.returncode == 1 and "--class needs --on-behalf-of <user_" in r.stderr, (r.returncode, r.stderr)
        assert "T4 ESCALATION" not in r.stderr and "would need the ACK" not in r.stderr
        assert harness.calls() == [] and r.stdout == ""


@pytest.mark.parametrize("modes", [("--class", "--writeup"), ("--course", "--class"),
                                   ("--writeup", "--course-video-export", COURSE_ID)])
def test_two_runs_at_once_are_refused(harness, modes):
    r = harness.run(*modes, "--dry-run", *OWNER)
    assert r.returncode == 1 and "are two different runs: pick one" in r.stderr, (r.returncode, r.stderr)
    assert harness.calls() == []


def test_the_export_never_reads_a_flag_as_its_course_id(harness):
    """``--course-video-export --dry-run`` names no course: the id is refused as missing, and --dry-run stays what it
    is. Read as the id, it would have dropped the dry run and sent the POST it was meant to rehearse."""
    r = harness.run("--course-video-export", "--dry-run", *OWNER)
    assert r.returncode == 1 and "takes a course id (letters, digits, _ and -), got ''" in r.stderr, r.stderr
    assert harness.calls() == []


@pytest.mark.parametrize("course_id", ["", "--dry-run", "-x", "a/b", "a b", "a?b=1", "../x"])
def test_a_course_id_that_is_not_one_path_segment_is_refused(course_id):
    with pytest.raises(ValueError):
        verification_flows.plan("video-export", topic="t", duration="0.167", style="Explainer", language="en-US",
                                course_id=course_id, workers_src="", catalog_label="")


# ---------------------------------------------------------------------------------------------
# The exit codes, over the fake api
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("flow_src,flow", [(f, f) for f in FLOWS], indirect=["flow_src"])
def test_a_flow_that_goes_well_posts_triggers_reads_back_and_exits_0(harness, flow_src, flow):
    harness.fresh_ack()
    r = run_flow(harness, flow, flow_src, *OWNER, "--on-behalf-of-email", "ops team@example.com")
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert FLOWS[flow]["id_line"] in r.stdout and f"status: {FLOWS[flow]['status']}" in r.stdout, r.stdout
    assert f"read back: the api stored the {FLOWS[flow]['noun']} as sent" in r.stderr
    sent = posts(harness)
    create = sent[0]
    assert url_of(create) == FLOWS[flow]["create"]
    assert headers(create) == ["Authorization: Bearer dummy-test-key", "X-On-Behalf-Of: user_abc123",
                               "X-On-Behalf-Of-Email: ops team@example.com", "Content-Type: application/json"]
    if flow == "video-export":
        assert "-d" not in create and len(sent) == 1, "the export route takes no body and has no trigger"
    else:
        body = EXPECTED_CLASS_BODY if flow == "class" else EXPECTED_WRITEUP_BODY
        assert create[create.index("-d") + 1] == body
        trigger = sent[1]
        assert len(sent) == 2 and url_of(trigger) == FLOWS[flow]["trigger"] and "-d" not in trigger
        assert headers(trigger) == headers(create)[:-1], "the trigger carries the create's headers minus Content-Type"
    gets = [c for c in harness.calls("curl") if "POST" not in c]
    assert len(gets) == 1 and url_of(gets[0]) == FLOWS[flow]["status"], gets


def test_an_export_already_ready_queues_nothing_and_says_so(harness):
    harness.fresh_ack()
    ready = export_stored(status="ready")
    r = run_flow(harness, "video-export", None, stored=[ready], STUB_POST_RESPONSE=ready, STUB_POST_HTTP="200")
    assert r.returncode == 0, r.stderr
    assert "already ready for this exact set of lessons: nothing was queued" in r.stderr


@pytest.mark.parametrize("flow_src,flow,http,body,rc,needle", [
    ("class", "class", "422", '{"detail": "validation"}', 1, "REJECTED the request (HTTP 422) before creating a class"),
    ("writeup", "writeup", "429", '{"detail": "rate"}', 1, "before creating a writeup; nothing was charged"),
    ("video-export", "video-export", "409", '{"detail": "not_enough_ready"}', 1,
     "REJECTED the request (HTTP 409) before creating a video export"),
    ("video-export", "video-export", "404", '{"detail": "Course not found"}', 1, "Course not found"),
    ("class", "class", "500", '{"detail": "boom"}', 8, "The class MAY have been created"),
    ("writeup", "writeup", "200", '{"job_id": "not_a_writeup"}', 8, "No writeup id in the api's reply (HTTP 200)"),
    ("video-export", "video-export", "500", '{"detail": "Failed to request continuous video"}', 8,
     "The video export MAY have been created"),
], indirect=["flow_src"])
def test_the_create_is_classified_by_what_it_says_about_what_exists(harness, flow_src, flow, http, body, rc, needle):
    harness.fresh_ack()
    r = run_flow(harness, flow, flow_src, STUB_POST_HTTP=http, STUB_POST_RESPONSE=body)
    assert r.returncode == rc and needle in r.stderr, (r.returncode, r.stderr)
    assert len(posts(harness)) == 1, "a create that did not answer with an id was followed by a trigger"


@pytest.mark.parametrize("flow_src,flow", [(f, f) for f in FLOWS], indirect=["flow_src"])
def test_a_create_transport_failure_says_it_may_exist(harness, flow_src, flow):
    harness.fresh_ack()
    r = run_flow(harness, flow, flow_src, STUB_POST_EXIT="28")
    assert r.returncode == 8 and f"The {FLOWS[flow]['noun']} MAY have been created" in r.stderr, r.stderr


@pytest.mark.parametrize("flow_src,flow", [(f, f) for f in TRIGGERED], indirect=["flow_src"])
@pytest.mark.parametrize("http,reply,rc,needle", [
    ("403", '{"detail": "Content creation is temporarily limited to approved accounts."}', 5,
     "REFUSED its generation trigger (HTTP 403): nothing\nwill run, and nothing was charged"),
    ("400", '{"detail": "Insufficient credits. Need 2 credits for this writeup."}', 5, "REFUSED its generation"),
    ("500", '{"detail": "boom"}', 8, "its generation MAY have started"),
    ("200", '{"message": "queued"}', 8, "without saying it started"),
])
def test_the_trigger_is_classified_by_what_it_says_about_the_generation(harness, flow_src, flow, http, reply, rc,
                                                                         needle):
    harness.fresh_ack()
    r = run_flow(harness, flow, flow_src, STUB_TRIGGER_HTTP=http, STUB_TRIGGER_RESPONSE=reply)
    assert r.returncode == rc and needle in r.stderr, (r.returncode, r.stderr)
    assert FLOWS[flow]["id_line"] in r.stdout, "the draft WAS created, so its id is printed"
    assert harness.gets() == 0, "a trigger that did not start was read back as if it had"


@pytest.mark.parametrize("flow_src,flow", [(f, f) for f in TRIGGERED], indirect=["flow_src"])
def test_a_trigger_transport_failure_says_the_generation_may_have_started(harness, flow_src, flow):
    harness.fresh_ack()
    r = run_flow(harness, flow, flow_src, STUB_TRIGGER_EXIT="28")
    assert r.returncode == 8, (r.returncode, r.stderr)
    assert "trigger failed at the transport level (timed out, curl exit 28): its generation MAY" in r.stderr
    assert FLOWS[flow]["id_line"] in r.stdout


@pytest.mark.parametrize("flow_src,flow,stored,needle", [
    ("class", "class", class_stored(total_lessons=5), "settings.total_lessons: sent 3, stored 5"),
    ("class", "class", class_stored(status="draft"), "the class is stored as draft"),
    ("writeup", "writeup", writeup_stored(formats_requested=["blog_post"]),
     'formats_requested: sent ["linkedin_post"], stored ["blog_post"]'),
    ("video-export", "video-export", export_stored(export_id="course_other"),
     'export_id: sent "course_export_1", stored "course_other"'),
    ("video-export", "video-export", export_stored(status="absent", export_id=None), "the course reports none"),
], indirect=["flow_src"])
def test_a_stored_request_other_than_sent_is_exit_6(harness, flow_src, flow, stored, needle):
    harness.fresh_ack()
    r = run_flow(harness, flow, flow_src, stored=[stored])
    assert r.returncode == 6 and "THE API STORED OTHER THAN THIS SCRIPT SENT" in r.stderr, (r.returncode, r.stderr)
    assert needle in r.stderr, r.stderr
    assert FLOWS[flow]["id_line"] in r.stdout


@pytest.mark.parametrize("flow_src,flow", [(f, f) for f in FLOWS], indirect=["flow_src"])
def test_an_unreadable_read_back_is_exit_7_after_three_reads(harness, flow_src, flow):
    harness.fresh_ack()
    r = run_flow(harness, flow, flow_src, stored=["EXIT7", '{"detail": "Not authenticated"}', "EXIT28"])
    assert r.returncode == 7 and "could not be read back (3 attempts)" in r.stderr, (r.returncode, r.stderr)
    assert harness.gets() == 3 and FLOWS[flow]["id_line"] in r.stdout


@pytest.mark.parametrize("flow_src,flow", [(f, f) for f in TRIGGERED], indirect=["flow_src"])
def test_a_signal_while_the_trigger_is_out_is_exit_8_and_names_what_exists(harness, flow_src, flow):
    """The draft exists once the create answered, so a stop while the trigger is in flight says it WAS created and
    exits 8, never 128+N, which says nothing was sent."""
    harness.fresh_ack()
    harness.statuses([FLOWS[flow]["stored"]()])
    proc = subprocess.Popen(["/bin/bash", str(harness.script), *FLOWS[flow]["args"]],
                            env=harness.env(env_for(flow, flow_src, STUB_TRIGGER_DELAY="2")),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 60
        while len(posts(harness)) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert len(posts(harness)) == 2, "the trigger never went out"
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 8, (proc.returncode, err)
    noun_id = FLOWS[flow]["id_line"].split("=")[1]
    assert f"Stopped (SIGTERM): {FLOWS[flow]['noun']} {noun_id} WAS created" in err, err


# ---------------------------------------------------------------------------------------------
# The read-back, unit by unit
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("flow,snapshot", [
    ("class", None), ("class", {"class_id": "cls_test_1"}), ("class", {"class_id": "cls_other", "settings": {}}),
    ("writeup", {"writeup_id": "wrt_other"}), ("video-export", {"export_id": "course_export_1"}),
])
def test_a_read_that_is_not_the_stored_doc_is_unreadable_never_a_pass(flow, snapshot):
    created = {"class": "cls_test_1", "writeup": "wrt_test_1", "video-export": "course_export_1"}[flow]
    with pytest.raises(verification_flows.UnreadableSnapshot):
        verification_flows.check_stored(flow, {}, snapshot, created_id=created)
    rc = verification_flows.main(["check-stored", f"--flow={flow}", "--requested=", f"--created-id={created}",
                                  f"--snapshot={json.dumps(snapshot)}"])
    assert rc == 2


@pytest.mark.parametrize("flow,sent,stored,created", [
    ("class", EXPECTED_CLASS_BODY, class_stored(), "cls_test_1"),
    ("writeup", EXPECTED_WRITEUP_BODY, writeup_stored(), "wrt_test_1"),
    ("video-export", "", export_stored(), "course_export_1"),
])
def test_what_was_sent_stored_as_sent_reads_back_clean(flow, sent, stored, created):
    assert verification_flows.check_stored(flow, json.loads(sent) if sent else {}, json.loads(stored),
                                           created_id=created) == []


# ---------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------

def test_help_documents_the_three_flows(harness):
    r = harness.run("--help")
    assert r.returncode == 0
    for needle in ("--class", "/v1/classes", "curriculum/generate", "autopilot", "--writeup", "/v1/writeups",
                   "linkedin_post", "--course-video-export <course_id>", "continuous-video", "NOT priced", "the ACK",
                   "409", "user_…", "reads back what\n#   the api STORED"):
        assert needle in r.stdout, needle


def test_the_cost_changelog_records_the_three_flows():
    sections = (QA / "COST_CHANGELOG.md").read_text().split("\n## ")[1:]
    entry = next((s for s in sections if s.startswith("2026-11-07 — the verification job can create the smallest "
                                                      "class, writeup and course video export")), "")
    assert entry, [s[:40] for s in sections[:3]]
    for needle in ("`--class`", "`--writeup`", "`--course-video-export", "scripts/create_verification_job.sh",
                   "scripts/verification_flows.py", "medium", "not priced", "Pricing-page implication"):
        assert needle in entry, needle
