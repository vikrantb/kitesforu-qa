"""QA Pipeline orchestration."""

import time
from typing import Optional

from .config import QAConfig, get_config
from .models.language import Language, LanguageConfig
from .models.results import QAResult, StageResult
from .stages.content import evaluate_script_quality, quick_script_checks
from .stages.format import check_audio_format
from .stages.pronunciation import check_pronunciation
from .stages.prosody import check_naturalness
from .stages.quality import check_audio_quality
from .stages.voice_matching import check_voice_matching
from .utils.audio import normalize_audio_path


class QAPipeline:
    """Orchestrates the 6-stage QA pipeline."""

    def __init__(
        self,
        language: Language = Language.ENGLISH,
        config: Optional[QAConfig] = None,
        verbose: bool = False,
    ):
        """
        Initialize QA pipeline.

        Args:
            language: Language for QA
            config: Custom configuration (default: from env)
            verbose: Enable verbose output
        """
        self.language = language
        self.config = config or get_config()
        self.verbose = verbose
        self.lang_config = LanguageConfig(language=language)

    def run(
        self,
        audio_path: str,
        user_request: str,
        script: Optional[str] = None,
        topic: Optional[str] = None,
        job_id: Optional[str] = None,
        expected_duration: Optional[float] = None,
        expected_hosts: int = 1,
    ) -> QAResult:
        """
        Run full QA pipeline on audio.

        Args:
            audio_path: Path to audio file (local or GCS)
            user_request: Original user request
            script: Script text (optional, will skip content eval if None)
            topic: Podcast topic for voice matching
            job_id: Job ID for tracking
            expected_duration: Expected duration in seconds
            expected_hosts: Number of expected hosts

        Returns:
            QAResult with all stage results
        """
        start_time = time.time()

        result = QAResult(
            job_id=job_id,
            language=self.language.value,
        )

        # Normalize audio path (download from GCS if needed)
        try:
            local_audio = normalize_audio_path(audio_path, self.config.temp_dir)
        except Exception as e:
            result.add_stage_result(StageResult(
                stage_name="format",
                passed=False,
                error=f"Failed to access audio: {e}",
            ))
            result.execution_time_seconds = time.time() - start_time
            return result

        # Stage 1: Format Validation
        if self.verbose:
            print("  Running Stage 1: Format validation...")
        format_result = check_audio_format(
            audio_path=local_audio,
            expected_duration=expected_duration,
        )
        result.add_stage_result(format_result)

        # If format fails, don't continue
        if not format_result.passed:
            result.execution_time_seconds = time.time() - start_time
            return result

        # Stage 2: Pronunciation (if script provided)
        if script:
            if self.verbose:
                print("  Running Stage 2: Pronunciation check...")
            pron_result = check_pronunciation(
                audio_path=local_audio,
                original_script=script,
                language=self.language.value,
                wer_threshold=self.config.wer_threshold,
            )
            result.add_stage_result(pron_result)
        else:
            result.add_stage_result(StageResult(
                stage_name="pronunciation",
                passed=True,
                data={"skipped": True, "reason": "No script provided"},
            ))

        # Stage 3: Audio Quality
        if self.verbose:
            print("  Running Stage 3: Audio quality (MOS)...")
        quality_result = check_audio_quality(
            audio_path=local_audio,
            mos_threshold=self.config.mos_threshold,
        )
        result.add_stage_result(quality_result)

        # Stage 4: Prosody (Naturalness)
        if self.verbose:
            print("  Running Stage 4: Prosody analysis...")
        prosody_result = check_naturalness(
            audio_path=local_audio,
            language=self.language.value,
            thresholds=self.lang_config.prosody_thresholds,
        )
        result.add_stage_result(prosody_result)

        # Stage 5: Content Evaluation (if script provided)
        if script and self.config.enable_content_eval:
            if self.verbose:
                print("  Running Stage 5: Content evaluation...")
            content_result = evaluate_script_quality(
                user_request=user_request,
                script=script,
                language=self.language.value,
                score_threshold=self.config.content_score_threshold,
            )
            result.add_stage_result(content_result)
        else:
            result.add_stage_result(StageResult(
                stage_name="content",
                passed=True,
                data={
                    "skipped": True,
                    "reason": "No script provided" if not script else "Content eval disabled",
                },
            ))

        # Stage 6: Voice Matching
        if self.verbose:
            print("  Running Stage 6: Voice matching...")
        voice_result = check_voice_matching(
            audio_path=local_audio,
            script=script or "",
            topic=topic or user_request,
            expected_hosts=expected_hosts,
            language=self.language.value,
            name_patterns=self.lang_config.name_patterns,
        )
        result.add_stage_result(voice_result)

        result.execution_time_seconds = time.time() - start_time
        return result

    def run_content_only(
        self,
        script: str,
        user_request: str,
        target_duration_min: int = 10,
    ) -> StageResult:
        """
        Run only content evaluation (before audio generation).

        Args:
            script: Script text
            user_request: Original user request
            target_duration_min: Target duration in minutes

        Returns:
            StageResult for content evaluation
        """
        # Run quick checks first (free)
        quick_issues = quick_script_checks(script, target_duration_min)

        if quick_issues:
            return StageResult(
                stage_name="content",
                passed=False,
                data={"quick_check_issues": quick_issues},
                issues=quick_issues,
            )

        # Run full LLM evaluation
        return evaluate_script_quality(
            user_request=user_request,
            script=script,
            language=self.language.value,
            score_threshold=self.config.content_score_threshold,
        )

    def run_audio_only(
        self,
        audio_path: str,
        expected_duration: Optional[float] = None,
    ) -> QAResult:
        """
        Run only audio-related stages (no content/pronunciation).

        Args:
            audio_path: Path to audio file
            expected_duration: Expected duration in seconds

        Returns:
            QAResult with format, quality, and prosody results
        """
        start_time = time.time()

        result = QAResult(language=self.language.value)

        local_audio = normalize_audio_path(audio_path, self.config.temp_dir)

        # Format
        result.add_stage_result(check_audio_format(
            audio_path=local_audio,
            expected_duration=expected_duration,
        ))

        # Quality
        result.add_stage_result(check_audio_quality(
            audio_path=local_audio,
            mos_threshold=self.config.mos_threshold,
        ))

        # Prosody
        result.add_stage_result(check_naturalness(
            audio_path=local_audio,
            language=self.language.value,
        ))

        result.execution_time_seconds = time.time() - start_time
        return result
