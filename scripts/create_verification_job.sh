#!/bin/bash
# create_verification_job.sh — the ONE sanctioned way to create a pipeline-verification job.
# Test-cost-ladder T3: cheapest real end-to-end run (~$0.025) — 10s, quality_tier=low,
# no visuals, no clarifier. Escalation to T4 (medium/high/visuals/long) requires the
# founder ack file (see .claude/rules/test-cost-ladder.md).
#
# Usage:
#   ./create_verification_job.sh                          # 10s low-tier audio job (~$0.025)
#   ./create_verification_job.sh --topic "b-trees" --wait # + poll until done
#   ./create_verification_job.sh --tier medium --visuals  # T4 — needs FOUNDER_SPEND_ACK
#   ./create_verification_job.sh --short --duration 1.0 --wait  # born-short (9:16), 60s
#   ./create_verification_job.sh --tier high --duration 2.0 --motion-clips 3  # T4 paid video — needs ACK
#   ./create_verification_job.sh ... --dry-run            # print the exact request; no auth, no POST, $0
#     — verifies the short-form craft (wpm band, caption dwell); duration>0.5 ⇒ needs ACK
#
# Auth: TEST_API_KEY env var, or fetched from Secret Manager (kitesforu-dev).

set -euo pipefail

API_BASE="${API_BASE:-https://kitesforu-api-m6zqve5yda-uc.a.run.app}"
ACK_FILE="/Users/vikrantbhosale/gitprojects/kitesforu/.claude/FOUNDER_SPEND_ACK"

TOPIC="pipeline verification"
DURATION="0.167"          # 10 seconds — the enforced API minimum
TIER="low"
# STYLE IS CONTRACT-REQUIRED — the api's CreateJobRequest has no default (round-2 critic on
# PR #173 proved an omitted key 422s: openapi.json required = ['topic','duration_min','style']),
# so every client manufactures one and this script must too.
#
# ⚠️ BUT A STYLE IS NOT INERT. It flows into context discovery as `content_domain`. Before
# schemas 2.81.0 an "Explainer" default silently DISCARDED the classifier's own genre — job
# `e9466de1` (2026-09-06): a $1.56 --tier high T4 asked for a mystery story, this default flipped
# it to educational, the architect planned a segment_plan, and the Veo entitlement planned ZERO
# beats — the harness defeated the very premium behaviour the spend was buying. 2.81.0's
# UMBRELLA_ALIAS_KEYS now makes mode words ("explainer"/"storytelling") YIELD to a specific
# classifier genre, so the default below is defused for that failure — but representativeness is
# still yours to choose: **pass --style matching the topic on any T4 whose routing you are
# testing** ("Storytelling" for fiction, etc.). The defaults can quietly defeat the thing you
# meant to test; this one did.
STYLE="Explainer"   # API style enum: Explainer|Storytelling|Interview|... ("conversation" was removed)
LANGUAGE="en-US"    # --language <bcp47>: the SPOKEN language. Was hardcoded to en-US in the body,
                    # so this script — the tool everyone verifies with — could not create a
                    # non-English job AT ALL. Measured 2026-08-15 on the full podcast_jobs
                    # collection (n=4079): 3645 en-US and ZERO non-English jobs after 2026-05-02.
                    # A defect no verification tool can express is a defect nobody re-tests.
FORMAT=""           # optional API format (drama|panel|...) — forces multi-speaker casting at T3 cost
SHORT="false"       # --short: born-short vertical Social Short (short_video: true)
SOURCE_WRITEUP=""   # --source-writeup <wrt_id>: simulate a writeup→podcast CONVERSION so the
                    # visuals path can ADOPT that writeup's already-authored grounded figures
                    # (C3-4). Needs a writeup that HAS figures — check
                    # formats_generated[fmt].figures, not "content" (no such key).
VISUALS="false"
WAIT="false"
ON_BEHALF_OF=""
ON_BEHALF_OF_EMAIL=""   # --on-behalf-of-email: the ADDRESS (the api reads it from its own header)
SHORT="false"       # born-short: short_video=true → 9:16, single-voice, intro/outro suppressed
CONTENT_RATING=""   # optional maturity dial: g|pg|pg_13|r (exercises ENABLE_CONTENT_MATURITY end-to-end)
MOTION_CLIPS="0"    # --motion-clips N (0-3): PURCHASED paid video clips, sent as visual_options.motion_clips.
                    # The ONLY way to exercise the purchased-motion arm (`paid_opt_in` in veo_hero), where
                    # the clip ranking, the figure rule and first_heroes all act. Before this flag no
                    # verification job could reach it (2026-09-11, workers #3116).
