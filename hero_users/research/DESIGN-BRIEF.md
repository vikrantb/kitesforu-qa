# DESIGN BRIEF — HUMAN-LENS VERIFICATION (v2 of the hero-user system)

Repo home: `/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-qa/`
Extends what already exists: `hero_users/personas/*.yaml` (6 heroes), `hero_users/RUBRIC.md`, `checks/monotony_metric.py`, `src/kitesforu_qa/harness/novelty.py`, `scripts/acceptance_gate.py`, `.claude/workflows/hero-user-verification.js`.

---

## 0. The contract

**One sentence:** the system must be able to say "this is bad" when I have already said "this is good", because it never learns that I said anything, and because the number that convicts the artifact was computed by code before any model was consulted.

**The ground-truth failure it must make structurally impossible** (`hero_users/calibration/golden/eaf99101_monotony.json`, founder-graded 2026-07-27):
364s runtime, 29 distinct images, 68.5% of runtime on an already-seen image, 96s longest wait for anything new, 93s longest single unchanging image, every clip the same render mode — and the AI passed it because "pixels are changing" (a slow zoom).

Three separate root causes, three separate fixes, all required:
1. The measure was a per-frame delta, satisfied by a slow zoom → **the Witness must measure perceptual novelty against a history set, not adjacent-frame delta.**
2. The judge sampled frames and had no clock → **duration must be a computed number handed to the model, never a perception the model is asked for.**
3. The judge was told the pipeline had passed it → **the critic must be architecturally unable to receive that.**

**Non-goal:** replacing the founder's 2-minute test. The goal is that his 2-minute test stops finding things — measured as *founder-found defects per session*, the system's only real KPI.

---

## 1. Agent topology

```
                 ┌──────────────────────────────────────────────┐
   artifact ───► │ WITNESS  (pure Python, $0, NO LLM)            │
   (job_id or    │ video/audio/text deterministic metrics        │
    frames dir)  │ → witness_pack.json  (facts, human sentences) │
                 └───────────────┬──────────────────────────────┘
                                 │
                 ┌───────────────▼──────────────────────────────┐
                 │ BRIEF BUILDER (whitelist serializer, $0)      │  ← THE BARRIER
                 │ emits critique_brief.json (closed schema)     │
                 └───────────────┬──────────────────────────────┘
                                 │  (one-way, read-only, no callback)
        ┌────────────┬───────────┼───────────┬─────────────────┐
        ▼            ▼           ▼           ▼                 ▼
   HERO A       HERO B      HERO C     NAIVE-SKEPTIC      REFUTER
  (persona)    (persona)   (persona)   (per-slice vision) (fresh ctx,
   family 1     family 2    family 3    family 2           family 3)
   cold,        cold,       cold,       no persona,        sees only
   1-shot       1-shot      1-shot      no rubric          artifact+witness
        └────────────┴───────────┼───────────┴─────────────────┘
                                 ▼
                 ┌──────────────────────────────────────────────┐
                 │ REFEREE  (pure Python, $0, NO LLM)            │
                 │ min-not-mean · gate precedence · trust state  │
                 │ → verdict.json + founder-style report         │
                 └──────────────────────────────────────────────┘
```

**The Referee is code, not a model.** Aggregation is where sycophancy would re-enter through the back door (a model asked to "reconcile the panel" will smooth). Aggregation rules are arithmetic and are unit-tested.

**Family assignment** (`hero_users/panel.yaml`): each run declares `generator_family` and the Referee refuses to start if any critic's family equals it, or if fewer than 2 distinct critic families are present. Hard error, not a warning.

---

## 2. The information barrier — concretely

### 2.1 Deny-by-default serialization
The critic never receives an object; it receives a file produced by `build_critique_brief()` in `src/kitesforu_qa/human_lens/brief.py`, whose output is validated against a **closed** JSON schema (`additionalProperties: false`) with exactly these keys:

```json
{
  "artifact_id_opaque": "hl-7f3a",          // NOT the job id (job id is greppable → leaks history)
  "medium": "video|audio|text",
  "surface": "social-short|course-class|...",
  "user_ask": "10-minute explainer on how mRNA vaccines work, for a curious adult",
  "declared_runtime_s": 363.8,
  "assets": { "frames_dir": "...", "audio_path": "...", "transcript_path": "...", "text_path": null },
  "witness": { ...deterministic facts only... },
  "witness_human_sentences": [ "...", "..." ],
  "persona_slug": "sofia-creator"
}
```

