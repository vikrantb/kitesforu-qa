# QUALITY_BACKLOG — Measured Quality Engine

Generated: 2026-07-20T02:41:22.145320+00:00 · Project: `kitesforu-dev` · Mode: `query-recent-10d`
Cells: 20/20 scored (the rest failed to score — see Unscored Jobs below; fail-open, not
a crash).

This backlog is produced by `scripts/quality_matrix.py` — a $0/T1 aggregation of the existing
8-axis SHORT SCORECARD (`kitesforu_qa.scorecard.score_short`) over a set of real completed jobs.
No new jobs were generated to produce this baseline.

## Baseline Summary (per axis, aggregated across all scored cells)

| Axis | Floor | Mean | Min | Max | Scored/Total | Pass rate |
|---|---|---|---|---|---|---|
| Hook stop-power (`hook_stop_power`) | 80 | 56.0 | 20.0 | 100.0 | 20/20 | 10% |
| Substance novelty (`substance_novelty`) | 70 | 100.0 | 100.0 | 100.0 | 20/20 | 100% |
| Visual truth (`visual_truth`) | 90 | 100.0 | 100.0 | 100.0 | 13/20 | 100% |
| Modality mix (`modality_mix`) | 70 | 22.7 | 0.0 | 50.0 | 15/20 | 0% |
| Motion density (`motion_density`) | 75 | — | — | — | 0/20 | — |
| Sync exactness (`sync_exactness`) | 85 | — | — | — | 0/20 | — |
| Audio feel (`audio_feel`) | 70 | — | — | — | 0/20 | — |
| Cost + safety (`cost_safety`) | 100 | 0.0 | 0.0 | 0.0 | 3/20 | 0% |

## Per (Genre x Format) Breakdown — mean score per axis

| Genre | Format | Hook stop-power | Substance novelty | Visual truth | Modality mix | Motion density | Sync exactness | Audio feel | Cost + safety | n |
|---|---|---|---|---|---|---|---|---|---|---|
| educational | short | 57.8 | 100.0 | 100.0 | 22.7 | — | — | — | 0.0 | 18 |
| general | short | 40.0 | 100.0 | 100.0 | — | — | — | — | 0.0 | 2 |

## Top Ranked Systematic Weaknesses