DRY_RUN="false"     # --dry-run: print the POST URL, headers and body, then exit 0 before any request.
PAID_STILLS="on"    # --paid-stills off: send visual_options with stills OFF. ON by default WITH
                    # --motion-clips, because a bare {"motion_clips": N} is normalised by the api to
                    # max_images=0 and the job then renders ZERO paid stills — the wrong population
                    # for any paid-video test (see the payload builder).

while [[ $# -gt 0 ]]; do
  case "$1" in
    --topic)    TOPIC="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --tier)     TIER="$2"; shift 2 ;;
    --style)    STYLE="$2"; shift 2 ;;
    --language) LANGUAGE="$2"; shift 2 ;;
    --format)   FORMAT="$2"; shift 2 ;;  # drama|panel — exercises multi-voice casting cheaply
    --short)    SHORT="true"; shift ;;   # Social Short path (9:16, kinetic captions, assembly)
    --visuals)  VISUALS="true"; shift ;;
    --visuals-auto) VISUALS="auto"; shift ;;  # T3-SAFE: send NEITHER wants_visuals nor
                    # visuals_opt_out, so the worker's non-fiction $0 auto-default applies
                    # (deterministic diagrams/cards, NO paid images). Use this to exercise the
                    # visual PLANNING path — info-figure routing, figure adoption — without
                    # tripping the T4 paid-visuals gate. `--visuals` remains T4 (real images).
                    #
                    # ⚠️ REQUIRES A LONGER DURATION — it does NOTHING at the 0.167min default.
                    # MEASURED 2026-08-18, job 9725a85c (~$0.025, wasted): a 10s run authors no
                    # BLUEPRINT, and `stages/visuals/flags.py::_is_nonfiction` fails SAFE to
                    # fiction when the blueprint is absent, so the stage logs
                    #   "story_visuals: blueprint never completed"
                    #   "story_visuals: skip — not opted in and (opted out or not non-fiction)"
                    # and renders nothing. That skip is BY DESIGN (no spend on an undetermined
                    # job) — do not "fix" the gate. Note the persisted doc afterwards reports
                    # _is_nonfiction=True, which makes it LOOK like an ordering race; it is not,
                    # the blueprint simply did not exist at decision time.
                    # ⇒ pair it with --duration (>0.5 needs the FOUNDER_SPEND_ACK file).
    --content-rating) CONTENT_RATING="$2"; shift 2 ;;  # g|pg|pg_13|r — sets body.content_rating
    --motion-clips) MOTION_CLIPS="$2"; shift 2 ;;  # 0-3 purchased Veo clips — T4, needs the ACK
    --paid-stills)  PAID_STILLS="$2"; shift 2 ;;   # on|off — only read when --motion-clips > 0
    --dry-run)  DRY_RUN="true"; shift ;;
    --wait)     WAIT="true"; shift ;;
    --source-writeup) SOURCE_WRITEUP="$2"; shift 2 ;;  # C3-4: verify figure ADOPTION from a writeup
    --on-behalf-of) ON_BEHALF_OF="$2"; shift 2 ;;  # a CLERK USER ID (user_…/test_…), NOT an email.
                    # VISIBILITY (2026-08-28): the default test_user_e2e's jobs NEVER render in the
                    # signed-in Playwright library (different account). A job that must be OBSERVED
                    # on the beta surface needs --on-behalf-of <the browser test account's user_… id>.
    --on-behalf-of-email) ON_BEHALF_OF_EMAIL="$2"; shift 2 ;;  # the ADDRESS, sent as X-On-Behalf-Of-Email
    # Stop at the first NON-comment line instead of `head -12`. The count was a duplicated constant
    # coupled to the header's length: this PR added two header lines and the window silently pushed
    # `--dry-run`, the short-form caveat and the `Auth:` line out of the help — so the safety flag
    # became undiscoverable through the documented route (qa #175 round-1 code critic and design).
    -h|--help)  sed -n '/^#/!q;p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ---- Motion clips: validate, and never let the $0 default opt the job OUT of visuals --------
