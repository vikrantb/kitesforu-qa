"""Tests for Language Verification Stage.

Tests the language detection, script verification, and pronunciation quality
assessment for multi-language podcast support.
"""

import sys
import pytest
from unittest.mock import Mock, patch, MagicMock
import numpy as np

from kitesforu_qa.stages.language_verification import (
    LANGUAGE_CODE_MAPPING,
    LANGUAGE_FAMILIES,
    SCRIPT_PATTERNS,
    ACCENT_QUALITY_INDICATORS,
    check_language_verification,
    _check_language_match,
    _check_script_correctness,
    _assess_pronunciation_quality,
    _calculate_language_score,
)


class TestLanguageCodeMapping:
    """Test BCP-47 to Whisper code mappings."""

    def test_all_supported_languages_mapped(self):
        """Verify all BCP-47 codes from schemas are mapped."""
        # BCP-47 codes from kitesforu-schemas
        bcp47_codes = [
            "en-US", "en-GB",  # English
            "es-ES", "es-MX",  # Spanish
            "fr-FR",  # French
            "de-DE",  # German
            "hi-IN",  # Hindi
            "ja-JP",  # Japanese
            "ko-KR",  # Korean
            "zh-CN",  # Chinese
            "pt-BR", "pt-PT",  # Portuguese
            "ar-SA",  # Arabic
            "ru-RU",  # Russian
            "it-IT",  # Italian
            "nl-NL",  # Dutch
        ]

        for code in bcp47_codes:
            assert code in LANGUAGE_CODE_MAPPING, f"Missing mapping for {code}"
            mapped = LANGUAGE_CODE_MAPPING[code]
            assert len(mapped) == 2, f"Mapped code should be 2-letter: {mapped}"

    def test_hindi_mapping(self):
        """Test Hindi BCP-47 to Whisper code mapping."""
        assert LANGUAGE_CODE_MAPPING["hi-IN"] == "hi"
        assert LANGUAGE_CODE_MAPPING.get("hi") == "hi"

    def test_english_variants(self):
        """Test English variants map to same short code."""
        assert LANGUAGE_CODE_MAPPING["en-US"] == "en"
        assert LANGUAGE_CODE_MAPPING["en-GB"] == "en"

    def test_spanish_variants(self):
        """Test Spanish variants map to same short code."""
        assert LANGUAGE_CODE_MAPPING["es-ES"] == "es"
        assert LANGUAGE_CODE_MAPPING["es-MX"] == "es"

    def test_portuguese_variants(self):
        """Test Portuguese variants map to same short code."""
        assert LANGUAGE_CODE_MAPPING["pt-BR"] == "pt"
        assert LANGUAGE_CODE_MAPPING["pt-PT"] == "pt"


class TestLanguageFamilies:
    """Test language family relationships for flexible matching."""

    def test_hindi_urdu_family(self):
        """Hindi and Urdu are mutually intelligible."""
        family = LANGUAGE_FAMILIES.get("hi", [])
        assert "hi" in family
        assert "ur" in family

    def test_spanish_portuguese_family(self):
        """Spanish and Portuguese share similarities."""
        family = LANGUAGE_FAMILIES.get("es", [])
        assert "es" in family
        assert "pt" in family

    def test_chinese_family(self):
        """Chinese variants are related."""
        family = LANGUAGE_FAMILIES.get("zh", [])
        assert "zh" in family
        assert "yue" in family  # Cantonese


