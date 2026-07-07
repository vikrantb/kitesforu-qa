"""axes.py — the 8 SHORT SCORECARD axis-scoring functions.

Each ``score_*`` function takes an ``Artifact`` + a ``ScorecardConfig`` and returns one
``AxisResult`` (via ``rubric.make_axis``). Every function is a pure, $0-by-default read over the
already-fetched job doc (+ optionally a downloaded video file) — no network calls except the
OPTIONAL injected ``judge_fn``/``vlm_fn`` (gated behind ``cfg.enable_judge``/``cfg.enable_vlm``,
both default OFF). See ``config.py`` for the cost-gate contract and ``rubric.py`` for the axis
floors/weights/ship-bar math this file feeds.

CRITICAL INTEGRITY (the whole point of the scorecard): an axis whose signal is genuinely
unmeasurable at the current tier returns ``score=None`` (via ``make_axis(..., score=None, ...)``)
and a ``needs`` string saying what would make it measurable. NEVER fabricate a passing score to
paper over missing instrumentation — ``rubric.evaluate()`` surfaces every ``None`` under
``missing_instrumentation`` and refuses to certify SHIP on a render with any.
"""
from __future__ import annotations

import math
from typing import Any

from . import signals
from .banned_openers import banned_opener_hit
from .config import ScorecardConfig
from .rubric import AxisResult, make_axis

try:  # audio_quality_judge lives in scripts/ (not an installed package) — import defensively.
    import sys as _sys
    from pathlib import Path as _Path

    _scripts_dir = str(_Path(__file__).resolve().parents[3] / "scripts")
    if _scripts_dir not in _sys.path:
        _sys.path.insert(0, _scripts_dir)
    import audio_quality_judge as _audio_quality_judge
except Exception:  # pragma: no cover — degrade honestly if the sibling script can't be found.
    _audio_quality_judge = None


# ── axis 1 — HOOK STOP-POWER (floor 80, T1 $0) ────────────────────────────────


