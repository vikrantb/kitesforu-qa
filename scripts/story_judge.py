#!/usr/bin/env python3
"""STORY JUDGE — an independent, deliberately harsh critic for what the listener actually HEARS.

FOUNDER, 2026-08-12, on jobs ``dc785764`` ("The Secret Affair") and ``6e38e036``:

    "this is such a pointless bits of words ... ask an unbiased independent LLM and you will see
     how pathetic this is. give it advise not to just give us good feedback, but give critical if
     needed."

    "it is NOT the voice or dialogs or expression, it is purely WHAT AM I LISTENING ... you need to
     really work on all genres and styles and types and ensure that we create engaging and really
     good content what the user expects not junk pile of words and dialogues"

So this judge scores **substance only**. Voice, TTS expressiveness, music, pacing and visuals are
explicitly OUT OF SCOPE — they are graded elsewhere (``audio_quality_judge.py``, the visual gates).
The question here is the one the founder asked: *as a piece of writing, is this worth listening to,
and does it deliver what the user asked for?*

WHY A DIFFERENT MODEL FAMILY. The pipeline's own authors and most of our tooling are Claude. A
Claude script graded by Claude is an echo chamber (`peer-session-collaboration.md`: "you share the
same model, so a naive review is an echo"). This judge calls **Gemini on Vertex** — a genuinely
different family — and is told to default to criticism. Model is overridable, but the DEFAULT must
stay off the family that wrote the thing being judged.

ANTI-SYCOPHANCY IS THE WHOLE DESIGN, not a line in the prompt:
  * the judge never sees the topic's marketing framing as praise, only as a CONTRACT to check;
  * it is told a review that agrees to be agreeable is a FAILED review;
  * it must name ONE most-damaging defect, and scores are anchored so 5 = "competent but forgettable"
    rather than the usual LLM 8-out-of-10 default;
  * it is asked what a listener DOES at 10 seconds in — the only question that predicts a skip;
  * praise sandwiches are banned; defects come FIRST and the verdict LAST.

GENRE AWARENESS. A horror short and a how-to explainer fail in different ways, so the rubric is
selected per content type instead of one flat list. Every rubric además carries the two axes the
founder's complaint is really about, for EVERY genre:
  * ``purpose_fulfillment`` — did it deliver what the TITLE/TOPIC promised the user? (job 6e38e036
    promised "Seductive Secrets of Halloween's Darkest Spirits" — a listicle framing — and
    delivered a fiction vignette. The user did not get what they asked for.)
  * ``substance`` — is there anything here, or is it atmosphere with no content?

COST: one Gemini Flash call per script (~$0.001). This is a T2 judgment tier on an EXISTING
artifact — no generation, no re-render. Reuses the persisted ``outputs.script`` and never creates a
job (test-cost ladder).

USAGE
    python3 scripts/story_judge.py <job_id> [<job_id> ...]        # judge persisted scripts
    python3 scripts/story_judge.py --all-recent 20                # sweep the fleet
    python3 scripts/story_judge.py <job_id> --model gemini-2.5-pro
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

PROJECT = "kitesforu-dev"
LOCATION = "us-central1"
#: Deliberately NOT a Claude model — see the module docstring.
DEFAULT_JUDGE_MODEL = "gemini-2.5-flash"

# ── The critic persona ─────────────────────────────────────────────────────────────
# A named human with taste and a reason to be hard to please. A persona outperforms "you are a
# helpful assistant" because it gives the model a stake: this one loses something (their time,
# their audience's trust) when the piece is mediocre.
CRITIC_PERSONA = """\
You are RUTH KELLER, a commissioning editor with 22 years in audio drama and narrative podcasting.
You have killed more scripts than you have greenlit. You are respected because you are RIGHT, not
because you are kind. Writers seek you out precisely because you tell them the thing their friends
will not.

Your standard: a listener has thousands of alternatives one thumb-swipe away. A piece earns its
runtime or it does not exist. "Nicely written" is not a pass — plenty of beautifully-worded pieces
are about nothing, and those are the ones you kill fastest, because good sentences disguise an
empty premise and waste everyone's time.

HOW YOU REVIEW:
- Defects FIRST. Verdict LAST. Never a praise sandwich.
- A review that agrees to be agreeable is a FAILED review. If you find yourself being encouraging,
  stop and ask what you are avoiding saying.
- You are not scoring effort or craft-in-isolation. You are scoring: would a real person keep
  listening, and did they get what they came for?
