"""Fiction named-cast check (Claude-main lane) — genre-gated, per peer review of #29.

#29 correctly stopped flagging the silent Host1/Host2 speaker LABEL (a false positive on every
genre — they are intentional TTS voice ids). But Claude-parallel rightly pushed back: for FICTION
the speaker field SHOULD carry named characters (Officer Clara Vance, Cole), and Host1/Host2 there
IS the named-cast-lost regression (FIX2c — Jake Martinez must survive, not collapse to Host1).
Verified on real drama jobs (named speakers) incl one with a stray generic "Host". So this check is
FICTION-ONLY; non-fiction keeps its intentional anonymous labels.
"""
import re

from ..check import check, skip

_FICTION_GENRES = (
    "drama", "horror", "psychological_horror", "comedy", "romance", "mystery", "scifi", "sci-fi",
    "fantasy", "story", "storytelling", "thriller", "bedtime", "audio_drama",
)


@check("fiction.speakers_named_not_generic", dimension="structure", genre=None, severity="high")
def fiction_speakers_named(art):
    "FICTION speakers must be NAMED characters, not generic Host1/Host2/Speaker1 (named-cast-lost)."
    g = (art.genre or "").lower()
    if not any(f in g for f in _FICTION_GENRES):
        skip("not fiction — anonymous Host1/Host2 voice labels are intentional for non-fiction")
    if not art.speakers:
        skip("no speakers to check")
    pat = re.compile(r"^\s*(host|speaker)\s?\d*\s*$", re.IGNORECASE)
    generic = [s for s in art.speakers if pat.match(s)]
    return len(generic) == 0, (
        f"{len(generic)} generic speaker(s) in FICTION (named-cast lost?): {generic[:4]}"
        if generic else "named cast preserved"
    )
