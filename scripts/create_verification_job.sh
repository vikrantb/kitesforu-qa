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
VISUALS="false"
WAIT="false"
ON_BEHALF_OF=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --topic)    TOPIC="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --tier)     TIER="$2"; shift 2 ;;
    --style)    STYLE="$2"; shift 2 ;;
    --format)   FORMAT="$2"; shift 2 ;;  # drama|panel — exercises multi-voice casting cheaply
    --visuals)  VISUALS="true"; shift ;;
    --wait)     WAIT="true"; shift ;;
    --on-behalf-of) ON_BEHALF_OF="$2"; shift 2 ;;  # founder-visible: job lands in this user's library
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

PAYLOAD=$(python3 - "$TOPIC" "$DURATION" "$TIER" "$STYLE" "$VISUALS" "$FORMAT" <<'PYEOF'
import json, sys
topic, duration, tier, style, visuals, fmt = sys.argv[1:7]
body = {
    "topic": topic,
    "duration_min": float(duration),
    "style": style,
    "quality_tier": tier,
    "economy_mode": tier == "low",
    "wants_visuals": visuals == "true",
    "visuals_opt_out": visuals != "true",
    "intro_enabled": False,
    "allow_premium": tier in ("high", "ultra"),
    "skip_clarifier": True,
    "language": "en-US",
}
if fmt:
    body["format"] = fmt
print(json.dumps(body))
PYEOF
)

echo "Creating verification job: tier=$TIER duration=${DURATION}min visuals=$VISUALS est=$EST"
OBO_ARGS=()
[[ -n "$ON_BEHALF_OF" ]] && OBO_ARGS=(-H "X-On-Behalf-Of: $ON_BEHALF_OF")
# ${arr[@]+...} guard: macOS bash 3.2 treats an EMPTY array expansion as an
# unbound variable under `set -u`.
RESP=$(curl -sS -X POST "$API_BASE/v1/podcasts" \
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
