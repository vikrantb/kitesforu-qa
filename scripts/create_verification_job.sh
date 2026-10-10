#!/bin/bash
# create_verification_job.sh — the ONE sanctioned way to create a pipeline-verification job.
# Test-cost-ladder T3: cheapest real end-to-end run (~$0.025) — 10s, quality_tier=low,
# no visuals, no clarifier. Escalation to T4 (medium/high/visuals/long/paid video) requires the
# founder ack file (see .claude/rules/03-money.md).
#
# Usage:
#   ./create_verification_job.sh                          # 10s low-tier audio job (~$0.025)
#   ./create_verification_job.sh --topic "b-trees" --wait # + poll until done
#   ./create_verification_job.sh --tier medium --visuals  # T4 — needs FOUNDER_SPEND_ACK
#   ./create_verification_job.sh --short --duration 1.0 --wait  # born-short (9:16), 60s
#     — verifies the short-form craft (wpm band, caption dwell); duration>0.5 ⇒ needs ACK
#   ./create_verification_job.sh --tier high --duration 2.0 --motion-clips 3  # T4 paid video — needs ACK
#     --paid-stills on|off  (default on WITH --motion-clips: ALSO buys 3 paid stills at --tier low,
#                            4 at any other tier; `off` buys clips only; rejected without --motion-clips)
#   ./create_verification_job.sh ... --on-behalf-of user_…  # a Clerk USER ID owns the job (default:
#                                                           # test_user_e2e, invisible to the founder)
#   ./create_verification_job.sh ... --dry-run            # print the request and its estimate; no auth, no POST, $0
#
# The estimate is computed FROM the body that is sent. A purchase (clips, stills, relimage bases,
# character plates) is priced by the kitesforu-workers functions that select and price it, at origin/main
# (KFU_WORKERS_REPO, or WORKERS_SRC=<tree>/src), imported under the workers venv's python
# (KFU_WORKERS_PYTHON, default <KFU_WORKERS_REPO>/.venv/bin/python).
# After a T4 POST the script reads back what the api STORED and fails if it is less than asked.
# Exit codes, in three classes, so a retry can never buy a job twice:
#   NOTHING WAS CREATED: 1 usage, pricing or auth refusal, or the api REJECTED the request (HTTP 4xx)
#                        · 2 bad --on-behalf-of · 3 the founder ACK is needed
#   A JOB MAY EXIST:     8 the POST's outcome is unknown (transport failure or timeout, HTTP 5xx, or no
#                        job id in the reply). Check the owner's library before re-running.
#   THE JOB WAS CREATED (its id is printed): 0 ok · 4 --wait ran out (NOT a result) · 5 ended with
#                        nothing to grade · 6 the api stored LESS than asked · 7 the stored request could
#                        not be read · 9 its status could not be read while waiting
#
# Auth: TEST_API_KEY env var, or fetched from Secret Manager (kitesforu-dev).

set -euo pipefail

API_BASE="${API_BASE:-https://kitesforu-api-m6zqve5yda-uc.a.run.app}"
ACK_FILE="/Users/vikrantbhosale/gitprojects/kitesforu/.claude/FOUNDER_SPEND_ACK"
HERE="$(cd "$(dirname "$0")" && pwd)"

# ONE EXIT TRAP. It removes the temporary workers tree, and once the POST has gone out it makes every exit
# that did not explain itself say whether a job exists, so no path after the POST can leave a caller
# guessing whether a retry buys the job twice (#175 round-5 code critic F3, latency L3). A deliberate
# exit calls `finish`, which marks it explained.
WORKERS_TREE=""
POST_STATE="unsent"   # unsent | sent (outcome unknown) | rejected (HTTP 4xx) | created (JOB_ID known)
JOB_ID=""
EXPLAINED="false"
finish() { EXPLAINED="true"; exit "$1"; }
on_exit() {
  local rc=$?
  [[ -n "$WORKERS_TREE" ]] && rm -rf "$WORKERS_TREE"
  if (( rc != 0 )) && [[ "$EXPLAINED" != "true" ]]; then
    case "$POST_STATE" in
      sent)    echo "Stopped (exit $rc) after the POST went out: a job MAY exist. Check the owner's library before re-running." >&2 ;;
      created) echo "Stopped (exit $rc): job $JOB_ID WAS created. Do not re-run to retry it; read it instead." >&2 ;;
    esac
  fi
  return 0
}
trap on_exit EXIT
# The kitesforu-workers checkout whose origin/main prices a purchased clip or still: its own
# selector and catalog, read from a temporary `git archive`, never from the working tree (a lane's
# feature branch is not what serves). Read only when the job buys clips. Same root as ACK_FILE.
WORKERS_REPO="${KFU_WORKERS_REPO:-/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-workers}"
# THE INTERPRETER THAT IMPORTS THE PRODUCER IS THE PRODUCER'S. Pricing imports workers.*, so it needs the
# workers dependencies; it used to run under whichever python3 was first on PATH, and a bare PATH refused
# every purchase with "No module named 'google'" (#175 round-5 design D3). Checked before it is used.
WORKERS_PY="${KFU_WORKERS_PYTHON:-$WORKERS_REPO/.venv/bin/python}"

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
DRY_RUN="false"     # --dry-run: print the POST URL, headers, body and estimate, then exit 0 before any request.
PAID_STILLS="on"    # --paid-stills on|off: with --motion-clips, buy paid stills too (on) or clips only
                    # (off). How many, and why, is decided beside the body: verification_job.py.
