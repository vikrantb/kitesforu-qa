"""Explainer / educational content long-tail (Claude-main lane) — deterministic, $0.

From the wlb8x39hx CHECK-CATALOG (the exp-c-* / edu-content-* battery), grounded in the workers'
content_types/educational rules. Genre 'explainer' (alias-aware → also 'educational'). These encode
the misconception_revelation arc + teacher-beside-not-above voice + no-NPR-meta the catalog defined.
"""
import re

from ..check import check, skip


def _is_explainer(art) -> bool:
    g = (art.genre or "").lower()
    ct = (art.content_type or "").lower()
    return any(k in g or k in ct for k in ("explain", "educational", "tutorial"))


def _text(art) -> str:
    # skip() is NoReturn, so this always returns a non-empty str or skips.
    if not _is_explainer(art):
        skip("not an explainer/educational piece")
    t = art.script_text
    if not t.strip():
        skip("no script text")
    return t


# ── opening craft ────────────────────────────────────────────────────────────
@check("explainer.no_today_we_discuss_opening", dimension="content", genre="explainer", severity="high")
def no_today_opening(art):
    "Opening must NOT be the flat 'today we'll discuss/learn about X' frame (catalog exp-c-no-today)."
    head = (_text(art)[:300]).lower()
    bad = bool(re.search(r"\btoday (we('| wi)ll|we are going to)\b|\bwe('| wi)ll (discuss|learn about|cover|talk about)\b", head))
    return not bad, "opens with a flat 'today we'll discuss' frame" if bad else "ok"


@check("explainer.opening_hooks_curiosity", dimension="content", genre="explainer", severity="medium")
def opening_hooks(art):
    "Opening should name a confusion / question / 'you've probably' hook (catalog exp-c-open-confusion)."
    head = (_text(art)[:400]).lower()
    ok = bool(re.search(r"\?|most people|you('| ha)ve probably|ever wondered|sounds (impossible|crazy)|here'?s the (weird|strange)", head))
    return ok, "opening hooks curiosity" if ok else "opening states the topic without a hook"


# ── the misconception_revelation arc ─────────────────────────────────────────
@check("explainer.surfaces_misconception", dimension="content", genre="explainer", severity="medium")
def surfaces_misconception(art):
    "A good explainer surfaces a WRONG model before correcting it (catalog exp-c-misconception)."
    t = _text(art).lower()
    ok = bool(re.search(r"most people (think|assume|believe)|you might (think|assume)|common misconception|it'?s tempting to|turns out|but actually|here'?s the (catch|twist)", t))
    return ok, "surfaces a misconception" if ok else "no wrong-model-before-correction"


@check("explainer.lands_takeaway", dimension="content", genre="explainer", severity="medium")
def lands_takeaway(art):
    "Lands a carry-away handle near the close (catalog exp-c-takeaway)."
    t = _text(art)
    close = " ".join(t.split()[-80:]).lower()
    ok = bool(re.search(r"takeaway|next time you|you can now|rule of thumb|remember this|if you remember nothing else|the one thing", close))
    return ok, "lands a takeaway" if ok else "no carry-away handle near the close"


# ── voice: teacher beside, not above ─────────────────────────────────────────
_CONDESCENSION = ("as we all know", "everyone knows that", "it's obvious that", "obviously,",
                  "needless to say", "any fool can see", "as you surely know")
_UNEARNED_AUTH = ("the science is settled", "there is no debate", "trust me", "take it from me",
                  "scientists have long known", "it is a well known fact")


@check("explainer.no_condescension", dimension="content", genre="explainer", severity="high")
def no_condescension(art):
    "Teacher BESIDE not above — no condescension phrases (catalog exp-c-condescension)."
    t = _text(art).lower()
    hits = [p for p in _CONDESCENSION if p in t]
    return not hits, (f"condescension: {hits}" if hits else "ok")