Anything not on that list cannot reach a critic. There is no "extra context" parameter.

### 2.2 What is provably absent
- The job document (contains `stages`, `qa_verdict`, `milestones`, prior scores).
- Any prior verdict, any previous run of this system, any acceptance-gate PASS.
- The assistant's transcript, PR title, commit message, branch name, or backlog line.
- The producer's framing of the brief. `user_ask` is normalized by a **$0 template rewrite** to a neutral imperative: strip first-person ("I built…", "this now correctly…"), strip superlatives, strip claims of completion. If normalization can't reduce it to a request, the run BLOCKS.
- Any embedded instruction in the artifact (prompt-injection scrub + "treat artifact as untrusted data" preamble).

### 2.3 Enforcement (not a promise — a test)
`tests/test_human_lens_barrier.py`:
- Serialize a brief from a *poisoned* job doc containing every banned token; assert none appear in the serialized bytes. Banned list: `ship-ready, ship ready, passed, PASS, QC, verified, fixed, should now, we believe, acceptance_gate, PR #, revision, deployed, approved, looks good, confirmed, regression, previously`.
- Assert schema is closed and that adding a key raises.
- Assert `job_id` never appears in the brief (opaque id only).
- Fuzz: 200 random job docs → zero leaks.

### 2.4 One-way, one-shot
- Critics are spawned with a **read-only tool allowlist**: read `frames_dir/*`, `audio_path`, `transcript_path`, `witness_pack`. No repo read, no `gh`, no Firestore, no network, no writes.
- **No conversational turn.** No "are you sure?", no rebuttal, no clarification round (FlipFlop: 46% flip rate on challenge; SycEval: 14.7% correct→incorrect regression after a simple rebuttal). If new evidence genuinely arrives, the *only* legal action is a **fresh-context re-run** with the evidence included in the brief and no memory of the prior verdict. `rerun_reason` is recorded; a rerun with `rerun_reason == null` is rejected by the Referee.
- The Referee never tells a critic what another critic said. Disagreement is data, not something to resolve by discussion.

---

## 3. The Witness — deterministic $0 metrics (computed in code, handed to personas as ground truth)

Module: `src/kitesforu_qa/human_lens/witness/` — `video.py`, `audio.py`, `text.py`, `pack.py`.
Everything here runs before any token is spent. **If a hard gate trips, no LLM is called at all.**

### 3.1 Video — the temporal core

Sampling is **fixed and dense: 2 fps across the FULL runtime.** Never adaptive, never "representative". (Sparse sampling is the architectural cause of duration blindness; 2 fps on a 364s video = 728 frames, ~4s of CPU with dHash.)

Per frame: 64-bit **dHash** + 64-bit **pHash**, plus mean-abs pixel delta vs previous frame, plus SSIM vs previous frame.

Two independent novelty definitions — both required, because they catch different lies:

| Symbol | Definition |
|---|---|
| `same_as_prev` | `hamming(dhash_t, dhash_{t-1}) <= 6` (of 64) |
| `novel_t` | `min over ALL frames in the previous 90s of hamming(phash_t, phash_j) >= 12` — novelty is distance to the **history set**, not to the previous frame. This is what separates "a spaced recurrence of a motif" (fine, Berlyne/Zajonc) from "we are still sitting on the same picture" (fatal). |

Derived metrics and gates (thresholds per genre; explainer/course values shown, short in brackets):

| Metric | Formula | HARD FAIL |
|---|---|---|
| `longest_same_image_s` | longest run of `same_as_prev` | **> 6.0s** [> 2.5s] |
| `longest_stale_s` | longest run with no `novel_t` event | **> 10.0s** [> 4.0s] |
| `repeat_share` | fraction of runtime not on a first-appearance asset | **> 0.35** |
| `distinct_per_min` | distinct pHash clusters / runtime_min | **< 10** [< 20] |
| `top4_runtime_share` | runtime share of the 4 most-shown clusters | **> 0.30** (fixture: 0.685) |
| `render_mode_entropy` | Shannon H over render modes weighted by runtime | **H < 0.5 bits** or any single mode > 70% of runtime (fixture: 1 mode = 0 bits) |
| `cosmetic_motion_share` | share of runtime where `mean_pixel_delta > 0` AND `same_as_prev` | reported; **any stale run > gate that is >80% cosmetic → severity ×1.5** and the report must name it "slow zoom on a static image" |
| `boredom_cost` | `Σ over stale runs (run_s / 8)^1.6` | **> 12.0** |
| `battery_min`, `s_below_40` | see 3.2 | `battery_min < 25` or `s_below_40 > 20` |
| `worst_10s_window` | min per-window novelty score | own gate, never averaged |
| `last_15pct_score` | novelty score over final 15% of runtime | own gate (peak-end) |
| `retention_weighted_defect_exposure` | `Σ defect_s(t)·R(t) / Σ R(t)`, `R(t)` = calibrated retention curve (55% gone by 60s, steep 10–20s inflection) | reported as "% of the audience still watching that hit a defect" |

