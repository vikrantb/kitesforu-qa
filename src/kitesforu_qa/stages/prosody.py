"""Stage 4: Prosody (naturalness) analysis using librosa."""

from typing import Dict, Optional

from ..config import get_config
from ..models.language import LANGUAGE_PROSODY_THRESHOLDS
from ..models.results import StageResult


def check_naturalness(
    audio_path: str,
    language: str = "en",
    thresholds: Optional[Dict[str, float]] = None,
) -> StageResult:
    """
    Check if speech sounds natural (not robotic/monotone).

    Analyzes pitch variation, dynamic range, and speaking patterns
    to detect unnatural or robotic speech.

    Args:
        audio_path: Path to the audio file
        language: Language code for language-specific thresholds
        thresholds: Custom thresholds dict (optional)

    Returns:
        StageResult with prosody analysis results
    """
    if thresholds is None:
        thresholds = LANGUAGE_PROSODY_THRESHOLDS.get(
            language,
            LANGUAGE_PROSODY_THRESHOLDS["en"]
        )

    try:
        import librosa
        import numpy as np

        # Load audio
        audio, sr = librosa.load(audio_path, sr=None)

        # Measure pitch variation using pyin
        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr
        )

        # Filter out unvoiced segments
        valid_f0 = f0[~np.isnan(f0)]

        if len(valid_f0) < 10:
            return StageResult(
                stage_name="prosody",
                passed=False,
                error="Insufficient voiced segments for analysis",
            )

        # Calculate pitch statistics
        pitch_mean = float(np.mean(valid_f0))
        pitch_std = float(np.std(valid_f0))
        pitch_range = float(np.max(valid_f0) - np.min(valid_f0))

        # Measure energy/loudness variation
        rms = librosa.feature.rms(y=audio)[0]
        mean_rms = np.mean(rms)

        if mean_rms > 0:
            dynamic_range_db = float(20 * np.log10(np.max(rms) / mean_rms))
        else:
            dynamic_range_db = 0.0

        # Calculate speaking rate proxy (tempo)
        tempo, _ = librosa.beat.beat_track(y=audio, sr=sr)
        # Note: This is a rough proxy, actual speaking rate needs word timing

        # Detect monotone speech
        pitch_std_min = thresholds.get("pitch_std_min", 20)
        pitch_std_max = thresholds.get("pitch_std_max", 80)
        dynamic_range_min = thresholds.get("dynamic_range_min", 4)

        is_monotone = pitch_std < pitch_std_min
        is_too_variable = pitch_std > pitch_std_max
        is_flat = dynamic_range_db < dynamic_range_min

        # Determine issues
        issues = []
        if is_monotone:
            issues.append(
                f"Speech is monotone: pitch variation {pitch_std:.1f}Hz "
                f"(min: {pitch_std_min}Hz)"
            )
        if is_too_variable:
            issues.append(
                f"Speech is too variable: pitch variation {pitch_std:.1f}Hz "
                f"(max: {pitch_std_max}Hz)"
            )
        if is_flat:
            issues.append(
                f"Dynamic range too low: {dynamic_range_db:.1f}dB "
                f"(min: {dynamic_range_min}dB)"
            )

        passed = not is_monotone and not is_flat

        return StageResult(
            stage_name="prosody",
            passed=passed,
            data={
                "pitch_mean_hz": round(pitch_mean, 1),
                "pitch_variation_hz": round(pitch_std, 1),
                "pitch_range_hz": round(pitch_range, 1),
                "dynamic_range_db": round(dynamic_range_db, 1),
                "is_monotone": is_monotone,
                "is_flat": is_flat,
                "estimated_tempo": float(tempo),
            },
            issues=issues,
        )

    except ImportError:
        return StageResult(
            stage_name="prosody",
            passed=False,
            error="librosa not installed. Install with: pip install librosa",
        )
    except Exception as e:
        return StageResult(
            stage_name="prosody",
            passed=False,
            error=f"Prosody analysis failed: {e}",
        )
