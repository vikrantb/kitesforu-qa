# kitesforu-qa Cost Changelog

Per Tenet 7 (cost transparency): every change affecting per-unit cost is documented here.

## 2026-07-02 — Test-cost-ladder: verification defaults to CHEAP (cost-SAVING)

**Files:** `scripts/create_verification_job.sh` (new), `scripts/canary_loop.py`,
`src/kitesforu_qa/integrations/kitesforu_api.py`, `src/kitesforu_qa/cli.py`

**Context:** June 2026 GCP audit — ~248 full-price verification episodes ≈ $250 (30% of the
month's bill) were generated just to check fixes.

**Change:** every job-creating path in this repo now defaults to the cheapest real pipeline
run (test-cost-ladder T3):
- `KitesForUClient.create_job`: `duration_min` 10 → **0.167** (10s), adds
  `quality_tier="low"` + `skip_clarifier=True` defaults (~**$0.025/job** vs ~$1.50+ for a
  10-min medium job — ~60× cheaper per call).
- `kqa e2e --duration` default 10 → 0.167 (float).
- `canary_loop.py` execute body: `quality_tier` defaults `"low"` (env `CANARY_QUALITY_TIER`
  to escalate), `wants_visuals` defaults off (env `CANARY_WANTS_VISUALS=true`). Canary
  per-run ≈ $0.15 → **≈ $0.03**; at the 30-min loop cadence ≈ $7/day → **≈ $1.4/day**.
- New sanctioned creator `scripts/create_verification_job.sh`: T3 defaults; T4 escalation
  (tier medium/high, visuals, duration > 0.5 min) refuses without a fresh founder ack file
  (`.claude/FOUNDER_SPEND_ACK`, 60-min validity).

**Per-unit $ delta:** verification job cost 6–60× DOWN by default. No pricing-page impact
(internal QA spend only). Escalation to real-price canaries remains possible, deliberately,
with founder ack — see `.claude/rules/test-cost-ladder.md`.