PAID_STILLS_GIVEN="false"  # set when --paid-stills is passed explicitly (validated below)

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
    --paid-stills)  PAID_STILLS="$2"; PAID_STILLS_GIVEN="true"; shift 2 ;;  # on|off — requires --motion-clips 1-3
    --dry-run)  DRY_RUN="true"; shift ;;
    --wait)     WAIT="true"; shift ;;
    --source-writeup) SOURCE_WRITEUP="$2"; shift 2 ;;  # C3-4: verify figure ADOPTION from a writeup
    --on-behalf-of) ON_BEHALF_OF="$2"; shift 2 ;;  # a CLERK USER ID (user_…/test_…), NOT an email.
                    # VISIBILITY (2026-08-28): the default test_user_e2e's jobs NEVER render in the
                    # signed-in Playwright library (different account). A job that must be OBSERVED
                    # on the beta surface needs --on-behalf-of <the browser test account's user_… id>.
    --on-behalf-of-email) ON_BEHALF_OF_EMAIL="$2"; shift 2 ;;  # the ADDRESS, sent as X-On-Behalf-Of-Email
    # Print the header comment block: every line up to the first that is not a comment, minus the
    # shebang. A fixed `head -12` was a duplicated constant coupled to the header's length, and
    # two new header lines pushed `--dry-run`, the short-form caveat and `Auth:` out of the help
    # (qa #175 round-1 code critic and design).
    -h|--help)  sed -n '1d;/^#/!q;p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ---- Duration: a number of minutes, or nothing below means anything ------------------------
# A non-numeric value used to reach the clip warning first ("for a 0s episode") and die later at the
# payload build (qa #175 round-2 code critic). Refuse it here, once.
[[ "$DURATION" =~ ^([0-9]+(\.[0-9]*)?|\.[0-9]+)$ ]] || { echo "--duration must be a number of minutes (e.g. 0.167, 2.0), got: '$DURATION'" >&2; exit 1; }

# ---- Motion clips: validate, and never let the $0 default opt the job OUT of visuals --------
[[ "$MOTION_CLIPS" =~ ^[0-3]$ ]] || { echo "--motion-clips must be 0-3 (the API's VisualOptions bound), got: $MOTION_CLIPS" >&2; exit 1; }
if [[ "$MOTION_CLIPS" != "0" && "$VISUALS" == "false" ]]; then
  # The default sends visuals_opt_out=true, which would cancel the clips the job just bought.
  # Send neither key instead; the api turns motion_clips>0 into wants_visuals itself.
  VISUALS="auto"
  echo "  note: --motion-clips implies --visuals-auto (the default would opt the job out of visuals)" >&2
fi

# ---- Paid stills: validate, and never let a typo fail toward SPEND -------------------------
# This used to test `paid_stills.lower() != "off"`, so every value that was not literally off —
# `false`, `0`, `no`, `none`, `of`, and the EMPTY string — sent real_images:true and bought stills
# (qa #175 round-2 code critic). Same house style as --motion-clips above: validate, exit 1.
_paid_stills_lc=$(printf '%s' "$PAID_STILLS" | tr '[:upper:]' '[:lower:]')
case "$_paid_stills_lc" in
  on|off) PAID_STILLS="$_paid_stills_lc" ;;
  *) echo "--paid-stills must be 'on' or 'off', got: '$PAID_STILLS'" >&2; exit 1 ;;
