"""Stage 3: Audio quality assessment using MOS prediction."""

from typing import Optional

from ..config import get_config
from ..models.results import StageResult


def check_audio_quality(
    audio_path: str,
    mos_threshold: Optional[float] = None,
) -> StageResult:
    """
    Predict audio quality using Mean Opinion Score (MOS).

    Uses UTMOS or similar neural MOS predictor to estimate
    how good the audio sounds on a 1-5 scale.

    Args:
        audio_path: Path to the audio file
        mos_threshold: Minimum acceptable MOS score (default from config)

    Returns:
        StageResult with audio quality results
    """
    config = get_config()
    if mos_threshold is None:
        mos_threshold = config.mos_threshold

    try:
        # Try UTMOS first
        mos_score = _predict_mos_utmos(audio_path)
    except ImportError:
        # Fallback to simpler signal-based quality estimation
        try:
            mos_score = _estimate_mos_signal(audio_path)
        except Exception as e:
            return StageResult(
                stage_name="quality",
                passed=False,
                error=f"Failed to analyze audio quality: {e}",
            )
    except Exception as e:
        return StageResult(
            stage_name="quality",
            passed=False,
            error=f"MOS prediction failed: {e}",
        )

    # Map score to quality level
    if mos_score >= 4.5:
        quality_level = "Excellent"
    elif mos_score >= 4.0:
        quality_level = "Good"
    elif mos_score >= 3.5:
        quality_level = "Okay"
    elif mos_score >= 3.0:
        quality_level = "Poor"
    else:
        quality_level = "Bad"

    passed = mos_score >= mos_threshold

    issues = []
    if not passed:
        issues.append(
            f"MOS score {mos_score:.2f} below threshold {mos_threshold:.2f}"
        )

    return StageResult(
        stage_name="quality",
        passed=passed,
        data={
            "mos_score": round(mos_score, 2),
            "quality_level": quality_level,
        },
        issues=issues,
    )


def _predict_mos_utmos(audio_path: str) -> float:
    """Predict MOS using UTMOS model."""
    try:
        from speechmos import UTMOS
        predictor = UTMOS()
        return predictor.predict(audio_path)
    except ImportError:
        raise ImportError("UTMOS not available")


def _estimate_mos_signal(audio_path: str) -> float:
    """
    Estimate MOS from signal characteristics.

    This is a fallback when UTMOS is not available.
    Uses basic audio quality heuristics.
    """
    import librosa
    import numpy as np

    # Load audio
    audio, sr = librosa.load(audio_path, sr=None)

    # Calculate signal-to-noise ratio proxy
    rms = librosa.feature.rms(y=audio)[0]
    mean_rms = np.mean(rms)
    std_rms = np.std(rms)

    # Dynamic range
    if mean_rms > 0:
        dynamic_range_db = 20 * np.log10(np.max(rms) / mean_rms)
    else:
        dynamic_range_db = 0

    # Spectral flatness (measure of noise)
    spectral_flatness = np.mean(librosa.feature.spectral_flatness(y=audio))

    # Simple heuristic MOS estimation
    # This is a rough approximation, not a validated model
    base_score = 3.5

    # Better dynamic range = higher score
    if dynamic_range_db > 10:
        base_score += 0.5
    elif dynamic_range_db < 4:
        base_score -= 0.5

    # Lower spectral flatness (less noisy) = higher score
    if spectral_flatness < 0.1:
        base_score += 0.3
    elif spectral_flatness > 0.3:
        base_score -= 0.5

    # RMS too low = too quiet
    if mean_rms < 0.01:
        base_score -= 0.3

    # Clamp to 1-5 range
    return max(1.0, min(5.0, base_score))
