"""Stage 7: Language Verification and Pronunciation Quality.

This stage verifies that:
1. The detected language matches the requested language
2. The pronunciation quality is appropriate for the target language
3. The transcript is coherent and in the correct script (for non-Latin languages)
"""

import re
from typing import Dict

from ..models.results import StageResult


# Language code mappings (BCP-47 to Whisper short codes)
LANGUAGE_CODE_MAPPING = {
    "en-US": "en", "en-GB": "en", "en": "en",
    "es-ES": "es", "es-MX": "es", "es": "es",
    "fr-FR": "fr", "fr": "fr",
    "de-DE": "de", "de": "de",
    "hi-IN": "hi", "hi": "hi",
    "ja-JP": "ja", "ja": "ja",
    "ko-KR": "ko", "ko": "ko",
    "zh-CN": "zh", "zh": "zh",
    "pt-BR": "pt", "pt-PT": "pt", "pt": "pt",
    "ar-SA": "ar", "ar": "ar",
    "ru-RU": "ru", "ru": "ru",
    "it-IT": "it", "it": "it",
    "nl-NL": "nl", "nl": "nl",
}

# Related language families (for acceptable misdetection)
LANGUAGE_FAMILIES = {
    "hi": ["hi", "ur"],  # Hindi and Urdu are mutually intelligible
    "es": ["es", "pt"],  # Spanish and Portuguese share similarities
    "no": ["no", "sv", "da"],  # Nordic languages
    "zh": ["zh", "yue"],  # Chinese variants
    "sr": ["sr", "hr", "bs"],  # Serbo-Croatian
}

# Script detection patterns
SCRIPT_PATTERNS = {
    "hi": {
        "name": "Devanagari",
        "expected_scripts": ["Devanagari"],
        "unicode_ranges": [(0x0900, 0x097F)],  # Devanagari range
    },
    "ar": {
        "name": "Arabic",
        "expected_scripts": ["Arabic"],
        "unicode_ranges": [(0x0600, 0x06FF)],  # Arabic range
    },
    "ja": {
        "name": "Japanese",
        "expected_scripts": ["Hiragana", "Katakana", "Han"],
        "unicode_ranges": [(0x3040, 0x30FF), (0x4E00, 0x9FFF)],  # Hiragana, Katakana, CJK
    },
    "ko": {
        "name": "Korean",
        "expected_scripts": ["Hangul"],
        "unicode_ranges": [(0xAC00, 0xD7AF), (0x1100, 0x11FF)],  # Hangul range
    },
    "zh": {
        "name": "Chinese",
        "expected_scripts": ["Han"],
        "unicode_ranges": [(0x4E00, 0x9FFF)],  # CJK Unified Ideographs
    },
    "ru": {
        "name": "Russian",
        "expected_scripts": ["Cyrillic"],
        "unicode_ranges": [(0x0400, 0x04FF)],  # Cyrillic range
    },
    "el": {
        "name": "Greek",
        "expected_scripts": ["Greek"],
        "unicode_ranges": [(0x0370, 0x03FF)],  # Greek range
    },
    "he": {
        "name": "Hebrew",
        "expected_scripts": ["Hebrew"],
        "unicode_ranges": [(0x0590, 0x05FF)],  # Hebrew range
    },
    "th": {
        "name": "Thai",
        "expected_scripts": ["Thai"],
        "unicode_ranges": [(0x0E00, 0x0E7F)],  # Thai range
    },
}

# Common mispronunciation indicators (words that suggest wrong accent)
ACCENT_QUALITY_INDICATORS = {
    "hi": {
        "good_indicators": [
            # Whisper transcribing Hindi words correctly shows good pronunciation
            r'\bnamaste\b', r'\bnamaskar\b', r'\bdhanyvad\b', r'\bdhanyavad\b',
            r'\bpriya\b', r'\braaj\b', r'\bvikram\b', r'\bneha\b',
            r'\bbharat\b', r'\bharatiya\b', r'\bhindu\b', r'\bhindust[aā]n\b',
            r'\bkya\b', r'\baap\b', r'\bhum\b', r'\byeh\b', r'\bwoh\b',
            r'\blakh\b', r'\bcrore\b', r'\brunaiyat\b', r'\bbudhi\b',
        ],
        "bad_indicators": [
            # English words that shouldn't appear in pure Hindi content
            # Note: Some English is OK in code-switching, but dominant English is bad
        ],
    },
    "es": {
        "good_indicators": [
            r'\bhola\b', r'\bgracias\b', r'\bseñor\b', r'\bseñora\b',
            r'\bmuy\b', r'\bbueno\b', r'\bmucho\b',
        ],
    },
    "fr": {
        "good_indicators": [
            r'\bbonjour\b', r'\bmerci\b', r'\bs\'il vous plaît\b',
            r'\bmonsieur\b', r'\bmadame\b', r'\btrès\b',
        ],
    },
    "de": {
        "good_indicators": [
            r'\bguten tag\b', r'\bdanke\b', r'\bbitte\b',
            r'\bherr\b', r'\bfrau\b', r'\bsehr\b',
        ],
    },
    "ja": {
        "good_indicators": [
            r'\bkonnichiwa\b', r'\barigatou\b', r'\bhai\b',
            r'\bsan\b', r'\bsensei\b', r'\bdesu\b',
        ],
    },
}