[[ "$MOTION_CLIPS" =~ ^[0-3]$ ]] || { echo "--motion-clips must be 0-3 (the API's VisualOptions bound), got: $MOTION_CLIPS" >&2; exit 1; }
if [[ "$MOTION_CLIPS" != "0" && "$VISUALS" == "false" ]]; then
  # The default sends visuals_opt_out=true, which would cancel the clips the job just bought.
  # Send neither key instead; the api turns motion_clips>0 into wants_visuals itself.
  VISUALS="auto"
  echo "  note: --motion-clips implies --visuals-auto (the default would opt the job out of visuals)" >&2
fi

# ---- Clips vs episode length ------------------------------------------------------
# `--motion-clips 3` at the 0.167 min default buys up to 18 s of video for a TEN-SECOND episode —
# roughly $0.90-1.08 of clips that cannot all be shown (qa #175 round-1 design). `--visuals-auto`
# carries a ten-line warning about exactly this hazard and `--motion-clips` carried none.
if [[ "$MOTION_CLIPS" != "0" ]]; then
  # 6 s per clip is the purchased-arm length (_MOTION_CLIP_USD_CAP is $0.65/6s).
  _clip_seconds=$(( MOTION_CLIPS * 6 ))
  _episode_seconds=$(awk "BEGIN{printf \"%d\", $DURATION * 60}")
  if (( _clip_seconds > _episode_seconds )); then
    echo "  ⚠️  $MOTION_CLIPS clip(s) is ~${_clip_seconds}s of video for a ${_episode_seconds}s episode." >&2
    echo "      You are buying more motion than the episode can show. Raise --duration or lower" >&2
    echo "      --motion-clips; a paid-video T4 wants at least ~2.0 min." >&2
  fi
fi

# ---- Ladder gate: anything beyond T3 needs a fresh founder ack -------------------
needs_ack="false"
reason=""
[[ "$TIER" != "low" ]] && { needs_ack="true"; reason+="tier=$TIER "; }
[[ "$VISUALS" == "true" ]] && { needs_ack="true"; reason+="visuals=on "; }
awk "BEGIN{exit !($DURATION > 0.5)}" && { needs_ack="true"; reason+="duration=${DURATION}min "; }
[[ "$MOTION_CLIPS" != "0" ]] && { needs_ack="true"; reason+="motion_clips=$MOTION_CLIPS "; }

if [[ "$needs_ack" == "true" && "$DRY_RUN" == "true" ]]; then
  echo "  dry-run: a real run would need the ACK ($reason)" >&2
elif [[ "$needs_ack" == "true" ]]; then
  fresh="false"
  if [[ -f "$ACK_FILE" ]]; then
    age=$(( $(date +%s) - $(stat -f %m "$ACK_FILE" 2>/dev/null || stat -c %Y "$ACK_FILE") ))
    [[ $age -lt 3600 ]] && fresh="true"
  fi
  if [[ "$fresh" != "true" ]]; then
    echo "T4 ESCALATION BLOCKED ($reason)— this is a real-price run." >&2
    echo "Ask the founder to run: touch $ACK_FILE   (valid 60 min)" >&2
    echo "Ladder: .claude/rules/test-cost-ladder.md — name WHICH premium-only behavior you are testing." >&2
    exit 3
  fi
fi

# ---- Cost estimate (from COST_CHANGELOG tier math) --------------------------------
case "$TIER" in
  low)    EST="~\$0.025" ;;
  medium) EST="~\$0.15"  ;;
  # high: two bands — story topics measured \$1.56-1.76 (n=2, 2026-09-06: e9466de1, df3de5bb).
  # Derivation + provenance: COST_CHANGELOG.md 2026-09-06 entry. Genre hazard: STYLE block above.
  high)   EST="~\$1.0-1.3 (non-story topic) / ~\$1.55-2.25 (story topic)" ;;
  *)      EST="unknown" ;;
esac
[[ "$VISUALS" == "true" ]] && EST="$EST + visuals (~\$0.10-0.50; a story band already counts veo — don't double-book)"
# Purchased arm: per-clip cap \$0.65 (\$0.65/6s); the live rows price \$0.30-0.36 a clip (workers COST_CHANGELOG 2026-09-10).
[[ "$MOTION_CLIPS" != "0" ]] && EST="$EST + $MOTION_CLIPS paid video clip(s) (~\$0.30-0.36 each, cap \$0.65)"