class TestScriptPatterns:
    """Test script detection for non-Latin languages."""

    def test_hindi_devanagari_range(self):
        """Hindi uses Devanagari script."""
        config = SCRIPT_PATTERNS["hi"]
        assert config["name"] == "Devanagari"
        assert len(config["unicode_ranges"]) > 0
        # Devanagari range: 0x0900-0x097F
        start, end = config["unicode_ranges"][0]
        assert start <= 0x0915  # 'क' (ka)
        assert end >= 0x0915

    def test_arabic_script_range(self):
        """Arabic uses Arabic script."""
        config = SCRIPT_PATTERNS["ar"]
        assert config["name"] == "Arabic"
        start, end = config["unicode_ranges"][0]
        assert start <= 0x0628  # 'ب' (ba)
        assert end >= 0x0628

    def test_japanese_multiple_scripts(self):
        """Japanese uses multiple scripts."""
        config = SCRIPT_PATTERNS["ja"]
        assert "Hiragana" in config["expected_scripts"]
        assert "Katakana" in config["expected_scripts"]
        assert "Han" in config["expected_scripts"]

    def test_korean_hangul(self):
        """Korean uses Hangul script."""
        config = SCRIPT_PATTERNS["ko"]
        assert config["name"] == "Korean"
        assert "Hangul" in config["expected_scripts"]


class TestAccentQualityIndicators:
    """Test pronunciation quality indicators per language."""

    def test_hindi_good_indicators(self):
        """Hindi has good pronunciation indicators."""
        indicators = ACCENT_QUALITY_INDICATORS.get("hi", {})
        good = indicators.get("good_indicators", [])
        assert len(good) > 0
        # Should include common Hindi words
        patterns_str = " ".join(good)
        assert "namaste" in patterns_str or "namaskar" in patterns_str

    def test_spanish_good_indicators(self):
        """Spanish has good pronunciation indicators."""
        indicators = ACCENT_QUALITY_INDICATORS.get("es", {})
        good = indicators.get("good_indicators", [])
        assert len(good) > 0

    def test_french_good_indicators(self):
        """French has good pronunciation indicators."""
        indicators = ACCENT_QUALITY_INDICATORS.get("fr", {})
        good = indicators.get("good_indicators", [])
        assert len(good) > 0


class TestCheckLanguageMatch:
    """Test language matching logic."""

    def test_exact_match(self):
        """Test exact language match."""
        result = _check_language_match("hi", "hi", strict=False)
        assert result["match"] is True
        assert "exact" in result["reason"]

    def test_family_match_non_strict(self):
        """Test family match in non-strict mode."""
        # Hindi detected as Urdu should be acceptable
        result = _check_language_match("ur", "hi", strict=False)
        assert result["match"] is True
        assert result.get("family_match", False) is True

    def test_family_match_strict_mode(self):
        """Test family match rejected in strict mode."""
        result = _check_language_match("ur", "hi", strict=True)
        assert result["match"] is False

    def test_mismatch_different_families(self):
        """Test mismatch for unrelated languages."""
        result = _check_language_match("en", "hi", strict=False)
        assert result["match"] is False

    def test_english_exact_match(self):
        """Test English exact match."""
        result = _check_language_match("en", "en", strict=False)
        assert result["match"] is True


class TestCheckScriptCorrectness:
    """Test script detection in transcripts."""

    def test_latin_languages_pass(self):
        """Latin-based languages don't need script check."""
        result = _check_script_correctness("Hello world this is a test", "en")
        assert result["has_issue"] is False
        assert result["score"] == 1.0

    def test_devanagari_detection(self):
        """Test Devanagari script detection for Hindi."""
        hindi_text = "नमस्ते दुनिया यह एक परीक्षण है"
        result = _check_script_correctness(hindi_text, "hi")
        assert result["has_issue"] is False
        assert result["score"] >= 0.7
        assert "Devanagari" in str(result.get("script", ""))

    def test_transliteration_acceptable(self):
        """Transliterated Hindi (Latin script) should be acceptable."""
        transliterated = "namaste duniya yeh ek test hai"
        result = _check_script_correctness(transliterated, "hi")
        # Transliteration should not fail the check
        assert result["has_issue"] is False
        assert result["score"] >= 0.5

    def test_empty_transcript(self):
        """Empty transcript should be flagged."""
        result = _check_script_correctness("", "hi")
        assert result["has_issue"] is True
        assert result["score"] == 0.0