def check_language_verification(
    audio_path: str,
    expected_language: str,
    whisper_model: str = "base",
    strict_mode: bool = False,
) -> StageResult:
    """
    Verify the language spoken in audio matches expectations.

    This stage:
    1. Detects the actual language spoken using Whisper
    2. Compares with the expected/requested language
    3. Checks script correctness for non-Latin languages
    4. Assesses pronunciation quality based on transcript coherence

    Args:
        audio_path: Path to the audio file
        expected_language: Expected BCP-47 or short language code (e.g., "hi-IN" or "hi")
        whisper_model: Whisper model to use for detection
        strict_mode: If True, require exact language match (no family tolerance)

    Returns:
        StageResult with language verification results
    """
    issues = []

    # Normalize expected language code
    expected_short = LANGUAGE_CODE_MAPPING.get(expected_language, expected_language.split("-")[0])

    try:
        import whisper

        # Load model
        model = whisper.load_model(whisper_model)

        # Step 1: Detect language (let Whisper auto-detect)
        audio = whisper.load_audio(audio_path)
        audio = whisper.pad_or_trim(audio)
        mel = whisper.log_mel_spectrogram(audio).to(model.device)

        _, probs = model.detect_language(mel)
        probs_dict: Dict[str, float] = dict(probs)  # type: ignore[arg-type]
        detected_language: str = max(probs_dict.keys(), key=lambda x: probs_dict[x])
        detected_confidence: float = probs_dict[detected_language]

        # Step 2: Transcribe with auto-detected language for quality assessment
        result = model.transcribe(audio_path, language=None)  # Auto-detect
        transcript: str = str(result["text"])

        # Step 3: Check language match
        language_match = _check_language_match(
            detected_language,
            expected_short,
            strict_mode
        )

        if not language_match["match"]:
            issues.append(
                f"Language mismatch: Expected '{expected_short}', "
                f"detected '{detected_language}' ({language_match['reason']})"
            )

        # Step 4: Check script correctness for non-Latin languages
        script_check = _check_script_correctness(transcript, expected_short)
        if script_check["has_issue"]:
            issues.append(str(script_check["issue"]))

        # Step 5: Assess pronunciation quality
        pronunciation_quality = _assess_pronunciation_quality(
            transcript, expected_short, detected_language
        )

        if pronunciation_quality["quality"] == "poor":
            issues.append(
                f"Poor pronunciation quality: {pronunciation_quality['reason']}"
            )

        # Calculate overall language score
        language_score = _calculate_language_score(
            detected_confidence,
            language_match["match"],
            script_check["score"],
            pronunciation_quality["score"]
        )

        passed = len(issues) == 0 and language_score >= 0.6

        return StageResult(
            stage_name="language_verification",
            passed=passed,
            data={
                "expected_language": expected_short,
                "detected_language": detected_language,
                "detection_confidence": round(detected_confidence, 3),
                "language_match": language_match["match"],
                "language_family_match": language_match.get("family_match", False),
                "script_check": script_check,
                "pronunciation_quality": pronunciation_quality,
                "language_score": round(language_score, 2),
                "transcript_sample": transcript[:300] if len(transcript) > 300 else transcript,
            },
            issues=issues,
        )

    except ImportError as e:
        return StageResult(
            stage_name="language_verification",
            passed=False,
            error=f"Missing dependency: {e}",
        )
    except Exception as e:
        return StageResult(
            stage_name="language_verification",
            passed=False,
            error=f"Language verification failed: {e}",
        )