- You quote the text when you criticise it. A criticism without a quote is an opinion.
- You never soften a score to be fair to the writer. The audience does not grade on a curve.
"""

SCORING_ANCHORS = """\
SCORING ANCHORS — use the full range. Most published work is a 5. Do NOT cluster at 7-8.
  1-2  Broken. Incoherent, or actively wastes the listener's time.
  3-4  Weak. Recognisable competence, nothing that would make anyone stay or share.
  5    Competent but FORGETTABLE. Nothing wrong; no reason to exist. This is the default.
  6-7  Good. One real strength that a listener would notice.
  8    Genuinely strong. A listener would finish it and remember one thing.
  9-10 Exceptional. A listener would send it to someone. Reserve this; almost nothing earns it.
"""

# ── Genre-aware rubrics ────────────────────────────────────────────────────────────
# Two axes are UNIVERSAL because they are the founder's actual complaint, and they are appended to
# every rubric below rather than living in only one.
_UNIVERSAL = [
    ("purpose_fulfillment",
     "Did this deliver what the TITLE and TOPIC promised the user? If the title frames a listicle "
     "or an explainer and the body is a fiction vignette (or vice versa), this is a severe failure "
     "no matter how good the prose is — the user did not get the thing they asked for."),
    ("substance",
     "Is there actually CONTENT here, or is it atmosphere, mood and texture with nothing underneath? "
     "Strip the adjectives and sensory detail: what remains? If almost nothing remains, say so."),
    ("engagement_at_10s",
     "Concretely: what does a real listener DO ten seconds in — keep listening, or swipe? Answer as "
     "a prediction about behaviour, not a compliment about the writing."),
]

_FICTION = [
    ("premise_clarity",
     "Within the first two beats, does the listener know WHO wants WHAT and what stands in the way? "
     "Name them from the text, or state that they are absent."),
    ("stakes",
     "What is actually at risk, and is it established ON SCREEN rather than merely implied by genre "
     "furniture? 'Something bad might happen' is not stakes."),
    ("escalation",
     "Does each beat raise pressure over the one before, or are the beats interchangeable mood "
     "fragments that could be reordered without loss? Test it: could you swap beats 2 and 4?"),
    ("turn_or_reveal",
     "Is there a genuine turn — a moment where the listener's understanding changes? Where is it?"),
    ("payoff",
     "Does the ending ANSWER the question the opening raised, or does it withhold/elide the climax "
     "and cut away? An artful fade where the payoff should be is the most common way these fail."),
    ("character_specificity",
     "Are these people, or are they positions in a scene? Could you describe either one to a friend?"),
    ("dialogue_earns_its_place",
     "Does each spoken line do work (reveal, advance, complicate), or is it atmosphere in quotes?"),
    ("originality",
     "Have you heard this exact piece a hundred times? Name the cliché if so."),
]

_INFORMATIONAL = [
    ("does_it_teach",
     "Name specifically what a listener KNOWS after this that they did not know before. If you "
     "cannot name at least two concrete things, the piece failed regardless of how it reads."),
    ("claim_density",
     "How many real, checkable claims per minute? Restating the topic in fresh words is not a claim."),
    ("evidence_and_specificity",
     "Are there numbers, names, dates, mechanisms — or only abstractions and vibes?"),
    ("structure",
     "Is there a spine a listener can follow, or is it a list of adjacent thoughts?"),
    ("takeaway",
     "Is there ONE thing worth repeating to a colleague tomorrow? Quote it, or state that none exists."),
    ("misleading_or_padding",
     "Flag padding, throat-clearing, and any claim stated with more confidence than it has earned."),
]

_NARRATIVE_NONFICTION = [
    ("hook", "Does the first beat create a real question, or merely announce the topic?"),
    ("through_line", "Is there ONE argument/story carried end to end, or a bundle of loose points?"),
    ("concreteness", "Scenes, people, numbers — or generalities?"),
    ("payoff", "Does the close deliver on the opening's promise?"),
    ("takeaway", "What does the listener carry away? Quote it."),
]

RUBRICS: Dict[str, List[Tuple[str, str]]] = {
    "fiction": _FICTION,
    "informational": _INFORMATIONAL,
    "narrative_nonfiction": _NARRATIVE_NONFICTION,
}

#: What a TITLE promises. A listicle/explainer framing sets an expectation the body must meet.
_PROMISE_INFO = re.compile(
    # A title that OPENS with an interrogative/explanatory frame is promising to explain something.
    # An earlier version only matched "how to", so "How a canal lock lifts a ship" — about as
    # explicit an explainer promise as exists — was read as a story.
    r"^\s*(how|why|what|when|where|the\s+(science|history|story|truth|reason|rise|fall)\s+of|"
    r"\d+\s+\w+|top\s*\d+|inside|explained?)\b"
    r"|\b(guide|explain(ed|er)?|tips?|ways?\s+to|steps?|reasons?\s+why|facts?\s+about|"
    r"secrets?\s+of|lessons?\s+from|habits?|mistakes?|rules?\s+of|beginners?|introduction\s+to|"
    r"basics\s+of|breakdown|deep\s+dive)\b",
    re.I,
)

#: What the SCRIPT actually IS, read from the body rather than the title. This is the primary
#: signal: a piece must be graded as the thing it is, or every score is noise. An earlier version
#: classified from the TITLE alone and sent "The Secret Affair" — a two-hander drama — to the
#: explainer rubric because the word "Secret" matched an informational tell. The judge caught it
#: via ``misclassified``, which is why that field exists, but the scores were wasted.
#: WHY CLASSIFICATION IS A MODEL CALL AND NOT A REGEX.
#: Two deterministic attempts failed on real data and both failed the SAME way — they called
#: everything fiction, which silently graded explainers against a story rubric and produced a fleet
#: of meaningless 1/10 scores:
#:   attempt 1 (title keywords):  "The Secret Affair" -> informational, because "Secret" matched an
#:                                informational tell. The judge caught it via ``misclassified``.
#:   attempt 2 (named speakers):  "How the Panama Canal lifts 50,000-ton ships" -> fiction, because
#:                                a TWO-HOST explainer has named speakers. Format (narration vs
#:                                dialogue) is not content type, and no amount of regex fixes that.
#: So this is one extra Gemini Flash call (~$0.0005) — the cost ladder's rung 2: a cheap model for
#: judgment the deterministic tools cannot make. It is validated by ``--selftest`` against
#: hand-labelled real jobs, so a regression in the classifier is visible rather than silent.
_CLASSIFY_PROMPT = """\
Read this audio transcript and classify it. Answer ONLY with strict JSON.

