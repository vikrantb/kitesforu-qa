# QUALITY_BACKLOG — Measured Quality Engine

## ⚠️ SCORER VALIDATION (2026-07-08) — READ BEFORE ACTING ON THE RANKING BELOW

Before fixing any pipeline issue this backlog surfaced, the engine itself was validated against
the real artifacts it scored (per-job Firestore docs + Cloud Logging on `kitesforu-dev`) so the
ranking below is trustworthy. Full findings:

| Axis | Verdict | Evidence |
|---|---|---|
| `substance_novelty` (mean 1.2/70, 100%) | **SCORER-RANKING ARTIFACT** (the underlying pipeline finding is real, see below) | The axis is a self-declared `proxy=True` $0 heuristic (research-grounding + angle-brief richness) whenever `--enable-judge` is off — `rubric.evaluate()` already refuses to certify SHIP on a proxy score, but the aggregate ranking (`rank_systematic_weaknesses`) was treating it identically to a fully-measured axis, so a non-authoritative heuristic ranked #1 severity above real measured failures. **FIXED**: proxy-dominant axes (>= 50% of scored cells `proxy=True`) are now excluded from the ranking and reported in a new "Non-Authoritative Proxy Axes" section instead. |
| `substance_novelty` (the underlying pipeline signal) | **REAL, and validated to a root cause** | Direct Firestore inspection of 3 "recent" jobs (`f2052d47…`, `e8f7d2ab…`, `caf39620…`) shows the research-route resolver correctly resolves `research_mode="llm"` (`scenario_tailor/route.py`), but the ACTUAL stage result persisted is `NoneStrategy`'s signature (`research_skipped: true, reason: "evergreen_topic"`) — i.e. the LLM knowledge-brief mechanism is silently degrading to skip. Cloud Logging for job `f2052d47…` confirms: `generate_knowledge_brief()`'s LLM call completes (`gpt-4o-mini`, provider-agnostic failover working correctly, Google→OpenAI) but the worker logs its own honest self-report `"LLM knowledge brief empty — falling back to skip (empty research)"` immediately after. `generate_knowledge_brief` (`kitesforu-workers/src/workers/stages/research/strategies/llm_brief.py:248-270`) swallows ALL exceptions silently (`except: return ""`) with no error log — so this failure mode is currently undiagnosable from logs alone. This is real and worth a future pipeline fix, but is OUT OF SCOPE for this measurement-tool PR (no pipeline changes here). |
| `cost_safety` (mean 0.0/100, 100%→still 100% after fix, but 20→17 scored) | **CONFIRMED MISCALIBRATED — FIXED** | `per_short_cost_cap_usd=0.10` was applied as ONE flat cap to every `quality_tier`. But the tier system itself targets low≈$0.025, medium≈$0.15, high≈$1.0–1.3, ultra="flagship headroom" (`kitesforu-docs` cost-reference.md) — so every medium/high/ultra job was **guaranteed to fail by construction**, not because it overspent. Confirmed live: job `d1581a4b…` (ultra, $1.05) and `669ee6b6…` (ultra, $1.55) were being counted as cost overruns pre-fix. **FIXED**: `cost_cap_usd_by_tier` (low $0.10 / medium $0.30 / high $2.00 / ultra $5.00) in `ScorecardConfig`. After the fix, those 2 jobs correctly drop out of `cost_safety`'s failing set (now `missing_instrumentation` — no persisted safety verdict — not a cost fail). |
| `cost_safety` (the 17 REMAINING failures, all `quality_tier=low`) | **REAL — genuine low-tier cost overruns, ranging $0.105–$1.13 against a $0.10 cap** (up to 11x over) | Confirmed by direct query: 12–17 of ~57 recent low-tier jobs exceed the low-tier cap even AFTER the miscalibration fix. This is a real, separate finding (worth its own investigation) but is not this PR's #1 target — see ranking below (severity 1700.0 vs modality_mix's 2775.6). |
| `motion_density` (mean 40.3/75, 91%) | **PARTIALLY A SCORER ARTIFACT — FIXED, AND a genuine finding underneath** | The axis already had a "provenance contradiction" check (`visual.rendered_motion_clips` vs render_mode-derived motion-beat count) but only ANNOTATED it as a text note — the numeric score still trusted the render_mode signal even when the pipeline's OWN telemetry disagreed with it. Direct doc inspection confirmed the contradiction fires on most sampled jobs (`rendered_motion_clips=0` while every beat is stamped `render_mode="video"`). **FIXED**: a contradiction now sets `proxy=True` (reusing the existing mechanism), so this axis is now correctly excluded from the ranking too — **96% of its scored cells turned out to be proxy** once measured. The genuine finding underneath (does `render_mode="video"` really mean motion, or is it a generic delivery-format label unrelated to the renderer's own motion counter?) is a real pipeline instrumentation question for whoever owns the render engine next — flagged, not fixed here (scope: scorer-only). |
| `modality_mix` (mean 20.8/70, 96%) | **REAL — confirmed via hand-computation against real docs and via the #24 evidence below** | Verified the Shannon-entropy math against 4 real jobs' actual `visual_clips` (bucket counts, entropy, normalization all reproduce the scorer's reported score exactly). This is the #1 TRUE, validated systematic failure — see NEXT FIX TARGET below. |
| `hook_stop_power` (mean 67.7/80, 55%) | **REAL — scorer sound, no changes needed** | Deterministic modality/word-count/banned-opener/timing checks; math confirmed correct by inspection. Genuinely varies job-to-job (20–100 range) consistent with a real per-job editorial signal, not a systematic scorer bug. |

