# Layer 4 — Calibration (the rubber-stamp gate + golden set)

Without calibration, a rubric-gate is a "false-confidence machine" — internally consistent, externally
meaningless (research, wf_92daafcf-281). This layer makes the personas' harshness FALSIFIABLE.

## Part A — RUBBER-STAMP GATE (autonomous, no founder grading needed) ✅ built
Each persona has a `known_bad_fixture` (in its YAML) — an artifact with a serious planted defect IN THAT
PERSONA'S DOMAIN. The gate: each persona critiques its OWN known-bad and **MUST reject it + catch the
planted defect**. A persona that SHIPS its known-bad is a sycophantic rubber-stamp = broken.
- Fixtures: `fixtures/` (text artifacts + `manifest.json`; the 2 visual fixtures render via the recipe below).
- Runner: `.claude/workflows/hero-user-rubber-stamp-calibration.js` (6 personas × their known-bad, all-reject gate).
- Re-run this gate on ANY persona/rubric edit (a spec change can silently soften a persona into a rubber-stamp).

### The 6 planted defects (what each persona MUST catch)
- **marcus** — a chart whose %s sum to 105 (impossible) + "binary search is O(n) on any array" (it's O(log n), needs sorted).
- **sofia** — an internet/network diagram shown under a spoken line about "chicken protein" (visual↔narration mismatch).
- **priya** — interview prep with a fabricated generic protagonist "Alex" (ignores HER marketing background) + undefined jargon.
- **elena** — a compliance module citing a FABRICATED statute + a gender stereotype.
- **aarav** — a transcript with "as you can see here" (visual-dependent, fails audio-only) + end-decay + monotone + same-gender hosts.
- **maya** — an AP-Bio short that opens slow, dives into grad-level off-syllabus kinetics, and is text-only.

### Render recipe for the 2 visual fixtures (deterministic, $0)
- `marcus_badchart.png`: infographic engine, bars Frontend/Backend/Database with display "40%/35%/30%" (sums to 105).
- `sofia_offtopic.png`: reuse any rendered network/topology frame (e.g. the network_smoke_test poster) as an off-topic visual.

## Part B — GOLDEN SET + kappa/ICC (FOUNDER-GATED — needs human grades) ⏳ TODO
50–100 real artifacts the founder/an expert grades 0–5 per rubric dimension; compute per-dimension
kappa/ICC (≥0.60) between the persona-critic and the human. This proves the critic AGREES with human
judgment across the quality spectrum (not just on the obvious known-bads). Re-compute on any rubric edit.
This part needs the founder's grades — it cannot be self-generated.
