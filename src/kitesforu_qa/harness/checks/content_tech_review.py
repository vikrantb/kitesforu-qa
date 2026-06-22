"""Tech-review content long-tail (Claude-main lane) — catalog tr-* battery, deterministic $0.

A good tech review is SPEC-grounded, not hype: named benchmarks + concrete numbers, zero unboxing
marketing-speak, no bare performance adjectives without a measurement (catalog tr-hype-* / tr-spec-*).
"""
import re

from ..check import check, skip


def _is_tech_review(art) -> bool:
    g = (art.genre or "").lower()
    ct = (art.content_type or "").lower()
    return "tech_review" in g or "tech review" in g or "review" in ct


_HYPE = (
    "game-changer", "game changer", "paradigm shift", "disruptor", "without compromise",
    "blows away", "blow you away", "mind-blowing", "next-level", "revolutionary", "insane",
    "unbelievable", "the best ever", "100x better", "buttery smooth",
)
_BENCH = ("geekbench", "cinebench", "3dmark", "antutu", "speedtest", "blender", "spec",
          "benchmark", "fps", "frames per second")


@check("tech_review.no_hype_phrases", dimension="content", genre="tech_review", severity="critical")
def tr_no_hype(art):
    "No unboxing/marketing hype-tells (game-changer, paradigm shift, mind-blowing) — the #1 tr fail."
    if not _is_tech_review(art):
        skip("not a tech review")
    hits = [p for p in _HYPE if p in art.script_text.lower()]
    return not hits, (f"hype phrases: {hits}" if hits else "ok")


@check("tech_review.has_spec_numbers", dimension="content", genre="tech_review", severity="high")
def tr_spec_numbers(art):
    "Spec-grounded: concrete spec/price number tokens present (not vibes-only)."
    if not _is_tech_review(art):
        skip("not a tech review")
    n = len(re.findall(
        r"\d[\d,\.]*\s*(%|gb|tb|mb|ghz|mhz|mp|mah|nits|hz|fps|ms|inch|mm|w\b|hours?)|\$\d",
        art.script_text, re.I,
    ))
    return n >= 3, f"{n} spec/price number tokens"


@check("tech_review.benchmark_named", dimension="content", genre="tech_review", severity="medium")
def tr_benchmark(art):
    "References a named benchmark/measurement, not just bare adjectives."
    if not _is_tech_review(art):
        skip("not a tech review")
    t = art.script_text.lower()
    return any(b in t for b in _BENCH), "benchmark/measurement referenced"
