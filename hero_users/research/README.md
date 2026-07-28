# Why an LLM says "looks good" and the founder says "this is bad"

Founder, 2026-07-27: *"every time you say all good everything is fine and i see the quality is not
good."* This folder is the researched answer to **why**, and the design that follows from it.

- `divergences.json` — 79 documented human-vs-LLM judgment divergences, **233 unique cited
  sources**, each with a mechanism, a countermeasure, and (in 76 of 79 cases) a **deterministic
  $0 measurable signal**.
- `DESIGN-BRIEF.md` — the full synthesized design brief.

## The headline: most of this gap is ARITHMETIC, not perception

76 of 79 divergences have a computable proxy. That is the single most important finding, because
it means the fix is mostly **not** "get a better judge model" — it is "stop asking the model to
perceive things you can measure exactly, and hand it the measurement instead."

## The root cause of the eaf99101 failure, named in the literature

**Temporal/duration blindness.** A human watches in continuous, embodied time and accumulates a
real vigilance decrement. An LLM/VLM evaluates video as a single non-temporal forward pass over
sparsely sampled frames — it has no accumulating internal state saying *"I have now been looking
at this for 67 seconds."* Duration is just another number in its context, never a lived cost.

- *Discrete Minds in a Continuous World: Do Language Models Know Time Passes?* (arXiv:2506.05790)
  — LLM time-tracking accuracy collapses under conflicting cues (Qwen-7B 80.9% → 26.8%).
- Mackworth Clock Test / vigilance decrement — detection performance drops 10–15% under monotony.
- Mere-exposure effect (Zajonc) — liking is an inverted U; repetition tips into aversion.
- *The evolution of pace in popular movies* (doi:10.1186/s41235-016-0029-0) — **average shot length
  fell from ~12s in 1930 to ~2.5s today.** Editors calibrate to real disengagement.

That last one is the empirical anchor for the founder's "a fresh image every 2 seconds." He is not
being unreasonable; he is quoting the modern grammar of the medium.

**My exact error, in their words:** a "pixels changed" proxy measures per-frame signal delta, *not
the accumulated cost of watching*. I ran that proxy, saw a slow zoom, and passed a 93-second static
diagram. Marcus (persona) independently called the same thing *"technically true and
experientially false."*

## What we already implement

| Countermeasure | Where | Status |
|---|---|---|
| Measure how long an **image** lasts, not a clip | `checks/visual.py::monotony` | shipped (qa #85) |
| Temporal viewer facts (repeat share, longest stale, worst wait) | `harness/novelty.py` | shipped (qa #86) |
| Personas get facts as **ground truth**, never asked to estimate | golden protocol | shipped (qa #87) |
| Founder-graded golden item, no planted defect | `calibration/golden/` | shipped (qa #87) |
| Deterministic L0 gate runs **before** any LLM judge is consulted | `RUBRIC.md` layering | pre-existing |

## Calibration evidence (2026-07-27)

The layer is only worth anything if it **discriminates**. Both halves were run:

- **Known-bad** (`eaf99101`, founder-graded REJECT): **4/4 personas REJECT**, zero rubber-stamps.
  Maya would drop off at 25s; Sofia at 100s.
- **Control** (same brief, healthy numbers: 44 pictures, 9% repeat, 11s worst wait, mixed modes):
  **4/4 SHIP**. Marcus still logged a real nit (41% photographic vs diagram budget) and shipped
  anyway — harsh, not indiscriminate.

A judge that rejects everything carries no information. This one separates the two.

## The other biases that matter here, with numbers

- **Position bias** — MT-Bench measured Claude-v1 position-*consistency* at **23.8%**; the verdict
  flipped on candidate order more often than not. → Score artifacts **pointwise against a rubric**,
  never pairwise, so there is no order to be biased by.
- **Verbosity bias** — padding a good answer with redundant restatement fooled Claude-v1 **91.3%**
  of the time. → Score information **density** (distinct beats per minute), and treat "duration
  with no new content" as an explicit penalty. This is exactly the monotony metric.
- **Sycophancy / framing-sensitivity** — the verdict follows the ask. → The persona must never see
  my claim, my verdict, or any hint the artifact is known-bad. In the calibration run the personas
  were given only the artifact facts, and were explicitly told rejecting everything is as useless
  as approving everything.
- **Self-preference** — judges favor text matching their own generation distribution. → Prefer a
  different model family for the critic where available; never let the generator grade itself.

## The standing rule this produces

> Never ask a model to perceive what you can compute. Compute it, hand it over as ground truth,
> and let the persona spend its judgment on the part that actually needs judgment: **is this
> acceptable for a real person with somewhere else to be?**
