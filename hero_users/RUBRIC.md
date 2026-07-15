# Hero-User Content-Quality + Engagement Rubric v1

Applies to a generated social short, course/class episode, writeup, or Car-Mode audio. **LAYERED and
PERSONA-WEIGHTED** — never one globally-averaged number (personas genuinely disagree on "good", and
averaging hides the exact fault a real user flags).

**Method:** temperature 0, pointwise, 0–5 per dimension (0–5 has the highest human–LLM agreement),
**REASONING-BEFORE-SCORE** (enumerate every defect with a timestamp/frame FIRST, emit the number LAST).
Watch/hear the WHOLE artifact across the full duration (every ~3s + first + last), **ON MUTE first**
for a short, never one hero frame.

## LAYER 0 — DETERMINISTIC $0 GATES  (run BEFORE any LLM/vision spend; reuse `acceptance_gate.py`)
Any one trips → FAIL, name it, stop (no LLM cost on a broken artifact):
- **G0.1 SURFACED & PLAYABLE** — fetch the exact URL the watch page reads (`visual.video_url`). A bare
  `gs://` / empty field = FAIL (rendered-but-not-surfaced class).
- **G0.2 ORIENTATION/ASPECT** — ffprobe true WxH; orientation(each authored clip) == orientation(render
  target). A 16:9 clip crop-filled into 9:16 (edges CUT) = FAIL.
- **G0.3 NO PERSISTENT LETTERBOX BAND; no high-contrast/text pixels inside the edge safety margin.**
- **G0.4 AUDIBLE ENDING** — whisper the last ~14s; the script's final words present + audible (end-decay class).
- **G0.5 DURATION** — measured ≥ ~60% of requested (undershoot class).
- **G0.6 CAPTIONS PRESENT & LEGIBLE** (shorts) — burned-in, ≤2 lines, ≤~45 chars/line, readable within dwell.

## LAYER 1 — HARD CONTENT GATES  (auto-fail; NOT weighted-average contributors)
GATES precisely because the 33% off-topic bug shipped when aptness was a WEIGHT a well-composed frame
out-voted. Any one = reject:
- **H1 RELEVANCE / ON-TOPIC** (own hard gate) — per-beat, the visual + narration match the SPOKEN line
  and the user's actual ASK (not the title). Method: reverse-generate the questions this artifact
  answers; they must match the request. Off-topic scene or wrong-entity = FAIL.
- **H2 FACTUAL TRUTH** — decompose narration into atomic claims; each supported/true; no fabricated
  fact/citation/regulation.
- **H3 NUMERIC/CHART INTEGRITY** — every chart label traces to the research corpus AND the arithmetic is
  internally consistent (percentages sum to 100, totals possible). A fabricated authoritative chart is
  higher-severity than a text card.
- **H4 NO GARBLED/BAKED-IN IMAGE TEXT** — real diagrams are crisp mermaid/HTML, not AI-image text.

## LAYER 2 — THE 8-AXIS SCORECARD  (reuse `short_scorecard.py` / `quality_matrix.py`)
hook_stop_power · substance · visual_truth · modality_fit · motion · sync_exactness · audio_feel · cost.
0–5 (or the scorecard's 0–100). Any axis in the failing floor (≈3.5–5/10) = fail; target consistent 8+/10.

## PER-PERSONA VERDICT  (never one blend)
Each routed persona emits: its own weighted score + its SINGLE most-damaging critique + a binary
`ship / reject` answering ITS `verdict_question`. The artifact SHIPS only if EVERY routed persona
ships AND all Layer-0/Layer-1 gates pass AND a final fresh-context adversary can't refute.

## NOVICE-PROTECTION ASYMMETRY (system default)
False-confident-WRONG is penalized far above merely-boring. When in doubt → FAIL.
