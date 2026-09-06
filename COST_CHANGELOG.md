# kitesforu-qa Cost Changelog

Per Tenet 7 (cost transparency): every change affecting per-unit cost is documented here.

## 2026-09-06 — the T4 estimate print learns the story-topic price; #173's owed record lands ($0 per-unit; operator-facing EST only)

**Files:** `scripts/create_verification_job.sh` (EST case arm + comments), `COST_CHANGELOG.md`
(this entry).

**$0 per-unit delta** — nothing here changes what a job costs; it changes what the OPERATOR is
told before spending, and the spend ledger consumes the printed string verbatim. Two things:

1. **#173's owed record.** The merged #173 is docs-only after its round: the payload is
   byte-identical (style stays contract-required; the omission draft 422'd and was reverted).
   The record at the site of the default: a manufactured `style=Explainer` voided a $1.56 T4
   (`e9466de1` bought a segment_plan of nothing); schemas 2.81.0's `UMBRELLA_ALIAS_KEYS` defuses
   the genre discard; pass `--style` matching the topic on routing T4s. This entry exists because
   the #173 cost lens found it missing — the round is this repo's only cost gate.
2. **The high-tier EST reprice.** Old print `~$1.0-1.3` understated exactly the runs this script
   exists to price. New print: `~$1.0-1.3 (non-story topic) / ~$1.55-2.25 (story topic)`.
   Derivation: base $1.0-1.3 (cost-reference; unverified against a measured informational high
   T4 — `e9466de1` presented informational and still logged $1.56, confounded by its story
   routing) + fiction architect tournament +$0.10-0.35 (measured $0.17/candidate; the BoN=4
   budget row is ultra-scoped, so the top of the range may not apply at high) + hero clips:
   1 × ≤$0.20 on the entitlement budget, up to 1-3 × ~$0.20 on the upfront-charged
   `motion_clips` path. **The floor is anchored to measurement, not arithmetic**: both
   story-topic T4s of 2026-09-06 logged $1.56 base / $1.76 with a hero clip (`e9466de1`,
   `df3de5bb`) — a $1.4 floor would under-book the ledger ~$0.16/job. Ceiling $2.25 is the
   arithmetic top.

**Pricing-page implication: none.** Operator-facing estimate in a QA script; user prices
unchanged.

## 2026-09-01 — A story persona, a runnable hero critic, and a gate that reads the whole video (cost-NEUTRAL by default, +$0.0002 on an opt-in flag)

**Files:** `scripts/story_judge.py` (`load_persona`, `--persona`), `scripts/acceptance_gate.py`
(`_sample_indices`, `_adversary_brief`, `--persona`), `hero_users/personas/nadia-story-listener.yaml`
(new), `tests/test_a_hero_persona_can_actually_judge.py`,
`tests/test_the_acceptance_gate_spans_the_whole_video.py`. PRs #171 + #172 (stacked).

**Per-unit $ delta: $0.00 on every existing caller; +$0.00006 to +$0.00019 per judged job when
`--persona` is passed.**

Written because this repo documents cost-neutral changes too — both preceding entries are
`(cost-NEUTRAL, $0)` — so a near-zero delta is the case these entries exist for, not an exemption.
The #172 cost lens found this entry missing; it was right.

**The default path is byte-identical.** Verified by comparing `build_prompt(...)` output across all
6 (family x promise) combinations between the base and head modules — all equal, matching SHA-256
prefixes — not by reading the comment that claims it.

**No new provider, model or call.** `story_judge.judge_job` makes the same **two** Gemini Flash
calls it always made (`classify` + `call_judge`); `--persona` changes only the prompt handed to the
second. `acceptance_gate.py` makes **zero** metered calls before and after — its externals are
ffprobe, ffmpeg, numpy/PIL and one Firestore read. `DEFAULT_JUDGE_MODEL` remains
`gemini-2.5-flash`, temperature 0.

**`--persona` prompt growth**, measured with `len(load_persona(name))` over every YAML in
`hero_users/personas/` against the 1179-char default `CRITIC_PERSONA`:

    maya-student   +206 tok    elena-ld  +211    marcus-technical +254
    aarav-audio    +328 tok    priya     +342    sofia-creator    +409
    nadia-story-listener +620 tok   (new in #171; the largest, +18% of one Flash prompt)

At Gemini 2.5 Flash input list price that is ~+$0.0002 per judged job for nadia — under two cents
per hundred jobs, on an opt-in flag. A hero-user review that is actually routed is worth more than
that.

**The frame-sampling change is $0 AND frame-count-neutral.** `_sample_indices` feeds
`_pixel_invariants`, which is local numpy/PIL — not a vision model. Even so the count is unchanged:
`len(head(n)) == len(base(n))` for all n in 1..240 (2814 frames either way). Head buys 100%
coverage at identical cost. The intermediate ceiling-stride commit was the only arm that moved the
number, and it moved it the wrong way — 90 fewer frames read across the range, i.e. "saving" money
by inspecting less, which is a gate weakening. Abandoned before merge. `_extract_frames` and its
ffmpeg invocation are untouched.

**Pricing-page implication:** none. This is internal QA tooling.

**Worth knowing for whoever optimises next:** `call_judge` sets `maxOutputTokens: 8000`; a response
that fills it costs ~$0.020 at output rates, roughly 100x the persona delta added here. Output is
the lever, not the brief. Measure real completion lengths before touching the input side.

## 2026-08-06 — Narration-sync instrument: does the picture follow the words? (cost-NEUTRAL, $0)

**Files:** `src/kitesforu_qa/harness/narration_alignment.py` (new),
`src/kitesforu_qa/harness/checks/narration_sync.py` (new), `scripts/narration_sync_audit.py`
(new), `tests/test_narration_alignment.py` (new), `harness/checks/__init__.py` (registration).

**Context:** the founder reported "whats shown on visuals and the audio dont match" on witness
`f6709ffc-1be9-4fb4-923e-1fd0bf0dbeb8`. Both existing gates PASS that job —
`video_sync.clips_beat_aligned` scores 61/61 = 1.00 and the pipeline-stamped
`visual.av_content_sync` reports 0 offenders with `median_offset_ms == max_offset_ms == 120` —
because both only ask whether a clip starts inside its own beat window, which placement
guarantees by construction. Four new axes measure against the caption cue track instead and all
four FAIL on that witness.

**Per-unit $ delta: $0.** Deterministic timing math over job docs the pipeline already writes
(T1 on the test-cost ladder). No LLM call, no VLM call, no provider call, no generation, no new
job created — `scripts/narration_sync_audit.py` is read-only and reuses existing artifacts, per
the reuse-before-generate rule. Firestore reads only, on the same docs other QA scripts already
read. No pricing-page implication.

**Cost story it improves:** the fleet-level defect (2.0 sentences per picture, 28% of cuts on a
speech boundary, 7423ms shown-vs-spoken lag, 22/38 jobs with a zero-duration clip) was
previously invisible, so paid image generations were being spent on visuals that land against
the wrong words — and 22/38 jobs paid to author at least one clip that never reached the screen.

## 2026-07-25 — Fleet Drift Sentinel severity/denominator hardening (cost-NEUTRAL, $0)

**Files:** `scripts/fleet_drift_sentinel.py`, `tests/test_fleet_drift_sentinel.py`, README.

**Context:** closes the three review blockers on the sentinel: (1) directionality-aware
severity (bad-signal collapse = improvement → INFO, never exit 1; bad-signal + cost spikes →
CRITICAL) plus an expected-changes ack file (`scratch/reports/drift/ack.json`); (2)
applicability denominators (motion/video_url over clip-bearing jobs only — kills the
QA-campaign false-positive class); (3) cost-spike gating (mean/p95 >= 2.5x → CRITICAL exit 1)
+ max-vs-prior-p95 single-job-burn outlier channel, dilution limit documented honestly.

**Per-unit $ delta: $0.** Pure detection-logic change over the same projected reads; no new
LLM/API/generation call. The IMPROVED cost story: fleet-wide cost burns now gate the deploy
round instead of rotting in an unread report. No pricing-page implication.

## 2026-07-25 — Fleet Drift Sentinel (cost-NEUTRAL, $0)

**Files:** `scripts/fleet_drift_sentinel.py` (new), `tests/test_fleet_drift_sentinel.py` (new),
README section.

**Context:** standing dark-feature detector born from the 2026-07 motion incident (zero jobs
surfaced parallax/kenburns for ~2 days and nothing alerted; the visual gate hard-failing ~100%
of shorts was itself an unread alarm). Trailing-vs-prior-window prevalence battery over
`podcast_jobs` + `writeups` with collapse/spike/gate-meta detection, transition-date bisection,
and deploy-revision correlation.

**Per-unit $ delta: $0.** No LLM/judge/generation call anywhere — Firestore field PROJECTIONS
(`.select`, never full docs) over data the pipeline already wrote, plus optional
`gcloud run revisions list` (free API reads). Read-only by construction (Tenet 9); exit code 1
on CRITICAL findings lets it gate a deploy round at zero verification spend. No pricing-page
implication.

## 2026-07-09 — Measured Quality Engine: EPISODES + COURSES extension (cost-NEUTRAL)

**Files:** `src/kitesforu_qa/harness/quality_matrix.py`, `scripts/quality_matrix.py` (new
`--content-class episodes` mode + `resolve_audio`), `tests/test_quality_matrix_episodes.py` (new),
`tests/test_quality_matrix_episodes_cli.py` (new)

**Context:** the Measured Quality Engine (PR #55/#56) only ever scored 9:16 SHORTS via the fixed
8-axis rubric — EPISODES (16:9/audio podcasts) and COURSE modules (corporate training; a course
episode IS a `podcast_jobs` doc stamped `parent_type=='course'`) were a total measurement blind
spot. Extended (not forked): a new `detect_content_class`/`score_episode_or_course` path reuses the
harness's existing GENERAL check battery (`battery.run_scorecard`, 138 checks across structure/
content/audio-mix/cost-correctness/visual-images/video-sync/music-sfx) instead of inventing a
parallel rubric, plus a check-level aggregator/ranker (`aggregate_all_checks`,
`rank_systematic_check_failures`, `aggregate_all_dimensions`) analogous to the short engine's
axis-level one. The short 8-axis path (`score_all`/`rank_systematic_weaknesses`/
`render_backlog_markdown`) is untouched — verified byte-identical behavior via the existing test
suite (511/511 pre-existing tests still pass) plus a new pin
(`test_main_default_content_class_is_short_unchanged`).

**Per-unit $ delta: $0.** No new LLM/VLM/judge call — every check in the general battery is
deterministic Python over the job doc (+ locally-resolved audio/video via `gsutil`, same technique
`resolve_video` already used). The $0 baseline run (`--content-class episodes --query-recent-days
14`, no new jobs) scores EXISTING completed episode/course jobs only. No pricing-page implication.

## 2026-07-08 — Measured Quality Engine: scorer calibration fixes (cost-NEUTRAL)

**Files:** `src/kitesforu_qa/scorecard/config.py`, `src/kitesforu_qa/scorecard/axes.py`,
`src/kitesforu_qa/scorecard/signals.py`, `src/kitesforu_qa/harness/quality_matrix.py`,
`scripts/quality_matrix.py`

**Context:** validated the $0 Measured Quality Engine baseline (QUALITY_BACKLOG.md, PR #55)
before acting on its findings. Found axis 8 (cost_safety) applied one flat $0.10 cap to every
`quality_tier`, when the tier system itself targets low ~$0.025, medium ~$0.15, high ~$1.0-1.3,
ultra "flagship headroom" — guaranteeing every non-low-tier job would fail the axis by
construction, not because it overspent. Fixed with a tier-aware cap table
(`cfg.cost_cap_usd_by_tier`). Also found the aggregate ranking (`rank_systematic_weaknesses`)
treated a self-declared non-authoritative `proxy=True` score (substance_novelty's $0
research-grounding heuristic when `--enable-judge` is off) identically to a fully-measured axis —
fixed by excluding majority-proxy axes from the ranked list into a new, honestly-labeled section.

**Per-unit $ delta: $0.** This is a SCORER/measurement-tool calibration fix only — no change to
any LLM/TTS/provider call, no new judge enabled by default, no pipeline behavior touched. The
$0 baseline re-run (query-recent-7d, no new jobs) used to validate the fix is itself $0 (existing
completed jobs, re-scored; no generation). No pricing-page implication.

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
