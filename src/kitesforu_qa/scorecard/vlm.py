"""vlm.py — the concrete VLM callable for axis 3 (VISUAL TRUTH).

Wired via ``ScorecardConfig.vlm_fn`` (the injection contract documented in ``config.py``):
``vlm_fn(image_uris, context) -> (score_0_100, note)``. ``axes.score_visual_truth`` calls this with the
photoreal-labeled beats' resolved asset URIs (``image_uris``, unchanged/back-compat) plus a richer
``context`` carrying ``video_path`` (the already-downloaded rendered MP4) and ``beats`` (the full per-beat
records: ``beat_index``, ``start_ms``, ``asset_uri``, ``modality``, ``render_mode``).

For each photoreal-labeled beat this module:

1. **Extracts a still frame** — ffmpeg-grabs a frame from the ALREADY-DOWNLOADED rendered video at the
   beat's ``start_ms`` (cheap: no extra network call, reuses what ``short_scorecard.py`` already pulled for
   the other axes). Falls back to downloading the beat's own stored asset (an image or, for motion-mode
   beats, a video render of just that beat) when no rendered video is available.
2. **Classifies the frame** via a cheap vision-capable LLM: a strict "is this a real PHOTOGRAPH or an
   ILLUSTRATION/cartoon/3d-render/painting" question — the axis that catches the "Pixar-labeled-photoreal
   lie" a keyword/heuristic check cannot see.
3. **Aggregates** — the axis score is the fraction of beats verified as genuine photographs x 100.

PROVIDER-AGNOSTIC (Tenet 1 — never hardcode a single provider): tries Gemini flash, then OpenAI's cheap
vision model, then Anthropic's cheap vision model, in ascending $/call order, using whichever has a live
API key. Cost is a ranking input only — every provider stays enabled; the cheapest available one is tried
first and the others are the failover chain, not a fallback-of-last-resort you'd have to explicitly opt into.

BOUNDED + FAIL-OPEN (orchestra-oe.md): each provider call is retried a small fixed number of times with a
per-call timeout; a single beat's extraction/classification failure degrades that beat to "unknown"
(excluded from the fraction, never counted as a fake pass) rather than crashing the run. If literally EVERY
beat fails, this function raises — the caller (``axes.score_visual_truth``) catches that and reports
``score=None`` (an honest null), never a fabricated score.

Cost (T2 cheap judge, ~$0.001/beat): see ``COST_CHANGELOG.md``. A 512px-downscaled still frame + a ~150-token
structured verdict is ¢-cheap on every provider in the chain.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# ── tuning knobs (bounded — Tenet: orchestra-oe.md) ───────────────────────────
_VLM_TIMEOUT_S = 20.0
_VLM_MAX_ATTEMPTS = 2          # per-provider retry cap (not a whole-chain retry)
_FRAME_MAX_DIM_PX = 512        # downscale — classification needs shape/texture, not fidelity ($ control)
_FFMPEG_TIMEOUT_S = 30.0
_DOWNLOAD_TIMEOUT_S = 60.0
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

_VERDICT_INSTRUCTION = (
    "You are a strict photo-forensics judge. Look at the image and answer ONLY one question: is this a "
    "REAL PHOTOGRAPH (an actual camera photo of real people/places/objects, with photographic noise, real "
    "skin/material texture, and realistic lighting/optics), or is this an ILLUSTRATION / CARTOON / 3D-RENDER "
    "/ PAINTING / STYLIZED-ART image (including AI-generated images that merely LOOK glossy or airbrushed "
    "but show smoothed-over 3D-render/CGI shading, painterly brushwork, or cartoon proportions)? Many AI "
    "image generators produce stylized, cartoon-ish, or 3D-rendered results even when the prompt asked for "
    "'photorealistic' — judge the ACTUAL pixels in front of you, not what the image was supposed to be. "
    'Respond with JSON ONLY, no markdown fence, no other text: '
    '{"verdict": "photo" | "illustration", "confidence": <0.0-1.0 float>, "reason": "<one short sentence>"}'
)


@dataclass
class BeatVerdict:
    """One beat's VLM verdict (or lack thereof — a failure is an honest ``verdict=None``, never a guess)."""
    beat_index: Any
    verdict: str | None            # "photo" | "illustration" | None (unjudged — extraction/provider failure)
    confidence: float | None
    reason: str
    provider: str | None = None