Definitions:
- "fiction": a made-up STORY. Invented characters in invented situations, a plot, a scene. A
  narrator telling a story counts. Being a drama or a two-hander does NOT matter.
- "informational": its job is to TEACH or EXPLAIN something true about the real world (how a thing
  works, what happened, why something is so). A two-HOST conversational explainer is STILL
  informational — named speakers do not make it fiction.
- "narrative_nonfiction": true events told with story craft (history, reportage, a real person's
  account).

TRANSCRIPT:
---
{transcript}
---

{{"form": "fiction|informational|narrative_nonfiction", "confidence": <0-1>, "why": "<12 words>"}}"""


def detect_promise(topic: str) -> str:
    """What the TITLE led the user to EXPECT: 'informational' or 'story'.

    Kept deterministic on purpose — this reads only the title, which is short and explicit, and it
    must not cost a model call per job. It is judged against :func:`detect_form` (what the body
    actually IS) to produce the ``purpose_fulfillment`` finding: title promised a listicle, body
    delivered a horror vignette."""
    return "informational" if _PROMISE_INFO.search(topic or "") else "story"


def detect_form(transcript: str, script: Any = None, model: str = DEFAULT_JUDGE_MODEL) -> str:
    """What the piece ACTUALLY IS, read from its own body by a cheap model. Fail-safe: any error
    returns ``narrative_nonfiction``, the least-opinionated rubric, rather than guessing."""
    if not transcript:
        return "narrative_nonfiction"
    res = call_judge(_CLASSIFY_PROMPT.format(transcript=transcript[:6000]), model)
    form = str((res or {}).get("form") or "").strip().lower()
    return form if form in RUBRICS else "narrative_nonfiction"


def classify_content(topic: str, genre: str, audio_format: str) -> str:
    """DEPRECATED shim — title-only classification. Kept only so an external caller does not
    break; ``judge_job`` uses :func:`detect_form` (body) + :func:`detect_promise` (title)."""
    return "informational" if _PROMISE_INFO.search(topic or "") else "fiction"


def script_to_text(script: Any) -> str:
    """Flatten the persisted script to what a listener actually HEARS, in order."""
    if isinstance(script, str):
        return script.strip()
    if not isinstance(script, dict):
        return ""
    items = script.get("dialogue") or script.get("segments") or script.get("items") or []
    out: List[str] = []
    for it in items:
        if isinstance(it, dict):
            t = it.get("text") or it.get("line") or it.get("content") or ""
            spk = it.get("speaker")
            out.append(f"{spk}: {t}" if spk and spk != "Narrator" else str(t))
        elif isinstance(it, str):
            out.append(it)
    return "\n\n".join(x for x in out if x).strip()


def build_prompt(topic: str, family: str, duration_s: Any, transcript: str,
                 promise: str = "story") -> str:
    rubric = RUBRICS[family]
    lines = [f"  - {k}: {d}" for k, d in rubric + _UNIVERSAL]
    keys = [k for k, _ in rubric + _UNIVERSAL]
    return f"""{CRITIC_PERSONA}

