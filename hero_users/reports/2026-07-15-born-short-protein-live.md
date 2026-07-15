# Hero-User LIVE Grade — Born-Short "Best protein sources for building muscle"

**First live run of the Hero-User Verification System** against a real deployed pipeline.

| | |
|---|---|
| Job | `4d4af8ad-c127-4dcb-b2c1-9d543cac5dd3` (T3, `quality_tier=low`, 60s, `--short --visuals`) |
| Deployed image | `7881bd23` (12/12 worker services, serving digest verified) |
| Surface | Social Short → topic intake → 9:16 render |
| Routed personas | **Sofia** (solo short-form creator — primary), **Marcus** (factual/number-consistency), Maya (learner relevance) |
| Cost | render ≈ **$0.025** (T3); this grade + the fix = **$0** (offline observation + deterministic code) |
| Frames observed | 7 distinct rendered clips (full set, not one hero frame) — silent pass first, per Sofia's protocol |

---

## What the deployed round achieved (measured, live)

The eight born-short fixes deployed this round are **working** on the live path:

- **Step A (beat-scaled visuals) fired:** `shot_specs = 9` for a 9-beat script — no longer the flat `SHORT_MAX_SEGMENTS = 3` slideshow. This is the single biggest lever off the corpus's 1.0/5 floor.
- **Latency held:** 7.4 min end-to-end — well under the ~20 min the old cap was band-aiding. Scaling shots did **not** blow the latency budget.
- **Distinct assets:** 1–2 → **5–6** distinct visuals across the 9 beats.
- **Relevance is strong:** every frame is about protein / muscle. The off-topic-visual class the founder complained about is **not present** in this render.

That is real, live improvement. It is also **not ship-ready** — two defects remain, one of them newly root-caused below.

---

## The 7 frames, observed and critiqued

| # | asset | what's on screen | aspect | verdict |
|---|---|---|---|---|
| 1 | `3c13409c` | Hook card — "best protein sources for **building muscle**" | 9:16 ✓ | **good** — clean, on-topic open |
| 2 | `544a065e` | AI illustration — "Same Protein, Different Gains": muscular man w/ chicken+greens plate, woman w/ shake, dumbbells | **16:9 ✗** | **strong content, wrong shape** — will letterbox into 9:16 |
| 3 | `950c8555` | Key-term card — "Examples of high-leucine, high-DIAAS protein sources: whey, eggs+whites, chicken breast, canned tuna" | 9:16 ✓ | **good** — factual, dense, on-topic |
| 4 | `4d2f6b85` | Text card printing an **image prompt**: "A stylized graphic of a plate with a protein source, with a 'switch' icon turning to 'on' to signify activation." | 9:16 | **LEAK** — diffusion prompt shown as caption |
| 5 | `6392a0fe` | Text card printing an **image prompt**: "A person confidently preparing a meal with high-quality protein sources." | 9:16 | **LEAK** |
| 6 | `63e4121b` | Text card printing an **image prompt**, cut off mid-sentence: "A single scoop of whey protein powder causing a significant muscle fiber to grow, **contrasted**" | 9:16 | **LEAK** + truncated |
| 7 | `9fdf985c` | (card, protein-topical) | 9:16 | acceptable |

### Sofia (would she post this under her own name?) — **NO**
> "The open is fine and the plate illustration is genuinely nice — but then it starts **printing the instructions to itself on screen**. 'A person confidently preparing a meal…' is not a caption, that's the machine's stage direction. Three of my nine beats are the robot talking to itself, and one gets **cut off mid-word**. Instant no — I'd be laughed out of my own comments. And the one real illustration is **letterboxed** — black bars top and bottom on a full-screen format. The relevance is there, the pacing is there, but I am not posting a video that leaks its own prompt."

Sofia's scorecard: relevance/hook/topicality **pass**; `visual_truth` and no-leaked-text **fail** → **rejected**. Correctly — this is exactly her `rejection_triggers` ("an AI-image pretending to be a diagram", "same image / dead text", wrong aspect).

