#!/usr/bin/env bash
# Morning render-proof batch (2026-07-09) — proves the overnight quality fixes at the ARTIFACT level.
# GATED on founder spend-ack (renders cost real money). Run:  touch .claude/FOUNDER_SPEND_ACK  then this.
# Each job re-scored via quality_matrix.py to show the DELTA vs the trustworthy baseline (QUALITY_BACKLOG.md).
set -uo pipefail
K=/Users/vikrantbhosale/gitprojects/kitesforu
ACK="$K/.claude/FOUNDER_SPEND_ACK"
QA="$K/kitesforu-qa/scripts/create_verification_job.sh"
if [ ! -f "$ACK" ]; then echo "BLOCKED: needs founder spend-ack — touch $ACK first (renders cost money)."; exit 1; fi

echo "== Morning proof batch — 3 render proofs + re-score =="
# 1. STORY-QUALITY (R1-R5): a romance DIALOGUE → man+woman cast + seductive tone + character images (~medium, real 2-speaker)
echo "[1] story-quality romance dialogue (medium)…"
J1=$("$QA" --duration 2.0 --tier medium --style Storytelling \
  --topic "a flirtatious late-night conversation between a man and a woman at a wine bar" --wait 2>&1 \
  | grep -oiE "[0-9a-f-]{36}" | head -1); echo "   job=$J1"
# 2. MODALITY_MIX / #24: a STRUCTURAL short → should now mix scene_image + diagram (not all-diagram)
echo "[2] structural short for modality_mix / #24 (short, scene richness)…"
J2=$("$QA" --short --duration 1.0 --tier low --topic "how a suspension bridge stays up" --wait 2>&1 \
  | grep -oiE "[0-9a-f-]{36}" | head -1); echo "   job=$J2"
# 3. SUBSTANCE (iter-2/3): a research-worthy topic → knowledge brief now non-empty (research grounds the content)
echo "[3] substance/research-grounding topic (short)…"
J3=$("$QA" --short --duration 1.0 --tier low --topic "the economic causes of the 2008 financial crisis" --wait 2>&1 \
  | grep -oiE "[0-9a-f-]{36}" | head -1); echo "   job=$J3"

echo "== Re-score the 3 proofs vs baseline (quality_matrix) =="
cd "$K/kitesforu-qa"
python3 scripts/quality_matrix.py --job-ids "$J1" "$J2" "$J3" --out /tmp/morning_proof_scorecard.json 2>&1 | tail -20
echo "== Compare per-axis vs QUALITY_BACKLOG.md baseline: modality_mix (was 20.8), substance research_skipped (was true), hook =="
echo "Jobs: story=$J1 structural=$J2 substance=$J3"
echo "ALSO: manually listen to $J1 (man+woman voices + seductive tone) + check its rendered frames (character images)."