esac
# REJECTED, not honoured, without clips. Honoured, it would order paid stills that no other flag
# here asks for; silently dropped, it hid a flag that did nothing.
if [[ "$PAID_STILLS_GIVEN" == "true" && "$MOTION_CLIPS" == "0" ]]; then
  echo "--paid-stills only applies with --motion-clips 1-3 (it sizes the stills bought alongside the clips)." >&2
  exit 1
fi

# ---- The producer that prices a purchase: a temporary tree of kitesforu-workers origin/main ---
# Only when the job buys clips (and the stills that ride with them). WORKERS_SRC=<tree>/src
# overrides it, and the tree's ../config/model_catalog.csv is the catalog. A job that buys
# nothing priced needs no tree, so the T3 default path reads no repo and runs no git.
WORKERS_SRC_FOR_PLAN=""
CATALOG_LABEL=""
PLAN_PY="python3"
if [[ "$MOTION_CLIPS" != "0" ]]; then
  if [[ ! -x "$WORKERS_PY" ]] || ! "$WORKERS_PY" -c 'import sys' >/dev/null 2>&1; then
    echo "Refusing: cannot price the clips this job buys. The workers interpreter '$WORKERS_PY' is missing or" >&2
    echo "does not run. Pricing imports the workers selector, so it runs under the workers venv: create it" >&2
    echo "(kitesforu-workers/.venv) or set KFU_WORKERS_PYTHON. Nothing was sent." >&2
    exit 1
  fi
  PLAN_PY="$WORKERS_PY"
  if [[ -n "${WORKERS_SRC:-}" ]]; then
    WORKERS_SRC_FOR_PLAN="$WORKERS_SRC"
    CATALOG_LABEL="WORKERS_SRC=$WORKERS_SRC (explicit)"
  else
    _wsha=$(git -C "$WORKERS_REPO" rev-parse --verify --quiet "origin/main^{commit}") \
      || { echo "Refusing: cannot price the clips this job buys. No origin/main in $WORKERS_REPO (set KFU_WORKERS_REPO or WORKERS_SRC)." >&2; exit 1; }
    _wdate=$(git -C "$WORKERS_REPO" log -1 --format=%cs "$_wsha")
    WORKERS_TREE=$(mktemp -d "${TMPDIR:-/tmp}/kfu-verify-workers.XXXXXX")
    git -C "$WORKERS_REPO" archive "$_wsha" -- src/workers config/model_catalog.csv | tar -x -C "$WORKERS_TREE" \
      || { echo "Refusing: cannot price the clips this job buys. git archive of workers $_wsha failed." >&2; exit 1; }
    WORKERS_SRC_FOR_PLAN="$WORKERS_TREE/src"
    CATALOG_LABEL="kitesforu-workers origin/main ${_wsha:0:12} ($_wdate) select_provider + config/model_catalog.csv"
  fi
fi

# ---- The request, and the estimate and ACK decision computed FROM IT --------------------------
# ONE call builds the body, serializes it, and computes the estimate and the ACK decision from
# those bytes (verification_job.py `plan`). The estimate used to be built ~100 lines from the body
# and shared nothing with it: round 2 began buying stills while the printed estimate stayed
# byte-identical (qa #175 round-2 cost D1/D2). A purchase it cannot price is a refusal (exit 1),
# not a guess.
PLAN=$("$PLAN_PY" "$HERE/verification_job.py" plan \
  "--topic=$TOPIC" "--duration=$DURATION" "--tier=$TIER" "--style=$STYLE" "--visuals=$VISUALS" \
  "--format=$FORMAT" "--content-rating=$CONTENT_RATING" "--source-writeup=$SOURCE_WRITEUP" \
  "--language=$LANGUAGE" "--motion-clips=$MOTION_CLIPS" "--paid-stills=$PAID_STILLS" "--short=$SHORT" \
  "--workers-src=$WORKERS_SRC_FOR_PLAN" "--catalog-label=$CATALOG_LABEL") \
  || { echo "Refusing: the request could not be built and priced (reason above). Nothing was sent." >&2; exit 1; }
plan_field() {
  printf '%s' "$PLAN" | python3 -c '
import json, sys
v = json.load(sys.stdin)[sys.argv[1]]
print(("true" if v else "false") if isinstance(v, bool) else ("\n".join(v) if isinstance(v, list) else v))' "$1"
}
PAYLOAD=$(plan_field payload)
EST=$(plan_field est)
EST_DETAIL=$(plan_field est_detail)
PLAN_WARNINGS=$(plan_field warnings)
NEEDS_ACK=$(plan_field needs_ack)
REASON=$(plan_field ack_reason)
[[ -n "$PLAN_WARNINGS" ]] && printf '  %s\n' "$PLAN_WARNINGS" >&2
print_est_detail() { [[ -n "$EST_DETAIL" ]] && while IFS= read -r _l; do echo "    $_l" >&2; done <<< "$EST_DETAIL"; return 0; }

