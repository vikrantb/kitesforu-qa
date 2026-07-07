# kitesforu-qa Cost Changelog

Per Tenet 7 (cost transparency): every change affecting per-unit cost is documented here.

## 2026-07-06 — SHORT SCORECARD axis 3 (visual truth): real VLM wired, ¢-cheap, OFF by default

**Files:** `src/kitesforu_qa/scorecard/vlm.py` (new), `src/kitesforu_qa/scorecard/axes.py`,
`scripts/short_scorecard.py`, `pyproject.toml` (new `vlm` extra: `openai`, `anthropic`)

**Context:** PR #52 shipped the 8-axis scorecard with axis 3 (VISUAL TRUTH, floor 90) fully
speced but its VLM dependency-injected and default OFF (`enable_vlm=False` → an honest
"needs-VLM" null). This closes that gap with a concrete, provider-agnostic photo-vs-illustration
VLM — the axis that catches the "Pixar-labeled-photoreal lie" a heuristic can't see.

**Change:** `--vlm` on `scripts/short_scorecard.py` (renamed from the unwired `--enable-vlm`) now
wires `kitesforu_qa.scorecard.vlm.photo_vs_illustration_vlm_fn` — for each beat the job doc labels
photoreal, ffmpeg-extracts a 512px-downscaled still (from the already-downloaded rendered video at
the beat's timestamp, or the beat's own stored asset as a fallback) and asks a cheap vision LLM a
strict photo-vs-illustration question. Provider-agnostic per Tenet 1: tries Gemini flash → OpenAI
gpt-4o-mini (`detail: "low"`) → Anthropic Claude Haiku 4.5, in ascending $/call order, whichever has
a live API key; bounded retries (2/provider) + a 20s per-call timeout; a single beat's
extraction/VLM failure degrades that beat to "unknown" (fail-open), never a crash or a fake pass.

**Per-unit $ delta:** **default is still $0** — `--vlm` is opt-in (default OFF preserves today's
null). When enabled: ~$0.001/photoreal beat (a downscaled still + ~150 output tokens on the cheapest
available provider) — e.g. a typical 5-photoreal-beat short costs ~$0.005 for the whole axis. This is
a T2 cheap-judge tier per the test-cost-ladder; no pricing-page impact (internal QA verification
spend only, not a product-facing cost).

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