**Bottom line:** substance_novelty and motion_density were being counted as validated "systematic
weaknesses" when they are (respectively, and now confirmed) a disabled-judge proxy and a
majority-provenance-contradiction proxy. Both are now correctly excluded from the ranking. Their
underlying pipeline signals are real and separately worth investigating, but neither should have
outranked modality_mix, hook_stop_power, or cost_safety as "the" systematic failure to fix next.

---

Generated: 2026-07-09T05:51:41.118356+00:00 · Project: `kitesforu-dev` · Mode: `query-recent-7d`
Cells: 80/80 scored (the rest failed to score — see Unscored Jobs below; fail-open, not
a crash). **This is the TRUSTWORTHY re-baseline** (same $0 query, same 80 recent completed jobs,
post scorer-fix) — see the before/after comparison below the ranking.

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
| Motion density (`motion_density`) | 75 | 39.9 | 7.2 | 100.0 | 55/80 | 9% |
| Sync exactness (`sync_exactness`) | 85 | — | — | — | 0/80 | — |
| Audio feel (`audio_feel`) | 70 | 69.9 | 40.5 | 93.0 | 55/80 | 87% |
| Cost + safety (`cost_safety`) | 100 | 0.0 | 0.0 | 0.0 | 17/80 | 0% |

## Per (Genre x Format) Breakdown — mean score per axis

| Genre | Format | Hook stop-power | Substance novelty | Visual truth | Modality mix | Motion density | Sync exactness | Audio feel | Cost + safety | n |
|---|---|---|---|---|---|---|---|---|---|---|
| educational | episode | 60.0 | 50.0 | 100.0 | 0.0 | — | — | — | — | 1 |
| educational | short | 68.6 | 0.0 | 100.0 | 22.0 | 38.5 | — | 68.6 | 0.0 | 51 |
| general | short | 64.4 | 0.0 | 100.0 | 18.1 | 44.3 | — | 70.6 | 0.0 | 26 |
| news | short | 100.0 | 50.0 | 100.0 | 45.9 | 21.2 | — | 93.0 | — | 1 |
| romance | episode | 80.0 | 0.0 | — | 36.1 | 8.2 | — | 72.0 | — | 1 |

## BEFORE / AFTER — the scorer fix's effect on the ranking

| Rank | BEFORE (untrustworthy) | severity | AFTER (trustworthy) | severity |
|---|---|---|---|---|
| 1 | `substance_novelty` (100% proxy — excluded now) | 5504.0 | **`modality_mix`** | 2775.6 |
| 2 | `modality_mix` | 2775.6 | **`cost_safety`** (down from 20→17 cells; miscalibrated non-low-tier jobs removed) | 1700.0 |
| 3 | `cost_safety` (20 cells, incl. miscalibrated tiers) | 2000.0 | **`hook_stop_power`** | 1504.8 |
| 4 | `motion_density` (91% proxy — excluded now) | 1994.3 | **`audio_feel`** (newly visible now 2 axes were removed above it) | 206.5 |
| 5 | `hook_stop_power` | 1504.8 | _(motion_density and substance_novelty now in the Proxy section, not here)_ | — |

**modality_mix is the #1 TRUE systematic failure both before and after the fix** — its rank was
never in question, but two axes that used to outrank or crowd it (substance_novelty,
motion_density) are now honestly reported as non-authoritative rather than presented as equally-
valid "systematic weaknesses." cost_safety's severity dropped 2000.0 → 1700.0 (3 miscalibrated
cells removed) while still correctly flagging 17 genuine low-tier overruns.

## Top Ranked Systematic Weaknesses