# ---- Auth --------------------------------------------------------------------------
if [[ "$DRY_RUN" != "true" && -z "${TEST_API_KEY:-}" ]]; then
  TEST_API_KEY=$(gcloud secrets versions access latest --secret=TEST_API_KEY --project=kitesforu-dev 2>/dev/null) \
    || { echo "TEST_API_KEY not in env and Secret Manager fetch failed" >&2; exit 1; }
fi

PAYLOAD=$(python3 - "$TOPIC" "$DURATION" "$TIER" "$STYLE" "$VISUALS" "$FORMAT" "$CONTENT_RATING" "$SOURCE_WRITEUP" "$LANGUAGE" "$MOTION_CLIPS" "$PAID_STILLS" <<'PYEOF'
import json, sys
topic, duration, tier, style, visuals, fmt, content_rating, source_writeup, language, motion_clips, paid_stills = sys.argv[1:12]
body = {
    "topic": topic,
    "duration_min": float(duration),
    "style": style,
    "quality_tier": tier,
    "economy_mode": tier == "low",

    "intro_enabled": False,
    "allow_premium": tier in ("high", "ultra"),
    "skip_clarifier": True,
    "language": language,
}
# "auto" sends NEITHER key → the worker's own non-fiction $0 visual default decides.
# Sending visuals_opt_out=true (the old else-branch) killed visuals outright, so the
# visual-planning path could never be exercised at T3.
if visuals != "auto":
    body["wants_visuals"] = visuals == "true"
    body["visuals_opt_out"] = visuals != "true"
if fmt:
    body["format"] = fmt
if content_rating:
    body["content_rating"] = content_rating
if int(motion_clips) > 0:
    # Declared on the api's own CreateJobRequest (models.py `visual_options: Optional[VisualOptions]`),
    # so the strict schemas model does not drop it.
    #
    # `real_images` AND `max_images` ARE SENT EXPLICITLY, and that is not a default-tidying tweak.
    # A bare `{"motion_clips": N}` is normalised by the api to
    # `{real_images: False, max_images: 0, motion_clips: N}` (`option_pricing.normalize_visual_options`
    # defaults a missing `real_images` to False and then FORCES `max_images` to 0), and the workers
    # policy turns that into `user_paid_cap = 0` — every paid still demoted to a $0 card. Executed,
    # both arms, 2026-09-12:
    #     --motion-clips 3 alone   -> max_scenes 0  allow_veo True  purchased_clips 3
    #     stills + clips           -> max_scenes 6  allow_veo True  purchased_clips 3
    # A verification job for the PAID VIDEO path whose stills are all $0 cards is not the shape a
    # paying user has, and #3116's whole mechanism turns on picture-vs-figure — a card IS a figure.
    # The job would have run and measured the wrong population. This is the
    # verification-job-defaults-defeat-the-test class, caught for $0 before spending.
    body["visual_options"] = {
        "real_images": paid_stills.lower() != "off",
        "max_images": 6 if paid_stills.lower() != "off" else 0,
        "motion_clips": int(motion_clips),
    }
if source_writeup:
    # Declared on CreateJobRequest (schemas 2.60.0) so the strict model keeps it; the
    # direct create path stamps it top-level onto the job doc (api #734).
    body["source_writeup_id"] = source_writeup
# NOTE: short_video is a QUERY PARAM (?short_video=true), NOT a body field — the
# strict schemas CreateJobRequest drops body extras, so a body short_video is
# silently ignored (verified live 2026-07-06: body-only rendered a normal episode).
# The query param is appended to the POST URL below when --short is set.
print(json.dumps(body))
PYEOF
)

# Born-short routing is a QUERY PARAM (crud.py: `?short_video=true`, P0d). duration<=2
# required (short_hint = short_video AND duration_min<=2), else the hint is ignored.
POST_URL="$API_BASE/v1/podcasts"
[[ "$SHORT" == "true" ]] && POST_URL="${POST_URL}?short_video=true"

echo "Creating verification job: tier=$TIER duration=${DURATION}min visuals=$VISUALS lang=$LANGUAGE est=$EST" >&2