def photo_vs_illustration_vlm_fn(image_uris: list[str], context: dict[str, Any]) -> tuple[float, str]:
    """The real ``vlm_fn`` for axis 3. See module docstring for the contract + design.

    ``context["beats"]`` (preferred, set by ``axes.score_visual_truth``) carries the full per-beat records
    needed for frame extraction; when absent (e.g. a minimal test double), falls back to treating each
    ``image_uris`` entry as a beat with only an ``asset_uri`` — extraction then relies solely on that asset
    (no video-timestamp frame is possible without a beat record).
    """
    beats = context.get("beats") or [{"beat_index": i, "asset_uri": u} for i, u in enumerate(image_uris)]
    video_path = context.get("video_path")

    verdicts = [judge_one_beat(beat, video_path) for beat in beats]

    judged = [v for v in verdicts if v.verdict is not None]
    if not judged:
        reasons = "; ".join(f"beat {v.beat_index}: {v.reason}" for v in verdicts) or "no beats to judge"
        raise RuntimeError(f"VLM could not judge any of {len(verdicts)} beat(s) — {reasons}")

    n_photo = sum(1 for v in judged if v.verdict == "photo")
    score = (n_photo / len(judged)) * 100.0

    per_beat = ", ".join(_format_beat_verdict(v) for v in verdicts)
    n_unjudged = len(verdicts) - len(judged)
    unknown_note = f"; {n_unjudged} beat(s) unjudged (extraction/provider failure, excluded)" if n_unjudged else ""
    note = f"{n_photo}/{len(judged)} judged-as-photo [{per_beat}]{unknown_note}"
    return score, note


def _format_beat_verdict(v: BeatVerdict) -> str:
    if v.confidence is None:
        return f"beat{v.beat_index}=unknown({v.reason[:60]!r})"
    provider_note = f" via {v.provider}" if v.provider else ""
    return f"beat{v.beat_index}={v.verdict or 'unknown'}({v.confidence:.2f}{provider_note})"


def judge_one_beat(beat: dict[str, Any], video_path: str | None) -> BeatVerdict:
    """Extract + classify ONE beat. Never raises — any failure becomes a ``verdict=None`` BeatVerdict
    (fail-open per beat; the aggregate in ``photo_vs_illustration_vlm_fn`` decides what that means)."""
    beat_index = beat.get("beat_index")
    cleanup: Callable[[], None] = lambda: None  # noqa: E731 — trivial no-op default, named for clarity
    try:
        frame_path, cleanup = extract_frame(beat, video_path)
        verdict, confidence, reason, provider = classify_frame(frame_path)
        return BeatVerdict(beat_index, verdict, confidence, reason, provider)
    except Exception as exc:  # noqa: BLE001 — one beat's failure must not crash the run
        return BeatVerdict(beat_index, None, None, f"{exc!r}")
    finally:
        cleanup()


# ── frame extraction ──────────────────────────────────────────────────────────


def extract_frame(beat: dict[str, Any], video_path: str | None) -> tuple[str, Callable[[], None]]:
    """Return ``(local_png_path, cleanup)``. Prefers ffmpeg-grabbing a frame from the already-downloaded
    rendered video at the beat's ``start_ms`` (cheap — no extra network hop); falls back to downloading the
    beat's own stored asset (``asset_uri``: an image, or a per-beat video render for motion-mode beats) and
    extracting a frame from THAT. Raises if no frame could be produced from any source."""
    tmp_dir = tempfile.mkdtemp(prefix="kqa_vlm_frame_")
    out_path = os.path.join(tmp_dir, "frame.png")

    def _cleanup() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    start_ms = beat.get("start_ms")
    if video_path and os.path.exists(video_path) and start_ms is not None:
        start_s = max(0.0, float(start_ms) / 1000.0)
        if _ffmpeg_frame(video_path, start_s, out_path):
            return out_path, _cleanup

    asset_uri = beat.get("asset_uri")
    if asset_uri:
        local_asset = _download_asset(str(asset_uri), tmp_dir)
        if local_asset:
            at_s = 0.0 if _is_image_path(local_asset) else 0.5
            if _ffmpeg_frame(local_asset, at_s, out_path):
                return out_path, _cleanup

    _cleanup()
    raise RuntimeError(
        f"no frame source available (video_path={video_path!r}, start_ms={start_ms!r}, "
        f"asset_uri={beat.get('asset_uri')!r})"
    )


