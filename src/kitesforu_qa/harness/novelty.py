"""Novelty timeline — what a human EXPERIENCES over the runtime, not what a model reads.

THE GAP THIS EXISTS TO CLOSE (founder, 2026-07-27): an LLM judge is handed frames and asks
"do these differ?"; a human SITS THROUGH a duration and asks "when does something new happen?".
Job eaf99101 passed every model-side check — frames measurably differed (a slow zoom) — and the
founder's verdict on the same artifact was "a boring screen and same image dancing".

The difference is TEMPORAL and it is arithmetic, not perception. These functions compute, at $0
from the clip list alone, the facts a human would report:

    "68% of this video you are looking at a picture you have already seen"
    "the longest you go without a NEW image is 102 seconds"

Those are human-meaningful sentences AND exact numbers. They are meant to be fed to a persona
judge as GROUND TRUTH rather than trusted to model perception — the persona reasons about whether
that is acceptable for its audience, it does not have to (and must not) estimate it.
"""

from __future__ import annotations

from typing import Any, Sequence


def _key(clip: dict[str, Any]) -> str:
    """Identity of the picture on screen. content_hash is authoritative (identical specs dedupe
    to identical pixels); asset_uri is the pre-hash fallback. Empty means NO picture — such a
    clip is excluded rather than treated as a distinct or repeated image."""
    return str(clip.get("content_hash") or clip.get("asset_uri") or "")


def _seconds(clip: dict[str, Any]) -> float:
    for field in ("duration_ms", "duration_millis"):
        v = clip.get(field)
        if isinstance(v, (int, float)) and v > 0:
            return float(v) / 1000.0
    v = clip.get("duration_s") or clip.get("duration")
    return float(v) if isinstance(v, (int, float)) and v > 0 else 0.0


def novelty_timeline(clips: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Per-runtime novelty facts.

    Returns:
        total_s              runtime covered by clips that carry a picture
        distinct             count of distinct pictures
        repeat_share         fraction of runtime spent on a picture already seen earlier
        longest_stale_s      longest continuous stretch with no NEW picture introduced
        longest_same_image_s longest continuous stretch showing ONE unchanging picture
        gaps_s               seconds between successive FIRST appearances (the "every N seconds"
                             the founder asks about), in order
    """
    seen: set[str] = set()
    total = 0.0
    repeat = 0.0
    stale = 0.0            # running time since the last NEW picture
    longest_stale = 0.0
    same_run = 0.0         # running time on the current unchanging picture
    longest_same = 0.0
    prev_key = None
    gaps: list[float] = []
    since_new = 0.0

    for clip in clips or []:
        key = _key(clip)
        secs = _seconds(clip)
        if not key or secs <= 0:
            continue
        total += secs

        is_new = key not in seen
        if is_new:
            seen.add(key)
            gaps.append(round(since_new, 2))
            since_new = secs
            stale = secs
        else:
            repeat += secs
            stale += secs
            since_new += secs
        longest_stale = max(longest_stale, stale)

        # A run of the SAME picture across consecutive clips (the hold-cap re-cut case: one image
        # split into N sub-clips still reads as one unchanging image to a viewer).
        if key == prev_key:
            same_run += secs
        else:
            same_run = secs
        longest_same = max(longest_same, same_run)
        prev_key = key

    return {
        "total_s": round(total, 1),
        "distinct": len(seen),
        "repeat_share": round(repeat / total, 4) if total > 0 else 0.0,
        "longest_stale_s": round(longest_stale, 1),
        "longest_same_image_s": round(longest_same, 1),
        "gaps_s": gaps,
    }


def human_sentences(t: dict[str, Any]) -> list[str]:
    """The timeline restated the way a viewer would say it — this is what gets handed to a
    persona judge, because a persona reasons far better about a sentence than a dict."""
    out = [
        f"The video runs {t['total_s']:.0f} seconds and shows {t['distinct']} different pictures.",
        f"{t['repeat_share']:.0%} of the time you are looking at a picture you have already seen.",
        f"The longest you go without any NEW picture appearing is {t['longest_stale_s']:.0f} seconds.",
        f"The longest single unchanging picture is on screen for {t['longest_same_image_s']:.0f} seconds.",
    ]
    gaps = t.get("gaps_s") or []
    if gaps:
        worst = max(gaps)
        out.append(f"A new picture arrives every {sum(gaps)/len(gaps):.1f} seconds on average, "
                   f"but the worst wait is {worst:.0f} seconds.")
    return out