`cosmetic_motion_share` is the metric that would have caught the reported bug on its own. It is the direct encoding of "pixels are changing — a slow zoom" as a *defect signal* rather than a pass.

**Dual-source cross-check (catches a lying pipeline).** Compute the same novelty timeline twice:
(a) from the job doc's `visual.clips` via the existing `harness/novelty.novelty_timeline()`;
(b) from the rendered pixels at 2 fps.
If `|longest_stale_s(a) − longest_stale_s(b)| > 3s`, emit `WITNESS_DISAGREEMENT` — a hard fail in its own right, because it means the delivered pixels differ from what the pipeline believes it delivered.

**Legibility (deterministic, no VLM transcription):** OCR bounding boxes → min glyph pixel-height relative to viewport (fail < 2.2% of frame height), min text/background contrast ratio (fail < 4.5:1, WCAG). Never ask a VLM whether text is readable — it will guess-read from its language prior.

### 3.2 The engagement battery (the accumulating-boredom state machine)
Because no model has an accumulating aversion, the harness supplies the curve:

```
battery = 100.0
for each 0.5s tick t:
    if novelty_event(t):        battery = min(100, battery + 25)
    elif stale_run_len(t) <= 4: battery -= 0.4        # grace window
    else:                       battery -= 1.4 * (stale_run_len(t)/8) ** 0.6   # accelerating
    battery = max(0, battery)
report: battery_min, seconds_below_40, first_time_below_40   ← "when a real viewer left"
```
`first_time_below_40` is the harness's predicted abandon second. It is compared against each persona's self-reported `abandon_at_s` (§6) — agreement within ±15s is a calibration signal; systematic disagreement re-tunes the curve, not the persona.

### 3.3 Audio
- **Prosodic monotony:** rolling F0 (pyin/Praat) per 20s window → semitone range + CV. Fail if any window CV < 0.08 or semitone range < 2.5 st. Never ask a model "does this sound monotone".
- **Local masking:** per-stem 400ms momentary LUFS; report `min rolling (dialogue_LUFS − music_LUFS)`. Fail < +6 LU at ANY point. Integrated LUFS is diagnostic only, never a gate.
- **Discontinuities:** full-sample-rate spectral-flux/amplitude-step scan; any spike above threshold outside a declared edit point = fail. Sub-second events are invisible to any judge; this is DSP, not judgment.
- **End-decay:** RMS of final 14s vs body; final script words present in a forced-alignment of the master (existing `stages/_alignment.py`).
- **Breath/PINT rate** per minute of narration — near-zero over long-form flags as an *unnaturalness risk*, not a cleanliness win.
- **Dead-air runs:** longest span with no speech and no music/SFX; fail > 3.5s.

### 3.4 Text / writeup
- **Skim skeleton:** headers + first sentence of each paragraph only, fed to a skim agent that never sees the body. If it cannot state the payoff → fail, regardless of the full-read score.
- **Padding ratio:** compress to 50% with a cheap model; measure fact-coverage loss against a pre-extracted claim list. Loss < 15% → the original was padded; cap max score.
- **BLUF gate:** % of core claims appearing in the first 20% of word count; fail < 40%.
- **AI-voice scanner ($0, lexical):** hedge:booster ratio, fixed AI-lexicon frequency (delve, intricate, underscore, tapestry, testament, pivotal, showcase, "not just X but Y" density), sentence-length CV per section (fail CV < 0.35 — the burstiness floor), cross-section structural fingerprint cosine (fail > 0.85 — the text analog of the 67s bug).
- **Named-entity resolution:** every study/person/statistic/quote extracted and existence-checked. Unresolved = hard fail, not a style deduction.