def score_hook_stop_power(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """First-beat modality (photo/high-contrast vs a plain text card) + hook word-count/timing
    (<=12 words, first word spoken by 2.5s) + a clean (non-banned) opener. All three deterministic
    signals are always $0-computable from the job doc; the timing sub-check alone degrades (its
    weight is dropped, not faked) when ``master_segment_timeline`` wasn't stamped (legacy jobs)."""
    clip = signals.first_beat_clip(art)
    modality_ok = signals.is_photo_first_frame(clip)

    hook = signals.hook_line(art)
    wc = signals.word_count(hook)
    word_count_ok = 0 < wc <= cfg.hook_max_words

    opener_hit = banned_opener_hit(hook) if hook else None
    opener_ok = bool(hook) and opener_hit is None

    first_ms = signals.first_speech_ms(art)
    timing_ok: bool | None = None
    if first_ms is not None:
        timing_ok = (first_ms / 1000.0) <= cfg.hook_first_word_max_s

    weights = {"modality": 40.0, "word_count": 20.0, "opener": 20.0, "timing": 20.0}
    total_weight = weights["modality"] + weights["word_count"] + weights["opener"]
    points = 0.0
    if modality_ok:
        points += weights["modality"]
    if word_count_ok:
        points += weights["word_count"]
    if opener_ok:
        points += weights["opener"]
    if timing_ok is not None:
        total_weight += weights["timing"]
        if timing_ok:
            points += weights["timing"]
    score = (points / total_weight) * 100.0 if total_weight else 0.0

    modality = str((clip or {}).get("modality") or "").strip().lower()
    render_mode = str((clip or {}).get("render_mode") or "").strip().lower()
    timing_note = (
        f"{first_ms:.0f}ms (<= {cfg.hook_first_word_max_s * 1000:.0f}ms: {timing_ok})"
        if first_ms is not None
        else "unmeasured (no master_segment_timeline on this job)"
    )
    evidence = (
        f"beat0 modality={modality!r} render_mode={render_mode!r} -> photo/high-contrast={modality_ok}; "
        f"hook={hook[:80]!r} words={wc} (<= {cfg.hook_max_words}: {word_count_ok}); "
        f"opener={'CLEAN' if opener_ok else f'BANNED:{opener_hit!r}'}; "
        f"first_speech={timing_note}"
    )
    return make_axis(
        "hook_stop_power",
        score=score,
        evidence=evidence,
        how_measured=(
            "T1 $0: beat0 modality/render_mode + hook word-count + banned-opener regex "
            "(narration_rules.yaml mirror) + master_segment_timeline first-speech offset"
        ),
        sub_signals={
            "first_beat_modality": modality or None,
            "first_beat_render_mode": render_mode or None,
            "modality_ok": modality_ok,
            "hook_line": hook,
            "hook_word_count": wc,
            "hook_word_count_ok": word_count_ok,
            "opener_banned_phrase": opener_hit,
            "opener_ok": opener_ok,
            "first_speech_ms": first_ms,
            "first_speech_ok": timing_ok,
        },
    )


# ── axis 2 — SUBSTANCE NOVELTY (floor 70, T2 ¢; $0 proxy when judge disabled) ──


def score_substance_novelty(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """Authoritative score comes from an injected LLM novelty judge (``cfg.judge_fn``, gated by
    ``cfg.enable_judge`` — both default OFF so baseline runs are $0). When disabled (or enabled but
    not wired), this axis reports a $0 heuristic PROXY (research-grounding + angle-brief richness)
    — clearly marked ``proxy=True`` so ``rubric.evaluate()`` treats it as non-authoritative and
    withholds SHIP, rather than silently passing on an unverified novelty claim."""
    grounding = signals.research_grounding(art)
    brief = art.llm_brief if hasattr(art, "llm_brief") else {}
    angle_count = len(brief.get("angles") or []) if isinstance(brief, dict) else 0
    brief_chars = int(brief.get("brief_chars") or 0) if isinstance(brief, dict) else 0

    heuristic_score = 0.0
    if grounding["grounded"]:
        heuristic_score += 50.0
    if angle_count > 0:
        heuristic_score += 25.0
    if brief_chars >= 500:
        heuristic_score += 25.0

    sub_signals: dict[str, Any] = {
        "research_mode": grounding["research_mode"],
        "research_skipped": grounding["research_skipped"],
        "skip_reason": grounding["skip_reason"],
        "research_grounded": grounding["grounded"],
        "angle_count": angle_count,
        "brief_chars": brief_chars,
        "heuristic_score": heuristic_score,
    }

    if cfg.enable_judge:
        if cfg.judge_fn is None:
            return make_axis(
                "substance_novelty",
                score=None,
                evidence="enable_judge=True but no judge_fn callable was injected — cannot fabricate a judge score",
                how_measured="T2 ¢ haiku novelty judge (enabled, not wired)",
                needs="inject a judge_fn callable (LLM novelty judge)",
                sub_signals=sub_signals,
            )
        try:
            judge_score, note = cfg.judge_fn(art)
        except Exception as exc:  # noqa: BLE001 — a judge failure must degrade, never crash scoring
            return make_axis(
                "substance_novelty",
                score=None,
                evidence=f"judge_fn raised: {exc!r}",
                how_measured="T2 ¢ haiku novelty judge (call failed)",
                needs="a working judge_fn",
                sub_signals=sub_signals,
            )
        sub_signals["judge_note"] = note
        return make_axis(
            "substance_novelty",
            score=judge_score,
            evidence=f"LLM novelty judge: {note} (score={judge_score})",
            how_measured="T2 ¢: LLM judge — 'would a domain-literate viewer learn something non-obvious?'",
            sub_signals=sub_signals,
        )

    evidence = (
        f"$0 PROXY (enable_judge=False, NOT authoritative): research_mode={grounding['research_mode']!r} "
        f"research_skipped={grounding['research_skipped']} angles={angle_count} brief_chars={brief_chars} "
        f"-> heuristic {heuristic_score:.0f}/100"
    )
    return make_axis(
        "substance_novelty",
        score=heuristic_score,
        evidence=evidence,
        how_measured=(
            "T1 $0 PROXY: research-grounding + angle-brief-richness heuristic "
            "(T2 haiku novelty judge disabled by default)"
        ),
        proxy=True,
        needs="enable_judge=True + a judge_fn callable (LLM novelty judge)",
        sub_signals=sub_signals,
    )


# ── axis 3 — VISUAL TRUTH (floor 90, T2 ¢ VLM; no honest $0 proxy) ────────────

_PHOTOREAL_MODALITIES = frozenset({"ai_photo", "photo", "scene_image"})


def score_visual_truth(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """VLM photo-vs-illustration verdict on every beat LABELED photoreal — the axis that catches
    the "Pixar-labeled-photoreal lie" (a beat marked ai_photo that is actually an illustration).
    There is deliberately NO $0 heuristic standing in for this: a heuristic cannot detect the lie
    (that's the whole reason it needs a vision model), so when the VLM is disabled/unwired this
    axis is an honest ``score=None`` rather than a fabricated pass — UNLESS there are simply no
    photoreal-labeled beats on this render, in which case the axis is vacuously satisfied (nothing
    to falsify)."""
    photoreal_beats = [
        c for c in art.visual_clips
        if isinstance(c, dict) and str(c.get("modality") or "").strip().lower() in _PHOTOREAL_MODALITIES
    ]
    if not photoreal_beats:
        return make_axis(
            "visual_truth",
            score=100.0,
            evidence="no beats labeled photoreal/ai_photo on this render — nothing to falsify (vacuously satisfied)",
            how_measured="T1 $0: no photoreal-labeled beats present",
            sub_signals={"photoreal_beat_count": 0},
        )

    if not cfg.enable_vlm:
        return make_axis(
            "visual_truth",
            score=None,
            evidence=(
                f"{len(photoreal_beats)} photoreal-labeled beat(s) found; VLM photo-vs-illustration check "
                "disabled (enable_vlm=False) — a heuristic cannot detect the Pixar-labeled-photoreal lie, "
                "so this is an honest null, not a pass"
            ),
            how_measured="needs-VLM (T2 ¢); no $0 proxy exists for this axis by design",
            needs="enable_vlm=True + a vlm_fn callable (VLM photo-vs-illustration judge)",
            sub_signals={"photoreal_beat_count": len(photoreal_beats)},
        )

    if cfg.vlm_fn is None:
        return make_axis(
            "visual_truth",
            score=None,
            evidence="enable_vlm=True but no vlm_fn callable was injected — cannot fabricate a VLM verdict",
            how_measured="T2 ¢ VLM (enabled, not wired)",
            needs="inject a vlm_fn callable (VLM photo-vs-illustration judge)",
            sub_signals={"photoreal_beat_count": len(photoreal_beats)},
        )

    image_uris = [
        str(c.get("asset_uri") or c.get("image_url") or c.get("gcs_uri") or c.get("url") or "")
        for c in photoreal_beats
    ]
    image_uris = [u for u in image_uris if u]
    try:
        score, note = cfg.vlm_fn(image_uris, {"job_id": art.job_id, "beat_count": len(photoreal_beats)})
    except Exception as exc:  # noqa: BLE001
        return make_axis(
            "visual_truth",
            score=None,
            evidence=f"vlm_fn raised: {exc!r}",
            how_measured="T2 ¢ VLM (call failed)",
            needs="a working vlm_fn",
            sub_signals={"photoreal_beat_count": len(photoreal_beats)},
        )
    return make_axis(
        "visual_truth",
        score=score,
        evidence=f"VLM photo-vs-illustration verdict on {len(photoreal_beats)} photoreal-labeled beat(s): {note}",
        how_measured="T2 ¢: VLM photo-vs-illustration gate",
        sub_signals={"photoreal_beat_count": len(photoreal_beats), "vlm_note": note},
    )


# ── axis 4 — MODALITY MIX (floor 70, T1 $0) ───────────────────────────────────


def score_modality_mix(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """Shannon entropy of the per-beat modality distribution across {ai_photo, diagram,
    kinetic_text, scene} (+ 'other' for unrecognized). Normalised against log2(4) so a perfectly
    balanced 4-way split scores 100 and an all-one-modality reel scores 0 — directly operationalising
    "no flat single-modality reel"."""
    counts = signals.modality_bucket_counts(art)
    total_beats = sum(counts.values())
    if total_beats == 0:
        return make_axis(
            "modality_mix",
            score=None,
            evidence="no visual_clips on the job doc — cannot compute shot-modality entropy",
            how_measured="T1 $0: Shannon entropy of per-beat modality buckets",
            needs="rendered visual_clips with modality/render_mode stamped",
        )
    probs = [c / total_beats for c in counts.values() if c > 0]
    h = -sum(p * math.log2(p) for p in probs)
    h_max = math.log2(len(signals.MODALITY_BUCKETS))
    score = (h / h_max) * 100.0 if h_max else 0.0
    dist = dict(counts)
    return make_axis(
        "modality_mix",
        score=score,
        evidence=(
            f"{total_beats} beats across buckets {dist} -> entropy {h:.2f} / {h_max:.2f} max bits = {score:.0f}"
        ),
        how_measured="T1 $0: Shannon entropy over {ai_photo, diagram, kinetic_text, scene, other} beat buckets",
        sub_signals={"bucket_counts": dist, "entropy_bits": round(h, 3), "entropy_max_bits": round(h_max, 3)},
    )


# ── axis 5 — MOTION DENSITY (floor 75, T1 $0; ffmpeg proxy fallback) ─────────


def score_motion_density(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """Engine-rendered motion events per second, from renderer provenance (``render_mode`` /
    ``motion_preset`` stamped per beat) when available; falls back to an ffmpeg scene-change PROXY
    on the rendered MP4 only when NO beat carries motion provenance at all (``proxy=True`` in that
    case — a scene-change count is a stand-in for, not equal to, an engine motion event)."""
    n_motion, n_total, motion_records = signals.motion_beats(art)
    duration = signals.video_duration_s(art)
    if duration is None or duration <= 0:
        return make_axis(
            "motion_density",
            score=None,
            evidence="no renderable video duration (ffprobe failed / no video_path)",
            how_measured="T1 $0: motion events/sec vs target",
            needs="a downloaded rendered MP4",
        )

    contradiction = ""
    rmc = signals.rendered_motion_clips_counter(art)
    if rmc is not None and n_motion > 0 and rmc == 0:
        contradiction = f" [NOTE: visual.rendered_motion_clips={rmc} contradicts {n_motion} motion-mode beat(s) — provenance discrepancy, not scored]"

    if n_total > 0:
        rate = n_motion / duration
        score = (rate / cfg.motion_target_rate_per_s) * 100.0
        return make_axis(
            "motion_density",
            score=score,
            evidence=(
                f"{n_motion}/{n_total} beats carry engine motion provenance over {duration:.1f}s -> "
                f"{rate:.3f} events/s (target {cfg.motion_target_rate_per_s}/s){contradiction}"
            ),
            how_measured="T1 $0: renderer-stamped motion beats / video duration",
            sub_signals={
                "motion_beats": n_motion, "total_beats": n_total, "rate_per_s": round(rate, 4),
                "rendered_motion_clips_counter": rmc, "motion_beat_records": motion_records,
            },
        )

    if not cfg.ffmpeg_motion_fallback:
        return make_axis(
            "motion_density",
            score=None,
            evidence="no renderer motion provenance stamped on any beat, and the ffmpeg fallback is disabled",
            how_measured="T1 $0: renderer provenance (absent, fallback disabled)",
            needs="renderer must stamp render_mode/motion_preset per beat, OR set ffmpeg_motion_fallback=True",
        )
    if not art.video_path:
        return make_axis(
            "motion_density",
            score=None,
            evidence="no renderer motion provenance AND no local video file to run the ffmpeg proxy on",
            how_measured="T1 $0 ffmpeg scene-change proxy (no video available)",
            needs="a downloaded rendered MP4",
        )
    scene_changes = signals.ffmpeg_scene_change_count(art.video_path)
    if scene_changes is None:
        return make_axis(
            "motion_density",
            score=None,
            evidence="ffmpeg scene-change proxy failed to run on the video",
            how_measured="T1 $0 ffmpeg scene-change proxy (failed)",
            needs="a decodable rendered MP4",
        )
    rate = scene_changes / duration
    score = (rate / cfg.motion_target_rate_per_s) * 100.0
    return make_axis(
        "motion_density",
        score=score,
        evidence=(
            f"NO renderer motion provenance stamped on any of {n_total} beat(s) — ffmpeg scene-change "
            f"PROXY: {scene_changes} changes over {duration:.1f}s -> {rate:.3f}/s "
            f"(target {cfg.motion_target_rate_per_s}/s)"
        ),
        how_measured="T1 $0 PROXY: ffmpeg scene-change count / video duration (renderer provenance absent)",
        proxy=True,
        needs="renderer must stamp render_mode/motion_preset per beat for an authoritative score",
        sub_signals={"ffmpeg_scene_changes": scene_changes, "rate_per_s": round(rate, 4)},
    )


# ── axis 6 — SYNC EXACTNESS (floor 85, T1 $0; needs Phase-1a word timestamps) ─


def score_sync_exactness(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """Karaoke word-sync error (median |truth_start - rendered_start| ms) vs a persisted
    GROUND-TRUTH word-timestamp track. The current pipeline persists neither the ground-truth track
    nor a queryable rendered karaoke word-display track (Phase 1a), so on every real job today this
    is an honest ``missing_instrumentation`` — NOT a fabricated pass. The comparison path is
    implemented (and unit-tested against a synthetic fixture) so the axis activates automatically
    the moment Phase 1a lands either track."""
    truth = signals.word_timestamp_track(art)
    if truth is None:
        return make_axis(
            "sync_exactness",
            score=None,
            evidence=(
                "no persisted GROUND-TRUTH word-level timestamp track (Phase 1a not yet landed on this "
                "job) — the karaoke .ass is char-proportional ESTIMATION and captions_vtt is "
                "segment-level; neither is ground truth, so a sync error cannot be computed honestly"
            ),
            how_measured="T1 $0: karaoke word-sync error vs word-timestamp track (absent)",
            needs="Phase 1a: a persisted per-word {word,start,end} ground-truth timestamp track",
        )
    karaoke = signals.karaoke_word_track(art)
    if karaoke is None:
        return make_axis(
            "sync_exactness",
            score=None,
            evidence=(
                f"ground-truth word track present ({truth['words']} words) but no queryable rendered "
                "karaoke word-display track to compare against — the .ass bakes per-character timing "
                "into subtitle text, not a per-word array"
            ),
            how_measured="T1 $0: karaoke word-sync error vs word-timestamp track (rendered side absent)",
            needs="renderer must persist a per-word karaoke DISPLAY timestamp track",
        )

    n = min(len(truth["pairs"]), len(karaoke["pairs"]))
    drifts = [abs(truth["pairs"][i][1] - karaoke["pairs"][i][1]) for i in range(n)]
    drifts.sort()
    median_drift_ms = drifts[n // 2] if n % 2 else (drifts[n // 2 - 1] + drifts[n // 2]) / 2.0
    # Anchored to config's documented contract: drift=0 -> 100; drift=word_sync_floor_ms -> exactly
    # the axis floor (85) — see ScorecardConfig.word_sync_floor_ms docstring.
    score = 100.0 - (median_drift_ms / cfg.word_sync_floor_ms) * 15.0
    return make_axis(
        "sync_exactness",
        score=score,
        evidence=(
            f"median word-sync drift {median_drift_ms:.1f}ms over {n} matched word(s) "
            f"(floor {cfg.word_sync_floor_ms:.0f}ms -> score {score:.0f})"
        ),
        how_measured="T1 $0: median |ground-truth word start - rendered karaoke word start| (ms)",
        sub_signals={"median_drift_ms": round(median_drift_ms, 1), "n_words_compared": n},
    )


# ── axis 7 — AUDIO FEEL (floor 70, T1 $0) ─────────────────────────────────────

_VERDICT_BASE_SCORE = {
    "likely_real_music": 90.0,
    "ambiguous": 60.0,
}


def _judge_verdict_base_score(verdict: str, music_expected: bool) -> float:
    if verdict.startswith("likely_real_music"):
        return 90.0
    if verdict.startswith("DRONE/HUM"):
        return 15.0
    if verdict.startswith("ambiguous"):
        return 60.0
    if verdict.startswith("no_music_bed") or verdict.startswith("too_short"):
        # A quiet/no-bed verdict is CORRECT (not a defect) on dry content that never planned music.
        return 20.0 if music_expected else 85.0
    return 50.0


def score_audio_feel(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """0.7 x the $0 spectral drone-vs-real-music judge verdict (``audio_quality_judge`` — reused,
    not reimplemented) + 0.3 x a planned-vs-detected music-cue check (pins the
    truncation -> 1-cue-silent-default regression class)."""
    music = signals.music_signals(art)
    audio_target = art.audio_path or art.video_path
    if not audio_target:
        return make_axis(
            "audio_feel",
            score=None,
            evidence="no audio or video file available to run the spectral judge",
            how_measured="T1 $0: audio_quality_judge spectral analysis",
            needs="a downloaded audio or video file",
        )
    if _audio_quality_judge is None:
        return make_axis(
            "audio_feel",
            score=None,
            evidence="scripts/audio_quality_judge.py could not be imported",
            how_measured="T1 $0: audio_quality_judge spectral analysis (import failed)",
            needs="scripts/audio_quality_judge.py importable from the kitesforu-qa checkout",
        )
    try:
        result = _audio_quality_judge.judge(audio_target)
    except Exception as exc:  # noqa: BLE001
        return make_axis(
            "audio_feel",
            score=None,
            evidence=f"audio_quality_judge raised: {exc!r}",
            how_measured="T1 $0 spectral judge (failed)",
            needs="a decodable audio/video file",
        )
    verdict = str(result.get("verdict") or "")
    planned_cues = music.get("planned_cue_count")
    music_expected = bool(music.get("music_recommended")) or bool(planned_cues and planned_cues > 0)
    base = _judge_verdict_base_score(verdict, music_expected)

    cue_score = 100.0
    cue_note = "no cue-count expectation stamped on this job"
    if planned_cues is not None and planned_cues > 0:
        detected = music.get("detected")
        if detected is False:
            cue_score, cue_note = 0.0, f"planned {planned_cues} cue(s) but listening-QA detected=False"
        elif detected is True:
            cue_score, cue_note = 100.0, f"planned {planned_cues} cue(s), listening-QA detected=True"
        else:
            cue_score, cue_note = 70.0, f"planned {planned_cues} cue(s), detection verdict unknown"

    score = 0.7 * base + 0.3 * cue_score
    evidence = f"spectral judge: {verdict} (base {base:.0f}); cues: {cue_note} (cue_score {cue_score:.0f}) -> {score:.0f}"
    return make_axis(
        "audio_feel",
        score=score,
        evidence=evidence,
        how_measured=(
            "T1 $0: audio_quality_judge spectral drone/music verdict (weight 0.7) + planned-vs-detected "
            "music cue check (weight 0.3)"
        ),
        sub_signals={"judge": result, "music_signals": music},
    )


# ── axis 8 — COST + SAFETY (hard gates, T1 $0) ────────────────────────────────


def _safety_passed(safety: dict[str, Any]) -> bool | None:
    for key in ("passed", "safe", "pass", "ok"):
        if key in safety:
            return bool(safety[key])
    if "flagged" in safety:
        return not bool(safety["flagged"])
    verdict = str(safety.get("verdict") or "").strip().lower()
    if verdict in ("pass", "passed", "safe", "ok"):
        return True
    if verdict in ("fail", "failed", "flagged", "unsafe"):
        return False
    return None  # unrecognized shape — treat as unmeasurable, never as a silent pass.


def score_cost_safety(art: Any, cfg: ScorecardConfig) -> AxisResult:
    """Hard gate: per-short spend <= cap AND a persisted safety-floor pass. A KNOWN failure on
    EITHER criterion fails the whole axis outright (score=0) even when the OTHER criterion is
    unmeasurable — unmeasurability can never rescue a known failure. Only when BOTH are known and
    both pass does the axis score 100; if either is simply unknown (and neither is a known fail),
    the axis is an honest ``missing_instrumentation`` null."""
    cost = signals.cost_total_usd(art)
    safety = signals.safety_verdict(art)
    safety_ok = _safety_passed(safety) if safety is not None else None
    cost_ok = (cost <= cfg.per_short_cost_cap_usd) if cost is not None else None

    notes = []
    if cost is not None:
        notes.append(f"cost=${cost:.4f} {'<=' if cost_ok else '>'} cap ${cfg.per_short_cost_cap_usd:.2f}")
    else:
        notes.append("cost=unknown (no costs.total_usd_estimate)")
    if safety is not None:
        notes.append(f"safety_ok={safety_ok} (verdict shape={sorted(safety.keys())})")
    else:
        notes.append("safety=unknown (no persisted safety/moderation verdict)")
    evidence = "; ".join(notes)
    sub_signals = {
        "cost_usd": cost, "cost_cap_usd": cfg.per_short_cost_cap_usd, "cost_ok": cost_ok,
        "safety_ok": safety_ok,
    }

    if cost_ok is False or safety_ok is False:
        return make_axis(
            "cost_safety", score=0.0, evidence=evidence,
            how_measured="T1 $0 hard gate: cost rollup <= cap AND safety floor pass",
            sub_signals=sub_signals,
        )
    if cost_ok is None or safety_ok is None:
        missing = []
        if cost_ok is None:
            missing.append("cost rollup (costs.total_usd_estimate)")
        if safety_ok is None:
            missing.append("safety verdict (no safety/moderation block persisted)")
        return make_axis(
            "cost_safety", score=None, evidence=evidence,
            how_measured="T1 $0 hard gate: cost rollup <= cap AND safety floor pass",
            needs=" + ".join(missing), sub_signals=sub_signals,
        )
    return make_axis(
        "cost_safety", score=100.0, evidence=evidence,
        how_measured="T1 $0 hard gate: cost rollup <= cap AND safety floor pass",
        sub_signals=sub_signals,
    )