# ---- Ladder gate: anything beyond T3 needs a fresh founder ack -------------------
# Keyed on what the BODY orders (tier, visuals, duration, clips, stills), and the price is on the
# screen of whoever is asked for the ACK.
if [[ "$NEEDS_ACK" == "true" && "$DRY_RUN" == "true" ]]; then
  echo "  dry-run: a real run would need the ACK ($REASON) est=$EST" >&2
elif [[ "$NEEDS_ACK" == "true" ]]; then
  fresh="false"
  if [[ -f "$ACK_FILE" ]]; then
    age=$(( $(date +%s) - $(stat -f %m "$ACK_FILE" 2>/dev/null || stat -c %Y "$ACK_FILE") ))
    [[ $age -lt 3600 ]] && fresh="true"
  fi
  if [[ "$fresh" != "true" ]]; then
    echo "T4 ESCALATION BLOCKED ($REASON)— this is a real-price run. est=$EST" >&2
    print_est_detail
    echo "Ask the founder to run: touch $ACK_FILE   (valid 60 min)" >&2
    echo "Ladder: .claude/rules/03-money.md — name WHICH premium-only behavior you are testing." >&2
    exit 3
  fi
fi

# ---- Auth --------------------------------------------------------------------------
if [[ "$DRY_RUN" != "true" && -z "${TEST_API_KEY:-}" ]]; then
  TEST_API_KEY=$(gcloud secrets versions access latest --secret=TEST_API_KEY --project=kitesforu-dev 2>/dev/null) \
    || { echo "TEST_API_KEY not in env and Secret Manager fetch failed" >&2; exit 1; }
fi

# Born-short routing is a QUERY PARAM (crud.py: `?short_video=true`, P0d). duration<=2
# required (short_hint = short_video AND duration_min<=2), else the hint is ignored.
POST_URL="$API_BASE/v1/podcasts"
[[ "$SHORT" == "true" ]] && POST_URL="${POST_URL}?short_video=true"

echo "Creating verification job: tier=$TIER duration=${DURATION}min visuals=$VISUALS lang=$LANGUAGE est=$EST" >&2
print_est_detail

if [[ -n "$ON_BEHALF_OF" ]]; then
  # `X-On-Behalf-Of` is a CLERK USER ID, never an email. The api validates it with
  # `_validate_clerk_user_id` (kitesforu-api/src/api/auth/clerk.py), which accepts only a
  # `user_*` or `test_*` prefix; anything else raises `Invalid X-On-Behalf-Of user ID format`
  # and the caller sees a bare `{"detail":"Invalid authentication credentials"}` — a 401 that
  # looks like a BAD KEY and sends you hunting the wrong thing (measured 2026-08-08: the same
  # TEST_API_KEY returned HTTP 200 on `GET /v1/podcasts` at that moment, and 401 with no key,
  # so the key was provably fine). The email belongs in the SEPARATE `X-On-Behalf-Of-Email`.
  # Fail FAST and say so, rather than emitting a request whose rejection is unreadable. Checked
  # BEFORE the OWNER line, which used to reassure about a value it then rejected (qa #175 round-2
  # design, claims D7).
  if [[ "$ON_BEHALF_OF" != user_* && "$ON_BEHALF_OF" != test_* ]]; then
    echo "--on-behalf-of expects a CLERK USER ID (user_… or test_…), not '$ON_BEHALF_OF'." >&2
    if [[ "$ON_BEHALF_OF" == *@* ]]; then
      echo "  That looks like an email. The api validates this header as a user ID and will" >&2
      echo "  reject it with a 401 that reads 'Invalid authentication credentials'." >&2
      echo "  Pass the Clerk user ID; use --on-behalf-of-email for the address." >&2
    fi
    exit 2
  fi
fi

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

# EVERY HEADER, IN ONE ARRAY. The dry run prints it, the POST sends it, and the /status reads send
# it minus Content-Type. The dry run used to print hand-typed copies of two of them, in a different
# order from curl's, so a header added to curl would not have appeared in the rehearsal (qa #175
# round-2 design D6, code D6). The bearer is redacted when printed.
HDR_ARGS=(-H "Authorization: Bearer ${TEST_API_KEY:-}")
if [[ -n "$ON_BEHALF_OF" ]]; then
  HDR_ARGS+=(-H "X-On-Behalf-Of: $ON_BEHALF_OF")
  [[ -n "$ON_BEHALF_OF_EMAIL" ]] && HDR_ARGS+=(-H "X-On-Behalf-Of-Email: $ON_BEHALF_OF_EMAIL")
