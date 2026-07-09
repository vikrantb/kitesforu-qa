# QUALITY_BACKLOG — Measured Quality Engine

Generated: 2026-07-09T05:20:17.578636+00:00 · Project: `kitesforu-dev` · Mode: `query-recent-7d`
Cells: 80/80 scored (the rest failed to score — see Unscored Jobs below; fail-open, not
a crash).

This backlog is produced by `scripts/quality_matrix.py` — a $0/T1 aggregation of the existing
8-axis SHORT SCORECARD (`kitesforu_qa.scorecard.score_short`) over a set of real completed jobs.
No new jobs were generated to produce this baseline.

## Baseline Summary (per axis, aggregated across all scored cells)

| Axis | Floor | Mean | Min | Max | Scored/Total | Pass rate |
|---|---|---|---|---|---|---|
| Hook stop-power (`hook_stop_power`) | 80 | 67.7 | 20.0 | 100.0 | 80/80 | 45% |
| Substance novelty (`substance_novelty`) | 70 | 1.2 | 0.0 | 50.0 | 80/80 | 0% |
| Visual truth (`visual_truth`) | 90 | 100.0 | 100.0 | 100.0 | 75/80 | 100% |
| Modality mix (`modality_mix`) | 70 | 20.8 | 0.0 | 82.0 | 56/80 | 4% |
| Motion density (`motion_density`) | 75 | 40.3 | 7.2 | 100.0 | 54/80 | 9% |
| Sync exactness (`sync_exactness`) | 85 | — | — | — | 0/80 | — |
| Audio feel (`audio_feel`) | 70 | 69.5 | 40.5 | 93.0 | 54/80 | 87% |
| Cost + safety (`cost_safety`) | 100 | 0.0 | 0.0 | 0.0 | 20/80 | 0% |

## Per (Genre x Format) Breakdown — mean score per axis

| Genre | Format | Hook stop-power | Substance novelty | Visual truth | Modality mix | Motion density | Sync exactness | Audio feel | Cost + safety | n |
|---|---|---|---|---|---|---|---|---|---|---|
| educational | episode | 60.0 | 50.0 | 100.0 | 0.0 | — | — | — | 0.0 | 1 |
| educational | short | 68.6 | 0.0 | 100.0 | 22.0 | 38.5 | — | 68.6 | 0.0 | 51 |
| general | short | 64.4 | 0.0 | 100.0 | 18.1 | 44.3 | — | 70.6 | 0.0 | 26 |
| news | short | 100.0 | 50.0 | 100.0 | 45.9 | — | — | — | 0.0 | 1 |
| romance | episode | 80.0 | 0.0 | — | 36.1 | 8.2 | — | 72.0 | 0.0 | 1 |

## Top Ranked Systematic Weaknesses

