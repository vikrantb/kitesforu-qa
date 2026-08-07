"""The audit must report the EXACT lag, not only the fuzzy one, and always with its denominator.

2026-08-07: `card_provenance_lag` (exact, verbatim-only, built in qa #105 precisely so a fix could
be driven by a trustworthy number) was computed at narration_sync_audit.py:116, stored in the
result dict as "provenance" — and NEVER PRINTED. Every reported "median shown-vs-spoken lag",
including the 7423ms figure that scoped an entire campaign, was silently `shown_words_lag`: a
SIMILARITY match over the enrichment's `on_screen_text`, which is a PARAPHRASE of the spoken line.

Thresholded similarity is the exact failure mode that made workers #2153 anchor 498/978 beats to
narration mentioning nothing of them (precision 89% -> 61%). Reporting it as if it were the exact
metric inverted the one safeguard that was built.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "narration_sync_audit.py"


def _src() -> str:
    return SCRIPT.read_text()


class TestBothLagsAreReported:
    def test_the_exact_metric_is_rendered_IN_THE_PER_JOB_ROW(self):
        """THE BUG: `prov` was computed and thrown away.

        Anchored to the PER-ROW block specifically. An earlier version of this pin matched
        `r['provenance']` anywhere in the file — which the FLEET summary also contains — so
        deleting the row-level render still passed it. A pin that cannot fail is not a pin
        (the #2151 adversary caught that same shape three times).
        """
        src = _src()
        start = src.index("for r in results:")
        row_block = src[start : src.index("if args.verbose:", start)]
        assert "provenance" in row_block, (
            "the per-job row does not render the exact lag — only the fuzzy one is visible per job"
        )
        assert "exact" in row_block

    def test_the_fleet_summary_prints_the_exact_lag_too(self):
        assert "EXACT  lag" in _src()

    def test_the_fuzzy_lag_is_labelled_as_fuzzy(self):
        """A number labelled only 'lag' gets quoted downstream as if it were exact."""
        src = _src()
        assert "FUZZY  lag" in src
        assert "similarity" in src

    def test_every_lag_median_carries_its_denominator(self):
        """A median states a fact about the rows that CONTRIBUTED. Both must show n=x/y."""
        src = _src()
        for marker in ("FUZZY  lag", "EXACT  lag"):
            i = src.index(marker)
            window = src[i : i + 400]
            assert re.search(r"n=\{?len\(", window), f"{marker} printed without its denominator"

    def test_the_no_data_branch_explains_why_rather_than_printing_nothing(self):
        """An absent exact metric must not read as 'no lag' — card_text only ships from #2155."""
        src = _src()
        assert "no data" in src and "#2155" in src

    def test_a_fuzzy_only_result_warns_which_number_to_trust(self):
        src = _src()
        assert "TRUST THE EXACT ROW" in src and "#2153" in src
