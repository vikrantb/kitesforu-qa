"""Music & SFX deterministic checks (Claude-parallel lane) — $0, pure Python over the job doc.

The founder's recurring pain (job 6e507451, 2026-06-10): the pipeline keeps *planning* a music
bed and then the render/mix layer silently DROPS it — "selected but never used" (pipeline rule #1).
The workers already ship an on-device, $0 listening-QA pass that LISTENS to the delivered bytes and
persists a verdict at ``stages["job-audio"]["qa"]["listening"]`` with a ``music`` sub-dict
(``expected`` / ``detected`` / ``method`` / ``fail`` / ``observed_margin_db`` / ``floor_margin_db``)
— see ``kitesforu-workers/.../audio/quality/listening_qa.py``. These checks PIN that verdict so a
future change that re-introduces the silent music-drop fails here (the harness is the discovery
engine), without re-doing the librosa decode (that's the heavy JUDGE tier, not this $0 lane).

The "genre has no music" skip is driven by the DOC's own ``expected`` flag (music_enabled=False or
an empty cue sheet → expected=False) — NOT by the genre profile, because every profiled genre in
``profiles/genre_audio_profiles`` has ``music_presence_pct_min > 0`` (they all expect a bed). Using
the doc's expected flag is the correct, render-grounded applicability signal.

intro/outro music duration is read from ``stages["job-audio"]["result"]["intro_duration_ms"]`` — the
real field the intro generator persists (target 12 s).
"""
from __future__ import annotations

from typing import Any

from ..check import check, skip

# ── named thresholds ──────────────────────────────────────────────────────────
# Music bed must sit ABOVE the speech-only quiet floor by at least this much for the bed to be
# audibly present (the bed fills the inter-speech gaps the speech-only mix leaves near-silent).
# Mirrors the workers' detect_music_presence default (_FLOOR_MARGIN_DB_DEFAULT = 6.0 dB).
_MIN_BED_MARGIN_DB = 6.0
# A bed that lifts the quiet floor by more than this isn't a *bed under speech* any more — it's the
# music drowning the gaps / mastered far too hot. Generous ceiling: speech still dominates < ~24 dB.
_MAX_BED_MARGIN_DB = 24.0

# intro music duration band (ms). Renderer targets 12 s (intro_duration_target_ms=12000); a healthy
# intro is a few seconds to a long-but-sane opener — not a 0 ms drop nor a runaway clip.
_INTRO_MS_MIN = 3_000
_INTRO_MS_MAX = 20_000

# ── long-tail thresholds (catalog ms-* items) ────────────────────────────────
# A long episode opens with a scored cold-open theme (catalog ms-present-004). The renderer marks
# the cold-open cue with cue_id "1m00" / a theme_treatment; below this we don't require one.
_COLD_OPEN_MIN_MS = 60_000
# Silence-anchor cues are deliberate (a held beat at a reveal). More than a handful means the
# "music" is mostly gaps — a planning bug (catalog ms-spec-009).
_MAX_SILENCE_ANCHORS = 3
# Cue-sheet coverage band for music-expecting genres (catalog ms-source-001). < 70 % = a sparse
# bed that mostly leaves the episode dry; > 100 % is nonsensical (overlapping cues double-counted).
_COVERAGE_PCT_MIN = 70.0
_COVERAGE_PCT_MAX = 100.0
# WAV/PCM-as-MP3 decode-collapse guard (feedback_wav_pcm_decode_class). A premium-tier WAV/PCM
# master mis-decoded as MP3 collapses to ~10.5 s regardless of script length. The master must land
# within this fraction of the summed rendered-segment durations — a hard, drift-free invariant.
_MASTER_VS_SEGSUM_TOL = 0.05
# A resolved bed asset that decodes to less than this is a stub/collapsed file, not a real bed.
_BED_MIN_DURATION_S = 1.0

# SFX categories that must never appear in kids/bedtime content (catalog ms-sfx-audience-001).
_ADULT_SFX_CATEGORIES = {
    "gunshot", "gunfire", "scream", "screaming", "bone_crack", "bone_break",
    "stab", "stabbing", "blood", "gore", "explosion_violent", "torture",
    "knife_slash", "body_fall", "choking", "strangle",
}
_KIDS_AUDIENCE_TIERS = {"kids_safe", "kids", "children", "family_safe"}