{SCORING_ANCHORS}

You are judging a piece of SHORT-FORM AUDIO the listener plays start to finish.

JUDGE THE WRITING ONLY. Voice acting, TTS quality, music, sound design and visuals are graded
elsewhere and are NOT your concern. Score only what is being SAID.

THE USER ASKED FOR: "{topic}"
INTENDED LENGTH: {duration_s} seconds
WHAT THE PIECE ACTUALLY IS (detected from its own body): {family}
WHAT THE TITLE PROMISED THE USER: {promise}
You are grading it AS THE THING IT IS ({family}) — that is the only fair way to score craft.
`purpose_fulfillment` is where the PROMISE is judged: if the title promised "{promise}" and the body
is a {family}, the user did not get what they asked for, and that is a severe failure NO MATTER how
good the writing is. If you think the detection itself is wrong, say so in `misclassified`.

--- BEGIN TRANSCRIPT (this is the complete piece) ---
{transcript}
--- END TRANSCRIPT ---

Score EVERY axis below 1-10 with a one-sentence justification that QUOTES the text:
{chr(10).join(lines)}

Return STRICT JSON, no markdown fence, exactly this shape:
{{
  "scores": {{ {", ".join(f'"{k}": {{"score": <1-10>, "why": "<quote-backed>"}}' for k in keys)} }},
  "overall": <1-10>,
  "most_damaging_defect": "<the ONE thing that most makes a listener leave>",
  "what_a_listener_does_at_10s": "<keeps listening | swipes> — and why",
  "misclassified": "<empty string, or why the rubric family is wrong>",
  "would_you_publish": "<yes|no>",
  "top_3_fixes": ["<concrete, specific to THIS text>", "...", "..."],
  "verdict": "<2-3 sentences, blunt, no encouragement>"
}}"""


def call_judge(prompt: str, model: str) -> Optional[Dict[str, Any]]:
    import httpx

    tok = subprocess.run(
        ["gcloud", "auth", "print-access-token"], capture_output=True, text=True
    ).stdout.strip()
    url = (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
        f"/locations/{LOCATION}/publishers/google/models/{model}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        # temperature 0 — a critic that returns a different verdict each run is not a measurement.
        "generationConfig": {"maxOutputTokens": 8000, "temperature": 0},
    }
    r = httpx.post(
        url, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json=body, timeout=240,
    )
    if r.status_code != 200:
        print(f"  judge HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    try:
        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        print(f"  judge returned no text: {str(r.json())[:200]}", file=sys.stderr)
        return None
    txt = txt.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\n?|```$", "", txt, flags=re.M).strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", txt, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        print(f"  judge returned non-JSON: {txt[:200]}", file=sys.stderr)
        return None


def judge_job(db: Any, job_id: str, model: str) -> Optional[Dict[str, Any]]:
    doc = db.collection("podcast_jobs").document(job_id).get()
    if not doc.exists:
        print(f"{job_id}: not found", file=sys.stderr)
        return None
    d = doc.to_dict() or {}
    topic = str(d.get("topic") or "")
    ep = d.get("episode_profile") or {}
    script = (d.get("outputs") or {}).get("script")
    transcript = script_to_text(script)
    if not transcript:
        print(f"{job_id}: no script text", file=sys.stderr)
        return None
    family = detect_form(transcript, script, model)
    promise = detect_promise(topic)
    dur = (script or {}).get("metadata", {}).get("total_duration_seconds") if isinstance(script, dict) else None
    res = call_judge(build_prompt(topic, family, dur, transcript, promise), model)
    if res is None:
        return None
    res["_job_id"] = job_id
    res["_topic"] = topic
    res["_family"] = family
    res["_promise"] = promise
    res["_words"] = len(transcript.split())
    return res


def print_report(r: Dict[str, Any]) -> None:
    print(f"\n{'='*78}")
    print(f"JOB {r['_job_id'][:8]}  |  {r['_topic'][:56]}")
    print(f"is={r['_family']}  title-promised={r.get('_promise')}  words={r['_words']}  OVERALL={r.get('overall')}/10  "
          f"publish={r.get('would_you_publish')}")
    print("-" * 78)
    for k, v in (r.get("scores") or {}).items():
        if isinstance(v, dict):
            print(f"  {v.get('score'):>2}/10  {k}")
            print(f"         {str(v.get('why'))[:150]}")
    if r.get("misclassified"):
        print(f"\n  ⚠ MISCLASSIFIED: {r['misclassified']}")
    print(f"\n  MOST DAMAGING: {r.get('most_damaging_defect')}")
    print(f"  AT 10s: {r.get('what_a_listener_does_at_10s')}")
    print("\n  TOP FIXES:")
    for f in (r.get("top_3_fixes") or []):
        print(f"    - {f}")
    print(f"\n  VERDICT: {r.get('verdict')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_ids", nargs="*")
    ap.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--all-recent", type=int, default=0, help="judge the N most-recent jobs")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--real-only", action="store_true",
                    help="skip QA verification stubs (test_user_e2e / 'pipeline verification' / "
                         "<80 words) — they are 10-second pipeline probes, not content, and "
                         "including them makes any fleet mean meaningless")
    ap.add_argument("--selftest", action="store_true",
                    help="validate the content-form classifier against hand-labelled real jobs")
    args = ap.parse_args()

    from google.cloud import firestore

    db = firestore.Client(project=PROJECT)

    if args.selftest:
        # The classifier is a MODEL call, so it needs a control or a regression is silent. These
        # labels are hand-assigned from reading the transcripts. Two earlier deterministic
        # classifiers passed unit tests and still called every explainer "fiction" on real data.
        cases = {
            "dc785764": "fiction", "6e38e036": "fiction", "58fa09b4": "informational",
            "6d5ea2b1": "informational", "ae5bae35": "informational", "a2748ef7": "informational",
        }
        rows = list(db.collection("podcast_jobs")
                    .order_by("created_at", direction=firestore.Query.DESCENDING).limit(80).stream())
        ok = bad = 0
        for jid, expect in cases.items():
            doc = next((d for d in rows if d.id.startswith(jid)), None)
            if not doc:
                print(f"  SKIP {jid} (outside window)")
                continue
            sc = (doc.to_dict().get("outputs") or {}).get("script")
            got = detect_form(script_to_text(sc), sc, args.model)
            ok += got == expect
            bad += got != expect
            print(f"  {'OK ' if got == expect else 'BAD'} {jid} expect={expect:20s} got={got}")
        print(f"\nCLASSIFIER CONTROL: {ok} correct / {bad} wrong")
        return 0 if bad == 0 else 1
    ids: List[str] = list(args.job_ids)
    if args.all_recent:
        q = (db.collection("podcast_jobs")
             .order_by("created_at", direction=firestore.Query.DESCENDING)
             .limit(args.all_recent))
        for d in q.stream():
            if d.id in ids:
                continue
            if args.real_only:
                jd = d.to_dict() or {}
                topic = str(jd.get("topic") or "")
                script = (jd.get("outputs") or {}).get("script")
                if jd.get("user_id") == "test_user_e2e" or "pipeline verification" in topic.lower():
                    continue
                if len(script_to_text(script).split()) < 80:
                    continue
            ids.append(d.id)
    if not ids:
        ap.error("give at least one job_id or --all-recent N")

    results: List[Dict[str, Any]] = []
    for jid in ids:
        r = judge_job(db, jid, args.model)
        if r:
            results.append(r)
            print_report(r)
    if results:
        scores = [x.get("overall") or 0 for x in results]
        print(f"\n{'='*78}\nJUDGED {len(results)} | mean overall "
              f"{sum(scores)/len(scores):.1f}/10 | "
              f"would-publish {sum(1 for x in results if str(x.get('would_you_publish')).lower()=='yes')}"
              f"/{len(results)}")
    if args.json_out and results:
        with open(args.json_out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