### Marcus (factual / number consistency) — **pass on facts, no fabrication**
> "The content claims are sound: leucine and DIAAS are the right axes for muscle-protein quality, and whey / eggs+whites / chicken breast / canned tuna are all legitimately high-DIAAS, high-leucine. No fabricated numbers, no impossible arithmetic in these frames. The **factual** floor holds. My objection is presentational, not technical."

### Maya (learner relevance) — **pass**
Every beat maps to the topic; a learner searching "protein for muscle" is served relevant substance.

---

## Systemic finding (root-caused, not patched) — prompt-leak-as-caption

Frames 4–6 are the **same class**: a born-short **visual-only** beat (no spoken `narration_text`) fell through `coverage_gate._lastresort_card_text`'s source ladder `("narration_text", "concept", "_concept_fallback")` to its **`concept`** — which is a describe-the-picture **image prompt authored for the diffusion model** — and rendered it **verbatim** as the on-screen caption.

**Why the existing guards missed it (verified live against `7881bd23`):** the shared `looks_like_image_prompt` guard is a **denylist of lead-ins** ("cinematic wide shot of…", "Hubble … image showing…"). But these concepts are **bare subject-depiction prose** ("A person confidently preparing a meal…"). Run against the three live strings, `looks_like_image_prompt()` returns **`False`** for all three and `screen_safe_caption_from_concept()` returns them **unchanged**. A lead-in denylist can never catch this — it's whack-a-mole.

This is **not born-short-specific**: the same ladder feeds long-form gap beats (the guard's own cited episodes 9889ac / 6f906c66). It is a universal source-selection bug.

## The fix (first-principles, at the source) — merged #1588

Exclude `concept` / `_concept_fallback` from the last-resort caption source **structurally** — ladder is now `narration_text → episode topic` only. An image prompt can **never** become caption text regardless of phrasing; no denylist to keep in sync.

- Flag `ENABLE_LASTRESORT_NO_CONCEPT_CAPTION` (default **ON**, kill-switch); **byte-identical when off**.
- Deterministic **$0** (a text rung removed, not added). `COST_CHANGELOG` updated (cost-neutral).
- Pins added; **40/40** `test_coverage_gate.py` pass; ruff clean.
- **Status: merged to `main`. Rides the next batched deploy round** (not re-rolled solo — test-cost-ladder batch discipline).

Expected effect: frames 4–6 become on-topic **topic** cards instead of leaked prompts (strict quality win). It does **not** by itself make each beat a *distinct* visual — see below.

---

## Remaining, tracked (deliberately not scoped into #1588)

1. **Monotony / distinct-visual-per-beat.** With the leak fixed, no-narration gap beats card the topic — on-topic but repeatable. The real target is a **distinct $0 visual per beat** (concrete-depiction axis + pictograph/icon + key-term), so the tail stops repeating. Larger, careful daytime work; partly built (concrete-depiction Part 1, Step A). → born-short monotony backlog.
2. **Scene aspect (16:9 → 9:16).** Frame 2 is a strong illustration generated **landscape**; it letterboxes in a 9:16 short. Born-short scene beats must render portrait. → born-short aspect backlog (corpus defect #4).

---

## Verdict

**The deployed round is a real, measured step up** (beat-scaling live, on-topic, latency safe, distinct assets doubled) **but the short is not yet postable** — Sofia rejects it on the leaked-prompt captions and the letterboxed scene. The acute embarrassment (prompt-leak) is now **root-caused and fixed at the source** (#1588, merged). Monotony and scene-aspect are the two tracked items between here and Sofia's "I'd post this."

---

_Frames archived (uncommitted, per vision-vault rule) at `scratchpad/live_verify/clips/` — 3c13409c, 544a065e, 950c8555, 4d2f6b85, 6392a0fe, 63e4121b, 9fdf985c._