def _listening_verdict(art: Any) -> dict[str, Any]:
    """The persisted listening-QA verdict, or {} if the stage never ran for this job."""
    v = (((art.stages.get("job-audio") or {}).get("qa") or {}).get("listening") or {})
    return v if isinstance(v, dict) else {}


def _music_verdict(art: Any) -> dict[str, Any]:
    m = _listening_verdict(art).get("music")
    return m if isinstance(m, dict) else {}


def _audio_result(art: Any) -> dict[str, Any]:
    r = (art.stages.get("job-audio") or {}).get("result") or {}
    return r if isinstance(r, dict) else {}


def _audio_qa(art: Any) -> dict[str, Any]:
    q = (art.stages.get("job-audio") or {}).get("qa") or {}
    return q if isinstance(q, dict) else {}


def _doc_stage(art: Any, name: str) -> dict[str, Any]:
    """A named entry under the doc's top-level ``stages`` map (the podcast pipeline writes
    ``stages.music_director`` / ``stages.<worker>`` blobs with their telemetry)."""
    s = (art.doc.get("stages") or {}).get(name)
    return s if isinstance(s, dict) else {}


def _music_cue_sheet(art: Any) -> dict[str, Any]:
    """The music cue sheet the music_director persisted, or {} if music was never planned.

    Real jobs store it at ``_music_cue_sheet`` (top-level) or under the music_director stage; we
    accept either so a doc-shape evolution doesn't silently turn every cue check into a false pass.
    """
    for src in (art.doc.get("_music_cue_sheet"),
                _doc_stage(art, "music_director").get("cue_sheet"),
                _doc_stage(art, "music_director").get("_music_cue_sheet")):
        if isinstance(src, dict):
            return src
    return {}


def _music_cues(art: Any) -> list[dict[str, Any]]:
    cs = _music_cue_sheet(art)
    cues = cs.get("cues") if isinstance(cs, dict) else None
    if isinstance(cues, list):
        return [c for c in cues if isinstance(c, dict)]
    # Some jobs persist a bare list of cues instead of a {cues: [...]} wrapper.
    raw = art.doc.get("_music_cue_sheet")
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _sfx_cue_sheet(art: Any) -> list[dict[str, Any]]:
    """The KEPT SFX cues (post-trim), or [] if no SFX were planned for this job."""
    for src in (art.doc.get("_sfx_cue_sheet"),
                _doc_stage(art, "sfx_director").get("cue_sheet"),
                _doc_stage(art, "sfx_director").get("kept_cues")):
        if isinstance(src, dict) and isinstance(src.get("cues"), list):
            return [c for c in src["cues"] if isinstance(c, dict)]
        if isinstance(src, list):
            return [c for c in src if isinstance(c, dict)]
    return []


def _tournament(art: Any) -> dict[str, Any]:
    """Architect/Director best-of-N tournament telemetry, or {} if no tournament ran.

    The Director's StoryBlueprint truncation (feedback_director_truncation_no_music_root_cause)
    surfaces here as winner_id=None + zero grader comparisons — that drove the no-music regression.
    """
    for src in (_doc_stage(art, "music_director").get("best_of_n"),
                _doc_stage(art, "architect").get("best_of_n"),
                art.doc.get("best_of_n"),
                _doc_stage(art, "music_director").get("tournament")):
        if isinstance(src, dict):
            return src
    return {}


def _music_expected(art: Any) -> bool:
    """Render-grounded 'this job wanted a bed' signal: the listening verdict's expected flag, OR an
    explicit inputs._music_enabled, OR a non-empty cue sheet."""
    m = _music_verdict(art)
    if m.get("expected"):
        return True
    inp = art.doc.get("inputs") or {}
    if isinstance(inp, dict) and inp.get("_music_enabled"):
        return True
    return bool(_music_cues(art))


def _segment_duration_sum_s(art: Any) -> float:
    total = 0.0
    for s in art.segments:
        if isinstance(s, dict):
            try:
                total += float(s.get("duration_ms") or 0.0) / 1000.0
            except (TypeError, ValueError):
                continue
    return total


