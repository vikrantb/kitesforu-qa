"""D21 L5.4 — live kqa pin: download a job's final mp3 + speech_only.mp3
from GCS and run the listen-test verifier against the genre profile
the renderer actually targeted.

This is the operational bridge between the L5.2 verifier module + the
L5.3 per-genre profile table and the real production audio. Without
this command the verifier exists only as a unit-tested library; with
it, an operator (or a Cloud-Scheduler cron) can post-deploy any audio
change and immediately see whether the rendered mp3 hits the bands
the renderer was supposed to.

Per-Firestore-job paths
-----------------------
The audio worker uploads each job's artifacts to a deterministic
public GCS prefix:

  gs://{public_bucket}/{public_prefix}podcasts/{user_id}/{job_id}/
    audio.mp3              -- final mastered mix (THE user-facing file)
    speech_only.mp3        -- speech bed before music/SFX (PR #737)
    blueprint_final.json   -- the architect's blueprint (for genre lookup)

When ``speech_only.mp3`` is missing (legacy jobs pre-PR #737) the
verifier still grades loudness + duration; STOI/SMR/music_presence/
SFX-transient axes degrade gracefully to ``None`` + a note.

Why a dedicated module rather than inline in cli.py
---------------------------------------------------
The CLI shape (click options + JSON pretty-print) is one thing. The
*download + verify + report* sequence is the part future automation
will reuse (Cloud Scheduler cron, Slack-post webhook). Keeping it in
``verify_live.py`` makes the public surface stable.

Fail-soft: every external call (Firestore, GCS) is wrapped — a partial
report is more useful than a stack trace, especially when this runs
on a cron.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .profiles import get_profile
from .stages.listen_test import (
    GenreProfile,
    ListenTestReport,
    verify_audio_quality,
)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_job_gcs_prefix(
    *, user_id: str, job_id: str, public_prefix: str = "v1/",
    bucket: Optional[str] = None,
) -> str:
    """Build the canonical gs://.../podcasts/{user_id}/{job_id}/ prefix.

    ``bucket`` defaults to the env-configured public bucket (mirrors
    the worker's ``self.public_prefix`` resolution). Callers that
    don't have it can pass it explicitly.
    """
    bucket = bucket or os.environ.get(
        "KFU_PUBLIC_BUCKET", "kitesforu-public",
    )
    return f"gs://{bucket}/{public_prefix.rstrip('/')}/podcasts/{user_id}/{job_id}/"


# ---------------------------------------------------------------------------
# Firestore lookup of genre + duration (so the verifier asks for the
# bands the renderer was actually targeting, not a guess)
# ---------------------------------------------------------------------------


@dataclass
class JobAudioContext:
    """The minimum job metadata the verifier needs."""

    job_id: str
    user_id: str
    genre: str = "default"
    expected_duration_s: Optional[float] = None
    audio_gcs_uri: Optional[str] = None
    speech_only_gcs_uri: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "genre": self.genre,
            "expected_duration_s": self.expected_duration_s,
            "audio_gcs_uri": self.audio_gcs_uri,
            "speech_only_gcs_uri": self.speech_only_gcs_uri,
            "notes": list(self.notes),
        }


def load_job_context(job_id: str) -> Optional[JobAudioContext]:
    """Pull (genre, duration, user_id) from Firestore for a job.

    Returns None when:
      - google-cloud-firestore is not installed
      - the job document is missing
      - the document lacks user_id (we can't build the GCS path)

    Other fields are populated best-effort; an empty/missing field
    becomes a ``notes`` entry rather than a hard failure (the
    verifier still grades loudness + duration without genre/speech).
    """
    try:
        from google.cloud import firestore  # type: ignore[import-not-found]
    except ImportError:
        return None

    try:
        db = firestore.Client()
        snap = db.collection("jobs").document(job_id).get()
    except Exception:
        return None
    if not snap.exists:
        return None
    doc = snap.to_dict() or {}

    user_id = doc.get("user_id") or doc.get("user")
    if not user_id:
        return None

    # Genre resolution: prefer the blueprint's genre_module (architect-
    # committed); fall back to the request's `genre` (intake-committed).
    # Both can be empty (audio_overview style); default profile handles it.
    genre = ""
    bp = doc.get("blueprint") or doc.get("story_blueprint") or {}
    if isinstance(bp, dict):
        gm = bp.get("genre_module") or {}
        if isinstance(gm, dict):
            genre = gm.get("genre") or ""
    if not genre:
        prefs = doc.get("preferences") or {}
        if isinstance(prefs, dict):
            genre = prefs.get("genre") or ""
    if not genre:
        genre = doc.get("genre") or "default"

    # Expected duration: prefer outline.duration_min (architect-sized);
    # fall back to inputs.duration_min (user-requested).
    expected_seconds: Optional[float] = None
    outline = doc.get("outline") or {}
    if isinstance(outline, dict):
        d = outline.get("duration_min")
        if isinstance(d, (int, float)) and d > 0:
            expected_seconds = float(d) * 60.0
    if expected_seconds is None:
        inputs = doc.get("inputs") or {}
        if isinstance(inputs, dict):
            d = inputs.get("duration_min")
            if isinstance(d, (int, float)) and d > 0:
                expected_seconds = float(d) * 60.0

    prefix = _resolve_job_gcs_prefix(user_id=str(user_id), job_id=job_id)
    ctx = JobAudioContext(
        job_id=job_id,
        user_id=str(user_id),
        genre=str(genre).strip() or "default",
        expected_duration_s=expected_seconds,
        audio_gcs_uri=prefix + "audio.mp3",
        speech_only_gcs_uri=prefix + "speech_only.mp3",
    )
    if expected_seconds is None:
        ctx.notes.append(
            "expected duration not found on job_doc — "
            "duration delta axis will not grade"
        )
    if not genre or genre == "default":
        ctx.notes.append(
            "genre not found on job_doc — using DEFAULT profile bands "
            "(catch-all; per-genre tightenings won't apply)"
        )
    return ctx


# ---------------------------------------------------------------------------
# GCS download (single file)
# ---------------------------------------------------------------------------


def _gcs_download(uri: str, dest_path: str) -> bool:
    """Download a single gs:// blob. Returns True on success, False
    when the blob is missing / google-cloud-storage isn't installed.
    Never raises — the verifier degrades gracefully on missing inputs."""
    try:
        from google.cloud import storage  # type: ignore[import-not-found]
        from urllib.parse import urlparse
    except ImportError:
        return False
    try:
        parsed = urlparse(uri)
        if parsed.scheme != "gs":
            return False
        bucket = parsed.netloc
        blob_path = parsed.path.lstrip("/")
        client = storage.Client()
        blob = client.bucket(bucket).blob(blob_path)
        if not blob.exists(client=client):
            return False
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        blob.download_to_filename(dest_path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public entry point — verify a single job by id
# ---------------------------------------------------------------------------


@dataclass
class LiveVerifyResult:
    """Combines the JobAudioContext (what we measured) + the
    ListenTestReport (the verdict) into a single dict the operator
    or a Slack-post helper can consume."""

    context: JobAudioContext
    report: Optional[ListenTestReport]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context": self.context.to_dict(),
            "report": (
                self.report.to_dict() if self.report is not None else None
            ),
            "error": self.error,
            "verdict": (
                self.report.verdict
                if self.report is not None
                else "ERROR"
            ),
        }


def verify_job_live(
    job_id: str, *,
    profile_override: Optional[GenreProfile] = None,
    work_dir: Optional[str] = None,
) -> LiveVerifyResult:
    """Download + verify a single live job by id.

    The orchestration:

      1. Look up the job in Firestore -> JobAudioContext (genre +
         expected duration + GCS paths).
      2. Download audio.mp3 + (best-effort) speech_only.mp3 to a
         tempdir.
      3. Resolve the GenreProfile via L5.3's ``get_profile()`` unless
         the caller passed an override (for canary / what-if).
      4. Call ``verify_audio_quality()`` from L5.2.
      5. Bundle the context + report.

    Cleanup of the tempdir runs in a finally block so a crashed
    verifier doesn't fill /tmp on a cron-scheduled rollout.
    """
    ctx = load_job_context(job_id)
    if ctx is None:
        return LiveVerifyResult(
            context=JobAudioContext(job_id=job_id, user_id="?"),
            report=None,
            error=(
                "could not load job from Firestore "
                "(missing google-cloud-firestore, missing doc, "
                "or doc lacked user_id)"
            ),
        )

    own_work_dir = work_dir is None
    work_dir = work_dir or tempfile.mkdtemp(prefix="kqa_live_")
    audio_path = os.path.join(work_dir, f"{job_id}.audio.mp3")
    speech_path = os.path.join(work_dir, f"{job_id}.speech_only.mp3")

    try:
        if not ctx.audio_gcs_uri or not _gcs_download(
            ctx.audio_gcs_uri, audio_path,
        ):
            return LiveVerifyResult(
                context=ctx, report=None,
                error=(
                    f"could not download {ctx.audio_gcs_uri} "
                    "(missing google-cloud-storage, missing blob, or "
                    "permission denied)"
                ),
            )

        speech_local: Optional[str] = None
        if ctx.speech_only_gcs_uri and _gcs_download(
            ctx.speech_only_gcs_uri, speech_path,
        ):
            speech_local = speech_path
        else:
            ctx.notes.append(
                "speech_only.mp3 not in GCS (legacy pre-PR-737 job?) — "
                "STOI/SMR/music_presence/SFX axes will not grade"
            )

        profile = profile_override or get_profile(ctx.genre)
        report = verify_audio_quality(
            audio_path=audio_path,
            speech_only_path=speech_local,
            genre=ctx.genre,
            profile=profile,
            expected_duration_s=ctx.expected_duration_s,
            work_dir=work_dir,
        )
        return LiveVerifyResult(context=ctx, report=report)

    finally:
        if own_work_dir and os.path.isdir(work_dir):
            try:
                shutil.rmtree(work_dir)
            except Exception:
                pass


__all__ = [
    "JobAudioContext",
    "LiveVerifyResult",
    "load_job_context",
    "verify_job_live",
]
