"""Stage 6: Voice/Host matching validation."""

import re
from typing import Dict, List, Optional

from ..config import get_config
from ..models.language import LANGUAGE_NAME_PATTERNS
from ..models.results import StageResult


# Placeholder name patterns to detect
PLACEHOLDER_PATTERNS = [
    r'\bhost\s*[0-9]+\b',           # "host 1", "host 2"
    r'\bspeaker\s*[0-9]+\b',        # "speaker 1"
    r'\bvoice\s*[0-9]+\b',          # "voice 1"
    r'\bperson\s*[0-9]+\b',         # "person 1"
    r'\b(host|speaker)\s*(one|two|three|four|five)\b',  # "host one"
]


# Topic-voice expectations
TOPIC_VOICE_EXPECTATIONS = {
    "senior": {"age_range": "mature", "min_pitch_variance": 20},
    "elderly": {"age_range": "mature", "min_pitch_variance": 20},
    "retirement": {"age_range": "mature", "min_pitch_variance": 20},
    "children": {"age_range": "young_adult_or_mature"},
    "kids": {"age_range": "young_adult_or_mature"},
    "parenting": {"age_range": "mature"},
    "teen": {"age_range": "young"},
    "college": {"age_range": "young_adult"},
    "youth": {"age_range": "young"},
}


def check_voice_matching(
    audio_path: str,
    script: str,
    topic: str = "",
    expected_hosts: int = 1,
    language: str = "en",
    name_patterns: Optional[Dict[str, List[str]]] = None,
) -> StageResult:
    """
    Complete voice/host matching validation.

    Checks for:
    - Placeholder names in audio ("host 1", "speaker 2")
    - Gender mismatch (female name but male voice)
    - Topic-voice mismatch (seniors topic but young voice)
    - Multi-host same voice detection

    Args:
        audio_path: Path to the audio file
        script: Original script text
        topic: Podcast topic for voice matching
        expected_hosts: Number of expected hosts
        language: Language code
        name_patterns: Language-specific name patterns

    Returns:
        StageResult with voice matching results
    """
    config = get_config()
    issues = []

    if name_patterns is None:
        name_patterns = LANGUAGE_NAME_PATTERNS.get(language, LANGUAGE_NAME_PATTERNS["en"])

    try:
        import whisper

        # Step 1: Transcribe audio (use fast model for this check)
        model = whisper.load_model(config.whisper_model_fast)
        result = model.transcribe(audio_path, language=language)
        transcript = result["text"]

        # Step 2: Check for placeholder names
        placeholder_issues = _detect_placeholder_names(transcript)
        issues.extend(placeholder_issues)

        # Step 3: Check gender consistency
        voice_gender = _detect_voice_gender(audio_path)
        gender_issues = _check_gender_consistency(script, transcript, voice_gender, name_patterns)
        issues.extend(gender_issues)

        # Step 4: Check topic-voice match
        if topic:
            voice_age = _estimate_voice_age(audio_path)
            topic_issues = _check_topic_voice_match(topic, voice_age)
            issues.extend(topic_issues)
        else:
            voice_age = "unknown"

        # Step 5: Check multi-host if expected
        diarization = None
        if expected_hosts > 1 and config.enable_diarization:
            try:
                diarization = _detect_multiple_speakers(audio_path)
                host_issues = _check_multi_host_consistency(script, diarization, expected_hosts)
                issues.extend(host_issues)
            except Exception as e:
                # Diarization is optional, don't fail the whole check
                diarization = {"error": str(e)}

        passed = len(issues) == 0

        return StageResult(
            stage_name="voice_matching",
            passed=passed,
            data={
                "transcript_sample": transcript[:500] if len(transcript) > 500 else transcript,
                "voice_gender": voice_gender,
                "voice_age": voice_age,
                "diarization": diarization,
                "placeholder_detected": len(placeholder_issues) > 0,
                "gender_match": len(gender_issues) == 0,
            },
            issues=issues,
        )

    except ImportError as e:
        return StageResult(
            stage_name="voice_matching",
            passed=False,
            error=f"Missing dependency: {e}",
        )
    except Exception as e:
        return StageResult(
            stage_name="voice_matching",
            passed=False,
            error=f"Voice matching check failed: {e}",
        )


def _detect_placeholder_names(transcript: str) -> List[str]:
    """Detect if audio literally says 'host 1' etc."""
    issues = []
    transcript_lower = transcript.lower()

    for pattern in PLACEHOLDER_PATTERNS:
        matches = re.findall(pattern, transcript_lower)
        if matches:
            match_text = matches[0] if isinstance(matches[0], str) else ' '.join(matches[0])
            issues.append(f"Audio contains placeholder name: '{match_text}'")

    return issues


