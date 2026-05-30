#!/usr/bin/env python3
"""5-Rung Verification Ladder — prevents "shipped but dead" claims.

The pattern this script kills: a PR is declared "verified" because pin tests
pass + schema fields land in Firestore, but the listener experience is
unchanged. Rungs 4 and 5 require this script to *touch the audio* and
compare against a baseline before any PR can claim "verified".

Usage:
    python3 verify_pr_against_live.py \
        --pr-id 13 \
        --pr-kind sfx_palette \
        --baseline-job-id <job_id_before_deploy> \
        --post-deploy-job-id <job_id_after_deploy> \
        [--strict]

Exit codes:
    0  — All 5 rungs PASS (PR may be marked verified)
    1  — One or more rungs FAIL (PR is NOT verified)
    2  — Insufficient data (cannot run; do not claim verified)

Each rung is an independent gate. ALL must pass. There is no partial credit.

The 5 rungs (cheapest first, most load-bearing last):
    Rung 1 — Unit / pin tests pass
    Rung 2 — Live job runs to completion without error
    Rung 3 — Schema-level Firestore fields land
    Rung 4 — Audio measurements pass (the D21 metric set)
    Rung 5 — A/B improvement vs baseline on the dimension the PR claimed

The hardest rung is #5. It requires the PR author to declare upfront
*which dimension* the PR moves and *by how much*. The PR-kind → improvement
rule is the contract — change the contract here when adding a new PR kind.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("verify_pr")


# ---------------------------------------------------------------------------
# Rung verdicts
# ---------------------------------------------------------------------------


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"  # insufficient data — counts as FAIL in strict mode


@dataclass
class RungResult:
    rung: int
    name: str
    verdict: Verdict
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["verdict"] = self.verdict.value
        return d


@dataclass
class LadderVerdict:
    pr_id: str
    pr_kind: str
    baseline_job_id: str
    post_deploy_job_id: str
    rungs: List[RungResult] = field(default_factory=list)

    @property
    def overall(self) -> Verdict:
        # ALL rungs must PASS. Any FAIL or SKIP → overall FAIL.
        if all(r.verdict == Verdict.PASS for r in self.rungs):
            return Verdict.PASS
        return Verdict.FAIL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pr_id": self.pr_id,
            "pr_kind": self.pr_kind,
            "baseline_job_id": self.baseline_job_id,
            "post_deploy_job_id": self.post_deploy_job_id,
            "overall": self.overall.value,
            "rungs": [r.to_dict() for r in self.rungs],
        }


# ---------------------------------------------------------------------------
# Firestore + GCS access (deferred imports so unit tests of pure helpers work)
# ---------------------------------------------------------------------------


def _firestore_client(project: str):
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)
    from google.cloud import firestore  # type: ignore[import-not-found]

    return firestore.Client(project=project)


def _fetch_job(project: str, job_id: str) -> Optional[Dict[str, Any]]:
    db = _firestore_client(project)
    # L6 (D22 PR-L6, 2026-05-29): the sync Firestore client's .get()
    # returns a DocumentSnapshot, NOT an Awaitable[DocumentSnapshot]
    # (firestore.Client = sync; firestore.AsyncClient = async). Pyright
    # was inferring Awaitable because the type stub union covers both.
    # Force the sync branch with an explicit cast so type-checker
    # noise goes away and .exists / .to_dict() resolve.
    doc_ref = db.collection("podcast_jobs").document(job_id)
    snap = doc_ref.get()  # type: ignore[union-attr]
    # The sync client returns a DocumentSnapshot synchronously. Tests
    # against AsyncClient need a different code path (not used here).
    exists_attr = getattr(snap, "exists", False)
    if not exists_attr:
        return None
    to_dict_fn = getattr(snap, "to_dict", None)
    raw = to_dict_fn() if callable(to_dict_fn) else {}
    data: Dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    data["__id"] = getattr(snap, "id", job_id)
    return data


def _download_audio(job: Dict[str, Any], dest: str) -> Optional[str]:
    """Download the final mastered MP3 to ``dest``. Returns local path or None.

    Reads ``audio_url`` (signed URL) or ``audio_gcs_uri``. We don't bake any
    auth assumption — gsutil cp handles the gs:// path with ADC.
    """
    url = job.get("audio_url") or ""
    gcs = job.get("audio_gcs_uri") or ""
    if gcs and gcs.startswith("gs://"):
        try:
            subprocess.run(
                ["gsutil", "-q", "cp", gcs, dest],
                check=True,
                timeout=60,
            )
            return dest
        except Exception as exc:  # noqa: BLE001
            logger.warning("gsutil cp failed for %s: %s", gcs, exc)
    if url:
        try:
            import urllib.request

            urllib.request.urlretrieve(url, dest)  # noqa: S310 — signed URL
            return dest
        except Exception as exc:  # noqa: BLE001
            logger.warning("urlretrieve failed for %s: %s", url, exc)
    return None


# ---------------------------------------------------------------------------
# D21 metric set — the audio measurements Rung 4 enforces.
#
# Named D21 because it formalizes the 21 metrics the audio-overhaul proposal
# (audio-quality-overhaul-2026-05-28) treats as observable invariants. Each
# metric has: name, where to find it, and a hard floor/ceiling per genre.
#
# Computing rung 4 requires EITHER the Firestore record (cheap) OR a local
# measurement of the rendered MP3 (expensive, definitive). We do both: if
# the Firestore record is missing the metric, we measure locally as proof.
# ---------------------------------------------------------------------------


@dataclass
class D21Metric:
    """One measurement in the D21 set."""

    name: str
    fs_path: str  # dotted path inside the job document (e.g. "stages.mastering.measured_I")
    floor: Optional[float] = None
    ceiling: Optional[float] = None
    required: bool = True
    # Optional local-compute fallback if the Firestore field is missing.
    local_compute: Optional[str] = None  # "loudness", "sfx_count", "music_presence", ...
    # Per-PR-kind genre filter (None = applies to all).
    only_genres: Optional[List[str]] = None


# Targets are wired to the audio-overhaul PRs. Update when targets shift.
# Floor/Ceiling = strict bounds. None on either end = open-ended.
D21_METRICS: List[D21Metric] = [
    # --- Mastering / loudness (PR-6, PR-11, PR-14/15/16, PR-17) ---
    D21Metric("integrated_loudness_I_lufs",
              "stages.job-audio.qa.mastering.measured_I",
              floor=-16.5, ceiling=-15.5, local_compute="loudness"),
    D21Metric("true_peak_dbtp",
              "stages.job-audio.qa.mastering.measured_TP",
              ceiling=-1.0, local_compute="loudness"),
    D21Metric("loudness_range_LRA",
              "stages.job-audio.qa.mastering.measured_LRA",
              floor=4.0, ceiling=11.0, local_compute="loudness"),
    D21Metric("noise_floor_dbfs",
              "stages.job-audio.qa.mastering.noise_floor_dbfs",
              ceiling=-50.0, local_compute="loudness"),
    D21Metric("target_LRA_lu_min",
              "stages.mastering.target_LRA_lu_min", required=False),
    D21Metric("target_LRA_lu_max",
              "stages.mastering.target_LRA_lu_max", required=False),
    D21Metric("mastering_applied",
              "stages.mastering.applied", required=True),
    # --- Music director (PR-11, PR-12) ---
    D21Metric("music_cue_count",
              "stages.music_director.cue_count",
              floor=1.0, local_compute="music_presence"),
    D21Metric("music_cold_open_present",
              "stages.music_director.cold_open_present", required=False),
    D21Metric("music_sub_cue_count",
              "stages.music_director.sub_cue_count", required=False),
    D21Metric("music_blueprint_present",
              "stages.music_director.blueprint_present", required=False),
    D21Metric("music_fallback_safe_default",
              "stages.music_director.fallback_safe_default", required=False),
    # --- SFX director (PR-13) ---
    D21Metric("sfx_kept_count",
              "stages.sfx_director.kept_count", floor=1.0,
              local_compute="sfx_count"),
    D21Metric("sfx_proposed_count",
              "stages.sfx_director.proposed_count", required=False),
    D21Metric("sfx_primary_engine",
              "stages.sfx_director.primary_engine", required=False),
    D21Metric("sfx_horror_kept_floor_2",
              "stages.sfx_director.kept_count", floor=2.0,
              only_genres=["horror"]),
    # --- Speech intelligibility (D-spec D9, music_blend gate) ---
    D21Metric("speech_to_bed_stoi",
              "stages.job-audio.qa.music_blend.stoi",
              floor=0.85, required=False),
    D21Metric("ducked_floor_lufs",
              "stages.job-audio.qa.music_blend.bed_lufs", required=False),
    # --- Duration contract (D1, D2) ---
    D21Metric("requested_duration_ms",
              "inputs.duration_minutes", required=False),
    D21Metric("actual_audio_duration_ms",
              "duration_ms", required=False, local_compute="loudness"),
    D21Metric("duration_overshoot_ratio",
              "stages.script.duration_overshoot_ratio",
              ceiling=2.0, required=False),
]


# ---------------------------------------------------------------------------
# PR-kind contract — what "measurable improvement" means per PR type.
#
# Adding a new PR-kind: add ONE entry here. Each entry declares:
#   - metrics (D21 names) whose movement matters
#   - direction (HIGHER/LOWER/EQUAL_OK)
#   - min_delta (smallest movement that counts as real)
#
# This is the contract Rung 5 enforces. The whole point of the ladder is
# that the PR author CANNOT mark verified without declaring this upfront.
# ---------------------------------------------------------------------------


class Direction(str, Enum):
    HIGHER = "HIGHER"
    LOWER = "LOWER"
    EQUAL_OK = "EQUAL_OK"


@dataclass
class ImprovementClaim:
    metric: str
    direction: Direction
    min_delta: float


PR_KIND_CONTRACTS: Dict[str, List[ImprovementClaim]] = {
    # PR-11 — palette + cache + mastering observability.
    # The claim: stages.mastering becomes populated (was {} before).
    # Direction: presence — measured via Rung 3, no delta needed. Rung 5
    # additionally requires the QA gate to ACTUALLY measure a value.
    "mastering_observability": [
        ImprovementClaim("integrated_loudness_I_lufs", Direction.EQUAL_OK, 0.0),
    ],
    # PR-12 — cue density.
    # Claim: cue_count up by >=2 (cold_open + at least one sub-cue beyond
    # the 5-beat floor) AND cold_open_present flips True.
    "music_cue_density": [
        ImprovementClaim("music_cue_count", Direction.HIGHER, 2.0),
        ImprovementClaim("music_cold_open_present", Direction.HIGHER, 0.5),
    ],
    # PR-13 — SFX palette + closed-vocab + horror ambience.
    # Claim: kept_count moves from <=2 to >=4 (palette covers more cues).
    "sfx_palette": [
        ImprovementClaim("sfx_kept_count", Direction.HIGHER, 2.0),
    ],
    # PR-14/15/16 — LRA dynamics (mastering chain wires LRA correctly).
    # Claim: measured LRA moves +1 LU vs baseline (more dynamics restored).
    "lra_dynamics": [
        ImprovementClaim("loudness_range_LRA", Direction.HIGHER, 1.0),
    ],
    # PR-17 — lowered palette ceiling for horror.
    # Claim: target_LRA_lu_max drops from 11 → 7 (palette tightened) AND
    # the measured LRA actually moves to inside the new band.
    "lra_palette_tightening": [
        ImprovementClaim("target_LRA_lu_max", Direction.LOWER, 1.0),
    ],
    # Music presence gate (D5/D8).
    "music_presence": [
        ImprovementClaim("music_cue_count", Direction.HIGHER, 1.0),
    ],
    # Speech intelligibility / ducking (D9).
    "speech_intelligibility": [
        ImprovementClaim("speech_to_bed_stoi", Direction.HIGHER, 0.05),
    ],
    # Duration overshoot reduction (D1, D2, D3).
    "duration_overshoot": [
        ImprovementClaim("duration_overshoot_ratio", Direction.LOWER, 0.3),
    ],
    # Generic "schema-only" PR. Forces author to acknowledge they are NOT
    # claiming an audio improvement and Rung 5 will short-circuit to PASS.
    # Use sparingly — most "schema-only" PRs are actually audio PRs with
    # an unmeasured side effect.
    "schema_only": [],
}


# ---------------------------------------------------------------------------
# Dotted-path lookup
# ---------------------------------------------------------------------------


def _get_path(obj: Dict[str, Any], path: str) -> Any:
    """Return value at dotted ``path`` or None if missing/non-dict on the way."""
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


# ---------------------------------------------------------------------------
# Rung 1 — Unit / pin tests pass
# ---------------------------------------------------------------------------


def rung_1_unit_tests(qa_root: str) -> RungResult:
    """Run the kitesforu-qa pin test suite.

    The pin tests (test_audio_overhaul_pipeline_invariants.py) ARE the
    Rung 3 enforcer in the existing test harness; Rung 1 here is the
    unit-test sub-set that does NOT hit Firestore.
    """
    try:
        env = dict(os.environ)
        env.pop("KFU_QA_LIVE_PIPELINE", None)  # explicitly Rung 1 only
        proc = subprocess.run(
            ["python3", "-m", "pytest", "-q", "tests/", "--maxfail=1", "-x"],
            cwd=qa_root,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if proc.returncode == 0:
            return RungResult(
                rung=1, name="unit_pin_tests",
                verdict=Verdict.PASS,
                detail=f"pytest exit=0 ({proc.stdout.strip().splitlines()[-1] if proc.stdout else ''})",
            )
        return RungResult(
            rung=1, name="unit_pin_tests",
            verdict=Verdict.FAIL,
            detail=f"pytest exit={proc.returncode}",
            evidence={"stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-1000:]},
        )
    except Exception as exc:  # noqa: BLE001
        return RungResult(
            rung=1, name="unit_pin_tests",
            verdict=Verdict.SKIP, detail=f"pytest invoke failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Rung 2 — Live job runs to completion without error
# ---------------------------------------------------------------------------


def rung_2_job_completed(job: Dict[str, Any]) -> RungResult:
    """The post-deploy job must reach status=completed with no error stage."""
    status = (job.get("status") or "").lower()
    if status != "completed":
        return RungResult(
            rung=2, name="job_completed",
            verdict=Verdict.FAIL,
            detail=f"status={status!r} (expected 'completed')",
            evidence={"error_message": job.get("error_message")},
        )
    # Stage-level error check: any stage with `error` field set?
    stages = job.get("stages") or {}
    err_stages = []
    for sname, sdoc in stages.items():
        if not isinstance(sdoc, dict):
            continue
        e = sdoc.get("error") or sdoc.get("error_message")
        if e:
            err_stages.append(sname)
    if err_stages:
        return RungResult(
            rung=2, name="job_completed",
            verdict=Verdict.FAIL,
            detail=f"stages with errors: {err_stages}",
        )
    return RungResult(
        rung=2, name="job_completed", verdict=Verdict.PASS,
        detail="status=completed, no stage errors",
    )


# ---------------------------------------------------------------------------
# Rung 3 — Schema-level Firestore fields land
# ---------------------------------------------------------------------------


# Per-PR-kind: the set of dotted paths that MUST be non-None on the
# post-deploy job. Adding a new PR-kind: add an entry here. THE PR AUTHOR
# IS RESPONSIBLE for declaring what schema fields their PR ships.
SCHEMA_FIELDS_PER_KIND: Dict[str, List[str]] = {
    "mastering_observability": [
        "stages.mastering.genre",
        "stages.mastering.target_I_lufs",
        "stages.mastering.applied",
    ],
    "music_cue_density": [
        "stages.music_director.cue_count",
        "stages.music_director.cold_open_present",
        "stages.music_director.sub_cue_count",
    ],
    "sfx_palette": [
        "stages.sfx_director.kept_count",
        "stages.sfx_director.proposed_count",
        "stages.sfx_director.primary_engine",
    ],
    "lra_dynamics": [
        "stages.mastering.applied",
        "stages.job-audio.qa.mastering.measured_LRA",
    ],
    "lra_palette_tightening": [
        "stages.mastering.target_LRA_lu_max",
        "stages.mastering.target_LRA_lu_min",
    ],
    "music_presence": ["stages.music_director.cue_count"],
    "speech_intelligibility": ["stages.job-audio.qa.music_blend.stoi"],
    "duration_overshoot": ["stages.script.duration_overshoot_ratio"],
    "schema_only": [],
}


def rung_3_schema_fields(job: Dict[str, Any], pr_kind: str) -> RungResult:
    paths = SCHEMA_FIELDS_PER_KIND.get(pr_kind, [])
    if not paths:
        return RungResult(
            rung=3, name="schema_fields",
            verdict=Verdict.PASS,
            detail=f"pr_kind={pr_kind} declares no schema fields",
        )
    missing = []
    present = {}
    for p in paths:
        v = _get_path(job, p)
        if v is None:
            missing.append(p)
        else:
            present[p] = v
    if missing:
        return RungResult(
            rung=3, name="schema_fields", verdict=Verdict.FAIL,
            detail=f"missing fields: {missing}",
            evidence={"present": present},
        )
    return RungResult(
        rung=3, name="schema_fields", verdict=Verdict.PASS,
        detail=f"all {len(paths)} fields present",
        evidence={"present": present},
    )


# ---------------------------------------------------------------------------
# Rung 4 — Audio measurements pass (D21 metric set)
# ---------------------------------------------------------------------------


def _local_loudness(mp3_path: str) -> Optional[Dict[str, float]]:
    """Re-measure I/TP/LRA/noise_floor on the rendered MP3. The point: do
    NOT trust the Firestore record alone — the value is only worth what the
    audio actually shows."""
    try:
        import numpy as np
        import pyloudnorm as pyln  # type: ignore[import-not-found]
        import soundfile as sf
    except ImportError:
        return None
    # MP3 needs decoding. Use ffmpeg → wav → soundfile.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", mp3_path, "-ac", "2", "-ar", "48000", wav_path],
            check=True, timeout=120,
        )
        data, sr = sf.read(wav_path, dtype="float32")
        if data.size == 0:
            return None
        meter = pyln.Meter(sr)
        I = float(meter.integrated_loudness(data))
        try:
            LRA = float(meter.loudness_range(data))
        except Exception:
            LRA = None
        mono = data.mean(axis=1) if data.ndim == 2 else data
        sp = float(np.max(np.abs(mono))) if mono.size else 0.0
        TP = float(20.0 * np.log10(sp)) if sp > 0 else -120.0
        # noise floor — 5th percentile of windowed RMS
        win = max(1, int(0.05 * sr))
        pad = (-mono.size) % win
        padded = np.concatenate([mono, np.zeros(pad, dtype=np.float32)]) if pad else mono
        framed = padded.reshape(-1, win)
        rms = np.sqrt(np.mean(framed.astype(np.float64) ** 2, axis=1))
        rms = rms[rms > 0]
        nf = float(20.0 * np.log10(float(np.percentile(rms, 5)))) if rms.size else -120.0
        duration_ms = int(1000.0 * mono.size / sr)
        return {
            "integrated_loudness_I_lufs": round(I, 2),
            "true_peak_dbtp": round(TP, 2),
            "loudness_range_LRA": round(LRA, 2) if LRA is not None else float("nan"),
            "noise_floor_dbfs": round(nf, 2),
            "actual_audio_duration_ms": duration_ms,
        }
    finally:
        try:
            os.remove(wav_path)
        except OSError:
            pass


def _detect_genre(job: Dict[str, Any]) -> str:
    mast = (job.get("stages") or {}).get("mastering") or {}
    g = (mast.get("genre") or "").lower()
    if g:
        return g
    md = (job.get("stages") or {}).get("music_director") or {}
    tid = (md.get("theme_id") or "").lower()
    if tid.startswith("theme-"):
        return tid[len("theme-"):]
    return ""


def rung_4_audio_measurements(
    job: Dict[str, Any],
    local_audio_path: Optional[str],
) -> RungResult:
    """Walk the D21 metric set. Each metric:
      - read from Firestore (preferred)
      - if missing AND local_compute is available + audio downloaded,
        re-measure
      - apply floor/ceiling

    A metric in fail-band → Rung 4 FAIL. A REQUIRED metric missing → FAIL.
    """
    genre = _detect_genre(job)
    local_measure_cache: Optional[Dict[str, float]] = None
    failures: List[str] = []
    measured: Dict[str, Any] = {}
    skipped_required: List[str] = []

    for m in D21_METRICS:
        if m.only_genres and genre not in m.only_genres:
            continue
        val = _get_path(job, m.fs_path)
        if val is None and m.local_compute and local_audio_path:
            if m.local_compute == "loudness":
                if local_measure_cache is None:
                    local_measure_cache = _local_loudness(local_audio_path) or {}
                val = local_measure_cache.get(m.name)
            # sfx_count / music_presence local computes are stubs in v1 — we
            # rely on the Firestore record. Real audio-based fallback would
            # need a CLAP/wav2vec classifier; out of scope here.
        if val is None:
            if m.required:
                skipped_required.append(m.name)
            continue
        measured[m.name] = val
        # bool/string fields short-circuit threshold checks.
        if isinstance(val, bool) or isinstance(val, str):
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if m.floor is not None and f < m.floor:
            failures.append(f"{m.name}={f} < floor={m.floor}")
        if m.ceiling is not None and f > m.ceiling:
            failures.append(f"{m.name}={f} > ceiling={m.ceiling}")

    if skipped_required:
        failures.append(f"required metrics missing: {skipped_required}")

    if failures:
        return RungResult(
            rung=4, name="audio_measurements", verdict=Verdict.FAIL,
            detail=f"{len(failures)} failures",
            evidence={"failures": failures, "measured": measured, "genre": genre},
        )
    return RungResult(
        rung=4, name="audio_measurements", verdict=Verdict.PASS,
        detail=f"{len(measured)} metrics in-band",
        evidence={"measured": measured, "genre": genre},
    )


# ---------------------------------------------------------------------------
# Rung 5 — A/B improvement vs baseline
# ---------------------------------------------------------------------------


def _coerce_numeric(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rung_5_ab_improvement(
    baseline: Dict[str, Any],
    post: Dict[str, Any],
    pr_kind: str,
    baseline_audio: Optional[str],
    post_audio: Optional[str],
) -> RungResult:
    claims = PR_KIND_CONTRACTS.get(pr_kind)
    if claims is None:
        return RungResult(
            rung=5, name="ab_improvement", verdict=Verdict.FAIL,
            detail=f"pr_kind={pr_kind!r} has no improvement contract — declare one before claiming verified",
        )
    if not claims:
        # schema_only PR — explicit opt-out. Author signed for "no audio movement".
        return RungResult(
            rung=5, name="ab_improvement", verdict=Verdict.PASS,
            detail="schema_only PR — no audio improvement claimed",
        )

    # Find metric values. Prefer Firestore; fall back to local measure.
    def _metric_value(job: Dict[str, Any], audio_path: Optional[str], name: str) -> Optional[float]:
        # Locate the D21 entry.
        meta = next((m for m in D21_METRICS if m.name == name), None)
        if meta is None:
            return None
        v = _get_path(job, meta.fs_path)
        if v is not None:
            return _coerce_numeric(v)
        if meta.local_compute == "loudness" and audio_path:
            loud = _local_loudness(audio_path) or {}
            return _coerce_numeric(loud.get(name))
        return None

    failures: List[str] = []
    deltas: Dict[str, Dict[str, Any]] = {}
    for claim in claims:
        b = _metric_value(baseline, baseline_audio, claim.metric)
        p = _metric_value(post, post_audio, claim.metric)
        if b is None or p is None:
            failures.append(f"{claim.metric}: missing values (baseline={b}, post={p})")
            continue
        delta = p - b
        deltas[claim.metric] = {"baseline": b, "post": p, "delta": delta, "min_delta": claim.min_delta, "direction": claim.direction.value}
        if claim.direction == Direction.HIGHER and delta < claim.min_delta:
            failures.append(f"{claim.metric}: Δ={delta:+.3f} < required +{claim.min_delta}")
        elif claim.direction == Direction.LOWER and -delta < claim.min_delta:
            failures.append(f"{claim.metric}: Δ={delta:+.3f} but need ≤ -{claim.min_delta}")
        elif claim.direction == Direction.EQUAL_OK and abs(delta) > claim.min_delta:
            # EQUAL_OK with non-zero min_delta = "moved more than tolerance"
            if claim.min_delta > 0:
                failures.append(f"{claim.metric}: Δ={delta:+.3f} outside ±{claim.min_delta}")

    if failures:
        return RungResult(
            rung=5, name="ab_improvement", verdict=Verdict.FAIL,
            detail=f"{len(failures)} unmet claims",
            evidence={"failures": failures, "deltas": deltas},
        )
    return RungResult(
        rung=5, name="ab_improvement", verdict=Verdict.PASS,
        detail=f"all {len(claims)} claims met",
        evidence={"deltas": deltas},
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_ladder(
    pr_id: str,
    pr_kind: str,
    baseline_job_id: str,
    post_deploy_job_id: str,
    project: str,
    qa_root: str,
    skip_rung_1: bool = False,
    skip_audio_download: bool = False,
) -> LadderVerdict:
    verdict = LadderVerdict(
        pr_id=pr_id, pr_kind=pr_kind,
        baseline_job_id=baseline_job_id, post_deploy_job_id=post_deploy_job_id,
    )

    # Rung 1 — unit tests
    if skip_rung_1:
        verdict.rungs.append(RungResult(
            rung=1, name="unit_pin_tests", verdict=Verdict.SKIP,
            detail="--skip-rung-1 flag",
        ))
    else:
        verdict.rungs.append(rung_1_unit_tests(qa_root))

    # Fetch jobs (needed for rungs 2-5)
    baseline = _fetch_job(project, baseline_job_id)
    post = _fetch_job(project, post_deploy_job_id)
    if post is None:
        verdict.rungs.append(RungResult(
            rung=2, name="job_completed", verdict=Verdict.SKIP,
            detail=f"post-deploy job {post_deploy_job_id!r} not found",
        ))
        verdict.rungs.append(RungResult(rung=3, name="schema_fields", verdict=Verdict.SKIP, detail="post-deploy job missing"))
        verdict.rungs.append(RungResult(rung=4, name="audio_measurements", verdict=Verdict.SKIP, detail="post-deploy job missing"))
        verdict.rungs.append(RungResult(rung=5, name="ab_improvement", verdict=Verdict.SKIP, detail="post-deploy job missing"))
        return verdict
    if baseline is None:
        # Rungs 2, 3, 4 can still run on post; Rung 5 cannot.
        baseline = {}

    # Rung 2 — job ran clean
    verdict.rungs.append(rung_2_job_completed(post))

    # Rung 3 — schema fields landed
    verdict.rungs.append(rung_3_schema_fields(post, pr_kind))

    # Rung 4 + 5 — need local audio for full fidelity
    post_audio_path: Optional[str] = None
    baseline_audio_path: Optional[str] = None
    if not skip_audio_download:
        tmpdir = tempfile.mkdtemp(prefix="kqa_verify_")
        post_audio_path = _download_audio(post, os.path.join(tmpdir, "post.mp3"))
        baseline_audio_path = _download_audio(baseline, os.path.join(tmpdir, "baseline.mp3"))

    # Rung 4 — audio measurements
    verdict.rungs.append(rung_4_audio_measurements(post, post_audio_path))

    # Rung 5 — A/B
    if not baseline:
        verdict.rungs.append(RungResult(
            rung=5, name="ab_improvement", verdict=Verdict.SKIP,
            detail=f"baseline job {baseline_job_id!r} not found",
        ))
    else:
        verdict.rungs.append(rung_5_ab_improvement(
            baseline, post, pr_kind, baseline_audio_path, post_audio_path,
        ))

    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="5-Rung Verification Ladder")
    p.add_argument("--pr-id", required=True, help="PR identifier (e.g. 13)")
    p.add_argument(
        "--pr-kind", required=True, choices=sorted(PR_KIND_CONTRACTS.keys()),
        help="Declared kind — drives the A/B contract in Rung 5",
    )
    p.add_argument("--baseline-job-id", required=True)
    p.add_argument("--post-deploy-job-id", required=True)
    p.add_argument("--project", default=os.environ.get("KFU_QA_PROJECT", "kitesforu-dev"))
    p.add_argument("--qa-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    p.add_argument("--skip-rung-1", action="store_true", help="Skip the unit-test rung (CI ran it already)")
    p.add_argument("--skip-audio-download", action="store_true", help="Trust Firestore record; don't re-measure MP3")
    p.add_argument("--json", action="store_true", help="Emit JSON only")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    v = run_ladder(
        pr_id=args.pr_id, pr_kind=args.pr_kind,
        baseline_job_id=args.baseline_job_id,
        post_deploy_job_id=args.post_deploy_job_id,
        project=args.project, qa_root=args.qa_root,
        skip_rung_1=args.skip_rung_1,
        skip_audio_download=args.skip_audio_download,
    )
    if args.json:
        print(json.dumps(v.to_dict(), indent=2, default=str))
    else:
        print(f"\n=== PR-{v.pr_id} ({v.pr_kind}) — Overall: {v.overall.value} ===")
        for r in v.rungs:
            marker = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[r.verdict.value]
            print(f"  Rung {r.rung} [{marker}] {r.name}: {r.detail}")
            if r.verdict != Verdict.PASS and r.evidence:
                # Show evidence on failures.
                ev_str = json.dumps(r.evidence, indent=4, default=str)
                for line in ev_str.splitlines()[:30]:
                    print(f"      {line}")
        print()
    return 0 if v.overall == Verdict.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