fi
POST_HDR_ARGS=("${HDR_ARGS[@]}" -H "Content-Type: application/json")

# THE DRY RUN EXITS HERE, NOT EARLIER: after the OWNER print and after the --on-behalf-of
# validation, because those are the two things it most needs to rehearse. It used to exit before
# both: `--dry-run --on-behalf-of <an email> --motion-clips 2` exited 0 with a clean-looking
# request, while the identical vector on the sending path is rejected with exit 2 (qa #175 round-1
# code critic and design D1).
if [[ "$DRY_RUN" == "true" ]]; then
  {
    echo "DRY RUN — nothing sent."
    echo "POST $POST_URL"
    for _h in "${POST_HDR_ARGS[@]}"; do
      [[ "$_h" == "-H" ]] && continue
      if [[ "$_h" == Authorization:* ]]; then echo "  -H 'Authorization: Bearer <TEST_API_KEY redacted>'"; else echo "  -H '$_h'"; fi
    done
  } >&2
  # The BODY alone goes to stdout, so `--dry-run 2>/dev/null | jq .` works. Every banner above is
  # on stderr for the same reason (round-1 code critic NIT).
  echo "$PAYLOAD"
  exit 0
fi

# THE POST. It is bounded (the /status reads always were, this was not, so a stalled api held the script
# with no word: #175 round-5 latency L3), and every outcome is classified by what it says about the job.
# A transport failure or a timeout can follow the api's write, so it is "a job MAY exist" (exit 8), never
# a plain failure a caller would retry.
POST_STATE="sent"
POST_OUT=$(curl -sS --connect-timeout 15 --max-time 120 -X POST "$POST_URL" "${POST_HDR_ARGS[@]}" \
  -d "$PAYLOAD" -w $'\n%{http_code}') || {
  _rc=$?
  _how="curl exit $_rc"; (( _rc == 28 )) && _how="timed out, curl exit 28"
  echo "POST failed at the transport level ($_how). The job MAY have been created and charged:" >&2
  echo "check the owner's library before re-running, or this buys it twice." >&2
  finish 8
}
HTTP_CODE="${POST_OUT##*$'\n'}"
RESP="${POST_OUT%$'\n'*}"
JOB_ID=$(echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('job_id') or d.get('id') or '')" 2>/dev/null || true)
if [[ -z "$JOB_ID" ]]; then
  if [[ "$HTTP_CODE" =~ ^4[0-9][0-9]$ ]]; then
    # 4xx is decided before the job is written or any credit is charged: auth and validation before the
    # handler runs, then the concurrency, quota and credit checks, all ahead of the charge and the job doc
    # (kitesforu-api routes/podcasts/crud.py create_podcast_job; services/podcast_services.py create_job
    # deducts and only then writes). A free owner's trial slot is reserved by the quota check, so a credit
    # 429 after it has spent that slot, never credits. The one 4xx that can follow a create, the
    # Idempotency-Key 409, needs a header this script does not send.
    POST_STATE="rejected"
    echo "The api REJECTED the request (HTTP $HTTP_CODE) before creating a job; nothing was charged. Response:" >&2
    echo "$RESP" >&2
    finish 1
  fi
  echo "No job id in the api's reply (HTTP ${HTTP_CODE:-none}). The job MAY have been created and charged: a 5xx or a" >&2
  echo "gateway timeout can follow the api's write. Check the owner's library before re-running. Response:" >&2
  echo "$RESP" >&2
  finish 8
fi
POST_STATE="created"
echo "job_id=$JOB_ID  (est $EST)"
echo "status: $API_BASE/v1/podcasts/$JOB_ID/status"