@check("explainer.no_unearned_authority", dimension="content", genre="explainer", severity="high")
def no_unearned_authority(art):
    "No overclaimed certainty / unearned authority (catalog exp-c-authority)."
    t = _text(art).lower()
    hits = [p for p in _UNEARNED_AUTH if p in t]
    return not hits, (f"unearned authority: {hits}" if hits else "ok")


# ── anti-meta + anti-filler ──────────────────────────────────────────────────
@check("explainer.no_npr_meta", dimension="content", genre="explainer", severity="high")
def no_npr_meta(art):
    "Not an NPR meta-panel about itself — no 'welcome to the show / your hosts' (catalog exp-c-meta)."
    t = _text(art).lower()
    hits = [p for p in ("welcome to the show", "welcome back to", "in this episode", "we're your hosts",
                        "thanks for tuning in", "thanks for listening", "on today's episode") if p in t]
    return not hits, (f"NPR-meta: {hits}" if hits else "ok")


@check("explainer.no_vacuous_conclusion", dimension="content", genre="explainer", severity="low")
def no_vacuous_conclusion(art):
    "No vacuous 'in conclusion' close (catalog exp-c-no-conclusion)."
    close = " ".join(_text(art).split()[-40:]).lower()
    bad = "in conclusion" in close or "to sum up," in close or "in summary," in close
    return not bad, "vacuous in-conclusion close" if bad else "ok"


@check("explainer.no_fun_fact_decoration", dimension="content", genre="explainer", severity="low")
def no_fun_fact(art):
    "Facts integrated, not announced as decoration — no 'fun fact:' (catalog exp-c-funfact)."
    t = _text(art).lower()
    bad = "fun fact:" in t or "fun fact —" in t or "fun fact -" in t
    return not bad, "fun-fact decoration" if bad else "ok"


# ── precision ────────────────────────────────────────────────────────────────
@check("explainer.example_has_numbers", dimension="content", genre="explainer", severity="medium")
def example_has_numbers(art):
    "A worked example should carry concrete numbers, not stay abstract (catalog exp-c-example-numbers)."
    t = _text(art)
    # only meaningful if an example marker is present
    if not re.search(r"for example|for instance|imagine|suppose|let'?s say|say you", t, re.I):
        skip("no worked-example marker to check for numbers")
    has_num = bool(re.search(r"\d[\d,\.]*\s*(%|percent|ms|seconds?|minutes?|hours?|x|times|million|billion|thousand|gb|mb|kb)?", t))
    return has_num, "example carries numbers" if has_num else "example stays abstract (no numbers)"


@check("explainer.stays_on_topic", dimension="content", genre="explainer", severity="critical")
def stays_on_topic(art):
    "The script must actually be about the requested topic (catalog exp-c-topic / outline_relevance)."
    topic = (art.topic or "").lower()
    if not topic:
        skip("no topic recorded")
    stop = {"how", "a", "an", "the", "of", "to", "is", "are", "what", "why", "does", "work", "works", "and", "in", "for"}
    words = [w for w in re.findall(r"[a-z]{4,}", topic) if w not in stop]
    if not words:
        skip("topic has no content words")
    t = art.script_text.lower()
    hits = sum(1 for w in words if w in t)
    ratio = hits / len(words)
    return ratio >= 0.4, f"{hits}/{len(words)} topic content-words present ({ratio:.0%})"



@check("explainer.grounded_research_or_brief", dimension="content", genre="explainer", severity="medium")
def grounded_research_or_brief(art):
    "A foundational explainer should be GROUNDED — have web research_results OR an LLM knowledge "
    "brief, not improvise from raw model weights (verifiable via stages.llm_brief)."
    if not _is_explainer(art):
        skip("not an explainer/educational piece")
    has_research = bool(art.doc.get("research_results"))
    chars = (art.llm_brief or {}).get("brief_chars") or 0
    return (has_research or chars > 0), f"research_results={has_research}, llm_brief_chars={chars}"
