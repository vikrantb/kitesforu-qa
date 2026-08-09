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
#     — verifies the short-form craft (wpm band, caption dwell); duration>0.5 ⇒ needs ACK
#
# Auth: TEST_API_KEY env var, or fetched from Secret Manager (kitesforu-dev).

set -euo pipefail

API_BASE="${API_BASE:-https://kitesforu-api-m6zqve5yda-uc.a.run.app}"
ACK_FILE="/Users/vikrantbhosale/gitprojects/kitesforu/.claude/FOUNDER_SPEND_ACK"

TOPIC="pipeline verification"
DURATION="0.167"          # 10 seconds — the enforced API minimum
TIER="low"
STYLE="Explainer"   # API style enum: Explainer|Storytelling|Interview|... ("conversation" was removed)
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    --topic)    TOPIC="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --tier)     TIER="$2"; shift 2 ;;
    --style)    STYLE="$2"; shift 2 ;;
    --format)   FORMAT="$2"; shift 2 ;;  # drama|panel — exercises multi-voice casting cheaply
    --short)    SHORT="true"; shift ;;   # Social Short path (9:16, kinetic captions, assembly)
    --visuals)  VISUALS="true"; shift ;;
    --visuals-auto) VISUALS="auto"; shift ;;  # T3-SAFE: send NEITHER wants_visuals nor
                    # visuals_opt_out, so the worker's non-fiction $0 auto-default applies
                    # (deterministic diagrams/cards, NO paid images). Use this to exercise the
                    # visual PLANNING path — info-figure routing, figure adoption — without
                    # tripping the T4 paid-visuals gate. `--visuals` remains T4 (real images).
    --content-rating) CONTENT_RATING="$2"; shift 2 ;;  # g|pg|pg_13|r — sets body.content_rating
    --wait)     WAIT="true"; shift ;;
    --source-writeup) SOURCE_WRITEUP="$2"; shift 2 ;;  # C3-4: verify figure ADOPTION from a writeup
    --on-behalf-of) ON_BEHALF_OF="$2"; shift 2 ;;  # a CLERK USER ID (user_…/test_…), NOT an email
    --on-behalf-of-email) ON_BEHALF_OF_EMAIL="$2"; shift 2 ;;  # the ADDRESS, sent as X-On-Behalf-Of-Email
    -h|--help)  grep '^#' "$0" | head -12; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ---- Ladder gate: anything beyond T3 needs a fresh founder ack -------------------
needs_ack="false"
reason=""
[[ "$TIER" != "low" ]] && { needs_ack="true"; reason+="tier=$TIER "; }
[[ "$VISUALS" == "true" ]] && { needs_ack="true"; reason+="visuals=on "; }
awk "BEGIN{exit !($DURATION > 0.5)}" && { needs_ack="true"; reason+="duration=${DURATION}min "; }

if [[ "$needs_ack" == "true" ]]; then
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
  high)   EST="~\$1.0-1.3" ;;
  *)      EST="unknown" ;;
esac
[[ "$VISUALS" == "true" ]] && EST="$EST + visuals (~\$0.10-0.50)"

# ---- Auth --------------------------------------------------------------------------
if [[ -z "${TEST_API_KEY:-}" ]]; then
  TEST_API_KEY=$(gcloud secrets versions access latest --secret=TEST_API_KEY --project=kitesforu-dev 2>/dev/null) \
    || { echo "TEST_API_KEY not in env and Secret Manager fetch failed" >&2; exit 1; }
fi

PAYLOAD=$(python3 - "$TOPIC" "$DURATION" "$TIER" "$STYLE" "$VISUALS" "$FORMAT" "$CONTENT_RATING" "$SOURCE_WRITEUP" <<'PYEOF'
import json, sys
topic, duration, tier, style, visuals, fmt, content_rating, source_writeup = sys.argv[1:9]
body = {
    "topic": topic,
    "duration_min": float(duration),
    "style": style,
    "quality_tier": tier,
    "economy_mode": tier == "low",

    "intro_enabled": False,
    "allow_premium": tier in ("high", "ultra"),
    "skip_clarifier": True,
    "language": "en-US",
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

echo "Creating verification job: tier=$TIER duration=${DURATION}min visuals=$VISUALS est=$EST"
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
  echo "Polling until terminal state..."
  for i in $(seq 1 60); do
    sleep 15
    STATUS=$(curl -sS "$API_BASE/v1/podcasts/$JOB_ID/status" -H "Authorization: Bearer $TEST_API_KEY" ${OBO_ARGS[@]+"${OBO_ARGS[@]}"} \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
    echo "  [$i] $STATUS"
    case "$STATUS" in completed|failed|failed_qa) break ;; esac
  done
  echo "Final status: $STATUS — grade it with the \$0 battery: kqa / Artifact.load('$JOB_ID')"
fi
