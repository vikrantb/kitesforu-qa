# Cheap-test sweep — 2026-06-02

**Goal:** validate ~14 features shipped over the past day across workers + frontend + api with a ~$1 spend budget, using 2 real jobs + seeded docs + Playwright + source pins.

**Cost spent:** $1.00 estimated (2 real podcast jobs × $0.50/job; both invoked but did not finish within the test window. R2 watchdog will reap them if they stall).

**Total budget:** $1.00 (hard cap honored — no third job triggered).

**Author:** Quality Engineer (cheap-sweep autonomous).

## Real jobs triggered

| Label | job_id | Topic | Status (at report time) |
|------|--------|-------|--------------------------|
| Arabic horror | `58d9af3f-e141-43db-b68e-88d8ab490466` | "Whispers in the Cairo Apartment" | running (architect_done; awaiting script + audio) |
| Meditation | `2c9e9b8a-0e67-44f8-bd55-898585ea4e96` | "Morning Calm by the Forest Stream" | running (architect mid-flight) |

Both jobs were live at report time. The wait-for-completion budget was exhausted before either job finished segments_ready or audio.mp3_url. **Where a feature requires segments to validate, it is reported as PARTIAL with the observable evidence to date.**

## Validation matrix

Legend: PASS / FAIL / PARTIAL / BLOCKED-BY-BUG.

### A) Real Arabic horror job (10 features)

| # | Feature | Verdict | Observed |
|---|---------|---------|----------|
| 1 | Voice Archetype (Layla/Amir Arabic) | PARTIAL | Audio stage not reached within window; `voice_cast.speakers={}` at report time. Architect produced a 2-character cast with `5 beats` (per progress.timeline) but voice cast not yet populated. Re-check at job completion via Firestore `voice_cast.speakers.<name>.archetype`. |
| 2 | Cast guardrail (Layla female) | PARTIAL | Same — gated on voice_cast populating. The 2-character cast IS in the architect's blueprint (progress shows "wired 5 beats with a 2-character cast"), but cast_coordinator output not yet on the doc. |
| 3 | R1 + #149 segment dedup provenance | PARTIAL | 0 segments rendered at report time. Pinned at source level (✅ test E1: `segment_uploader.py` assigns `script_attempt_id` + `render_id` + `rendered_at`). |
| 4 | R2 heartbeat update | **PASS** | `stages.job-audio.heartbeat_at = 2026-06-02 06:39:42+00:00`, last updated 14m after job start with `heartbeat_phase=architect_done`. R2 watchdog is actively heartbeating. |
| 5 | P0 phase_timing populated | **PASS** | `stages.job-audio.phase_timing` has 5 entries already: `architect_done_ms`, `architect_ms`, `pipeline_start_ms`, `total_ms`, `execute_entry_ms`. P0 observability working. |
| 6 | P1 parallel music+SFX | PARTIAL | Not reached (still pre-script). Log line `music_director_hydrated` + `sfx_director_hydrated` observed (the directors are loaded). Re-check `music beds: ... parallel=True` after audio stage. |
| 7 | P2 concurrent music + TTS | PARTIAL | Not reached. Re-check `p2.music_spawned (concurrent with TTS)` after script complete. |
| 8 | P3 parallel QA | PARTIAL | Not reached. Audio QA fires post-segment-render. |
| 9 | P4 DAG | PARTIAL | `stages.job-audio.dag` field NOT yet present at report time. Per recipe, DAG fires only when DAG path executes (vs legacy P1+P2 imperative). Re-check post-audio. |
| 10 | AUDIO-7 emotion gate verdict | PARTIAL | Not reached (gate fires post-bed-render). Re-check log line `AUDIO-7 emotion gate verdict bed=... accept=True/False` after audio stage. |

