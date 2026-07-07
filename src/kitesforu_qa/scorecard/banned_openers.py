"""Banned wind-up openers — MIRRORS the prompt's own list so the scorecard can score the hook.

SSOT of this list is the prompt itself:
``kitesforu-workers/src/workers/prompts/content_types/short_form/narration_rules.yaml``
(the ``hook_first_line`` block: "BANNED WIND-UP OPENERS … throat-clearing that leads UP to the
point instead of BEING it"). kitesforu-qa is a separate repo and does not import the workers, so the
list is mirrored here with the source cited (same pattern as ``harness/checks/cost.py`` mirroring the
workers' TTS rates). If the prompt's list changes, update this file in lockstep.

Two families:
  * WIND-UP openers — throat-clearing PREFIXES a hook must not start with ("Imagine this…",
    "Have you ever…", "In this video…").
  * SUBORDINATE-CLAUSE openers — a hook may not open on a dangling dependent clause ("Despite…",
    "Although…") that reads as broken when the line is trimmed to fit.
"""
from __future__ import annotations

import re

# Wind-up / throat-clearing openers (from narration_rules.yaml + the zero_pleasantries block).
BANNED_WINDUP_OPENERS: tuple[str, ...] = (
    "imagine this",
    "picture this",
    "have you ever",
    "you wake up",
    "it's a universal experience",
    "its a universal experience",
    "we've all",
    "weve all",
    "let me tell you",
    "in this video",
    "in this short",
    "today we're going to",
    "today were going to",
    "let's talk about",
    "lets talk about",
    "let's dive in",
    "lets dive in",
    "in this one",
    "welcome",
    "hey guys",
    "what's up",
    "whats up",
)

# Subordinate-clause openers a self-contained hook must not START with (narration_rules.yaml).
SUBORDINATE_CLAUSE_OPENERS: tuple[str, ...] = (
    "despite",
    "although",
    "while",
    "because",
    "even though",
    "whereas",
    "since",
)

_LEADING_JUNK = re.compile(r"^[\s\"'“”‘’\-–—.,:;(]+")


def _normalize(line: str) -> str:
    """Lowercase, strip leading quotes/punctuation, and collapse curly apostrophes so the phrase
    match is robust to the ways a hook line is quoted/typeset."""
    s = _LEADING_JUNK.sub("", (line or "").strip().lower())
    return s.replace("’", "'").replace("‘", "'")


def banned_opener_hit(line: str) -> str | None:
    """Return the banned opener the hook line starts with (wind-up prefix OR subordinate clause),
    or ``None`` when the line is a clean, self-contained opener.

    Matching is anchored to the START of the line: a banned phrase appearing mid-sentence is not a
    wind-up opener. Subordinate-clause openers match only as the first WORD (word-boundary), so a
    legitimate use mid-hook ("...while everyone watched") is not flagged."""
    norm = _normalize(line)
    if not norm:
        return None
    for phrase in BANNED_WINDUP_OPENERS:
        p = phrase.replace("’", "'")
        if norm.startswith(p):
            return phrase
    first_word = re.split(r"[\s,]", norm, maxsplit=1)[0]
    for clause in SUBORDINATE_CLAUSE_OPENERS:
        # "even though" is two words — compare against the whole normalized prefix, others by word.
        if " " in clause:
            if norm.startswith(clause + " "):
                return clause
        elif first_word == clause:
            return clause
    return None
