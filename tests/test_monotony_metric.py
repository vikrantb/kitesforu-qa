"""Monotony metric (Step F) — flags the 1.0/5 corpus, passes a distinct-per-beat short."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from checks.monotony_metric import monotony_metric


def _clips(distinct, n, mode="still"):
    """n clips drawn from `distinct` unique assets (round-robin), all render_mode=mode."""
    return [{"asset_uri": f"gs://a/{i % distinct}.png", "render_mode": mode} for i in range(n)]


# ── the 5 real corpus shorts (hero-user avg 1.0/5) — ALL must be flagged monotone ──
def test_corpus_all_flagged():
    corpus = [
        ("jet", 2, 3, 1), ("backprop", 2, 3, 2), ("mllife", 5, 7, 1),
        ("sunset", 5, 3, 3), ("protein", 6, 12, 2),
    ]
    for name, beats, nclips, distinct in corpus:
        m = monotony_metric(_clips(distinct, nclips), beat_count=beats, rendered_motion_clips=0)
        assert m["is_monotone"], f"{name} must be flagged monotone: {m}"
        assert m["distinct_asset_count"] == distinct
        assert m["onscreen_motion_count"] == 0


# ── a real short: one distinct MOVING visual per beat — must PASS ──
def test_distinct_per_beat_short_passes():
    m = monotony_metric(_clips(6, 6, mode="kinetic_type"), beat_count=6)
    assert not m["is_monotone"], m
    assert m["distinct_asset_count"] == 6 and m["onscreen_motion_count"] == 6


def test_distinct_but_no_motion_still_flags():
    # 6 distinct assets but all stills (Ken-Burns) → still monotone (no real motion)
    m = monotony_metric(_clips(6, 6, mode="parallax_2_5d"), beat_count=6)
    assert m["is_monotone"] and "0 real motion" in " ".join(m["reasons"])


def test_two_distinct_is_slideshow():
    m = monotony_metric(_clips(2, 8, mode="kinetic_type"), beat_count=8)
    assert m["is_monotone"] and "distinct" in " ".join(m["reasons"])


def test_empty_clips_is_monotone():
    assert monotony_metric([], beat_count=5)["is_monotone"]
