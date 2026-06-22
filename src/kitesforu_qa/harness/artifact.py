"""Artifact — a typed local view of a COMPLETED job for the check battery.

Built from the job document (the dict ``KitesForUClient.get_job`` returns, or a Firestore export)
plus optionally-downloaded local audio/images. Checks read typed accessors (``art.script_text``,
``art.genre``, ``art.segments``, ``art.beat_map``, ``art.audio_info``, ``art.costs``) instead of
digging through the raw doc, so a doc-shape change touches ONE place.

Primary path is ``from_doc`` — fully offline, $0, no network (the path for fixtures + CI). ``load``
fetches a live job + downloads its audio for ad-hoc local verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any


def _g(d: Any, *path: str, default: Any = None) -> Any:
    """Safe nested get: ``_g(doc, 'episode_profile', 'genre')``."""
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


@dataclass
class Artifact:
    job_id: str
    doc: dict[str, Any]
    audio_path: str | None = None
    image_paths: list[str] = field(default_factory=list)
    video_path: str | None = None

    # ── identity ──
    @property
    def genre(self) -> str:
        return (_g(self.doc, "episode_profile", "genre")
                or _g(self.doc, "inputs", "genre")
                or self.content_type or "general")

    @property
    def content_type(self) -> str | None:
        return _g(self.doc, "audio_config", "content_type") or _g(self.doc, "inputs", "content_type")

    @property
    def status(self) -> str | None:
        return self.doc.get("status")

    @property
    def created_at(self):
        """Job creation timestamp — for created_at-aware checks that must not fire on pre-fix jobs."""
        return self.doc.get("created_at")

    @property
    def topic(self) -> str:
        return _g(self.doc, "episode_profile", "topic") or _g(self.doc, "inputs", "topic") or ""

    # ── script ──
    @property
    def script(self) -> dict[str, Any]:
        # Real completed jobs store the canonical full script under outputs.script
        # ({metadata, dialogue: [{text, speaker, emotion, ...}]}); the top-level "script" is
        # usually absent. Prefer whichever is a dict (verified on real jobs 64b5df17/695dee1e).
        for s in (self.doc.get("script"), _g(self.doc, "outputs", "script")):
            if isinstance(s, dict):
                return s
        return {}

    @property
    def dialogue(self) -> list[dict[str, Any]]:
        return self.script.get("dialogue") or self.doc.get("dialogue") or []

    @property
    def speakers(self) -> list[str]:
        """The speaker/character names from the dialogue (for the generic-host-name check)."""
        out: list[str] = []
        for d in self.dialogue:
            if isinstance(d, dict):
                sp = d.get("speaker") or d.get("name") or d.get("role")
                if sp:
                    out.append(str(sp))
        return out

    @cached_property
    def script_text(self) -> str:
        """All spoken text joined — the substrate for content/structure checks."""
        parts: list[str] = []
        for d in self.dialogue:
            if isinstance(d, dict):
                parts.append(str(d.get("text") or d.get("line") or ""))
        if not parts:
            # Segment fallback: real segments_ready store the (truncated) text under text_preview.
            for s in self.segments:
                if isinstance(s, dict):
                    parts.append(str(s.get("text") or s.get("text_preview") or ""))
        return "\n".join(p for p in parts if p)

    @property
    def word_count(self) -> int:
        return len(self.script_text.split())

    @property
    def segments(self) -> list[dict[str, Any]]:
        return self.doc.get("segments_ready") or []

    @property
    def beat_map(self) -> list[dict[str, Any]]:
        return self.doc.get("segment_beat_map") or []

    @property
    def master_segment_timeline(self) -> list[dict[str, Any]]:
        """The canonical per-segment offsets in the DELIVERED (post-intro, muxed) master —
        ``[{index, start_ms, end_ms}]`` in delivered-master coordinates. The audio stage writes it
        (``master_segment_timeline.build_master_segment_timeline``) as ``intro_offset_ms +
        placed_start_ms``: the REAL spoken offset the visual clips are stamped against. Empty list on
        legacy jobs that predate it (then consumers detect the intro offset another way)."""
        tl = self.doc.get("master_segment_timeline")
        return [r for r in tl if isinstance(r, dict)] if isinstance(tl, list) else []

    # ── telemetry ──
    @property
    def costs(self) -> dict[str, Any]:
        return self.doc.get("costs") or {}

    @property
    def stages(self) -> dict[str, Any]:
        return self.doc.get("stages") or {}

    @property
    def tts_segment_logs(self) -> list[dict[str, Any]]:
        return self.doc.get("tts_segment_logs") or []

    # ── research grounding (the llm angle-research brief) ──
    @property
    def research_mode(self):
        """research_mode {none|llm|web} the route resolver chose (stages.job-research-planner.route)."""
        rp = _g(self.doc, "stages", "job-research-planner") or {}
        return (rp.get("route") or {}).get("research_mode")

    @property
    def llm_brief(self) -> dict[str, Any]:
        """The LLM angle-research brief metadata {angles, brief_chars} (stages, not research_results)."""
        rp = _g(self.doc, "stages", "job-research-planner") or {}
        b = rp.get("llm_brief")
        return b if isinstance(b, dict) else {}

    # ── audio (lazy ffprobe) ──
    @cached_property
    def audio_info(self) -> dict[str, Any]:
        if not self.audio_path:
            return {}
        try:
            from ..utils.audio import get_audio_info
            return get_audio_info(self.audio_path)
        except Exception:
            return {}

    @property
    def audio_duration_s(self) -> float:
        return float(self.audio_info.get("duration") or 0.0)

    @cached_property
    def _volumedetect(self) -> dict[str, float]:
        """ffmpeg volumedetect — peak + mean dBFS (for clipping/loudness checks). Cached, $0."""
        if not self.audio_path:
            return {}
        import re
        import subprocess
        try:
            err = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", self.audio_path, "-af", "volumedetect",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            ).stderr
            out: dict[str, float] = {}
            for key in ("max_volume", "mean_volume"):
                m = re.search(rf"{key}:\s*(-?[\d.]+) dB", err)
                if m:
                    out[key] = float(m.group(1))
            return out
        except Exception:
            return {}

    @property
    def audio_peak_dbfs(self) -> float | None:
        """Peak level in dBFS (>= -0.1 ≈ clipping). None if no audio."""
        return self._volumedetect.get("max_volume")

    @property
    def audio_mean_dbfs(self) -> float | None:
        """Mean level in dBFS (a rough loudness proxy). None if no audio."""
        return self._volumedetect.get("mean_volume")

    @cached_property
    def video_info(self) -> dict[str, Any]:
        """ffprobe the video_path → {duration, width, height, fps, codec}. Cached, $0."""
        if not self.video_path:
            return {}
        import json
        import subprocess
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
                 "-show_streams", self.video_path],
                capture_output=True, text=True, timeout=120,
            ).stdout
            data = json.loads(out or "{}")
            vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            fmt = data.get("format", {})
            fps = 0.0
            rfr = str(vs.get("r_frame_rate") or "")
            if "/" in rfr:
                n, d = rfr.split("/")
                fps = float(n) / float(d) if float(d or 0) else 0.0
            return {
                "duration": float(fmt.get("duration") or 0),
                "width": int(vs.get("width") or 0),
                "height": int(vs.get("height") or 0),
                "fps": round(fps, 2),
                "codec": vs.get("codec_name"),
            }
        except Exception:
            return {}

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_path)

    # ── visuals (nested under doc['visual'] on real jobs) ──
    @property
    def visual(self) -> dict[str, Any]:
        """The visual compartment — real jobs nest it under doc['visual']
        ({clips, captions_vtt, video_url, video_burned_url, chapters, ...})."""
        v = self.doc.get("visual")
        return v if isinstance(v, dict) else {}

    @property
    def visual_clips(self) -> list[dict[str, Any]]:
        """VisualClip list — doc['visual']['clips'] (real), top-level visual_clips as fallback."""
        return self.visual.get("clips") or self.doc.get("visual_clips") or []

    @property
    def captions_vtt(self) -> str | None:
        """Burned/sidecar captions VTT — doc['visual']['captions_vtt'] (real) or top-level."""
        return self.visual.get("captions_vtt") or self.doc.get("captions_vtt")

    @property
    def chapters(self) -> list[dict[str, Any]]:
        return self.visual.get("chapters") or self.doc.get("chapters") or []

    @property
    def video_url(self) -> str | None:
        """GCS URL of the assembled episode video (burned-caption export preferred)."""
        return (self.visual.get("video_burned_url") or self.visual.get("video_url")
                or _g(self.doc, "outputs", "video_url"))

    @property
    def has_visuals(self) -> bool:
        return bool(self.image_paths) or bool(self.visual_clips)

    # ── loaders ──
    @classmethod
    def from_doc(
        cls,
        doc: dict[str, Any],
        *,
        audio_path: str | None = None,
        image_paths: list[str] | None = None,
        video_path: str | None = None,
    ) -> Artifact:
        """Build directly from a job-doc dict + local asset paths — offline, $0, no network."""
        return cls(
            job_id=str(doc.get("job_id") or doc.get("id") or "local"),
            doc=doc,
            audio_path=audio_path,
            image_paths=image_paths or [],
            video_path=video_path,
        )

    @classmethod
    def load(cls, job_id: str, client: Any, *, download: bool = True,
             temp_dir: str = "/tmp/kitesforu-qa") -> Artifact:
        """Fetch a live job doc (+ optionally download its audio) via the kqa client."""
        doc = client.get_job(job_id)
        art = cls.from_doc(doc if isinstance(doc, dict) else {"job_id": job_id})
        art.job_id = job_id
        if download:
            audio_url = None
            try:
                audio_url = client.get_job_audio(job_id)
            except Exception:
                pass
            audio_url = audio_url or _g(doc, "outputs", "audio_url")
            if audio_url:
                art.audio_path = _download(audio_url, f"{temp_dir}/{job_id}.audio")
        return art


def _download(url: str, local_path: str) -> str | None:
    import os
    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        if url.startswith("gs://"):
            from ..integrations.gcs import download_from_gcs
            return download_from_gcs(url, local_path)
        import urllib.request
        urllib.request.urlretrieve(url, local_path)
        return local_path
    except Exception:
        return None