class TestAssessPronunciationQuality:
    """Test pronunciation quality assessment."""

    def test_hindi_good_pronunciation(self):
        """Test good Hindi pronunciation detection."""
        # Transcript with many recognizable Hindi words
        transcript = (
            "Namaste, aaj hum bharat ke bare mein baat karenge. "
            "Yeh desh bahut khoobsurat hai aur yahan ke log "
            "bahut achhe hain. Dhanyavad."
        )
        result = _assess_pronunciation_quality(transcript, "hi", "hi")
        assert result["quality"] in ["excellent", "good", "acceptable"]
        assert result["score"] >= 0.6

    def test_insufficient_data(self):
        """Test handling of short transcripts."""
        result = _assess_pronunciation_quality("hello", "hi", "hi")
        assert result["quality"] == "insufficient_data"
        assert result["score"] == 0.5

    def test_spanish_pronunciation(self):
        """Test Spanish pronunciation detection."""
        transcript = (
            "Hola amigos, muy buenas tardes. Gracias por escuchar. "
            "El señor Garcia es muy amable."
        )
        result = _assess_pronunciation_quality(transcript, "es", "es")
        assert result["quality"] in ["excellent", "good", "acceptable"]

    def test_family_language_acceptable(self):
        """Test that related language detection is acceptable."""
        # Need at least 10 words for assessment (not insufficient_data)
        transcript = (
            "This is some generic text content without any specific "
            "language indicators that would match Hindi patterns"
        )
        # Hindi expected, Urdu detected - should be acceptable due to family match
        result = _assess_pronunciation_quality(transcript, "hi", "ur")
        assert result["quality"] == "acceptable"
        assert result["score"] >= 0.5


class TestCalculateLanguageScore:
    """Test overall language score calculation."""

    def test_perfect_score(self):
        """Test perfect conditions produce high score."""
        score = _calculate_language_score(
            detection_confidence=0.95,
            language_match=True,
            script_score=1.0,
            pronunciation_score=1.0
        )
        assert score >= 0.9

    def test_mismatch_reduces_score(self):
        """Test language mismatch reduces score."""
        score_match = _calculate_language_score(
            detection_confidence=0.9,
            language_match=True,
            script_score=0.8,
            pronunciation_score=0.8
        )
        score_mismatch = _calculate_language_score(
            detection_confidence=0.9,
            language_match=False,
            script_score=0.8,
            pronunciation_score=0.8
        )
        assert score_match > score_mismatch

    def test_low_confidence_reduces_score(self):
        """Test low detection confidence reduces score."""
        score_high = _calculate_language_score(
            detection_confidence=0.95,
            language_match=True,
            script_score=0.8,
            pronunciation_score=0.8
        )
        score_low = _calculate_language_score(
            detection_confidence=0.3,
            language_match=True,
            script_score=0.8,
            pronunciation_score=0.8
        )
        assert score_high > score_low