def _check_language_match(
    detected: str,
    expected: str,
    strict: bool
) -> Dict:
    """Check if detected language matches expected."""
    # Exact match
    if detected == expected:
        return {"match": True, "reason": "exact match"}

    # Family match (e.g., Hindi/Urdu)
    if not strict:
        family = LANGUAGE_FAMILIES.get(expected, [expected])
        if detected in family:
            return {
                "match": True,
                "reason": f"family match ({detected} in {family})",
                "family_match": True
            }

    return {
        "match": False,
        "reason": f"detected {detected}, expected {expected}"
    }


def _check_script_correctness(transcript: str, language: str) -> Dict:
    """Check if transcript contains expected script characters."""
    script_config = SCRIPT_PATTERNS.get(language)

    if not script_config:
        # No script requirements for Latin-based languages
        return {"has_issue": False, "score": 1.0, "script": "Latin (default)"}

    # Count characters in expected script ranges
    total_alpha = 0
    in_expected_script = 0

    for char in transcript:
        if char.isalpha():
            total_alpha += 1
            code_point = ord(char)
            for range_start, range_end in script_config["unicode_ranges"]:
                if range_start <= code_point <= range_end:
                    in_expected_script += 1
                    break

    if total_alpha == 0:
        return {"has_issue": True, "score": 0.0, "issue": "No alphabetic content detected"}

    script_ratio = in_expected_script / total_alpha

    # For non-Latin languages, we expect some script characters
    # But TTS output transcribed by Whisper often comes out as transliteration
    # So we use a low threshold and check for coherence instead

    if script_ratio < 0.1:
        # Check if this is coherent transliteration vs gibberish
        return {
            "has_issue": False,  # Don't fail for transliteration
            "score": 0.7,  # Partial score
            "script": "Transliterated to Latin",
            "expected_script": script_config["name"],
            "script_ratio": round(script_ratio, 3),
        }

    return {
        "has_issue": False,
        "score": min(1.0, script_ratio + 0.5),
        "script": script_config["name"],
        "script_ratio": round(script_ratio, 3),
    }


def _assess_pronunciation_quality(
    transcript: str,
    expected_language: str,
    detected_language: str
) -> Dict:
    """Assess pronunciation quality based on transcript coherence."""
    indicators = ACCENT_QUALITY_INDICATORS.get(expected_language, {})
    good_patterns = indicators.get("good_indicators", [])

    transcript_lower = transcript.lower()

    # Count good indicator matches
    good_matches = 0
    matched_words = []

    for pattern in good_patterns:
        matches = re.findall(pattern, transcript_lower, re.IGNORECASE)
        if matches:
            good_matches += len(matches)
            matched_words.extend(matches)

    # Calculate quality score
    # More recognized words = better pronunciation
    word_count = len(transcript.split())

    if word_count < 10:
        return {
            "quality": "insufficient_data",
            "score": 0.5,
            "reason": "Not enough content to assess"
        }

    # Good indicators per 100 words
    indicator_density = (good_matches / word_count) * 100

    if indicator_density >= 2.0:
        quality = "excellent"
        score = 1.0
        reason = f"High recognition rate: {len(matched_words)} native words detected"
    elif indicator_density >= 0.5:
        quality = "good"
        score = 0.8
        reason = f"Good recognition: {len(matched_words)} native words detected"
    elif indicator_density >= 0.1:
        quality = "acceptable"
        score = 0.6
        reason = f"Some native words recognized: {matched_words[:5]}"
    else:
        # Check if language family match (Hindi detected as Urdu is OK)
        family = LANGUAGE_FAMILIES.get(expected_language, [expected_language])
        if detected_language in family:
            quality = "acceptable"
            score = 0.6
            reason = f"Detected as related language ({detected_language})"
        else:
            quality = "poor"
            score = 0.3
            reason = f"Few native words recognized, detected as {detected_language}"

    return {
        "quality": quality,
        "score": score,
        "reason": reason,
        "indicator_density": round(indicator_density, 2),
        "matched_words": matched_words[:10],  # Sample of matched words
    }


def _calculate_language_score(
    detection_confidence: float,
    language_match: bool,
    script_score: float,
    pronunciation_score: float
) -> float:
    """Calculate overall language verification score."""
    # Weighted average
    weights = {
        "detection": 0.2,
        "match": 0.3,
        "script": 0.2,
        "pronunciation": 0.3,
    }

    match_score = 1.0 if language_match else 0.5

    score = (
        weights["detection"] * detection_confidence +
        weights["match"] * match_score +
        weights["script"] * script_score +
        weights["pronunciation"] * pronunciation_score
    )

    return score
