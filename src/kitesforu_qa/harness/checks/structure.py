"""Structure + script checks (Claude-main lane) — all deterministic ($0).

Genre-agnostic structural checks + explainer-specific content checks that encode the founder's
explainer DEEP-quality bar (density > length, substance > banter, real examples, top-down). These
are the TEMPLATE for the thousands the research synthesis will add — each check is one tiny function
returning bool | (bool, evidence) | (bool, score, evidence); raise skip() when N/A.
"""
import re

from ..check import check, skip

# ── genre-agnostic structure ────────────────────────────────────────────────


@check("structure.completed", dimension="structure", severity="critical")
def completed(art):
    "A verifiable job must be in a completed terminal state."
    return art.status == "completed", f"status={art.status}"


@check("structure.has_script", dimension="structure", severity="critical")
def has_script(art):
    "A completed job must have non-empty script text."
    return art.word_count > 0, f"{art.word_count} words"


@check("structure.segments_present", dimension="structure", severity="high")
def segments_present(art):
    "A rendered job should have ready segments (streaming path). Skip when the script is present "
    "but segments_ready is absent — older/non-streaming jobs render without that intermediate array."
    if not art.segments and art.word_count > 0:
        skip("script present but no segments_ready (non-streaming/older job)")
    return len(art.segments) > 0, f"{len(art.segments)} segments"


@check("structure.no_spoken_host_labels", dimension="structure", severity="high")
def no_spoken_host_labels(art):
    "Host1/Host2 must never appear in the SPOKEN dialogue text — that is the robotic bug the drama "
    "prompt warns against ('Host2, did you hear that?'). The speaker LABEL being 'Host1'/'Host2' is "
    "INTENTIONAL: it is a silent TTS voice-mapping id, never heard by the listener (verified 0 "
    "occurrences in real explainer audio). So we check the spoken TEXT, not the speaker field — "
    "flagging the field was an over-aggressive false positive (every genre uses these labels by "
    "design). Whether explainer hosts should be NAMED is a product/UX decision, tracked separately."
    spoken = len(re.findall(r"\bHost\s?[12]\b", art.script_text))
    return spoken == 0, f"{spoken} 'Host1/Host2' label(s) leaked into spoken text"


@check("structure.duration_adherence", dimension="structure", severity="medium")
def duration_adherence(art):
    "Audio duration within range of the requested duration (no gross undershoot, the 5min->91s class)."
    req = art.doc.get("inputs", {}).get("duration_min")
    if not req or not art.audio_duration_s:
        skip("no requested duration or no audio")
    req_s = float(req) * 60
    ratio = art.audio_duration_s / req_s if req_s else 1.0
    return ratio >= 0.6, min(1.0, ratio), f"audio {art.audio_duration_s:.0f}s vs req {req_s:.0f}s (ratio {ratio:.2f})"


@check("structure.no_empty_segments", dimension="structure", severity="medium")
def no_empty_segments(art):
    "No rendered segment should be empty (real segments_ready store text under text_preview)."
    empties = sum(
        1 for s in art.segments
        if isinstance(s, dict) and not str(s.get("text") or s.get("text_preview") or "").strip()
    )
    return empties == 0, f"{empties} empty segments"


# ── explainer content (the founder's deep-quality bar) ───────────────────────


def _is_explainer(art) -> bool:
    g = (art.genre or "").lower()
    ct = (art.content_type or "").lower()
    return "explain" in g or "educational" in g or "explain" in ct


@check("explainer.has_example", dimension="content", genre="explainer", severity="high")
def explainer_has_example(art):
    "An explainer must give at least one concrete/practical example, not pure abstraction."
    if not _is_explainer(art):
        skip("not an explainer")
    t = art.script_text.lower()
    n = t.count("for example") + t.count("for instance") + t.count("imagine") + t.count("say you") + t.count("think of") + t.count("like when")
    return n >= 1, min(1.0, n / 2), f"{n} concrete-example markers"


@check("explainer.defines_terms", dimension="content", genre="explainer", severity="medium")
def explainer_defines_terms(art):
    "An explainer should define/ground its terms (is/means/refers to/called/works by)."
    t = art.script_text.lower()
    n = t.count(" is ") + t.count(" means ") + t.count("refers to") + t.count("called") + t.count("works by")
    return n >= 2, f"{n} definition signals"


@check("explainer.density", dimension="content", genre="explainer", severity="medium")
def explainer_density(art):
    "Density > length: substantive words-per-minute (a sparse explainer is filler, not detail)."
    if not _is_explainer(art):
        skip("not an explainer")
    if not art.audio_duration_s:
        skip("no audio duration")
    wpm = art.word_count / (art.audio_duration_s / 60)
    return wpm >= 100, min(1.0, wpm / 140), f"{wpm:.0f} words/min"


@check("explainer.substance_over_banter", dimension="content", genre="explainer", severity="medium")
def explainer_substance_over_banter(art):
    "Substance > banter: the explanation must dominate; host pleasantries must not crowd it out."
    if not _is_explainer(art):
        skip("not an explainer")
    t = art.script_text.lower()
    banter = sum(t.count(p) for p in (
        "haha", "lol", "right?", "you know", "i mean", "totally", "for sure",
        "great question", "love it", "exactly", "so true", "absolutely",
    ))
    words = max(art.word_count, 1)
    ratio = banter / (words / 100)  # banter markers per 100 words
    return ratio <= 4, max(0.0, 1 - ratio / 8), f"{banter} banter markers over {words} words ({ratio:.1f}/100w)"


@check("explainer.top_down_opening", dimension="content", genre="explainer", severity="low")
def explainer_top_down_opening(art):
    "Top-down: the opening should frame WHAT the thing is before diving into mechanics."
    if not _is_explainer(art):
        skip("not an explainer")
    opening = " ".join(art.script_text.split()[:60]).lower()
    framed = any(k in opening for k in (" is ", " are ", "think of", "imagine", "at its core", "basically", "in short"))
    return framed, "opening frames the concept" if framed else "opening dives in without framing"