Ranked by **severity = (# cells below floor) x (mean point-gap below floor)** — widespread AND deep
problems rank first; a single one-off low score on an otherwise-fine axis will not appear here.
Proxy-dominant axes (>= 50% of scored cells non-authoritative) are excluded — see the Proxy section.

### 1. `modality_mix` — severity 2775.6

- **Mean score:** 20.8 (floor 70)
- **Affected:** 54/56 scored cells (96%), mean gap below floor: 51.4 pts
- **Hypothesis:** Low Shannon entropy across {ai_photo, diagram, kinetic_text, scene} beat buckets — the reel leans on one modality (commonly all kinetic_text or all photo) instead of a balanced mix. Check the Visual Director's per-beat modality allocation.
- **Suggested fix area:** visuals: Visual Director modality allocation
- **Affected job_ids:** d1581a4b-bb2a-4fb7-9c2e-d5ce055a3060, eea068de-5f07-4850-8205-a94b94763d34, 103d05ff-8f43-447e-8249-2bdcff660b10, 669ee6b6-6e07-40bf-8599-feeaba9985a0, c8de3611-b66a-4965-bcf6-fd90851dfab5, 31d1e088-5c1e-4d63-a14c-582b26ea00ec, dd8224b4-90e3-4fa9-82df-2069f44a7283, 703bded1-c15b-43f9-8b56-86fcddb53de2 (+46 more)

### 2. `cost_safety` — severity 1700.0

- **Mean score:** 0.0 (floor 100)
- **Affected:** 17/17 scored cells (100%), mean gap below floor: 100.0 pts
- **Hypothesis:** Now tier-calibrated — every remaining failure is `quality_tier=low` genuinely exceeding its own (low-tier) $0.10 cap, by as much as 11x ($1.13 seen on job `fb20f137…`). Not a scorer artifact; a real low-tier cost-overrun class worth its own investigation (out of scope here).
- **Suggested fix area:** low-tier cost telemetry — trace which stage(s) push a low-tier short past $0.10
- **Affected job_ids:** eea068de-5f07-4850-8205-a94b94763d34, 7908d37d-4d3e-44b0-bf08-524a37f404a5, 4c3a1e5f-9b08-4b0e-b903-0932cae961db, b3443263-6b2c-43fb-a661-eed3333311da, 588e97d0-8c49-4099-8c1b-83902c19d0c3, 2553586d-518f-45fc-bf8f-b72c55572686, e740f63a-2296-4bc9-8bb0-5862f869220f, e87635f9-2854-4347-b218-c0e57e82b1fb (+9 more)

### 3. `hook_stop_power` — severity 1504.8

- **Mean score:** 67.7 (floor 80)
- **Affected:** 44/80 scored cells (55%), mean gap below floor: 34.2 pts
- **Hypothesis:** First beat isn't photo/high-contrast, the hook line is too long (>12 words) or opens with a banned phrase, or the first spoken word lands after 2.5s — check beat0 modality selection and the hook-line generator against narration_rules.yaml's banned-opener list.
- **Suggested fix area:** visuals: beat0 modality selection + script: hook-line generation
- **Affected job_ids:** f2052d47-8fec-47ab-9ed4-5704ec804c38, e8f7d2ab-7de2-4683-8912-470d73466657, caf39620-2613-42ae-a768-f0acee8473b6, 486291d1-31b9-4c46-a8d0-e5a8267eb6f7, f0d1a44f-568e-4d40-b64a-e3308f4010d9, b63f665d-41d2-4d68-b864-ead5a5e13ce4, b82bc8c9-8320-4957-83bb-7a6d0bd763f5, 2553586d-518f-45fc-bf8f-b72c55572686 (+36 more)

### 4. `audio_feel` — severity 206.5

- **Mean score:** 69.9 (floor 70)
- **Affected:** 7/55 scored cells (13%), mean gap below floor: 29.5 pts
- **Hypothesis:** The spectral judge is detecting drone/hum instead of real music, or a planned music cue wasn't detected by listening-QA — check music-bed selection and the TTS/mastering final seam for drone artifacts.
- **Suggested fix area:** audio: music-bed selection + mastering final seam
- **Affected job_ids:** eea068de-5f07-4850-8205-a94b94763d34, 0652380a-7c0c-4740-951f-3522de4c264f, fb20f137-a67a-4e2c-93bc-d4b2b6e2184a, 746e0d70-18b8-461f-a061-ade4c4f8b917, fc3b9338-26ce-485e-b075-bb8713a6b54f, 3ebf39b5-523b-4fb4-9232-a04e615f9f27, c6c1d36b-1856-4528-8381-e65c1ff5b380


## Non-Authoritative Proxy Axes (score is a $0 heuristic, not a validated failure)

| Axis | Mean score | Proxy cells | Proxy fraction |
|---|---|---|---|
| `substance_novelty` | 1.2 | 80/80 | 100% |
| `motion_density` | 39.9 | 53/55 | 96% |

A **proxy** score is a non-authoritative $0 heuristic standing in for a disabled paid judge/VLM (e.g. substance_novelty's research-grounding proxy when `--enable-judge` is off) or a producer-side provenance contradiction the axis detected in its own inputs (e.g. motion_density when the renderer's own telemetry disagrees with the render_mode signal). These axes are deliberately EXCLUDED from Top Ranked Systematic Weaknesses above — a low mean here may be a REAL pipeline gap or may just reflect the disabled judge; escalate the relevant flag (`--enable-judge` / `--vlm`) before treating it as a validated class-level failure. `rubric.evaluate()` already refuses to certify SHIP on any proxy axis for the same reason.

## Instrumentation Gaps (axis is UNMEASURABLE, not low — a different failure class)

| Axis | Measured | Coverage |
|---|---|---|
| `sync_exactness` | 0/80 | 0% |
| `cost_safety` | 17/80 | 21% |

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

---

## 🎯 NEXT FIX TARGET (STEP 3) — `modality_mix`: Visual Director modality allocation

**The #1 TRUE, validated systematic pipeline failure.** Mean 20.8/70 floor, 96% of scored cells
below floor, severity 2775.6 (highest of any non-proxy axis, before AND after the scorer fix).

### Root cause hypothesis (grounded in real evidence, not speculation)

The Shannon-entropy math was hand-verified against 4 real job docs' actual `visual_clips` arrays
(bucket counts reproduce the scorer's reported score exactly in every case) — this is a REAL
finding, not a scorer bug. The pipeline mostly toggles between just 2 of the 4 canonical modality
buckets (`diagram` and `scene`) — `ai_photo` and `kinetic_text` are frequently absent entirely —
which caps the achievable entropy well below the 70-floor-worthy balanced mix the rubric wants.

**Is this already being fixed?** PARTIALLY — and the evidence shows it is NOT sufficient.
`kitesforu-workers` shipped `ensure_scene_richness` in two commits on 2026-07-08
(`4978a49d` 03:41 UTC, `6af5c9c2`/#1437 05:34 UTC) — `stages/visuals/short_visual_director.py:539-610`.
This function is a **narrower fix than modality_mix needs**:

- It only fires when the FINAL modality mix is "entirely diagram-family (diagram/chart/card) and
  NEVER a scene/photo" (`short_visual_director.py:571`: `if SCENE in modalities: return
  {"injected": False, "reason": "already_present"}`) — a **zero-photoreal-beats floor guard**, not
  a balanced-mix guarantee.
- It converts **exactly ONE beat** to `scene_image` (`short_visual_director.py:539` docstring:
  "force ONE beat"; `target = candidates[0]`) — bounded to raise the floor, never a real
  redistribution across all 4 buckets. `policy.py:283-308` (`resolve_paid_generation`) confirms
  the same: "Bounded to ONE beat (`max(max_scenes, 1)`) — this raises the floor for the single
  `ensure_scene_richness` conversion; it never grants a full opt-in visual budget."

**Direct evidence this is insufficient:** job `d1581a4b-bb2a-4fb7-9c2e-d5ce055a3060` was created
2026-07-09T05:03 UTC — **~16 hours AFTER both #24 commits landed** — and still scores
`modality_mix = 45.9` (bucket counts `{scene: 2, diagram: 1}`, only 2 of the 4 canonical buckets
populated, no `ai_photo`/`kinetic_text` at all) — well below the 70 floor. Even a maximally-
generous version of the ONE-beat floor guarantee (converting a diagram-only short to
diagram+1-scene) caps the achievable entropy at roughly 25-50/100 for a typical 3-9-beat short —
mathematically incapable of reaching 70 on its own.

### What an actual fix would need (not built here — scope of this PR is scorer-only)

`ensure_scene_richness` solves "never zero photoreal beats" (the scroll-stop lever); modality_mix
needs a DIFFERENT, complementary guarantee: a genuine allocation pass across all 4 buckets
(`ai_photo`, `diagram`, `kinetic_text`, `scene`) proportional to beat count — e.g. a target
distribution (not just a floor) the Visual Director allocates toward BEFORE the render-time
`weave`/`ensure_scene_richness` demotion/floor passes run, so `kinetic_text` (currently near-absent
in the sample) gets a real allocation slot too. This is the next fix-target for whoever owns
`stages/visuals/short_visual_director.py` / the Visual Director's per-beat modality allocation —
flagged here with root-cause evidence, not implemented in this PR (out of scope: this PR is the
measurement-tool fix only, no pipeline changes).
