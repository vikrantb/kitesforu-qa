"""Finds a flat band that a full-width seam separates from the picture, along each edge of a frame.

A band is what a viewer sees when the picture does not fill the frame: a letterbox bar, a pillarbox bar, or a
strip of exposed canvas where a slide was shifted or rendered short. Two things make one, and both are needed:

* **flat**: every line of the strip, from the frame edge inward, is one colour across the frame (row spread at
  most :data:`FLAT_ROW_SPREAD` levels) and stays near the edge line's level (:data:`FLAT_LEVEL_DRIFT`). The band
  may be any colour: a black bar, a lighter canvas, a dark navy strip;
* **seamed**: where the strip stops, the picture beside it differs from it in at least :data:`SEAM_SPAN` of the
  columns. The picture's edge runs the full width of the frame.

The rule this replaces asked for a dark band at the top AND the bottom, darker than the middle by 16 levels, on
half the frames. It could not see a band on one side or a band lighter than the picture, and it read a dark
full-frame card (dark margins above and below centred text) as a letterbox: the dark margins of a card are flat,
but no seam runs across the frame where they end, because the text covers only part of the width.

Measured on 656 stored gate frames of real jobs (35 frame sets from five release batteries and four hero reviews,
540 px wide), with the thresholds below: 15 frames in 6 sets show a band, and every one, opened by eye, is a real
band (canvas exposed below a shifted or short diagram, on 16:9 and on 9:16; a black bar under a picture). The three
sets the old rule called letterboxed are dark full-frame text cards with no bar, and show none. A content box inset
18 px from each side reaches 0.93 of the columns and is no band. 9 of the 10 frames of a padded letterbox control
show both bars.
"""
from __future__ import annotations

from typing import Any

import numpy as np

#: A band line's spread across the frame, in grey levels (standard deviation). A real canvas band measured 2.5 to
#: 4.1 levels (a soft gradient across it); 6 keeps those and still refuses a line that crosses text or a picture.
FLAT_ROW_SPREAD = 6.0
#: How far a band line's mean may drift from the edge line's, in grey levels: a band is one strip, not a ramp.
FLAT_LEVEL_DRIFT = 6.0
#: The step, in grey levels, between the picture and the band that counts a column as seamed.
SEAM_STEP = 8.0
#: The share of columns that must be seamed. The picture's edge spans the frame; a box inset from the sides does
#: not (measured 0.93 for an 18 px inset on a 540 px frame; every real band measured 0.99 or more).
SEAM_SPAN = 0.97
#: A band's thickness as a share of the frame side it lies across. Under 3% is a hairline or an encoder edge;
#: over 45% is a frame that is mostly flat (a fade to black, an empty card), not a picture with a band.
MIN_THICKNESS, MAX_THICKNESS = 0.03, 0.45
#: Rows between the band's last flat line and the lines compared across the seam: a scaled frame blurs the seam
#: over about four rows.
_SEAM_BLUR_ROWS = 2

SIDES = ("top", "bottom", "left", "right")


def _band_thickness(lines: Any) -> float:
    """The thickness, as a share of the side, of the band at the END of ``lines`` (an H x W grey array whose last
    row lies on the frame edge), or 0.0 when there is none."""
    height = lines.shape[0]
    spreads, levels = lines.std(axis=1), lines.mean(axis=1)
    edge_level = levels[height - 1]
    flat = 0
    for row in range(height - 1, -1, -1):
        if spreads[row] > FLAT_ROW_SPREAD or abs(levels[row] - edge_level) > FLAT_LEVEL_DRIFT:
            break
        flat += 1
    thickness = flat / height
    if not MIN_THICKNESS <= thickness <= MAX_THICKNESS:
        return 0.0
    first_picture_row = height - 1 - flat
    picture = first_picture_row - _SEAM_BLUR_ROWS
    band = min(height - 1, first_picture_row + 2 * _SEAM_BLUR_ROWS)
    if picture < 0:
        return 0.0
    seamed = float((np.abs(lines[picture] - lines[band]) > SEAM_STEP).mean())
    return thickness if seamed >= SEAM_SPAN else 0.0


def edge_bands(gray: Any) -> dict[str, float]:
    """``{side: thickness}`` for every side of ``gray`` (an H x W array of grey levels) that carries a band, the
    thickness as a share of the frame's height (top, bottom) or width (left, right)."""
    oriented = {"bottom": gray, "top": gray[::-1], "right": gray.T, "left": gray.T[::-1]}
    found = {side: _band_thickness(oriented[side]) for side in SIDES}
    return {side: round(t, 3) for side, t in found.items() if t}
