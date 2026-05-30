"""Per-genre listen-test acceptance bands (D21 L5.3).

Single source of truth for what "PASS" sounds like per genre. The numbers
here are pulled from the production-shipped YAML palettes in workers
(``workers/profiles/genre_palette/*.yaml``) + the B3 research doc
(``.claude/research/audio-overhaul-2026-05-28/B3-genre-mix-profiles.md``)
so the verifier asks for the SAME bands the renderer is targeting. If
those two disagree, that's a cross-layer drift bug — L7 (D23) is
designed to pin them together.

Why pull from production rather than re-derive
-----------------------------------------------
The horror profile lived in ``profiles/genre_palette/horror.yaml`` as
``lra_target_lu: [5, 7]`` after the PR-14/15/16/17 LRA chase. If this
file invented a different LRA band for horror (say [9, 11]) the
verifier would WARN on every horror master shipped by the renderer that
landed on the renderer's actual target. Same SSOT, no drift.

Phase 1 ships **8 profiles**: default + 7 genres the architect actually
emits today (horror, thriller, drama, comedy, romance, mystery,
bedtime). Reading models tolerate ``extra='ignore'`` — adding profiles
later is safe.

Per-genre rationale lines are kept short; the deep version lives in the
worker YAMLs (which include reference-show lists, ffmpeg chains, etc.).
"""

from __future__ import annotations

from typing import Dict

from kitesforu_qa.stages.listen_test import GenreProfile


# ---------------------------------------------------------------------------
# The 8-profile table
# ---------------------------------------------------------------------------

# Default = the house mix the renderer falls back to when no genre profile
# is selected. Numbers mirror workers/profiles/genre_palette/_default.yaml.
_DEFAULT = GenreProfile(
    genre="default",
    lufs_min=-17.0, lufs_max=-15.0,
    lra_min=5.0, lra_max=9.0,
    lufs_slack_lu=1.0, lra_slack_lu=1.5,
    tp_max_dbtp=-1.0,
    stoi_min=0.85,
    smr_lu_min=12.0,
    music_presence_pct_min=0.20,
    music_presence_pct_max=0.65,
    sfx_events_per_5min_min=0,
    sfx_events_per_5min_max=8,
    duration_delta_pct_max=0.10,
    silence_ratio_max=0.10,
    naturalness_min=3.5,
)

# Horror — widest dynamics, sub-bass headroom, longest release.
# Source: workers/profiles/genre_palette/horror.yaml (PR-17 shipped
# lra_target_lu: [5, 7] after the PR-14/15/16 LRA chase).
_HORROR = GenreProfile(
    genre="horror",
    lufs_min=-19.0, lufs_max=-17.0,
    lra_min=5.0, lra_max=7.0,
    lufs_slack_lu=1.0, lra_slack_lu=2.0,  # wider slack — chase landed at 4.0 LU
    tp_max_dbtp=-1.0,
    stoi_min=0.80,                # whisper-narration tolerates lower STOI
    smr_lu_min=10.0,              # quieter speech vs music drone
    music_presence_pct_min=0.35,  # drone-heavy — bed should be audible most of the episode
    music_presence_pct_max=0.85,
    sfx_events_per_5min_min=2,    # horror is SFX-driven
    sfx_events_per_5min_max=30,   # density_per_minute_max=6 → 30 per 5min
    duration_delta_pct_max=0.10,
    silence_ratio_max=0.20,       # silence is an instrument
    naturalness_min=3.3,
)

# Thriller — denser cues than horror, slightly louder.
# Source: workers/profiles/genre_palette/thriller.yaml.
_THRILLER = GenreProfile(
    genre="thriller",
    lufs_min=-18.0, lufs_max=-16.0,
    lra_min=5.0, lra_max=7.0,
    lufs_slack_lu=1.0, lra_slack_lu=2.0,
    tp_max_dbtp=-1.0,
    stoi_min=0.83,
    smr_lu_min=11.0,
    music_presence_pct_min=0.35,
    music_presence_pct_max=0.80,
    sfx_events_per_5min_min=2,
    sfx_events_per_5min_max=40,   # density_per_minute_max=8 → 40 per 5min
    duration_delta_pct_max=0.10,
    silence_ratio_max=0.12,
    naturalness_min=3.4,
)

# Bedtime — quietest LUFS, narrowest dynamics, sparse cues.
# Source: workers/profiles/genre_palette/bedtime.yaml.
# CRITICAL: sleep retention dies on level surprise; TP -3 dBTP (not the
# default -1) and silence_ratio is tight (no abrupt silence either).
_BEDTIME = GenreProfile(
    genre="bedtime",
    lufs_min=-24.0, lufs_max=-22.0,
    lra_min=6.0, lra_max=8.0,
    lufs_slack_lu=1.0, lra_slack_lu=1.0,
    tp_max_dbtp=-3.0,             # extra-safe — no peak transients
    stoi_min=0.88,                # slow narration → STOI naturally high
    smr_lu_min=14.0,              # music stays present + gentle (4 dB duck)
    music_presence_pct_min=0.50,  # bed should be felt almost continuously
    music_presence_pct_max=0.95,
    sfx_events_per_5min_min=0,
    sfx_events_per_5min_max=10,   # density_per_minute_max=2 → 10 per 5min
    duration_delta_pct_max=0.08,
    silence_ratio_max=0.05,       # gentle continuous presence
    naturalness_min=3.5,
)

