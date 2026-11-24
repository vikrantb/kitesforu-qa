"""Finds the frame edges that a faint straight edge of the picture runs into: content the frame cuts.

The acceptance gate's edge rule counts steps of more than 28 grey levels inside the 3% margin. A frosted-glass
card on a dark slide has edges only 14 to 20 levels above the background, so a push-in that carries such a card
off the frame scored 0 on every edge: the last frame of a 16:9 explainer showed a card cut at the right edge and
every card cut at the bottom, and the gate saw nothing.

Lowering that threshold is not the fix: at 14 levels it flags 117 more of 656 stored gate frames, mostly
photographs and illustrations whose texture crosses every margin. What sets a cut card apart from texture is
that its edge is STRAIGHT and runs through the whole margin: one row (for the left and right margins) or one
column (for the top and bottom) where at least :data:`LINE_SPAN` of the margin steps by more than
:data:`FAINT_STEP` levels. Texture steps in scattered pixels, never along a full line of the margin.

A line that crosses the WHOLE frame is a seam (the edge of a band, a full-width rule), not a cut: it is left to
:mod:`kitesforu_qa.edge_band`, and not counted here.

Measured on the same 656 frames: the faint rule finds an edge the 28-level rule does not on 35 frames. One is the
cut last frame above; the other 34, each opened by eye, are photographs and illustrations, which the gate exempts
from the edge rule by the clip's modality.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# The share of the WHOLE frame a step must span to count as a seam rather than a cut: edge_band's own, so a step
# this module skips as a seam is one edge_band reports as a band.
from kitesforu_qa.edge_band import SEAM_SPAN

#: The step, in grey levels, that a faint edge clears. The cut frosted-glass card measured 14 to 20; a slide's
#: background grid measured under 6.
FAINT_STEP = 12.0
#: The share of the margin a line must step across to count as a straight edge running into the frame edge.
LINE_SPAN = 0.8
#: The margin, as a share of the frame side: the gate's own 3%.
MARGIN = 0.03
#: Lines either side of a seam that a scaled frame blurs into it.
_SEAM_BLUR_ROWS = 2


def _cut_lines(lines: Any, margin: int) -> int:
    """How many rows of ``lines`` (an H x W grey array whose last ``margin`` columns lie on the frame edge) carry
    a straight step across the margin that is not part of a full-width seam."""
    steps = np.abs(np.diff(lines, axis=0)) > FAINT_STEP
    across_margin = steps[:, -margin:].mean(axis=1) >= LINE_SPAN
    span = 2 * _SEAM_BLUR_ROWS
    seam = np.zeros(steps.shape[0], dtype=bool)
    if lines.shape[0] > span:
        wide = (np.abs(lines[span:] - lines[:-span]) > FAINT_STEP / 2).mean(axis=1) >= SEAM_SPAN
        for offset in range(span + 1):  # mark every step row the blurred seam covers
            seam[offset:offset + wide.shape[0]] |= wide[:steps.shape[0] - offset]
    return int((across_margin & ~seam).sum())


def faint_cut_edges(gray: Any) -> frozenset[str]:
    """The edges of ``gray`` (an H x W array of grey levels) that a faint straight edge runs into."""
    height, width = gray.shape
    side_margin = max(3, int(width * MARGIN))
    end_margin = max(3, int(height * MARGIN))
    oriented = {"right": (gray, side_margin), "left": (gray[:, ::-1], side_margin),
                "bottom": (gray.T, end_margin), "top": (gray.T[:, ::-1], end_margin)}
    return frozenset(edge for edge, (lines, margin) in oriented.items() if _cut_lines(lines, margin))
