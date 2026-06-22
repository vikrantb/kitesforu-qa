"""News content long-tail (Claude-main lane) — attribution + neutrality + recency, deterministic $0.

Straight news must ATTRIBUTE claims (according to / reported), stay NEUTRAL (no editorializing
adjectives), and be RECENT (a date/timeframe). Opinion/analysis genres are skipped.
"""
import re

from ..check import check, skip


def _is_news(art) -> bool:
    g = (art.genre or "").lower()
    ct = (art.content_type or "").lower()
    return "news" in g or "news" in ct or "current_events" in g


def _text(art) -> str:
    if not _is_news(art):
        skip("not a news piece")
    t = art.script_text
    if not t.strip():
        skip("no script text")
    return t


_EDITORIAL = (
    "outrageous", "shocking", "disgraceful", "appalling", "stunning", "incredible", "unbelievable",
    "shamefully", "disgusting", "heroic", "evil", "wonderful news", "terrible news",
)


@check("news.has_attribution", dimension="content", genre="news", severity="high")
def news_attribution(art):
    "Claims must be ATTRIBUTED (according to / reported / said / sources) — not stated as bare fact."
    t = _text(art).lower()
    cues = len(re.findall(r"according to|reported|sources? (say|said|told)|officials? said|\bsaid\b|announced|confirmed|statement", t))
    return cues >= 1, f"{cues} attribution cue(s)"


@check("news.no_editorializing", dimension="content", genre="news", severity="high")
def news_neutral(art):
    "Straight news stays NEUTRAL — no editorializing adjectives (outrageous, shocking, disgraceful)."
    t = _text(art).lower()
    hits = [w for w in _EDITORIAL if w in t]
    return not hits, (f"editorializing: {hits}" if hits else "neutral")


@check("news.has_recency_marker", dimension="content", genre="news", severity="medium")
def news_recency(art):
    "News should anchor a timeframe (today/this week/a weekday/a date/year)."
    t = _text(art).lower()
    ok = bool(re.search(r"today|yesterday|this (week|morning|year)|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\b20\d\d\b|just announced|breaking", t))
    return ok, "recency anchored" if ok else "no timeframe anchor"