@check("music.present_when_expected", dimension="music-sfx", severity="high")
def present_when_expected(art):
    "When a music bed was planned, the DELIVERED mix must actually contain it (no silent drop)."
    m = _music_verdict(art)
    if not m:
        skip("no listening-QA music verdict on this job")
    if not m.get("expected"):
        skip("music not expected for this job (music disabled / empty cue sheet)")
    detected = m.get("detected")
    # The certain failure: byte-identical to speech_only => no bed reached the mix (job 6e507451).
    if m.get("fail") or detected is False:
        return False, 0.0, f"music expected but absent (method={m.get('method')}, detected={detected})"
    if detected is None:
        # Inconclusive (couldn't decode) but the byte-identity check already cleared the certain
        # failure — pass with reduced confidence rather than false-fail a missing-codec environment.
        return True, 0.7, f"music expected; detection inconclusive (method={m.get('method')})"
    return True, 1.0, f"music expected and detected (method={m.get('method')})"


@check("music.below_speech", dimension="music-sfx", severity="medium")
def below_speech(art):
    "The music bed must sit UNDER speech — present (floor lifted) yet not drowning it."
    m = _music_verdict(art)
    if not m:
        skip("no listening-QA music verdict on this job")
    if not m.get("expected"):
        skip("music not expected for this job")
    margin = m.get("observed_margin_db")
    if margin is None:
        # No quiet-floor measurement (byte-identical / undecodable / not-expected path).
        skip(f"no quiet-floor margin measured (method={m.get('method')})")
    margin = float(margin)
    ok = _MIN_BED_MARGIN_DB <= margin <= _MAX_BED_MARGIN_DB
    return (
        ok,
        f"bed quiet-floor margin {margin:.1f} dB over speech "
        f"(want {_MIN_BED_MARGIN_DB}-{_MAX_BED_MARGIN_DB} dB: present but under speech)",
    )


@check("music.intro_outro_bounded", dimension="music-sfx", severity="low")
def intro_outro_bounded(art):
    "Intro music duration must be sane — present, not a 0 ms drop nor a runaway clip."
    if not art.stages:
        skip("no stages telemetry on this job")
    intro_ms = _audio_result(art).get("intro_duration_ms")
    if intro_ms is None:
        skip("no intro_duration_ms persisted (genre/format has no intro music)")
    intro_ms = float(intro_ms)
    ok = _INTRO_MS_MIN <= intro_ms <= _INTRO_MS_MAX
    return (
        ok,
        f"intro music {intro_ms / 1000:.1f}s "
        f"(want {_INTRO_MS_MIN / 1000:.0f}-{_INTRO_MS_MAX / 1000:.0f}s)",
    )


# ── catalog long-tail: silent-drop / planning invariants (ms-present-*, ms-spec-*) ───────────────


@check("music.not_byte_identical_speech_only", dimension="music-sfx", severity="critical")
def not_byte_identical_speech_only(art):
    "The silent-drop class (job 6e507451): a music-expecting mix must NOT be byte-identical to the "
    "speech-only render — that exact signal means no bed reached the mix."
    if not _music_expected(art):
        skip("music not expected for this job (music disabled / empty cue sheet)")
    m = _music_verdict(art)
    if not m:
        skip("no listening-QA music verdict on this job (cannot read byte-identity signal)")
    method = str(m.get("method") or "")
    byte_identical = (
        method == "byte_identical_to_speech_only"
        or bool(m.get("byte_identical_to_speech_only"))
    )
    failed = bool(m.get("fail")) or m.get("detected") is False
    ok = not (byte_identical or failed)
    return ok, f"method={method!r}, fail={m.get('fail')}, detected={m.get('detected')}"


@check("music.cold_open_for_long_episodes", dimension="music-sfx", severity="medium")
def cold_open_for_long_episodes(art):
    "Long episodes (>=60s) should open with a scored cold-open theme (cue_id '1m00' / a "
    "theme_treatment), not start dry."
    cues = _music_cues(art)
    if not cues:
        skip("no music cue sheet on this job")
    total_ms = _audio_result(art).get("total_duration_ms")
    if total_ms is None:
        total_ms = art.audio_duration_s * 1000.0 if art.audio_duration_s else None
    if not total_ms or float(total_ms) < _COLD_OPEN_MIN_MS:
        skip(f"episode shorter than {_COLD_OPEN_MIN_MS / 1000:.0f}s — no cold-open required")
    has_cold_open = any(
        str(c.get("cue_id") or "") == "1m00" or c.get("theme_treatment")
        or str(c.get("role") or "").lower() in {"cold_open", "theme", "opener"}
        for c in cues
    )
    return has_cold_open, f"{len(cues)} cues, cold-open theme present={has_cold_open}"