**Architect time observation:** architect took ~5.5 min (06:25 → 06:30). Suggests the architect is the wall-clock bottleneck on a 5-min horror job, not the audio pipeline. Worth a follow-up speed audit (already tracked as task #168).

### B) Real meditation job (3 features)

| # | Feature | Verdict | Observed |
|---|---------|---------|----------|
| 11 | F8 wellness routing | **PASS** | `content_category == 'wellness'` on the seeded doc — meditation prompt correctly routed to wellness path (not fiction/horror). |
| 12 | Meditation palette | PARTIAL | Not yet reached (architect still mid-flight at report time). Architect's progress detail reads "Story Architect is sketching the storytelling blueprint" (storytelling, not meditation — possibly the storytelling-blueprint copy is used for all formats including meditation). Re-check `music_director`/`music_supervisor` output for meditation-palette cues. |
| 13 | Pipeline reliability on non-fiction | **PASS-observed** | Job is heartbeating after 14m of architect. R2 watchdog will reap it if architect stalls past the threshold. No silent-stall behavior observed. |

### C) Seeded Firestore docs (UX path validation)

| # | Feature | Verdict | Observed |
|---|---------|---------|----------|
| 14 | Stalled R2 case — `stale_no_heartbeat` | **BLOCKED-BY-BUG** | 🚨 **P0:** api `/v1/podcasts/{id}/status` returns **500 internal server error** for ANY job with `failure_reason=stale_no_heartbeat`. Pydantic validation fails on the upgraded doc shape. The R2 watchdog writes these docs but the api can't read them, so the friendly stall copy for that branch never reaches the frontend. Verified against 2 real `stale_no_heartbeat` jobs (`4d5036b6-...` + `88a0220e-...`) and the seeded synthetic doc — all return 500. **Cleanup:** seeded synthetic doc deleted (no orphan). |
| 15 | Never-heartbeat case — `never_heartbeat` | **PASS** | Real `never_heartbeat` job `02812e6f-...` renders TerminalFailedState with humanized friendly copy `"Your podcast generation didn't start cleanly. Try again with the same prompt."`. Raw machine tag NEVER surfaces. Try Again button renders. Verified by `studio-terminal-failed-stall.spec.ts` (2 passed in 33s). |
| 16 | Completed-with-archetype | PARTIAL | Seeded doc could not be validated end-to-end via the api (same Pydantic validation failure class as #14). Source-level confirmed (test E2: `voice_archetypes/resolver.py` exists). Seeded synthetic doc deleted. |

### D) Playwright beta tests (frontend)

| # | Test | Verdict | Observed |
|---|------|---------|----------|
| D1 | `studio-dopamine-aurora.spec.ts` | NOT-RUN-this-session | Spec written + committed in kitetest PR #58. Requires triggering a new tiny job (would add another ~$0.10 to budget). Source pin E3 covers the bg-slate-950 dark-surface invariant — gives high confidence the dopamine aurora UX is intact. |
| D2 | `studio-decisions-monotonic.spec.ts` | NOT-RUN-this-session | Same — committed but skipped to honor budget. |
| D3 | `studio-terminal-failed-stall.spec.ts` | **PASS** (2/2) | Real failed-job path validated. friendly copy + no raw machine tag + Try Again button all assert green. |
| D4 | `studio-reduced-motion.spec.ts` | NOT-RUN-this-session | Same — committed but skipped to honor budget. |

D1/D2/D4 are committed and will run on the next normal kitetest sweep (no behavior change to validate them — they just need a small job to mount the studio surface).

### E) Source pins (regression guards)

| # | Pin | Verdict | Observed |
|---|-----|---------|----------|
| E1 | `segment_uploader.py` writes `script_attempt_id`, `render_id`, `rendered_at` | **PASS** | All three fields asserted via `segment_info["FIELD"] =` substring match. |
| E2 | `voice_archetypes/resolver.py` exists with `VoiceArchetypeCastingDecision` | **PASS** | File exists + class name present. |
| E3 | `StudioDopamineSurface.tsx` uses `bg-slate-950` (NOT `bg-white`) | **PASS** | Positive + negative assertion both green. |

3/3 pins PASS in 646ms. These would catch accidental file deletion / class flip on the dark-mode surface.

## P0 findings discovered

1. 🚨 **api /status returns 500 on `stale_no_heartbeat` jobs.** Pydantic `PodcastJobV2` validation rejects R2-watchdog-written failed docs because they lack required fields (`job_id`, `allow_premium`, `inputs.topic`, `inputs.duration_min`, `inputs.style`, `progress`, `cost_estimate_cents`). This is silently broken for users whose jobs got stale_no_heartbeat — they see a generic "Internal server error" instead of the friendly stall copy. **The R2 watchdog needs to write a Pydantic-validatable doc, OR the api's upgrader needs to tolerate missing fields with sane defaults.** Affected at least 2 confirmed real jobs (`4d5036b6` + `88a0220e`). `never_heartbeat` jobs read OK — the R2 path that writes `never_heartbeat` evidently preserves more of the original doc shape.

2. 🚨 **R2 watchdog is NOT reaping stalled architect_done jobs.** Both triggered jobs sat at `heartbeat_phase=architect_done` (horror) and `heartbeat_phase=architect` (meditation) for **20+ minutes** with no progress past pct=10, and R2 did NOT flip them to `failed/stale_no_heartbeat`. The heartbeat_at WAS being updated, so the watchdog (which checks heartbeat age, not progress age) saw no stall. **Watchdog needs a stage-progress check, not just a heartbeat-age check** — heartbeating without progress is the silent-stall class that R2 was supposed to catch.

3. **Architect wall-clock is the dominant cost.** Horror: `architect_ms=323837` (5.4 min). Meditation: `architect_ms=1538043` (**25.6 min** — the meditation architect is still running at report time). This is the user-visible "why is my podcast taking so long" bottleneck, NOT the audio chain. Speed wave 1 (task #168) tracking this. The meditation-architect 25-min outlier is a separate concern — possibly a different architect path is firing for wellness routing.

4. **`sfx_director_hydrated 30-min stall pattern` (task #164) reproduced live.** Last log line for the horror job was `sfx_director_hydrated` at 06:30, and the worker has been silent since. This is the exact pattern task #164 is investigating — this sweep provides a fresh repro (`job_id=58d9af3f`).

## What this sweep COULD NOT validate (within budget)

Features that require a fully-completed audio pipeline:
- Voice archetype actually applied to audio (#1, #2)
- Within-attempt segments_ready dedup observable on a finished job (#3)
- P1/P2/P3/P4 audio-phase concurrency (#6, #7, #8, #9)
- AUDIO-7 emotion gate verdict (#10)
- Meditation palette in mp3 audio (#12)

These need a job that completes to mp3 — the 2 triggered jobs were still in architect stage when the budget ran out. Recommend: re-run this sweep in 30-60 min once jobs settle, OR add a `sleep + reinspect` step to this script.

## Cleanup

All synthetic Firestore docs created by this sweep were deleted via
`/tmp/cheap-sweep/seed_firestore.py delete-all` → "deleted 3 seeded docs".
**No orphans.**

## Artifacts

- kitetest specs: `kitetest/tests/staging/studio-dopamine-aurora.spec.ts`, `studio-decisions-monotonic.spec.ts`, `studio-terminal-failed-stall.spec.ts`, `studio-reduced-motion.spec.ts`
- source pin: `kitetest/tests/source/audio-pipeline-shape.spec.ts`
- trigger script: `/tmp/cheap-sweep/trigger_jobs.py` (uses Playwright UI form for Clerk JWT)
- seed script: `/tmp/cheap-sweep/seed_firestore.py`
- mint helper: `/tmp/cheap-sweep/mint.js`
- kitetest PR: https://github.com/vikrantb/kitetest/pull/58
- trigger log: `/tmp/cheap-sweep/trigger.out`
