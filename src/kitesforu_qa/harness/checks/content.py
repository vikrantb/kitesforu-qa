"""Per-genre content checks (Claude-main lane) — deterministic, $0.

Each genre is "100% different" (founder): an explainer wants worked examples + density; horror wants
rising dread + atmosphere; comedy wants comedic turns; bedtime wants calm + ZERO scary words;
tutorial wants ordered steps; drama wants conflict. These are the OBVIOUS high-value deterministic
checks per genre (keyword/structure over the script). The wlb8x39hx research catalog adds the long
tail + the judge-layer qualitative checks ("is the dread actually scary?").
"""
import re

from ..check import check, skip


def _genre_is(art, *names: str) -> bool:
    g = (art.genre or "").lower()
    ct = (art.content_type or "").lower()
    return any(n in g or n in ct for n in names)


def _count(text: str, words) -> int:
    t = text.lower()
    return sum(t.count(w) for w in words)


# ── horror ───────────────────────────────────────────────────────────────────
@check("horror.has_atmosphere", dimension="content", genre="horror", severity="high")
def horror_atmosphere(art):
    "Horror must build dread with sensory/tension language, not flat narration."
    if not _genre_is(art, "horror"):
        skip("not horror")
    n = _count(art.script_text, ("dark", "shadow", "cold", "silence", "whisper", "creak",
                                 "blood", "fear", "dread", "alone", "footsteps", "breath", "chill"))
    return n >= 3, min(1.0, n / 6), f"{n} atmosphere markers"


@check("horror.no_comic_deflation", dimension="content", genre="horror", severity="medium")
def horror_no_comedy(art):
    "Horror should not puncture the dread with comedy/banter."
    if not _genre_is(art, "horror"):
        skip("not horror")
    n = _count(art.script_text, ("haha", "lol", "just kidding", "hilarious", "so funny"))
    return n == 0, f"{n} comic-deflation markers"


# ── comedy ───────────────────────────────────────────────────────────────────
@check("comedy.has_comedic_turns", dimension="content", genre="comedy", severity="high")
def comedy_turns(art):
    "Comedy needs comedic structure — punchy turns/reversals, not a dry lecture."
    if not _genre_is(art, "comedy"):
        skip("not comedy")
    n = _count(art.script_text, ("but then", "turns out", "plot twist", "the thing is",
                                 "wait,", "actually,", "ridiculous", "absurd", "of course"))
    return n >= 2, f"{n} comedic-turn markers"


# ── bedtime ──────────────────────────────────────────────────────────────────
_SCARY = ("blood", "death", "kill", "scream", "monster", "terror", "violent", "gun", "war", "die")


@check("bedtime.no_scary_words", dimension="content", genre="bedtime", severity="critical")
def bedtime_no_scary(art):
    "A bedtime story must NOT contain scary/violent words (the hardest invariant for this genre)."
    if not _genre_is(art, "bedtime"):
        skip("not bedtime")
    t = art.script_text.lower()
    # WORD-BOUNDARY match — "war" must not fire on "warm", "die" not on "studied" (a critical
    # check can't false-positive). This bug was caught by the self-test (warm cozy bed).
    hits = [w for w in _SCARY if re.search(rf"\b{re.escape(w)}\b", t)]
    return len(hits) == 0, (f"scary words: {hits}" if hits else "none")


@check("bedtime.calm_tone", dimension="content", genre="bedtime", severity="medium")
def bedtime_calm(art):
    "Bedtime should use soothing/calm language."
    if not _genre_is(art, "bedtime"):
        skip("not bedtime")
    n = _count(art.script_text, ("gently", "softly", "sleepy", "quiet", "warm", "cozy",
                                 "dream", "peaceful", "slowly", "calm", "snuggle"))
    return n >= 2, f"{n} soothing markers"


# ── tutorial / how-to ────────────────────────────────────────────────────────
@check("tutorial.has_steps", dimension="content", genre="tutorial", severity="high")
def tutorial_steps(art):
    "A tutorial should be stepwise (first/next/then/finally)."
    if not _genre_is(art, "tutorial", "how-to", "how to"):
        skip("not a tutorial")
    n = _count(art.script_text, ("first", "next", "then", "after that", "finally", "step "))
    return n >= 3, f"{n} step markers"


# ── news ─────────────────────────────────────────────────────────────────────
@check("news.has_attribution", dimension="content", genre="news", severity="medium")
def news_attribution(art):
    "News should attribute claims (according to / reported / officials)."
    if not _genre_is(art, "news"):
        skip("not news")
    n = _count(art.script_text, ("according to", "reported", "sources say", "officials",
                                 "announced", "confirmed", "said"))
    return n >= 1, f"{n} attribution markers"


# ── drama ────────────────────────────────────────────────────────────────────
@check("drama.has_conflict", dimension="content", genre="drama", severity="high")
def drama_conflict(art):
    "Drama needs emotional conflict / stakes."
    if not _genre_is(art, "drama"):
        skip("not drama")
    markers = ("but", "however", "afraid", "wanted", "couldn't", "won't", "can't", "secret",
               "betray", "lost", "fight", "tears", "gun", "kill", "dead", "arrest", "threat",
               "blood", "bury", "burn", "weapon", "desperate", "danger", "trapped", "lie")
    t = art.script_text.lower()
    hit = {m for m in markers if re.search(rf"\b{re.escape(m)}\b", t)}
    return len(hit) >= 2, f"{len(hit)} distinct conflict/stakes markers"


# ── shared: AI-tells (every genre) ───────────────────────────────────────────
_AI_TELLS = (
    "as an ai", "i cannot", "language model", "i'm just an ai", "delve into",
    "it's important to note", "in conclusion,", "tapestry of", "navigating the",
    "in today's fast-paced", "it's worth noting",
)


@check("content.no_ai_tells", dimension="content", severity="high")
def no_ai_tells(art):
    "No AI-tell phrases (delve into / it's important to note / as an AI / tapestry of ...)."
    hits = [p for p in _AI_TELLS if p in art.script_text.lower()]
    return len(hits) == 0, (f"AI-tells: {hits}" if hits else "none")
