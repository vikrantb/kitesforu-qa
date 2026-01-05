"""Stage 2: Pronunciation accuracy using Whisper STT + WER."""

from typing import Optional

from ..config import get_config
from ..models.results import StageResult


def check_pronunciation(
    audio_path: str,
    original_script: str,
    language: str = "en",
    wer_threshold: Optional[float] = None,
) -> StageResult:
    """
    Check pronunciation accuracy by comparing Whisper transcription to script.

    Args:
        audio_path: Path to the audio file
        original_script: Original script text
        language: Language code (e.g., 'en', 'es', 'hi')
        wer_threshold: Maximum acceptable word error rate (default from config)

    Returns:
        StageResult with pronunciation accuracy results
    """
    config = get_config()
    if wer_threshold is None:
        wer_threshold = config.wer_threshold

    try:
        import whisper
        from jiwer import wer

        # Load Whisper model
        model = whisper.load_model(config.whisper_model)

        # Transcribe audio
        result = model.transcribe(
            audio_path,
            language=language if language != "auto" else None,
            task="transcribe",
        )
        transcript = result["text"].strip()

        # Normalize texts for comparison
        reference = _normalize_text(original_script)
        hypothesis = _normalize_text(transcript)

        # Calculate WER
        if not reference:
            return StageResult(
                stage_name="pronunciation",
                passed=False,
                error="Original script is empty",
            )

        error_rate = wer(reference, hypothesis)
        accuracy = 1.0 - error_rate

        # Determine pass/fail
        passed = error_rate <= wer_threshold

        issues = []
        if not passed:
            issues.append(
                f"Word error rate {error_rate:.1%} exceeds threshold {wer_threshold:.1%}"
            )

        return StageResult(
            stage_name="pronunciation",
            passed=passed,
            data={
                "word_error_rate": round(error_rate, 4),
                "accuracy_percent": round(accuracy * 100, 2),
                "transcript_sample": transcript[:500] if len(transcript) > 500 else transcript,
                "detected_language": result.get("language", language),
            },
            issues=issues,
        )

    except ImportError as e:
        return StageResult(
            stage_name="pronunciation",
            passed=False,
            error=f"Missing dependency: {e}. Install with: pip install openai-whisper jiwer",
        )
    except Exception as e:
        return StageResult(
            stage_name="pronunciation",
            passed=False,
            error=f"Transcription failed: {e}",
        )


def _normalize_text(text: str) -> str:
    """Normalize text for WER comparison."""
    import re

    # Convert to lowercase
    text = text.lower()

    # Remove punctuation except apostrophes
    text = re.sub(r"[^\w\s']", " ", text)

    # Normalize whitespace
    text = " ".join(text.split())

    return text
