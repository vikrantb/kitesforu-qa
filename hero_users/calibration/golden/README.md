# Part B — the FOUNDER-GRADED golden set

The calibration README says the quiet part already: *"needs founder-graded items; without it,
harshness is unverifiable."* Part A (the rubber-stamp gate) proves a persona rejects a **planted**
defect. That is necessary and not sufficient, and the founder's standing complaint says why:

> "every time you say all good everything is fine and i see the quality is not good"

A planted defect is *verbal* — a statute that does not exist, a chart summing to 105%, a line
saying "as you can see here" in audio-only. An LLM catches those by **reading**. The artifacts the
founder actually rejects are usually not like that.

## The first item: `eaf99101_monotony.json`

Graded **REJECT** by the founder, 2026-07-27, verbatim:

> "the visuals were horrible, a boring screen and same image dancing. havent i told u i need to see
> a new fresh beautiful image every 2 seconds or zoom to some other part. see there has to be
> always something happening"

**Why this fixture is hard, and why it is the important one.** Nothing in it is factually wrong,
garbled, off-topic, or mismatched. The diagrams are correct. The narration is coherent. The audio
is fine. There is **no planted defect at all**. The only thing wrong is that it is *boring* — a
property of a DURATION, which a judge reading frames or a transcript structurally cannot perceive.

The measured ground truth (`novelty_timeline`, $0, from the clip list):

| | |
|---|---|
| runtime | 363.8s across 48 clips |
| distinct pictures | 29 |
| **runtime spent on an already-seen picture** | **67%** |
| longest wait for any NEW picture | 96s |
| longest single unchanging picture | 93s |
| new picture arrives | every 12.5s avg, worst wait 96s |

Founder's stated want: a fresh picture every ~2 seconds.

For scale: measured across 85 completed jobs, the **median** job puts 49% of its runtime on a
single picture and 95% on its top three. Eight-plus jobs show ONE picture for the entire video.
`eaf99101` is *better than the median* — the founder complained about an above-average episode.

## The protocol

1. The personas routed to this surface (`sofia`, `maya`, `marcus`, `aarav`) each critique it.
2. They are given the **measured novelty facts as ground truth**, never asked to estimate them —
   estimating temporal experience is exactly the perception an LLM does not have. The persona's job
   is to decide whether those facts are acceptable *for its audience*, which is judgment, not
   perception.
3. **Every routed persona must REJECT.** A persona that ships this is a rubber stamp, and the
   whole layer is worthless — that is the failure this set exists to detect.

## Adding items

Any artifact the founder grades — pass or fail — belongs here, especially **passes**. A set of only
known-bads trains a system that rejects everything, which is as useless as one that accepts
everything. Record the verbatim verdict; a paraphrase loses the signal.