# WHOSE LIBRARY DOES THIS LAND IN? Without --on-behalf-of the job is owned by the API key's
# own identity (test_user_e2e), which does NOT appear on the founder's signed-in home page.
# Measured 2026-08-25: a whole afternoon of "verification" jobs landed on test_user_e2e; the
# founder opened beta.kitesforu.com, saw none of them, and asked "where are ur test couple
# videos u created". Verifying on a surface the reviewer cannot open is not verification —
# and nothing said so, because the owner was never printed. Now it always is.
# ...on STDERR, like every other banner here, so that `--dry-run 2>/dev/null` emits the request BODY
# and nothing else and can be piped straight to jq (round-1 code critic NIT).
if [[ -z "$ON_BEHALF_OF" ]]; then
  echo "  ⚠️  OWNER: the API key's own identity (test_user_e2e)." >&2
  echo "      This job will NOT appear on the founder's signed-in home page." >&2
  echo "      For anything a human must SEE, pass:  --on-behalf-of <user_…>" >&2
else
  echo "  OWNER: $ON_BEHALF_OF — visible in that account's library." >&2
fi
OBO_ARGS=()
if [[ -n "$ON_BEHALF_OF" ]]; then
  # `X-On-Behalf-Of` is a CLERK USER ID, never an email. The api validates it with
  # `_validate_clerk_user_id` (kitesforu-api/src/api/auth/clerk.py), which accepts only a
  # `user_*` or `test_*` prefix; anything else raises `Invalid X-On-Behalf-Of user ID format`
  # and the caller sees a bare `{"detail":"Invalid authentication credentials"}` — a 401 that
  # looks like a BAD KEY and sends you hunting the wrong thing (measured 2026-08-08: the same
  # TEST_API_KEY returned HTTP 200 on `GET /v1/podcasts` at that moment, and 401 with no key,
  # so the key was provably fine). The email belongs in the SEPARATE `X-On-Behalf-Of-Email`.
  # Fail FAST and say so, rather than emitting a request whose rejection is unreadable.
  if [[ "$ON_BEHALF_OF" != user_* && "$ON_BEHALF_OF" != test_* ]]; then
    echo "--on-behalf-of expects a CLERK USER ID (user_… or test_…), not '$ON_BEHALF_OF'." >&2
    if [[ "$ON_BEHALF_OF" == *@* ]]; then
      echo "  That looks like an email. The api validates this header as a user ID and will" >&2
      echo "  reject it with a 401 that reads 'Invalid authentication credentials'." >&2
      echo "  Pass the Clerk user ID; use --on-behalf-of-email for the address." >&2
    fi
    exit 2
  fi
  OBO_ARGS=(-H "X-On-Behalf-Of: $ON_BEHALF_OF")
  [[ -n "$ON_BEHALF_OF_EMAIL" ]] && OBO_ARGS+=(-H "X-On-Behalf-Of-Email: $ON_BEHALF_OF_EMAIL")
fi

# THE DRY RUN EXITS HERE, NOT EARLIER — after the OWNER print and after the --on-behalf-of
# validation, because those are the two things it most needs to rehearse. It used to exit before
# both: `--dry-run --on-behalf-of <an email> --motion-clips 2` exited 0 with a clean-looking
# request, while the identical vector on the sending path is rejected with exit 2. A rehearsal that
# green-lights a request the real path refuses is worse than no rehearsal, and the OWNER warning
# (which decides whether a human can SEE the job at all) was skipped entirely — the comment above
# saying "Now it always is" was falsified by the flag that shipped alongside it
# (qa #175 round-1 code critic and design D1).
#
# The HEADERS are printed too, with the bearer redacted. For a T4 that lands in the wrong library
# the header set is the deciding half, and "the exact request" without it is not the exact request.
if [[ "$DRY_RUN" == "true" ]]; then
  {
    echo "DRY RUN — nothing sent."
    echo "POST $POST_URL"
    echo "  -H 'Authorization: Bearer <TEST_API_KEY redacted>'"
    echo "  -H 'Content-Type: application/json'"
    for _h in ${OBO_ARGS[@]+"${OBO_ARGS[@]}"}; do
      [[ "$_h" == "-H" ]] || echo "  -H '$_h'"
    done
  } >&2
  # The BODY alone goes to stdout, so `--dry-run 2>/dev/null | jq .` works. Every banner above is
  # on stderr for the same reason (round-1 code critic NIT).
  echo "$PAYLOAD"
  exit 0
fi