# Comedy — punchy/loud, midrange-forward, fast-attack.
# Source: B3 §2.2 — Comedy is "loud" vs drama (+1 LU above house),
# tight silences > 600 ms by 30%, snappy 250 ms music release.
_COMEDY = GenreProfile(
    genre="comedy",
    lufs_min=-16.0, lufs_max=-14.0,
    lra_min=4.0, lra_max=6.0,     # narrow — punchy energy not dynamic
    lufs_slack_lu=1.0, lra_slack_lu=1.5,
    tp_max_dbtp=-1.0,
    stoi_min=0.88,                # punchline intelligibility is the product
    smr_lu_min=13.0,              # speech sits well above bumpers
    music_presence_pct_min=0.15,  # mostly inter-bit bumpers (~1.5-3s)
    music_presence_pct_max=0.45,
    sfx_events_per_5min_min=1,
    sfx_events_per_5min_max=15,
    duration_delta_pct_max=0.10,
    silence_ratio_max=0.05,       # comedy hates dead air
    naturalness_min=3.8,          # delivery quality is critical
)

# Romance — intimate, warm, breath-preserved.
# Source: B3 §2.3 — Romance preserves all breaths, ambience -26 dBFS
# (subtle warm room tone), 2.5:1 transparent compression.
_ROMANCE = GenreProfile(
    genre="romance",
    lufs_min=-18.0, lufs_max=-16.0,
    lra_min=4.0, lra_max=7.0,
    lufs_slack_lu=1.0, lra_slack_lu=1.5,
    tp_max_dbtp=-1.0,
    stoi_min=0.85,
    smr_lu_min=12.0,
    music_presence_pct_min=0.30,
    music_presence_pct_max=0.65,
    sfx_events_per_5min_min=0,
    sfx_events_per_5min_max=10,   # restraint — voice + bed carry the genre
    duration_delta_pct_max=0.10,
    silence_ratio_max=0.10,
    naturalness_min=3.7,          # breath/intimacy = production value
)

# Mystery — preserves silence at clue reveals, fewer SFX than horror.
# Source: B3 §2.5 — "Allow up to 3 s silence at clue reveals" + the
# Magnus/Bear Brook/Serial reference set tolerates more LRA than thriller.
_MYSTERY = GenreProfile(
    genre="mystery",
    lufs_min=-19.0, lufs_max=-17.0,
    lra_min=6.0, lra_max=9.0,
    lufs_slack_lu=1.0, lra_slack_lu=2.0,
    tp_max_dbtp=-1.0,
    stoi_min=0.85,
    smr_lu_min=12.0,
    music_presence_pct_min=0.25,
    music_presence_pct_max=0.70,
    sfx_events_per_5min_min=1,
    sfx_events_per_5min_max=20,
    duration_delta_pct_max=0.10,
    silence_ratio_max=0.18,       # 3s reveal-silences allowed
    naturalness_min=3.5,
)

# Drama — Homecoming/Passenger List analog; standard cinematic mix.
# Source: B3 §2.12 — "Drama" archetype anchors the dynamic-cinematic
# bucket (vs warm-intimate / journalistic-tight / punchy).
_DRAMA = GenreProfile(
    genre="drama",
    lufs_min=-17.0, lufs_max=-15.0,
    lra_min=6.0, lra_max=8.0,
    lufs_slack_lu=1.0, lra_slack_lu=1.5,
    tp_max_dbtp=-1.0,
    stoi_min=0.85,
    smr_lu_min=12.0,
    music_presence_pct_min=0.25,
    music_presence_pct_max=0.65,
    sfx_events_per_5min_min=1,
    sfx_events_per_5min_max=15,
    duration_delta_pct_max=0.10,
    silence_ratio_max=0.12,
    naturalness_min=3.5,
)


GENRE_AUDIO_PROFILES: Dict[str, GenreProfile] = {
    "default": _DEFAULT,
    "horror": _HORROR,
    "thriller": _THRILLER,
    "bedtime": _BEDTIME,
    "comedy": _COMEDY,
    "romance": _ROMANCE,
    "mystery": _MYSTERY,
    "drama": _DRAMA,
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def get_profile(genre: str) -> GenreProfile:
    """Look up a profile by genre token. Unknown → DEFAULT.

    Matching is case-insensitive + trims whitespace. Common aliases
    (``true_crime`` → ``mystery``, ``audio_overview`` → ``default``,
    ``fiction`` → ``drama``) are accepted so the architect's genre
    output doesn't need to round-trip through a normalizer.

    Resolver is the SINGLE entry point — callers should never read
    GENRE_AUDIO_PROFILES directly. That way the alias table evolves
    in one place and the verifier can never look up the wrong band by
    accident of capitalization.
    """
    if not genre:
        return _DEFAULT
    key = genre.strip().lower().replace("-", "_").replace(" ", "_")

    # Cheap aliases for genres the architect emits but that map onto an
    # existing profile rather than warranting their own bands.
    aliases = {
        "true_crime": "mystery",
        "truecrime": "mystery",
        "investigation": "mystery",
        "noir": "thriller",
        "psychological_thriller": "thriller",
        "supernatural": "horror",
        "ghost_story": "horror",
        "scifi": "drama",          # sci-fi defaults to drama bands
        "sci_fi": "drama",
        "fantasy": "drama",
        "history": "drama",
        "documentary": "default",
        "news": "default",
        "audio_overview": "default",
        "explainer": "default",
        "interview": "default",
        "fiction": "drama",        # if architect emits bare "fiction"
        "nonfiction": "default",
        "non_fiction": "default",
        "love": "romance",         # love→romance alias from the workers vocab fix
    }
    if key in aliases:
        key = aliases[key]
    return GENRE_AUDIO_PROFILES.get(key, _DEFAULT)


__all__ = ["GENRE_AUDIO_PROFILES", "get_profile"]