Ranked by **severity = (# cells below floor) x (mean point-gap below floor)** — widespread AND deep
problems rank first; a single one-off low score on an otherwise-fine axis will not appear here.

### 1. `modality_mix` — severity 709.5

- **Mean score:** 22.7 (floor 70)
- **Affected:** 15/15 scored cells (100%), mean gap below floor: 47.3 pts
- **Hypothesis:** Low Shannon entropy across {ai_photo, diagram, kinetic_text, scene} beat buckets — the reel leans on one modality (commonly all kinetic_text or all photo) instead of a balanced mix. Check the Visual Director's per-beat modality allocation.
- **Suggested fix area:** visuals: Visual Director modality allocation
- **Affected job_ids:** c0f4d6aa-53e1-45ff-9d59-15c38af44516, a5db9658-4524-4746-a91e-81b376e5c3c0, b1f7473f-eaf0-4393-8da9-1f1c7a196dea, c87ea6d4-42c7-4fd2-8446-a17777093ea5, 86c07ec6-3e94-4fa2-b9ad-f7f0fcda323a, b4d54a97-3950-4885-ae6f-a6c27f1dd270, 27b31356-12c2-4cb2-a9a2-7fa245450460, a815ef45-4b20-4d73-b96a-7b0211d49e21 (+7 more)

### 2. `hook_stop_power` — severity 520.2

- **Mean score:** 56.0 (floor 80)
- **Affected:** 18/20 scored cells (90%), mean gap below floor: 28.9 pts
- **Hypothesis:** First beat isn't photo/high-contrast, the hook line is too long (>12 words) or opens with a banned phrase, or the first spoken word lands after 2.5s — check beat0 modality selection and the hook-line generator against narration_rules.yaml's banned-opener list.
- **Suggested fix area:** visuals: beat0 modality selection + script: hook-line generation
- **Affected job_ids:** 22919cc2-9313-4d9b-bce6-74dba9afa76e, b69436dd-954f-42de-86c3-7770ec1e2f1a, 7a588518-184d-49dc-9cc4-a15d496df77c, 2e68cdf9-ebdc-4277-a72c-46d0adfe9bf1, f1d10a50-a4c6-4c48-8be9-717b1e2343c4, c0f4d6aa-53e1-45ff-9d59-15c38af44516, a5db9658-4524-4746-a91e-81b376e5c3c0, b1f7473f-eaf0-4393-8da9-1f1c7a196dea (+10 more)

### 3. `cost_safety` — severity 300.0

- **Mean score:** 0.0 (floor 100)
- **Affected:** 3/3 scored cells (100%), mean gap below floor: 100.0 pts
- **Hypothesis:** A hard gate: either the per-job cost rollup exceeded the per-short cap, or a safety/moderation verdict is failing/missing. ANY low score here is a priority fix — it means a cost overrun or an unmoderated render is reaching ship.
- **Suggested fix area:** cost telemetry + safety/moderation pipeline
- **Affected job_ids:** 2e68cdf9-ebdc-4277-a72c-46d0adfe9bf1, f1d10a50-a4c6-4c48-8be9-717b1e2343c4, 27b31356-12c2-4cb2-a9a2-7fa245450460


## Non-Authoritative Proxy Axes (score is a $0 heuristic, not a validated failure)

| Axis | Mean score | Proxy cells | Proxy fraction |
|---|---|---|---|
| `substance_novelty` | 100.0 | 20/20 | 100% |

A **proxy** score is a non-authoritative $0 heuristic standing in for a disabled paid judge/VLM (e.g. substance_novelty's research-grounding proxy when `--enable-judge` is off) or a producer-side provenance contradiction the axis detected in its own inputs (e.g. motion_density when the renderer's own telemetry disagrees with the render_mode signal). These axes are deliberately EXCLUDED from Top Ranked Systematic Weaknesses above — a low mean here may be a REAL pipeline gap or may just reflect the disabled judge; escalate the relevant flag (`--enable-judge` / `--vlm`) before treating it as a validated class-level failure. `rubric.evaluate()` already refuses to certify SHIP on any proxy axis for the same reason.

## Instrumentation Gaps (axis is UNMEASURABLE, not low — a different failure class)

| Axis | Measured | Coverage |
|---|---|---|
| `motion_density` | 0/20 | 0% |
| `sync_exactness` | 0/20 | 0% |
| `audio_feel` | 0/20 | 0% |
| `cost_safety` | 3/20 | 15% |

## Unscored Jobs (fetch/score failures — fail-open, never crashed the run)

_none — every job in this run produced a scorecard._

## PROPOSED MATRIX FILL (needs founder ack)

Target grid: 8 genres x 2 formats = 16 cells. Covered by this run's cell set: **1/16**.

T3 = a 10s quality_tier=low cell per gap (~$0.025/ea, no founder ack needed) via scripts/create_verification_job.sh. T4 = one representative 30-60s medium/high-tier cell per gap for premium-only behaviors (founder-ack gated, touch .claude/FOUNDER_SPEND_ACK). This function only COMPUTES the estimate — it never creates a job.

**Missing cells (no scored job in the current set):**

| Genre | Format |
|---|---|
| explainer | short |
| explainer | episode |
| storytelling | short |
| storytelling | episode |
| educational | episode |
| horror | short |
| horror | episode |
| interview | short |
| interview | episode |
| documentary | short |
| documentary | episode |
| comedy | short |
| comedy | episode |
| romance | short |
| romance | episode |

**Estimated cost to fill all 15 gaps at T3 (10s, quality_tier=low, no ack needed):** ~$0.375. **T4 representative-cell validation** (premium-tier/longer-form behaviors only, founder-ack gated): ~$0.15–$1.30 per gap.

**This section is a proposal only — no job has been created.** Filling these gaps is the founder's ack'd next step (`touch .claude/FOUNDER_SPEND_ACK` then `kitesforu-qa/scripts/create_verification_job.sh`).
