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
from dataclasses import dataclass, field
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
    # axis 8 hard gate — TIER-AWARE (2026-07 calibration fix). The Measured Quality Engine's first
    # baseline (QUALITY_BACKLOG.md) found cost_safety at 0/100 on 100% of scored cells and root-caused
    # it to a single flat $0.10 cap applied regardless of quality_tier: the tier system itself targets
    # low ~$0.025/unit, medium ~$0.15, high ~$1.0-1.3, ultra "flagship headroom" (see
    # kitesforu-docs/proposals/quality-slider-cost-mode-2026-06-24 + .claude/knowledge/cost-reference.md)
    # — so a medium/high/ultra job was GUARANTEED to fail this axis by design, not because it overspent.
    # Each cap is a generous multiple of its tier's documented target (headroom for real duration/
    # provider variance) without silently passing every non-low job. ``per_short_cost_cap_usd`` remains
    # the STRICT fallback for a missing/unrecognized tier (never relax the gate when tier is unknown).
    cost_cap_usd_by_tier: dict[str, float] = field(default_factory=lambda: {
        "low": 0.10,
        "medium": 0.30,
        "high": 2.00,
        "ultra": 5.00,
    })
    per_short_cost_cap_usd: float = 0.10       # axis 8 hard gate — fallback for unknown/missing tier
    motion_target_rate_per_s: float = 0.25     # axis 5 — >= 1 engine motion event / 4s
    hook_max_words: int = 12                   # axis 1
    hook_first_word_max_s: float = 2.5         # axis 1
    word_sync_floor_ms: float = 60.0           # axis 6 — median drift at which the score hits the floor
    ffmpeg_motion_fallback: bool = True        # axis 5 — use ffmpeg scene-change when provenance absent
