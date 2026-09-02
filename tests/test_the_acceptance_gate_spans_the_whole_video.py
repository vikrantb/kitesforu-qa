"""The acceptance gate must score the FULL duration, not the first half of it.

`.claude/rules/02-done.md` relies on this gate precisely because it "scores every frame across the
full duration rather than one hero frame". It did not.

`step = max(1, len(frames) // 12)` FLOORS to 1 for any frame count in 13..23, and `[::1][:12]` then
takes the FIRST twelve. Measured 2026-09-02, before the fix::

    frames   step   window    coverage
      18      1     0..11       67%
      22      1     0..11       55%
      23      1     0..11       52%
      24      2     0..22       96%    <- only once the floor reaches 2

At the `fps=1/3` extraction the gate uses, that band is roughly 39-69 second videos — squarely
where real episodes land.

HOW IT WAS FOUND, because the method is the reusable part: job `0082f988` carries `maps_sequence`
at beats 6-7, which occupy 57.9-66.6s = frames 19..22 of 22. The gate returned a MAJOR edge-clip
verdict on that job — and that verdict said nothing whatever about the map frames, because they
were outside the sampled window. Correlating the finding back to the clip that authored it is what
exposed the gap; the verdict alone looked like a real answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _window(n: int):
    """The frames `_pixel_invariants` actually inspects — from the REAL function.

    An earlier version recomputed the stride here, which meant reverting the source left every
    parametrised case green: it tested a copy of the rule rather than the rule. Calling
    `_sample_indices` is the difference between a test and a restatement.
    """
    from acceptance_gate import _sample_indices

    return _sample_indices(n)


@pytest.mark.parametrize("n", [12, 13, 18, 22, 23, 24, 36, 48, 100, 240])
def test_the_sample_spans_at_least_90_percent_of_the_video(n: int):
    w = _window(n)
    coverage = (w[-1] + 1) / n
    assert coverage >= 0.90, (
        f"{n} frames -> checks {w[0]}..{w[-1]} = {coverage:.0%} of the video. The gate is scoring "
        f"a prefix, so anything in the tail — a cut-off close, a late map, a final card — is "
        f"invisible to it."
    )


@pytest.mark.parametrize("n", [13, 18, 22, 23])
def test_the_band_that_was_broken_is_the_one_that_regressed(n: int):
    """The specific counts where the old floor-division took only the first twelve frames.

    Kept separate from the sweep above so a future regression names the band rather than a generic
    coverage number.
    """
    old = list(range(n))[::max(1, n // 12)][:12]   # the FLOOR-division sampling, restated on purpose
    new = _window(n)
    assert old[-1] < n - 1, "premise: the OLD sampling really did stop short on this count"
    assert new[-1] > old[-1], f"{n} frames: sampling still stops at {new[-1]} of {n - 1}"


# `test_the_source_uses_ceiling_division` used to live here, asserting that
# `"-(-len(frames) // 12)"` appeared in `_pixel_invariants`' source text. Deleted 2026-09-02:
# once the arithmetic moved into `_sample_indices` and the sweep above began calling it, the
# source test was both redundant AND wrong (it inspected the function the code had left). Two
# review lanes flagged the same shape on a sibling PR the same day — a source-text assertion pins
# the SPELLING, not the behaviour, and goes red on the legitimate refactor it exists to protect.


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE HOLE THE #172 CODE-CRITIC LENS FOUND, and the reason it mattered.
#
# Every test above reads only `w[-1]` — how FAR the sample reaches. None reads how MANY frames it
# returns or whether they are spread. The reviewer proved it by mutating `_sample_indices` to
# `return sorted({0, n - 1})`: two frames for a 240-frame video, and all 14 tests stayed green.
# That is the very defect this file exists to catch — a gate that LOOKS like it observed the video
# and did not — reintroduced one axis over.
#
# It is not hypothetical: it is exactly what the first fix did. A ceiling stride reaches the end of
# the array but thins the sample to 7 frames of 13, and because both verdict thresholds derive from
# `len(samp)`, a thinner sample also makes the gate more trigger-happy. Coverage alone cannot see
# that. These two assert the axis the coverage tests are blind to.
# ══════════════════════════════════════════════════════════════════════════════════════════════

_WANT = 12


@pytest.mark.parametrize("n", list(range(1, 65)) + [80, 120, 199, 240])
def test_the_sample_never_declines_the_budget_it_asks_for(n: int):
    """A sample thinner than `want` is a gate inspecting less than it claims — and it drags
    `max(2, len(samp) // 2)` and `max(2, edge_checked // 3)` down with it. Goes red on the ceiling
    stride at n=13 (7 frames) and on any degenerate `{0, n-1}` sampler."""
    from acceptance_gate import _sample_indices

    w = _sample_indices(n, _WANT)
    assert len(w) == min(n, _WANT), (
        f"{n} frames -> only {len(w)} inspected of a {_WANT}-frame budget"
    )


@pytest.mark.parametrize("n", list(range(2, 65)) + [80, 120, 199, 240])
def test_the_sample_is_spread_and_not_clustered(n: int):
    """Reaching the last frame is not the same as covering the middle. `[0, n-1]` scores 100% on
    every coverage test above while inspecting two frames; this fails it. The largest permitted gap
    is one full stride — anything wider means a run of the video no frame represents."""
    from acceptance_gate import _sample_indices

    w = _sample_indices(n, _WANT)
    assert w == sorted(set(w)), f"{n} frames -> sample is not strictly increasing: {w}"
    # Even spacing across n-1 gaps in want-1 hops, so the widest step is ceil((n-1)/(want-1)) —
    # NOT ceil(n/want), which is the STRIDE's bound and is too tight here (n=240 gives 22, not 21).
    allowed = 1 if n <= _WANT else -(-(n - 1) // (_WANT - 1))
    widest = max(b - a for a, b in zip(w, w[1:]))
    assert widest <= allowed, (
        f"{n} frames -> widest gap {widest} > {allowed} between inspected frames; clustered"
    )
