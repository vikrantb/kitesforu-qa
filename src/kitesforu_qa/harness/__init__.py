"""Quality harness — thousands of small LOCAL checks over a generated Artifact, per genre.

Extends the kqa pipeline with a granular ``Check`` primitive: the bulk are $0 deterministic Python
(over the job doc / audio via ffprobe / images via PIL / timing math on the segment_beat_map);
``judge`` checks defer to a LOCAL agent. Checks aggregate into the existing kqa ``StageResult`` /
``QAResult`` so the harness reuses the pipeline + improvement-feedback loop.

Design: kitesforu-docs/proposals/quality-harness-2026-06-21/ (TASK.md, COORDINATION.md).

    from kitesforu_qa.harness import Artifact, run_scorecard, scorecard_summary
    art = Artifact.from_doc(job_doc, audio_path="ep.mp3")
    print(scorecard_summary(run_scorecard(art)))
"""
from .artifact import Artifact
from .battery import run_dimension, run_scorecard, scorecard_summary, to_stage_result
from .check import (
    Check,
    CheckResult,
    all_checks,
    check,
    checks_for,
    clear_registry,
    register,
    skip,
)

__all__ = [
    "Artifact",
    "Check",
    "CheckResult",
    "check",
    "register",
    "skip",
    "all_checks",
    "checks_for",
    "clear_registry",
    "run_dimension",
    "run_scorecard",
    "scorecard_summary",
    "to_stage_result",
    "JudgePrompt",
    "judge_check",
    "judge_checks_for",
    "run_judges",
]

# Importing the harness registers the built-in check batteries + the judge checks.
from . import checks  # noqa: E402,F401
from .judge import (  # noqa: E402
    JudgePrompt,
    judge_check,
    judge_checks_for,
    run_judges,
)