def _detect_voice_gender(audio_path: str) -> Dict:
    """Estimate voice gender based on fundamental frequency (F0)."""
    try:
        import librosa
        import numpy as np

        audio, sr = librosa.load(audio_path, sr=None)

        # Extract pitch using pyin
        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C6'),
            sr=sr
        )

        valid_f0 = f0[~np.isnan(f0)]

        if len(valid_f0) == 0:
            return {"gender": "unknown", "confidence": 0}

        avg_f0 = float(np.mean(valid_f0))

        # Typical ranges: Male 85-180 Hz, Female 165-255 Hz
        if avg_f0 < 140:
            return {"gender": "male", "avg_f0": avg_f0, "confidence": 0.9}
        elif avg_f0 > 190:
            return {"gender": "female", "avg_f0": avg_f0, "confidence": 0.9}
        else:
            return {"gender": "uncertain", "avg_f0": avg_f0, "confidence": 0.5}

    except Exception:
        return {"gender": "unknown", "confidence": 0}


def _check_gender_consistency(
    script: str,
    transcript: str,
    voice_gender: Dict,
    name_patterns: Dict[str, List[str]],
) -> List[str]:
    """Check if claimed gender matches detected voice gender."""
    issues = []

    female_names = name_patterns.get("female", [])
    male_names = name_patterns.get("male", [])

    # Build patterns
    female_patterns = [
        rf"I am ({"|".join(female_names)})",
        rf"my name is ({"|".join(female_names)})",
        rf"this is ({"|".join(female_names)}) speaking",
    ] if female_names else []

    male_patterns = [
        rf"I am ({"|".join(male_names)})",
        rf"my name is ({"|".join(male_names)})",
        rf"this is ({"|".join(male_names)}) speaking",
    ] if male_names else []

    combined_text = (script + " " + transcript).lower()

    claimed_female = any(re.search(p, combined_text, re.I) for p in female_patterns)
    claimed_male = any(re.search(p, combined_text, re.I) for p in male_patterns)

    if claimed_female and voice_gender.get("gender") == "male" and voice_gender.get("confidence", 0) > 0.7:
        issues.append(
            f"Gender mismatch: Script claims female name but voice is male "
            f"(F0={voice_gender.get('avg_f0', 0):.0f}Hz)"
        )

    if claimed_male and voice_gender.get("gender") == "female" and voice_gender.get("confidence", 0) > 0.7:
        issues.append(
            f"Gender mismatch: Script claims male name but voice is female "
            f"(F0={voice_gender.get('avg_f0', 0):.0f}Hz)"
        )

    return issues


def _estimate_voice_age(audio_path: str) -> str:
    """Rough voice age estimation based on pitch characteristics."""
    try:
        import librosa
        import numpy as np

        audio, sr = librosa.load(audio_path, sr=None)

        f0, voiced_flag, voiced_probs = librosa.pyin(
            audio,
            fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C7'),
            sr=sr
        )

        valid_f0 = f0[~np.isnan(f0)]
        if len(valid_f0) == 0:
            return "unknown"

        avg_f0 = np.mean(valid_f0)
        f0_std = np.std(valid_f0)

        # Very rough heuristics
        if avg_f0 > 220:
            return "young" if f0_std > 40 else "young_adult"
        elif avg_f0 > 150:
            return "young_adult" if f0_std > 35 else "mature"
        else:
            return "mature"

    except Exception:
        return "unknown"


def _check_topic_voice_match(topic: str, voice_age: str) -> List[str]:
    """Check if voice characteristics match topic expectations."""
    issues = []
    topic_lower = topic.lower()

    for keyword, expectations in TOPIC_VOICE_EXPECTATIONS.items():
        if keyword in topic_lower:
            expected_age = expectations.get("age_range", "any")

            if expected_age == "mature" and voice_age == "young":
                issues.append(
                    f"Voice mismatch: Topic '{keyword}' suggests mature voice, "
                    f"but detected young-sounding voice"
                )
            if expected_age == "young" and voice_age == "mature":
                issues.append(
                    f"Voice mismatch: Topic '{keyword}' suggests young voice, "
                    f"but detected mature-sounding voice"
                )

    return issues


def _detect_multiple_speakers(audio_path: str) -> Dict:
    """Detect if audio has multiple distinct speakers using pyannote."""
    try:
        import os
        from pyannote.audio import Pipeline

        hf_token = os.environ.get("HUGGINGFACE_TOKEN")
        if not hf_token:
            return {"error": "HUGGINGFACE_TOKEN not set for diarization"}

        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token
        )

        diarization = pipeline(audio_path)

        speakers = set()
        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            speakers.add(speaker)
            segments.append({
                "speaker": speaker,
                "start": turn.start,
                "end": turn.end,
            })

        return {
            "num_speakers_detected": len(speakers),
            "speaker_segments": segments[:20],  # Limit to first 20 segments
        }

    except ImportError:
        return {"error": "pyannote.audio not installed"}
    except Exception as e:
        return {"error": str(e)}


def _check_multi_host_consistency(
    script: str,
    diarization: Dict,
    expected_hosts: int,
) -> List[str]:
    """Check if multi-host script has multiple actual voices."""
    issues = []

    if "error" in diarization:
        return []  # Skip if diarization failed

    detected_speakers = diarization.get("num_speakers_detected", 0)

    if expected_hosts > 1 and detected_speakers == 1:
        issues.append(
            f"Multi-host mismatch: Expected {expected_hosts} hosts, "
            f"but only 1 voice detected in audio"
        )

    return issues