def _ffmpeg_frame(src: str, at_s: float, out_path: str) -> bool:
    """ffmpeg-extract one downscaled frame from ``src`` at ``at_s`` seconds -> ``out_path`` (PNG).
    Works on both video files (seeks) and static images (``-ss`` on a still is a no-op, still emits it).
    ``False`` on any failure — never raises (the caller tries the next source)."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{at_s:.3f}", "-i", src, "-frames:v", "1",
                "-vf", f"scale='min({_FRAME_MAX_DIM_PX},iw)':-2",
                out_path,
            ],
            capture_output=True, timeout=_FFMPEG_TIMEOUT_S,
        )
        return result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:  # noqa: BLE001
        return False


def _download_asset(uri: str, dest_dir: str) -> str | None:
    """Download (or resolve a local path for) ``uri`` into ``dest_dir``. ``None`` on any failure."""
    dest = os.path.join(dest_dir, os.path.basename(uri) or "asset")
    try:
        if uri.startswith("gs://"):
            subprocess.run(["gsutil", "-q", "cp", uri, dest], check=True, timeout=_DOWNLOAD_TIMEOUT_S)
        elif uri.startswith("http://") or uri.startswith("https://"):
            import urllib.request
            urllib.request.urlretrieve(uri, dest)  # noqa: S310 — vetted job-doc-stamped asset URI
        elif os.path.exists(uri):
            return uri
        else:
            return None
        return dest
    except Exception:  # noqa: BLE001
        return None


def _is_image_path(path: str) -> bool:
    return path.lower().endswith(_IMAGE_EXTS)


# ── VLM classification (provider-agnostic — Tenet 1) ──────────────────────────


def _openai_ready() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY")) and importlib.util.find_spec("openai") is not None


def _anthropic_ready() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and importlib.util.find_spec("anthropic") is not None


def _gemini_ready() -> bool:
    has_key = bool(os.environ.get("GOOGLE_AI_API_KEY") or os.environ.get("GEMINI_API_KEY"))
    return has_key and importlib.util.find_spec("google.generativeai") is not None


# Ascending-$/call order (ranked by cost per Tenet 1 — every provider stays enabled; the cheapest
# AVAILABLE one is tried first, the rest are the failover chain, not an opt-in escalation).
_PROVIDER_CHAIN: list[tuple[str, Callable[[], bool], Callable[[str], tuple[str, float, str]]]] = []


def classify_frame(image_path: str) -> tuple[str, float, str, str]:
    """Try each vision provider in cost order; the first with a live key + importable SDK that succeeds
    wins. Raises only when every available provider failed (or none is configured) — the caller treats
    that as one unjudged beat, never a crash of the whole run."""
    errors: list[str] = []
    tried_any = False
    for name, ready, call_fn in _PROVIDER_CHAIN:
        if not ready():
            continue
        tried_any = True
        try:
            verdict, confidence, reason = _with_retries(call_fn, image_path)
            return verdict, confidence, reason, name
        except Exception as exc:  # noqa: BLE001 — try the next provider, never crash the beat
            errors.append(f"{name}: {exc!r}")
    if not tried_any:
        raise RuntimeError("no VLM provider configured (no API key set for gemini/openai/anthropic)")
    raise RuntimeError(f"every available VLM provider failed: {'; '.join(errors)}")


def _with_retries(call_fn: Callable[[str], tuple[str, float, str]], image_path: str) -> tuple[str, float, str]:
    last_exc: Exception | None = None
    for _attempt in range(_VLM_MAX_ATTEMPTS):
        try:
            return call_fn(image_path)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _parse_verdict_json(text: str) -> tuple[str, float, str]:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    data = json.loads(t)
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict not in ("photo", "illustration"):
        raise ValueError(f"unexpected verdict {verdict!r} in VLM response: {text[:200]!r}")
    confidence = float(data.get("confidence") or 0.0)
    reason = str(data.get("reason") or "")[:200]
    return verdict, confidence, reason


def _call_gemini(image_path: str) -> tuple[str, float, str]:
    import google.generativeai as genai

    api_key = os.environ.get("GOOGLE_AI_API_KEY") or os.environ["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    resp = model.generate_content(
        [{"mime_type": "image/png", "data": image_bytes}, _VERDICT_INSTRUCTION],
        generation_config={"temperature": 0.0, "max_output_tokens": 200},
        request_options={"timeout": _VLM_TIMEOUT_S},
    )
    return _parse_verdict_json(resp.text)


def _call_openai(image_path: str) -> tuple[str, float, str]:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=_VLM_TIMEOUT_S)
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _VERDICT_INSTRUCTION},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
                },
            ],
        }],
        max_tokens=200,
        temperature=0.0,
    )
    return _parse_verdict_json(resp.choices[0].message.content or "")


def _call_anthropic(image_path: str) -> tuple[str, float, str]:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=_VLM_TIMEOUT_S)
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    resp = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": _VERDICT_INSTRUCTION},
            ],
        }],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    return _parse_verdict_json(text)


_PROVIDER_CHAIN[:] = [
    ("gemini", _gemini_ready, _call_gemini),
    ("openai", _openai_ready, _call_openai),
    ("anthropic", _anthropic_ready, _call_anthropic),
]