# ${arr[@]+...} guard: macOS bash 3.2 treats an EMPTY array expansion as an
# unbound variable under `set -u`.
RESP=$(curl -sS -X POST "$POST_URL" \
  -H "Authorization: Bearer $TEST_API_KEY" \
  ${OBO_ARGS[@]+"${OBO_ARGS[@]}"} \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

JOB_ID=$(echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('job_id') or d.get('id') or '')" 2>/dev/null || true)
if [[ -z "$JOB_ID" ]]; then
  echo "Job creation failed. Response:" >&2; echo "$RESP" >&2; exit 1
fi
echo "job_id=$JOB_ID  (est $EST)"
echo "status: $API_BASE/v1/podcasts/$JOB_ID/status"

if [[ "$WAIT" == "true" ]]; then
  # BOUNDED, AND A PROBE FAILURE IS ITS OWN STATE.
  #
  # The iteration bound was added after a poll loop billed Cloud Run for 8 days 17 hours. But a
  # bound alone does not fix the class: the loop below used to fold an UNREADABLE status into
  # "keep waiting". An unauthenticated or 5xx response makes the parse print an empty string,
  # which matches no terminal state, so the loop kept polling a job it could not read — 60
  # useless requests that learn nothing, and in the unbounded ancestor ~26,000 of them.
  #
  # A probe that cannot answer is a DIFFERENT condition from "the job is still running", and it
  # is fatal to the wait: if we cannot read status once, we almost certainly cannot read it on
  # attempt 60 either. Consecutive failures abort with a distinct message and a non-zero exit,
  # so the caller sees "I could not read this job" rather than a silent timeout.
  # SIZED FOR WHAT WAS BOUGHT. 60 x 15 s = 900 s is fine for an audio-only job and far too short for
  # one that bought paid video: the visuals stage is designed to run to 2100 s soft / 3300 s hard
  # (kitesforu-workers `stages/visuals/pass_deadline.py`), so a --motion-clips run could not finish
  # inside the old bound (qa #175 round-1 latency). 260 x 15 s = 3900 s covers the hard ceiling.
  MAX_POLLS=60
  [[ "$MOTION_CLIPS" != "0" ]] && MAX_POLLS=260
  MAX_CONSECUTIVE_PROBE_FAILURES=3
  probe_failures=0
  STATUS=""
  echo "Polling until terminal state (max $MAX_POLLS x 15s = $((MAX_POLLS * 15))s)..."
  for i in $(seq 1 "$MAX_POLLS"); do
    sleep 15
    RAW=$(curl -sS --max-time 20 "$API_BASE/v1/podcasts/$JOB_ID/status" \
      -H "Authorization: Bearer $TEST_API_KEY" ${OBO_ARGS[@]+"${OBO_ARGS[@]}"} 2>/dev/null)
    STATUS=$(printf '%s' "$RAW" \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
    if [[ -z "$STATUS" ]]; then
      probe_failures=$((probe_failures + 1))
      echo "  [$i] (status unreadable — probe failure $probe_failures/$MAX_CONSECUTIVE_PROBE_FAILURES): ${RAW:0:120}"
      if (( probe_failures >= MAX_CONSECUTIVE_PROBE_FAILURES )); then
        echo "ABORTING: cannot read job status ($probe_failures consecutive failures)." >&2
        echo "This is a PROBE failure, not a job state — check auth (TEST_API_KEY) and the api." >&2
        echo "Job $JOB_ID may still be running; read it directly rather than polling blind." >&2
        exit 3
      fi
      continue
    fi
    probe_failures=0
    echo "  [$i] $STATUS"
    case "$STATUS" in completed|failed|failed_qa) break ;; esac
  done
  if [[ -z "$STATUS" ]]; then
    echo "Final status: UNKNOWN (never read a status) — job $JOB_ID" >&2
    exit 3
  fi
  # RUNNING OUT OF POLLS IS NOT SUCCESS. The loop used to fall through to the line below and exit 0
  # with whatever non-terminal status it last read, so the caller could not tell a finished job from
  # one still rendering — and was invited to grade it. On a --motion-clips run that is a $2+ episode
  # graded half-built (qa #175 round-1 latency). A distinct exit and a distinct message.
  case "$STATUS" in
    completed|failed|failed_qa) ;;
    *)
      echo "TIMED OUT after $MAX_POLLS polls ($((MAX_POLLS * 15))s): job $JOB_ID is still '$STATUS'." >&2
      echo "This is NOT a result — the job is still running and nothing here is gradeable yet." >&2
      echo "Re-read it later: kqa / Artifact.load('$JOB_ID')" >&2
      exit 4
      ;;
  esac
  echo "Final status: $STATUS — grade it with the \$0 battery: kqa / Artifact.load('$JOB_ID')"
fi