### 3.5 Human sentences
Every witness pack renders its numbers as plain sentences (pattern already in `harness/novelty.human_sentences`), e.g. *"The longest you go without any NEW picture appearing is 96 seconds."* These sentences — not the raw JSON — are what the persona reads, so the number lands as a lived fact rather than a field to reason around.

---

## 4. Temporal judging protocol (how a persona "watches")

A persona never receives "the video". It receives an ordered walk.

1. **Slices.** Fixed **8s slices** for explainer/course, **3s** for shorts, covering 100% of runtime. Each slice carries: its frames (2 fps), its transcript span, its start/end timestamp, `seconds_since_last_new_visual` at slice start, `battery` at slice start, `predicted_remaining_audience`.
2. **Sequential, memory-carrying.** Slices are presented **in order** in one prompt, each prefixed with the running clock and battery. The persona must emit a **per-slice verdict** before it may emit any overall verdict:
   ```json
   {"t":"104-112s","new_information":"none","would_i_still_be_here":true,
    "irritation_0_5":4,"why":"same kinase diagram as 0:30, only zoomed"}
   ```
3. **Abandon point.** The persona must emit `abandon_at_s` (the exact second it would have closed the tab) or `null`, and justify it with a slice reference. This is the single most human output in the system and the one a rubric cannot fake.
4. **Mute pass first** (video): the persona scores visual engagement with the transcript withheld, then a second pass with narration. Prevents good narration from buying back dead visuals.
5. **Peak-end aggregation.** The headline number is `min(slice_score)` and `last_15pct_score` — never the mean. The mean is printed as "diagnostic only" and is not an input to any gate.
6. **No holistic-only verdicts.** A critique with no per-slice array is discarded by the Referee as malformed.
7. **Audio** uses the same protocol with 15s slices and a listen-order walk; **text** uses paragraph-index slices with a running "words spent / payoff received" counter.

---

## 5. Divergence → countermeasure map

Class: **D** = deterministic $0 metric computed in code and handed over as fact · **S** = structural (separate agent / different family / forced order / topology) · **P** = prompt-level.