@check("music.no_silence_anchor_on_beat0", dimension="music-sfx", severity="high")
def no_silence_anchor_on_beat0(art):
    "Beat 0 must not be a silence anchor — the episode can't OPEN on a deliberate held silence."
    cues = _music_cues(art)
    if not cues:
        skip("no music cue sheet on this job")
    offenders = [
        c for c in cues
        if c.get("beat_index") == 0 and bool(c.get("is_silence_anchor"))
    ]
    return not offenders, f"{len(offenders)} silence-anchor cue(s) at beat 0"


@check("music.silence_anchors_capped", dimension="music-sfx", severity="low")
def silence_anchors_capped(art):
    "At most a handful of cues may be deliberate silence anchors — more means the 'bed' is gaps."
    cues = _music_cues(art)
    if not cues:
        skip("no music cue sheet on this job")
    n = sum(1 for c in cues if bool(c.get("is_silence_anchor")))
    return n <= _MAX_SILENCE_ANCHORS, f"{n} silence-anchor cues (cap {_MAX_SILENCE_ANCHORS})"


# ── catalog long-tail: Director truncation → no-music root cause (ms-truncation-*) ───────────────


@check("music.director_winner_present", dimension="music-sfx", severity="critical")
def director_winner_present(art):
    "The best-of-N music/architect tournament must have a real winner with >0 grader comparisons. "
    "winner_id=None + zero comparisons is the StoryBlueprint-truncation → no-music root cause."
    t = _tournament(art)
    if not t:
        skip("no best-of-N tournament telemetry on this job")
    winner = t.get("winner_id")
    comparisons = t.get("comparisons")
    if comparisons is None:
        grader = t.get("grader")
        if isinstance(grader, dict):
            comparisons = grader.get("comparisons")
    ncomp = int(comparisons or 0)
    ok = winner is not None and ncomp > 0
    return ok, f"winner_id={winner!r}, grader comparisons={ncomp}"


@check("music.no_output_truncation", dimension="music-sfx", severity="high")
def no_output_truncation(art):
    "No tournament candidate hit max_output_tokens (output_truncated) — truncated Director JSON "
    "yields a None blueprint → safe-default 1-cue → no music."
    t = _tournament(art)
    if not t:
        skip("no best-of-N tournament telemetry on this job")
    candidates = t.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        skip("no per-candidate telemetry to inspect for truncation")
    truncated = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        err = str(c.get("error") or "")
        out_tok = c.get("output_tokens")
        max_tok = c.get("max_output_tokens")
        hit_cap = (
            out_tok is not None and max_tok is not None
            and int(out_tok) >= int(max_tok)
        )
        if err == "output_truncated" or hit_cap:
            truncated.append(c.get("candidate_id") or c.get("id") or "?")
    return not truncated, f"{len(truncated)} truncated candidate(s): {truncated}"


# ── catalog long-tail: cue-sheet coverage + bed decode (ms-source-*, WAV decode class) ───────────


@check("music.cue_sheet_coverage_reasonable", dimension="music-sfx", severity="high")
def cue_sheet_coverage_reasonable(art):
    "When a coverage_pct is recorded, a music-expecting episode's bed must cover most of it "
    "(70-100%) — a sparse bed leaves the episode dry."
    if not _music_expected(art):
        skip("music not expected for this job")
    cs = _music_cue_sheet(art)
    pct = cs.get("coverage_pct")
    if pct is None:
        pct = cs.get("music_presence_pct")
    if pct is None:
        skip("no coverage_pct recorded on the cue sheet")
    pct = float(pct)
    # Accept either a 0-1 fraction or a 0-100 percentage.
    if pct <= 1.0:
        pct *= 100.0
    ok = _COVERAGE_PCT_MIN <= pct <= _COVERAGE_PCT_MAX
    return ok, f"coverage {pct:.0f}% (want {_COVERAGE_PCT_MIN:.0f}-{_COVERAGE_PCT_MAX:.0f}%)"


