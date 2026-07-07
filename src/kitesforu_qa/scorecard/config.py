"""Runtime configuration for the short scorecard.

Everything that costs money is a FLAG that defaults OFF, so a baseline run is strictly $0/¢:
``enable_judge`` (the axis-2 substance LLM judge) and ``enable_vlm`` (the axis-3 visual-truth VLM,
and the optional axis-1 scroll-stop check). The paid calls are injected as callables (``judge_fn`` /
``vlm_fn``) so the scorer never imports a provider SDK and tests can exercise the enabled path with a
fake — dependency injection, not a stub. When a flag is on but no callable is injected, the axis
honestly reports "enabled but not wired" rather than fabricating a score.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# judge_fn(art) -> (score_0_100: float, note: str)
JudgeFn = Callable[[Any], "tuple[float, str]"]
# vlm_fn(image_uris: list[str], context: dict) -> (score_0_100: float, note: str)
# context carries {"job_id", "beat_count", "video_path", "beats": [{"beat_index", "start_ms", "asset_uri",
# "modality", "render_mode"}, ...]} — "beats" + "video_path" are what a real vlm_fn needs to extract a
# frame (ffmpeg at the beat's start_ms, or the beat's own stored asset as a fallback); image_uris is kept
# for back-compat with simple injected test doubles. See kitesforu_qa.scorecard.vlm for the reference impl.
VlmFn = Callable[["list[str]", "dict[str, Any]"], "tuple[float, str]"]


@dataclass
class ScorecardConfig:
    # ── cost gates (all default OFF ⇒ baseline is $0) ──
    enable_judge: bool = False       # axis 2 — substance novelty LLM judge (¢)
    enable_vlm: bool = False         # axis 3 — visual-truth VLM (¢); also axis-1 scroll-stop
    judge_fn: JudgeFn | None = None  # injected LLM judge (unset ⇒ axis 2 stays a $0 proxy)
    vlm_fn: VlmFn | None = None      # injected VLM (unset ⇒ axis 3 stays needs-VLM/null)

    # ── thresholds (SSOT for the tunable knobs the axes read) ──
    per_short_cost_cap_usd: float = 0.10       # axis 8 hard gate
    motion_target_rate_per_s: float = 0.25     # axis 5 — >= 1 engine motion event / 4s
    hook_max_words: int = 12                   # axis 1
    hook_first_word_max_s: float = 2.5         # axis 1
    word_sync_floor_ms: float = 60.0           # axis 6 — median drift at which the score hits the floor
    ffmpeg_motion_fallback: bool = True        # axis 5 — use ffmpeg scene-change when provenance absent