class TestCheckLanguageVerificationIntegration:
    """Integration tests for full language verification."""

    @pytest.fixture
    def mock_whisper(self):
        """Mock Whisper model for testing without actual audio."""
        # Create mock whisper module
        mock_whisper_module = MagicMock()
        mock_model = MagicMock()
        mock_whisper_module.load_model.return_value = mock_model

        # Mock audio loading functions
        mock_whisper_module.load_audio.return_value = np.zeros(16000)  # 1 second
        mock_whisper_module.pad_or_trim.return_value = np.zeros(480000)
        mock_whisper_module.log_mel_spectrogram.return_value = MagicMock(
            to=MagicMock(return_value=np.zeros((80, 3000)))
        )

        # Patch sys.modules so `import whisper` inside function gets our mock
        with patch.dict(sys.modules, {"whisper": mock_whisper_module}):
            yield mock_whisper_module, mock_model

    def test_hindi_language_verification(self, mock_whisper):
        """Test Hindi language verification end-to-end."""
        mock, mock_model = mock_whisper

        # Mock language detection - Hindi detected
        mock_model.detect_language.return_value = (
            None,
            {"hi": 0.85, "ur": 0.10, "en": 0.05}
        )

        # Mock transcription - Hindi text
        mock_model.transcribe.return_value = {
            "text": "Namaste, aaj hum bharat ke bare mein baat karenge. Dhanyavad."
        }

        result = check_language_verification(
            audio_path="/fake/audio.mp3",
            expected_language="hi-IN"
        )

        assert result.passed is True
        assert result.data["detected_language"] == "hi"
        assert result.data["language_match"] is True

    def test_english_language_verification(self, mock_whisper):
        """Test English language verification end-to-end."""
        mock, mock_model = mock_whisper

        # Mock language detection - English detected
        mock_model.detect_language.return_value = (
            None,
            {"en": 0.95, "de": 0.03, "nl": 0.02}
        )

        # Mock transcription - English text
        mock_model.transcribe.return_value = {
            "text": "Hello and welcome to this podcast. Today we discuss science."
        }

        result = check_language_verification(
            audio_path="/fake/audio.mp3",
            expected_language="en-US"
        )

        assert result.passed is True
        assert result.data["detected_language"] == "en"

    def test_language_mismatch_detection(self, mock_whisper):
        """Test that language mismatch is detected."""
        mock, mock_model = mock_whisper

        # Expected Hindi, but English detected
        mock_model.detect_language.return_value = (
            None,
            {"en": 0.90, "hi": 0.05, "ur": 0.05}
        )

        mock_model.transcribe.return_value = {
            "text": "Hello world, this is a test in English."
        }

        result = check_language_verification(
            audio_path="/fake/audio.mp3",
            expected_language="hi-IN"
        )

        assert result.passed is False
        assert result.data["language_match"] is False
        assert len(result.issues) > 0
        assert any("mismatch" in issue.lower() for issue in result.issues)

    def test_spanish_language_verification(self, mock_whisper):
        """Test Spanish language verification."""
        mock, mock_model = mock_whisper

        mock_model.detect_language.return_value = (
            None,
            {"es": 0.88, "pt": 0.08, "en": 0.04}
        )

        mock_model.transcribe.return_value = {
            "text": "Hola y bienvenidos. Gracias por escuchar este podcast."
        }

        result = check_language_verification(
            audio_path="/fake/audio.mp3",
            expected_language="es-ES"
        )

        assert result.passed is True
        assert result.data["detected_language"] == "es"


class TestMultiLanguageCoverage:
    """Test that all supported languages have adequate test coverage."""

    @pytest.mark.parametrize("lang_code,short_code", [
        ("en-US", "en"),
        ("en-GB", "en"),
        ("es-ES", "es"),
        ("es-MX", "es"),
        ("fr-FR", "fr"),
        ("de-DE", "de"),
        ("hi-IN", "hi"),
        ("ja-JP", "ja"),
        ("ko-KR", "ko"),
        ("zh-CN", "zh"),
        ("pt-BR", "pt"),
        ("pt-PT", "pt"),
        ("ar-SA", "ar"),
        ("ru-RU", "ru"),
        ("it-IT", "it"),
        ("nl-NL", "nl"),
    ])
    def test_language_code_normalizes_correctly(self, lang_code, short_code):
        """Test all supported BCP-47 codes normalize correctly."""
        mapped = LANGUAGE_CODE_MAPPING.get(lang_code, lang_code.split("-")[0])
        assert mapped == short_code, f"{lang_code} should map to {short_code}"

    @pytest.mark.parametrize("lang_code", [
        "hi-IN", "ja-JP", "ko-KR", "zh-CN", "ar-SA", "ru-RU",
    ])
    def test_non_latin_languages_have_script_patterns(self, lang_code):
        """Test non-Latin languages have script detection patterns."""
        short_code = LANGUAGE_CODE_MAPPING.get(lang_code, lang_code.split("-")[0])
        assert short_code in SCRIPT_PATTERNS, f"Missing script pattern for {lang_code}"
        config = SCRIPT_PATTERNS[short_code]
        assert "unicode_ranges" in config
        assert len(config["unicode_ranges"]) > 0