# ---- Read back what the api STORED, for every run the ACK gate priced -------------------------
# The api clamps a FREE subscription silently. `podcast_services.create_job` drops visual_options
# (`visual_options_resolved = None` when not tier_is_paid) and lowers any quality above `medium`,
# and the 200 response says nothing. So a T4 could be created, charged and approved for clips it
# will never render. `GET /status` echoes the stored request (`inputs`, `quality_tier`).
# 6 = stored less than asked; 7 = could not read it. In both the job WAS created.
if [[ "$NEEDS_ACK" == "true" ]]; then
  _readback="unread"
  SHORTFALL=""
  for _attempt in 1 2 3; do
    _snap=$(curl -sS --connect-timeout 10 --max-time 20 "$API_BASE/v1/podcasts/$JOB_ID/status" "${HDR_ARGS[@]}" 2>/dev/null) || _snap=""
    if SHORTFALL=$(printf '%s' "$_snap" | python3 "$HERE/verification_job.py" check-stored --requested "$PAYLOAD" --snapshot - 2>/dev/null); then
      _readback="read"
      break
    fi
    sleep 2
  done
  if [[ "$_readback" != "read" ]]; then
    echo "UNVERIFIED: job $JOB_ID WAS created, but what the api stored could not be read back (3 attempts)." >&2
    echo "Read its visual_options and quality_tier before grading it, and before re-running anything." >&2
    finish 7
  fi
  if [[ -n "$SHORTFALL" ]]; then
    echo "THE API STORED LESS THAN THIS SCRIPT ASKED FOR. Job $JOB_ID WAS created and charged for what it stored:" >&2
    while IFS= read -r _l; do echo "  $_l" >&2; done <<< "$SHORTFALL"
    echo "The usual cause is the owner's subscription: a free tier drops visual_options and caps quality at" >&2
    echo "medium (kitesforu-api podcast_services.create_job). The estimate above does not describe this job." >&2
    echo "Do not re-run with the same owner; it gets the same clamp." >&2
    finish 6
  fi
  echo "  read back: the api stored the requested quality_tier and visual_options" >&2
fi