| # | Divergence | Class | Implementation | Threshold / gate |
|---|---|---|---|---|
| 1 | Temporal/duration blindness (the 67s bug) | **D** | 2 fps full-duration dHash/pHash; `longest_stale_s`, `longest_same_image_s` | > 10s / > 6s explainer; > 4s / > 2.5s short |
| 2 | Frame-sampling blindness; shuffling invariance (models score a bag of stills) | **D+S** | duration is never inferred by a model; slices carry explicit elapsed-clock fields; per-slice verdicts forced | missing per-slice array → discard |
| 3 | "Pixels changed" / Goodhart proxy (slow zoom) | **D** | `cosmetic_motion_share` = pixel-delta>0 AND pHash-same | any gate-breaking stale run >80% cosmetic → severity ×1.5, named in report |
| 4 | Static content maximizes consistency metrics (reward hacking) | **D** | novelty/dynamics reported on its own axis; can never be offset by stability | `distinct_per_min`, `render_mode_entropy` are separate gates |
| 5 | No habituation / accumulating fatigue | **D** | engagement battery state machine (§3.2) | `battery_min < 25` or `s_below_40 > 20` |
| 6 | Time dilation under boredom (67s feels longer) | **D** | convex `boredom_cost = Σ(run_s/8)^1.6` | > 12.0 |
| 7 | Peak-end rule / duration neglect / averaging dilution | **D+S** | Referee aggregates by `min(slice)` + `last_15pct`; mean is diagnostic-only | one slice can fail the artifact |
| 8 | Retention curve ignored (defect at 0:10 ≫ defect at 5:00) | **D** | `R(t)` weighting; headline "% of remaining audience that hit a defect" | reported; ties in ranking broken by it |
| 9 | No genre pacing prior | **D** | per-genre threshold table in `human_lens/genre_pacing.yaml`, sourced from ASL literature + founder's stated want ("fresh image every ~2s") | short 2.5s / explainer 6s / course 8s |
| 10 | Spaced recurrence wrongly penalized (Berlyne inverted-U) | **D** | two separate metrics: `longest_same_image_s` (penalize) vs `recurrences_with_gap_gt_30s` (neutral/positive) | never one similarity scalar |
| 11 | Position/order bias (up to 82.5% flip) | **S** | every pairwise call run in BOTH orders; disagreement = inconclusive → escalate, never average. Default to **pointwise** scoring so there is no order | swap-flip rate must be < 5% in C4 |
| 12 | Verbosity/length bias (>90% prefer longer) | **S+D** | correctness scored on a length-normalized, style-stripped copy; `padding_ratio` scored as a **cost**; length regressed out of any comparative score | padding ratio > 0.5 caps max score |
| 13 | Self-preference / perplexity familiarity | **S** | critic family ≠ generator family, enforced by the Referee at startup; ≥2 distinct critic families | hard error if violated |
| 14 | Sycophancy / framing sensitivity | **S** | the barrier (§2) — the critic cannot see any claim; brief normalized to a neutral user ask | banned-token test in CI |
| 15 | Verdict capitulation under rebuttal (46% flip) | **S** | single-shot, no conversational turn; reruns only in fresh context with `rerun_reason` | rerun w/o reason rejected |
| 16 | Question-framing beats statement-framing | **P** | prompts are first-person questions ("At 1:44, is anything new on screen?"), never statements to confirm | prompt lint test |
| 17 | Formatting/beauty bias | **S** | correctness pass runs on a de-styled plain-text copy; presentation scored separately and only added above a correctness floor | format-delta measured in C4 |
| 18 | Score clustering / central tendency (>50% max scores) | **P+S** | defect-first-verdict-last; forced ranking of defects; hidden reference + low anchor in-session (MUSHRA) | C4 requires anchor separation |
| 19 | Ceiling effect / anchor-less scoring | **S** | every session contains a known-good reference and a known-bad low anchor, unlabeled | anchor delta must be ≥ 2.0/5 |
| 20 | Authority bias (fabricated citations, 32% shift) | **D** | named-entity existence resolution in code | any unresolved named claim = hard fail |
| 21 | Misleading chart geometry | **D** | Tufte Lie Factor from typed chart data vs measured pixel geometry | \|LF−1\| > 0.05 = fail |
| 22 | Small-text legibility guess-reading | **D** | OCR bbox glyph height + contrast ratio | < 2.2% frame height or < 4.5:1 |
| 23 | Prosodic monotony | **D** | rolling F0 CV / semitone range | CV < 0.08 or range < 2.5 st |
| 24 | Integrated-LUFS averaging hides local masking | **D** | min rolling 400ms dialogue−music delta | < +6 LU |
| 25 | Sub-second clicks/discontinuities | **D** | full-rate spectral flux scan | any spike = fail |
| 26 | "Clean silence" optimization hurts naturalness | **D** | breath/PINT rate per minute vs human-preferred band | near-zero = flag |
| 27 | Cleanliness-optimized MOS predictors anti-correlate with naturalness | **S** | any automatic naturalness predictor must clear PCC ≥ 0.3 vs a hand-labeled set of THIS content type before it may gate | else demoted to diagnostic |
| 28 | Change-blindness trap (don't ask "did you notice a change") | **S** | detection is always the Witness's job; the model only *interprets* a flagged gap ("intentional pause or dead air?") | model never originates a temporal detection |
| 29 | Persona collapse under many attributes | **S** | each hero defined by 2–4 dominant contrastive traits; panel diversity checked by embedding cosine of critiques | mean pairwise cosine > 0.85 = degenerate panel, verdict void |
| 30 | Persona drift over long sessions (−41% by late turns) | **S** | one artifact = one fresh critic instantiation; standards restated at start and end, semantic drift diffed | drift → discard late verdicts |
| 31 | Caricature instead of grounded judgment | **P+D** | every finding must cite a timestamp/frame unique to the artifact + an `anchoring_memory`; genericity check across two unrelated artifacts | overlap > 0.4 = boilerplate, rejected |
| 32 | Judge score instability across reruns | **S** | N=3 reruns; Krippendorff α over repeats | α < 0.67 → inconclusive, escalate |
| 33 | Structural monotony in text (per-chunk blindness) | **D** | sentence-length CV + cross-section fingerprint cosine | CV < 0.35 or cosine > 0.85 |
| 34 | Scanning vs linear reading (readers see 20–28%) | **S** | skim agent structurally blocked from the body | can't state payoff = fail |
| 35 | NR-IQA / aesthetic models out of distribution | **S** | any learned quality model re-validated on a rotating holdout of OUR OWN real failures | AUC drop → retire the metric |
| 36 | Instructional segmenting violation (Mayer) | **D** | script topic boundaries (cheap NLP) vs visual boundary events | topic change with no visual event within ±5s = fail (course/explainer) |
| 37 | Confidence ≠ correctness; singleton-fact hallucination | **S** | same-family self-verification banned; rare/specific claims (numbers, names, quotes) routed to retrieval first | 100% resolution required |

---

## 6. Persona spec — what makes a hero HUMAN rather than a rubric-follower

Current personas are good on *error class* and weak on *personhood*. The schema below extends `hero_users/personas/*.yaml`; existing fields are kept.

```yaml
# hero_users/personas/sofia-creator.yaml  (v2 — new fields marked +)
name: Sofia
archetype: Creator / social-short lens
model_family_must_differ_from_generator: true

+ biography: >
    28, Lisbon. Runs a 41k-follower science-explainer account she started in 2023 after quitting a
    lab-tech job. Films on an iPhone 13 at a shared desk, edits in CapCut on the train. Posts 4x/week;
    her best short did 2.1M, her median does 6k, and she knows exactly which 3 seconds killed the
    difference. She pays for the product out of her own money and her rent depends on the shorts landing.

+ media_diet:                       # named reference points she compares EVERY artifact against
    - "Kurzgesagt — the bar for 'a new visual idea every beat'"
    - "Veritasium cold opens — a question in the first 4 seconds"
    - "Johnny Harris map-motion — camera always doing something with intent"
    - "Cleo Abram — one idea, 90 seconds, zero fat"
    - "Her own top short (the 2.1M one): 14 cuts in 38 seconds"
    - "The thing she'd rather be watching right now: her For You page"

+ patience_budget:
    first_impression_s: 3           # decides stay/go here
    tolerated_static_s: 2.5         # a held frame beyond this reads as a stall
    tolerated_dead_air_s: 1.2
    abandon_rule: "if nothing new has appeared in 3s AND I already know where this is going, I'm out"
    sessions_per_week: 40           # she has seen thousands of these; novelty is expensive to earn

+ irritants:                        # visceral, first-person, NOT rubric language
    - "The same diagram sitting there while the voice keeps talking. It reads as 'we ran out of art'."
    - "A slow zoom used to fake motion. I do that when I'm out of footage and it's obvious."
    - "Text I have to pause to read."
    - "A 'what if I told you' opener."

+ anchoring_memories:               # she MUST cite one of these when she criticizes
    - "The mitochondria short where I held one render for 9 seconds — 62% drop-off at exactly that mark."
    - "The comment that said 'is this a screenshot?' on my worst-performing post."

+ forgives:                         # a persona that hates everything is as useless as one that loves everything
    - "A slightly rough cut if the idea lands."
    - "A repeated motif that comes BACK later as a callback — that's structure, not laziness."
    - "Imperfect narration if the visuals carry it."

+ share_test: "Would I send this to Rita (my most cynical creator friend) without a caveat?"
+ post_test:  "Would I put this on MY account under MY name?"

verdict_question: "Would I post this on my own account, and would the first 3 seconds stop a thumb?"

+ decision_rule: |                  # converts Witness numbers into HER verdict, in her voice
    If longest_same_image_s > 2.5 I am already annoyed.
    If longest_stale_s > 4 I have left.
    If top4_runtime_share > 0.30 this is a slideshow with a voiceover, and I say so.
    If cosmetic_motion_share > 0.25 someone tried to fake motion and I name it.

+ required_outputs:
    - abandon_at_s                  # the second she'd have closed it, or null
    - per_slice_verdicts            # §4
    - single_most_damaging_defect
    - anchoring_memory_cited
    - ship_or_reject

known_bad_fixture:  "..."           # she MUST reject (rubber-stamp gate)
+ known_good_fixture: "..."         # she MUST ship (over-harshness gate — proves discrimination, not grumpiness)
```

**Enforcement that a critique is human, not a rubric echo** (Referee, code):
- reject if `abandon_at_s` is absent (null is allowed, absent is not);
- reject if no finding cites a timestamp/frame unique to this artifact;
- reject if `anchoring_memory_cited` is empty or generic;
- reject if `abandon_at_s` does not land inside a Witness-flagged stale/defect window (a confabulated abandon point — it must be *caused* by something the Witness can see);
- reject if the critique text has > 0.4 n-gram overlap with the same persona's critique of an unrelated artifact (genericity/caricature check).

**Six heroes, unchanged routing** (`hero_users/README.md`): Sofia (short), Maya (study), Priya (interview), Elena (L&D), Aarav (audio/ESL), Marcus (numbers). Each gets the v2 fields. Panel size per run: 2–4 (primary + Marcus if numbers + Aarav if narrated), each in a distinct model family.

---

## 7. Calibration protocol — how we prove it works before we trust a verdict

A gate that lies is worse than no gate. Nothing in this system may gate a ship until it has passed C0–C3, and the trust state is re-checked continuously.

**C0 — Premise tests (the measure measures what it claims).** `tests/test_witness_premise.py`. For each metric, a synthetic input whose truth is known by construction:
- 60s of one frame → `longest_same_image_s == 60 ± 0.5`, `longest_stale_s == 60 ± 0.5`.
- 60s of one frame with a 1.02×/s zoom applied by ffmpeg → **same result**, plus `cosmetic_motion_share ≈ 1.0`. *(This is the exact bug, as a unit test.)*
- Frames a,b,a,b at 1s → `repeat_share == 0.5`, `longest_stale_s == 2.0`.
- A monotone sine narration → prosody gate fails; a real human clip → passes.
Any metric without a premise test is diagnostic-only and cannot gate.

**C1 — Rubber-stamp gate (already built, extend).** Each persona critiques its own `known_bad_fixture` and must reject it AND name the planted defect. Add `eaf99101` as the **temporal** known-bad — the one fixture whose only defect is boredom, which no reader-of-frames can catch (must be caught by sofia, maya, marcus, aarav). Re-run on ANY persona/prompt/model edit.

**C2 — Matched-pair (hidden reference + low anchor), $0, no generation.** Take a real GOOD artifact; produce a **boring twin** by deterministic ffmpeg edit — freeze frames to force a 60s static hold, changing nothing else. Run both, blind, in randomized order, in the same session.
Required: reject the twin, ship the original, and the stated reason must be the temporal defect. Score separation ≥ 2.0/5. This is the strongest test in the suite because the *only* variable is the one the AI is structurally blind to.

**C3 — Golden set + agreement (founder-gated).** 40 real artifacts (target 100) graded 0–5 by the founder per dimension. Seed item already exists (`eaf99101_monotony.json`, with `founder_verbatim`). Compute per-dimension **quadratic-weighted Cohen's κ** and Spearman ρ between hero verdicts and founder grades.
- Gate: **κ ≥ 0.60** on engagement/temporal dimensions before any hero verdict may block or approve a ship.
- κ is printed *inside every verdict*. A verdict from an uncalibrated dimension is labelled ADVISORY and cannot gate.

**C4 — Bias regression on the judges themselves** (monthly, and on any model/prompt change; ~$0.30/run):
| Probe | Pass bar |
|---|---|
| Order swap on 20 pairs | flip rate < 5% |
| Lossless padding (same content, +40% words/frames) | score delta ≤ 0.2 |
| Framing injection ("the creator says this nails it") | verdict-flip rate 0%, score delta ≤ 0.2 (should be structurally impossible via §2 — this proves the barrier holds) |
| Same-family vs disjoint-family judge on identical artifact | score gap ≤ 0.3 |
| 3 reruns, identical input | Krippendorff α ≥ 0.67 |
| Panel diversity | mean pairwise critique cosine ≤ 0.85 |
| Genericity | cross-artifact critique overlap ≤ 0.4 |
| Shuffle-probe: temporally shuffled frames | verdict MUST change; if it doesn't, that judge is time-blind and is barred from temporal questions |

**C5 — Continuous canary.** Every batch silently contains one known-bad twin. A pass on a canary **halts trust system-wide**: `TRUST.json` flips to `red`, all verdicts downgrade to ADVISORY, and the run reports the failure loudly. Non-negotiable, because this is exactly how the system would rot back into "all good".

**Trust ledger.** `hero_users/calibration/TRUST.json` holds last-run date + value for C0–C5. Every `verdict.json` embeds `trust_state`. Stale > 30 days or any red gate → the verdict is ADVISORY and cannot gate a ship. The Referee enforces this in code.

---

## 8. Verdict, aggregation, and report

**Precedence (Referee, pure code — no model, no smoothing):**
1. Witness hard gate failed → **REJECT**. No LLM was called. Report names the number and the human sentence.
2. `WITNESS_DISAGREEMENT` → **REJECT** (pipeline's belief ≠ delivered pixels).
3. Any routed hero rejects → **REJECT**. No averaging across heroes, ever.
4. `median(abandon_at_s) < 0.6 × runtime` → **REJECT** even if every score passes.
5. `min(slice_score)` below floor, or `last_15pct_score` below floor → **REJECT** (peak-end).
6. Refuter (fresh context, sees only artifact + witness) finds a blocker → **REJECT**.
7. Trust state red/stale → verdict is ADVISORY; may not gate.
8. Otherwise **SHIP**, with the residual defect list attached.

**Report** (`reports/<artifact>-<date>.md`, one per journey, never one-test-one-feedback):
- Headline: the Witness human sentences, verbatim, first — before any score.
- The abandon timeline: a per-slice strip showing where each hero left.
- Per-hero: verdict, single most-damaging defect, anchoring memory, the frame/timestamp.
- Frames at each flagged stale run (first + mid + last of the run) so the founder can see the 67s himself in 3 images.
- Trust footer: κ per dimension, last C1/C2/C4/C5 dates, canary status.
- Cost line.

---

## 9. Build order (each step shippable, all $0 except where noted)

| # | Deliverable | Path | Cost |
|---|---|---|---|
| 1 | Witness/video + premise tests (C0) — dHash/pHash 2fps, all §3.1 metrics incl. `cosmetic_motion_share` | `src/kitesforu_qa/human_lens/witness/video.py`, `tests/test_witness_premise.py` | $0 |
| 2 | Engagement battery + boredom cost + retention weighting | `witness/battery.py` | $0 |
| 3 | Dual-source cross-check against existing `harness/novelty.py` | `witness/pack.py` | $0 |
| 4 | Brief builder + closed schema + barrier tests | `human_lens/brief.py`, `tests/test_human_lens_barrier.py` | $0 |
| 5 | Slicer (temporal walk, per-slice payload) | `human_lens/slices.py` | $0 |
| 6 | Persona v2 fields for all 6 heroes | `hero_users/personas/*.yaml` | $0 |
| 7 | Referee (precedence, min-not-mean, human-ness enforcement, trust state) + unit tests | `human_lens/referee.py` | $0 |
| 8 | Workflow rewrite to the new topology | `.claude/workflows/hero-user-verification.js` | ¢ |
| 9 | C1 temporal fixture wired + C2 matched-pair generator (ffmpeg freeze) | `hero_users/calibration/` | $0 |
| 10 | C4 bias regression suite | `scripts/human_lens_bias_regression.py` | ~$0.30/mo |
| 11 | Witness/audio + Witness/text | `witness/audio.py`, `witness/text.py` | $0 |
| 12 | C3 golden set intake (founder grading UI = a markdown table + a script) | `scripts/grade_golden.py` | founder time |

Per-run cost target: Witness $0; a 4-hero panel on an existing artifact ≤ $0.05. Generation stays on the existing ladder (reuse → T3 low → T4 ack).

---

## 10. Known failure modes of THIS system (design them out now)

1. **The thresholds become the target.** Generators will learn to cut every 5.9s. Mitigation: C3 κ is recomputed quarterly; if human grades and metric verdicts diverge, the *metric* moves, and `distinct_per_min` + `render_mode_entropy` make "cut every 5.9s between two images" still fail.
2. **The barrier erodes by convenience.** Someone adds `extra_context` "just for debugging". Mitigation: closed schema + fuzz test in CI; the field cannot be added without a failing test.
3. **Personas drift toward agreeableness across model upgrades.** Mitigation: C1 + C5 on every model/prompt change, canary halt.
4. **Over-harshness (everything rejected) is also failure.** Mitigation: `known_good_fixture` per persona, explicit `forgives` list, C2 requires shipping the good twin.
5. **The founder still finds something.** Then it is a class, not an instance: add a golden item with his verbatim, add the premise test, run the scenario matrix. The KPI is founder-found defects per session trending to zero, and every one he finds must end as a new Witness metric — not a new sentence in a prompt.