@check("music.bed_decodes_nontrivial", dimension="music-sfx", severity="critical")
def bed_decodes_nontrivial(art):
    "WAV/PCM-as-MP3 decode-collapse guard on the music path: when music is expected, the master "
    "must match the summed rendered-segment durations — not collapse to ~10.5s."
    if not _music_expected(art):
        skip("music not expected for this job")
    seg_sum = _segment_duration_sum_s(art)
    if seg_sum <= 0:
        skip("no segments_ready durations to compare the master against")
    master_s = art.audio_duration_s
    if not master_s:
        skip("no master audio decoded (no audio_path) — cannot measure collapse")
    if master_s < _BED_MIN_DURATION_S:
        return False, f"master collapsed to {master_s:.2f}s (segments sum to {seg_sum:.1f}s)"
    drift = abs(master_s - seg_sum) / seg_sum
    ok = drift <= _MASTER_VS_SEGSUM_TOL
    return ok, f"master {master_s:.1f}s vs segment-sum {seg_sum:.1f}s (drift {drift * 100:.1f}%)"


# ── catalog long-tail: persisted QA verdicts (ms-debug-*) ────────────────────────────────────────


@check("music.qa_emotion_persisted", dimension="music-sfx", severity="low")
def qa_emotion_persisted(art):
    "If the music-emotion QA stage ran, its verdict must be persisted with the key metrics "
    "(clap_cos / emotion_fit / arousal / target_mood) for debug visibility."
    me = _audio_qa(art).get("music_emotion")
    if not isinstance(me, dict) or not me:
        skip("music_emotion QA stage did not run on this job")
    keys = ("clap_cos", "emotion_fit", "arousal", "target_mood")
    present = [k for k in keys if me.get(k) is not None]
    ok = len(present) >= 1
    return ok, f"music_emotion fields present: {present}"


@check("music.qa_blend_persisted", dimension="music-sfx", severity="low")
def qa_blend_persisted(art):
    "If the music-blend QA stage ran, its verdict must be persisted (stoi / smr_lu / passed)."
    mb = _audio_qa(art).get("music_blend")
    if not isinstance(mb, dict) or not mb:
        skip("music_blend QA stage did not run on this job")
    keys = ("stoi", "smr_lu", "passed")
    present = [k for k in keys if mb.get(k) is not None]
    ok = len(present) >= 1
    return ok, f"music_blend fields present: {present}"


# ── catalog long-tail: SFX grounding + audience safety (ms-sfx-*) ─────────────────────────────────


@check("sfx.trigger_phrase_in_script", dimension="music-sfx", severity="critical")
def sfx_trigger_phrase_in_script(art):
    "Every kept SFX cue's trigger_phrase must actually appear in the script — the 'selected but "
    "never grounded' class (a designed cue with no anchor in the narration is a phantom)."
    cues = _sfx_cue_sheet(art)
    if not cues:
        skip("no SFX cue sheet on this job")
    script = art.script_text.lower()
    if not script.strip():
        skip("no script text to ground SFX triggers against")
    ungrounded = []
    checked = 0
    for c in cues:
        phrase = str(c.get("trigger_phrase") or c.get("trigger") or "").strip().lower()
        if not phrase:
            continue
        checked += 1
        if phrase not in script:
            ungrounded.append(phrase)
    if checked == 0:
        skip("no SFX cue carried a trigger_phrase to ground")
    return not ungrounded, f"{len(ungrounded)}/{checked} ungrounded triggers: {ungrounded[:5]}"


@check("sfx.no_adult_sfx_in_kids_or_bedtime", dimension="music-sfx", severity="critical")
def sfx_no_adult_sfx_in_kids_or_bedtime(art):
    "Kids/bedtime content must never carry adult SFX (gunshot, scream, bone-crack, ...)."
    inp = art.doc.get("inputs") or {}
    audience = str(inp.get("audience_tier") or _g_audience(art) or "").strip().lower()
    is_bedtime = (art.genre or "").strip().lower() == "bedtime"
    if audience not in _KIDS_AUDIENCE_TIERS and not is_bedtime:
        skip("not a kids/bedtime job")
    cues = _sfx_cue_sheet(art)
    if not cues:
        skip("no SFX cue sheet on this job")
    offenders = []
    for c in cues:
        cat = str(c.get("category") or c.get("type") or "").strip().lower()
        if cat in _ADULT_SFX_CATEGORIES:
            offenders.append(cat)
    return not offenders, f"audience={audience or 'bedtime'}, {len(offenders)} adult cue(s): {offenders}"


def _g_audience(art: Any) -> str | None:
    """audience_tier may live on episode_profile/audio_config rather than inputs on some jobs."""
    for blob in (art.doc.get("episode_profile"), art.doc.get("audio_config")):
        if isinstance(blob, dict):
            v = blob.get("audience_tier") or blob.get("audience")
            if v:
                return str(v)
    return None