if [[ "$WAIT" == "true" ]]; then
  # BOUNDED, AND A PROBE FAILURE IS ITS OWN STATE.
  #
  # The bound exists because a poll loop billed Cloud Run for 8 days 17 hours. A bound alone does
  # not fix the class: the loop used to fold an UNREADABLE status into "keep waiting", 60 useless
  # requests (in the unbounded ancestor, ~26,000). A probe that cannot answer is a DIFFERENT
  # condition from "still running", and three in a row abort with exit 9 (the job exists).
  #
  # A TRANSPORT failure is a failed probe too. `RAW=$(curl ...)` under `set -e` used to END the
  # script on one timeout (curl exit 28) or refused connection (7), with no message, skipping the
  # probe counter entirely. On a paid wait that abandons a render being charged for, and invites a
  # re-run that buys it twice (qa #175 round-3 code FIX-1, design FIX-1, cost FIX-2).
  #
  # WHAT TO WAIT FOR: ONE readiness rule, the package's (kitesforu_qa.visual_readiness, #175 round 5).
  # `status` turns terminal when the AUDIO completes; visuals run on a parallel subscriber and finish
  # later. A run that asked for a video (the script's own --visuals / --visuals-auto / purchase, OR the
  # doc's wants_visuals) is gradeable only when the video ladder says `ready` AND its clip array has
  # settled: the clips_settled_at stamp is not cleared and the same clip fingerprint holds for the
  # settled_clips window. Only that read's hero count is reported. A video that is NOT coming
  # (video_status=failed_assembly, a terminal skip reason such as audio_only_no_clips, visual.status=failed)
  # ends the wait with exit 5, naming the reason, once two reads a poll apart agree (workers say a retry
  # can flip failed_assembly back to ready). It used to wait out the whole budget for that video.
  #
  # HOW LONG, by wall clock (SECONDS), and never STARTING a poll whose sleep plus its probe's own
  # timeout would end past the budget, so the printed bound is the real ceiling (#175 round-5 latency
  # N2). The budgets are chosen, and the census beside each says how often a healthy job outlasts it.
  # Read-only census 2026-10-10, newest 600 podcast_jobs by created_at, video time = the GCS object
  # time_created of visual.video_url (the job doc stamps no video time): see the PR body for the command.
  #   audio-only, 1800s: created -> completed_at p99 1208s, max 2761s (n=591, the 2026-10-08 census)
  #   a video, no purchase, 5400s: created -> video, 18 of 443 jobs after 5400s (p95 3597s)
  #   purchased clips, 10800s: created -> video, 3 of 21 jobs after 10800s (median 4280s, p90 12719s);
  #     all 21 are ultra / motion_clips 3 / one owner, not a low-tier 2-clip run
  # Running out is exit 4, never success: a paid --wait can run out while the job is healthy.
  # Once the audio is done, poll once a minute: a video is minutes away, and the clip array needs reads
  # a window apart. (The 60 s cadence used to be justified by a refund transaction each /status read ran;
  # api #867 moved that refund to the watchdog sweep, so the reason is latency, not cost.)
  TERMINAL_STATUSES=$(python3 "$HERE/../src/kitesforu_qa/job_status.py" terminal)
  FINISHED_STATUSES=$(python3 "$HERE/../src/kitesforu_qa/job_status.py" finished-rendering)
  SETTLE_WINDOW_S=$(python3 "$HERE/verification_job.py" settle-window)
  in_list() { [[ -n "$1" && " $2 " == *" $1 "* ]]; }
  AUDIO_BUDGET_S=1800
  VISUALS_BUDGET_S=5400
  PURCHASE_BUDGET_S=10800
  POLL_INTERVAL_S=15
  VISUALS_POLL_INTERVAL_S=60
  PROBE_TIMEOUT_S=20
  MAX_CONSECUTIVE_PROBE_FAILURES=3
  EXPECT_VIDEO="no"
  [[ "$VISUALS" != "false" || "$MOTION_CLIPS" != "0" ]] && EXPECT_VIDEO="yes"
  probe_failures=0
  polls=0
  scheduled=0
  LAST_STATUS=""; VIDEO_EXPECTED="$EXPECT_VIDEO"; PHASE=""; REASON=""; SETTLE=""; HERO="0"
  FP=""; FP_STILL_S=0; PREV_ENDING=""
  AUDIO_DONE_AT=""
  RESULT=""
  wait_start=$SECONDS
  echo "Polling until gradeable (wall clock, at most: audio-only ${AUDIO_BUDGET_S}s; a video ${VISUALS_BUDGET_S}s; purchased clips ${PURCHASE_BUDGET_S}s)..."
  while :; do
    _budget=$AUDIO_BUDGET_S
    [[ "$VIDEO_EXPECTED" == "yes" ]] && _budget=$VISUALS_BUDGET_S
    (( MOTION_CLIPS > 0 )) && _budget=$PURCHASE_BUDGET_S
    _interval=$POLL_INTERVAL_S
    [[ -n "$AUDIO_DONE_AT" ]] && _interval=$VISUALS_POLL_INTERVAL_S
    # Never START a poll that could end past the budget: its sleep and its probe's own timeout must fit.
    # The scheduled sleep total bounds it too, so the bound holds even where time does not advance.
    (( SECONDS - wait_start + _interval + PROBE_TIMEOUT_S > _budget || scheduled + _interval > _budget )) && break
    polls=$(( polls + 1 ))
    sleep "$_interval"
    scheduled=$(( scheduled + _interval ))
    RAW=$(curl -sS --connect-timeout 10 --max-time "$PROBE_TIMEOUT_S" "$API_BASE/v1/podcasts/$JOB_ID/status" "${HDR_ARGS[@]}" 2>/dev/null) || RAW=""
    IFS='|' read -r STATUS _vexp _phase _reason _settle _fp _hero <<< "$(printf '%s' "$RAW" | python3 "$HERE/verification_job.py" status-fields --snapshot - --expect-video "$EXPECT_VIDEO")"
    if [[ -z "$STATUS" ]]; then
      probe_failures=$((probe_failures + 1))
      _why="${RAW:0:120}"; [[ -z "$RAW" ]] && _why="(no response: transport failure or timeout)"
      echo "  [$polls] (status unreadable — probe failure $probe_failures/$MAX_CONSECUTIVE_PROBE_FAILURES): $_why"
      if (( probe_failures >= MAX_CONSECUTIVE_PROBE_FAILURES )); then
        RESULT="unreadable"; break
      fi
      continue
    fi
    probe_failures=0
    LAST_STATUS="$STATUS"; VIDEO_EXPECTED="$_vexp"; PHASE="$_phase"; REASON="$_reason"; SETTLE="$_settle"
    _vis=""
    [[ "$VIDEO_EXPECTED" == "yes" ]] && _vis="  video=$PHASE ($REASON) clips_settled_at=$SETTLE"
    echo "  [$polls] $STATUS (waited $(( SECONDS - wait_start ))s)$_vis"
    if in_list "$STATUS" "$TERMINAL_STATUSES" && ! in_list "$STATUS" "$FINISHED_STATUSES"; then
      RESULT="ended"; break
    fi
    in_list "$STATUS" "$FINISHED_STATUSES" || continue
    [[ -z "$AUDIO_DONE_AT" ]] && AUDIO_DONE_AT=$(( SECONDS - wait_start ))
    if [[ "$VIDEO_EXPECTED" != "yes" ]]; then RESULT="gradeable"; break; fi
    case "$PHASE" in
      failed|no_video)
        FP=""; FP_STILL_S=0
        if [[ "$PREV_ENDING" == "$PHASE:$REASON" ]]; then RESULT="no_video"; break; fi
        PREV_ENDING="$PHASE:$REASON" ;;
      ready)
        PREV_ENDING=""
        if [[ "$SETTLE" == "in_flight" ]]; then
          FP=""; FP_STILL_S=0              # a pass is writing the array: no read of it counts
        elif [[ "$_fp" != "$FP" ]]; then
          FP="$_fp"; FP_STILL_S=0          # the array moved: start the window again
        else
          FP_STILL_S=$(( FP_STILL_S + _interval ))
          if (( FP_STILL_S >= SETTLE_WINDOW_S )); then HERO="$_hero"; RESULT="gradeable"; break; fi
        fi ;;
      *)
        PREV_ENDING=""; FP=""; FP_STILL_S=0 ;;
    esac
  done
  _waited=$(( SECONDS - wait_start ))
  case "$RESULT" in
    unreadable)
      echo "ABORTING: cannot read job status ($probe_failures consecutive failures, waited ${_waited}s)." >&2
      echo "This is a PROBE failure, not a job state — check auth (TEST_API_KEY), the api and the network." >&2
      echo "Job $JOB_ID WAS created and may still be running; read it directly rather than polling blind, and do not re-run it." >&2
      finish 9 ;;
    ended)
      # failed / cancelled: terminal, and there is no episode. It used to exit 0 with "grade it".
      echo "ENDED: job $JOB_ID is '$LAST_STATUS' after waiting ${_waited}s. There is nothing to grade." >&2
      finish 5 ;;
    no_video)
      echo "ENDED: job $JOB_ID finished its audio ('$LAST_STATUS') but NO VIDEO is coming: $PHASE, $REASON" >&2
      echo "(the same on two reads a poll apart, waited ${_waited}s). A run that asked for a video has nothing to grade for it." >&2
      finish 5 ;;
    gradeable)
      if [[ "$VIDEO_EXPECTED" == "yes" ]]; then
        echo "Video ready and its clip array settled (unchanged for ${FP_STILL_S}s, clips_settled_at $SETTLE): $HERO delivered hero clip(s)."
        if (( MOTION_CLIPS > 0 && HERO < MOTION_CLIPS )); then
          echo "⚠️  Only $HERO of $MOTION_CLIPS PURCHASED clip(s) landed in the delivered video. That is a finding to" >&2
          echo "   grade, not a pass: the rest failed or were refunded (read visual_veo on the job doc)." >&2
        fi
      fi
      case "$LAST_STATUS" in
        needs_review) _what="finished, held for human review (audio usable)" ;;
        failed_qa)    _what="finished, failed the automatic QA gate (audio may still be usable)" ;;
        *)            _what="finished" ;;
      esac
      echo "Final status: $LAST_STATUS — $_what, after waiting ${_waited}s. Grade it with the \$0 battery: kqa / Artifact.load('$JOB_ID')"
      exit 0 ;;
  esac
  if [[ -z "$LAST_STATUS" ]]; then
    echo "Final status: UNKNOWN (never read a status in ${_waited}s of waiting, $polls polls) — job $JOB_ID WAS created; read it directly." >&2
    finish 9
  fi
  # RUNNING OUT IS NOT SUCCESS. The loop used to fall through and exit 0 with whatever non-terminal
  # status it last read, so the caller could not tell a finished job from one still rendering.
  if [[ -n "$AUDIO_DONE_AT" ]]; then
    echo "TIMED OUT after waiting ${_waited}s ($polls polls): job $JOB_ID finished its audio ('$LAST_STATUS', waited +${AUDIO_DONE_AT}s)," >&2
    echo "but its video is not ready to grade: ${PHASE:-none} (${REASON:-no read}), clips_settled_at ${SETTLE:-none}." >&2
    if [[ "$PHASE" == "ready" ]]; then
      echo "The video exists but its clip array never held still for ${SETTLE_WINDOW_S}s, so no clip count was taken." >&2
    fi
    echo "This is NOT a result: a healthy paid job can outlast this wait (see the budgets in this script). Re-read it later: kqa / Artifact.load('$JOB_ID')" >&2
  else
    echo "TIMED OUT after waiting ${_waited}s ($polls polls): job $JOB_ID last read as '$LAST_STATUS'." >&2
    echo "This is NOT a result — the job is still running and nothing here is gradeable yet." >&2
    echo "Re-read it later: kqa / Artifact.load('$JOB_ID')" >&2
  fi
  finish 4
fi