Ranked by **severity = (# cells below floor) x (mean point-gap below floor)** — widespread AND deep
problems rank first; a single one-off low score on an otherwise-fine axis will not appear here.

### 1. `substance_novelty` — severity 5504.0

- **Mean score:** 1.2 (floor 70)
- **Affected:** 80/80 scored cells (100%), mean gap below floor: 68.8 pts
- **Hypothesis:** This axis is a $0 PROXY (research-grounding + angle-brief richness) unless --enable-judge is wired — a low mean usually means the research route resolver is choosing 'none'/'skip' too often, or the angle brief is thin (<500 chars, 0 angles).
- **Suggested fix area:** research: route resolver + angle-research brief generation
- **Affected job_ids:** d1581a4b-bb2a-4fb7-9c2e-d5ce055a3060, f2052d47-8fec-47ab-9ed4-5704ec804c38, e8f7d2ab-7de2-4683-8912-470d73466657, caf39620-2613-42ae-a768-f0acee8473b6, eea068de-5f07-4850-8205-a94b94763d34, 103d05ff-8f43-447e-8249-2bdcff660b10, 669ee6b6-6e07-40bf-8599-feeaba9985a0, c8de3611-b66a-4965-bcf6-fd90851dfab5 (+72 more)

### 2. `modality_mix` — severity 2775.6

- **Mean score:** 20.8 (floor 70)
- **Affected:** 54/56 scored cells (96%), mean gap below floor: 51.4 pts
- **Hypothesis:** Low Shannon entropy across {ai_photo, diagram, kinetic_text, scene} beat buckets — the reel leans on one modality (commonly all kinetic_text or all photo) instead of a balanced mix. Check the Visual Director's per-beat modality allocation.
- **Suggested fix area:** visuals: Visual Director modality allocation
- **Affected job_ids:** d1581a4b-bb2a-4fb7-9c2e-d5ce055a3060, eea068de-5f07-4850-8205-a94b94763d34, 103d05ff-8f43-447e-8249-2bdcff660b10, 669ee6b6-6e07-40bf-8599-feeaba9985a0, c8de3611-b66a-4965-bcf6-fd90851dfab5, 31d1e088-5c1e-4d63-a14c-582b26ea00ec, dd8224b4-90e3-4fa9-82df-2069f44a7283, 703bded1-c15b-43f9-8b56-86fcddb53de2 (+46 more)

### 3. `cost_safety` — severity 2000.0

- **Mean score:** 0.0 (floor 100)
- **Affected:** 20/20 scored cells (100%), mean gap below floor: 100.0 pts
- **Hypothesis:** A hard gate: either the per-job cost rollup exceeded the per-short cap, or a safety/moderation verdict is failing/missing. ANY low score here is a priority fix — it means a cost overrun or an unmoderated render is reaching ship.
- **Suggested fix area:** cost telemetry + safety/moderation pipeline
- **Affected job_ids:** d1581a4b-bb2a-4fb7-9c2e-d5ce055a3060, eea068de-5f07-4850-8205-a94b94763d34, 669ee6b6-6e07-40bf-8599-feeaba9985a0, 7908d37d-4d3e-44b0-bf08-524a37f404a5, 4c3a1e5f-9b08-4b0e-b903-0932cae961db, b3443263-6b2c-43fb-a661-eed3333311da, f0d1a44f-568e-4d40-b64a-e3308f4010d9, 588e97d0-8c49-4099-8c1b-83902c19d0c3 (+12 more)

### 4. `motion_density` — severity 1994.3

- **Mean score:** 40.3 (floor 75)
- **Affected:** 49/54 scored cells (91%), mean gap below floor: 40.7 pts
- **Hypothesis:** Too few beats carry engine motion provenance (render_mode in parallax/kenburns/video) relative to static stills — the render engine is defaulting to still cards. Check motion_preset selection in the render pipeline.
- **Suggested fix area:** visuals: render engine motion_preset selection
- **Affected job_ids:** eea068de-5f07-4850-8205-a94b94763d34, 103d05ff-8f43-447e-8249-2bdcff660b10, 669ee6b6-6e07-40bf-8599-feeaba9985a0, c8de3611-b66a-4965-bcf6-fd90851dfab5, 31d1e088-5c1e-4d63-a14c-582b26ea00ec, dd8224b4-90e3-4fa9-82df-2069f44a7283, 703bded1-c15b-43f9-8b56-86fcddb53de2, fa8ec798-ef34-42d2-a181-45640a46d4fc (+41 more)

### 5. `hook_stop_power` — severity 1504.8

- **Mean score:** 67.7 (floor 80)
- **Affected:** 44/80 scored cells (55%), mean gap below floor: 34.2 pts
- **Hypothesis:** First beat isn't photo/high-contrast, the hook line is too long (>12 words) or opens with a banned phrase, or the first spoken word lands after 2.5s — check beat0 modality selection and the hook-line generator against narration_rules.yaml's banned-opener list.
- **Suggested fix area:** visuals: beat0 modality selection + script: hook-line generation
- **Affected job_ids:** f2052d47-8fec-47ab-9ed4-5704ec804c38, e8f7d2ab-7de2-4683-8912-470d73466657, caf39620-2613-42ae-a768-f0acee8473b6, 486291d1-31b9-4c46-a8d0-e5a8267eb6f7, f0d1a44f-568e-4d40-b64a-e3308f4010d9, b63f665d-41d2-4d68-b864-ead5a5e13ce4, b82bc8c9-8320-4957-83bb-7a6d0bd763f5, 2553586d-518f-45fc-bf8f-b72c55572686 (+36 more)


## Instrumentation Gaps (axis is UNMEASURABLE, not low — a different failure class)

| Axis | Measured | Coverage |
|---|---|---|
| `sync_exactness` | 0/80 | 0% |
| `cost_safety` | 20/80 | 25% |

## Unscored Jobs (fetch/score failures — fail-open, never crashed the run)

_none — every job in this run produced a scorecard._

## PROPOSED MATRIX FILL (needs founder ack)

Target grid: 8 genres x 2 formats = 16 cells. Covered by this run's cell set: **3/16**.

T3 = a 10s quality_tier=low cell per gap (~$0.025/ea, no founder ack needed) via scripts/create_verification_job.sh. T4 = one representative 30-60s medium/high-tier cell per gap for premium-only behaviors (founder-ack gated, touch .claude/FOUNDER_SPEND_ACK). This function only COMPUTES the estimate — it never creates a job.

**Missing cells (no scored job in the current set):**

| Genre | Format |
|---|---|
| explainer | short |
| explainer | episode |
| storytelling | short |
| storytelling | episode |
| horror | short |
| horror | episode |
| interview | short |
| interview | episode |
| documentary | short |
| documentary | episode |
| comedy | short |
| comedy | episode |
| romance | short |

**Estimated cost to fill all 13 gaps at T3 (10s, quality_tier=low, no ack needed):** ~$0.325. **T4 representative-cell validation** (premium-tier/longer-form behaviors only, founder-ack gated): ~$0.15–$1.30 per gap.

**This section is a proposal only — no job has been created.** Filling these gaps is the founder's ack'd next step (`touch .claude/FOUNDER_SPEND_ACK` then `kitesforu-qa/scripts/create_verification_job.sh`).
