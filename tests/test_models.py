"""Tests for data models."""

import pytest

from kitesforu_qa.models.language import Language, LanguageConfig, LANGUAGE_PROSODY_THRESHOLDS
from kitesforu_qa.models.results import QAResult, StageResult


class TestLanguage:
    """Test cases for Language enum and config."""

    def test_language_values(self):
        """Test language enum values."""
        assert Language.ENGLISH.value == "en"
        assert Language.SPANISH.value == "es"
        assert Language.HINDI.value == "hi"

    def test_language_config_defaults(self):
        """Test LanguageConfig default initialization."""
        config = LanguageConfig(language=Language.ENGLISH)

        assert config.language == Language.ENGLISH
        assert config.whisper_model == "large-v3"
        assert config.name_patterns is not None
        assert "female" in config.name_patterns
        assert "male" in config.name_patterns
        assert config.prosody_thresholds is not None

    def test_language_config_custom(self):
        """Test LanguageConfig with custom values."""
        custom_patterns = {"female": ["Ana"], "male": ["Carlos"]}
        config = LanguageConfig(
            language=Language.SPANISH,
            whisper_model="base",
            name_patterns=custom_patterns,
        )

        assert config.whisper_model == "base"
        assert config.name_patterns == custom_patterns

    def test_prosody_thresholds_by_language(self):
        """Test that different languages have different prosody thresholds."""
        # Tonal languages should have higher pitch variation thresholds
        zh_thresholds = LANGUAGE_PROSODY_THRESHOLDS.get("zh", {})
        en_thresholds = LANGUAGE_PROSODY_THRESHOLDS.get("en", {})

        assert zh_thresholds.get("pitch_std_min", 0) > en_thresholds.get("pitch_std_min", 0)


class TestStageResult:
    """Test cases for StageResult."""

    def test_passed_result(self):
        """Test creating a passed stage result."""
        result = StageResult(
            stage_name="format",
            passed=True,
            data={"format": "mp3", "duration": 300},
        )

        assert result.passed is True
        assert result.stage_name == "format"
        assert result.data["format"] == "mp3"
        assert len(result.issues) == 0

    def test_failed_result_with_issues(self):
        """Test creating a failed stage result with issues."""
        result = StageResult(
            stage_name="quality",
            passed=False,
            data={"mos_score": 2.5},
            issues=["MOS score too low"],
        )

        assert result.passed is False
        assert len(result.issues) == 1
        assert "MOS" in result.issues[0]

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = StageResult(
            stage_name="pronunciation",
            passed=True,
            data={"accuracy": 0.95},
        )

        d = result.to_dict()

        assert d["passed"] is True
        assert d["accuracy"] == 0.95


class TestQAResult:
    """Test cases for QAResult."""

    def test_all_passed(self):
        """Test QAResult when all stages pass."""
        result = QAResult(job_id="test_123")
        result.add_stage_result(StageResult("format", True, {}))
        result.add_stage_result(StageResult("quality", True, {}))
        result.add_stage_result(StageResult("prosody", True, {}))

        assert result.passed is True
        assert len(result.failed_stages) == 0
        assert result.job_id == "test_123"

    def test_some_failed(self):
        """Test QAResult when some stages fail."""
        result = QAResult()
        result.add_stage_result(StageResult("format", True, {}))
        result.add_stage_result(StageResult("quality", False, {}, issues=["Bad quality"]))
        result.add_stage_result(StageResult("prosody", True, {}))

        assert result.passed is False
        assert "quality" in result.failed_stages
        assert len(result.all_issues) == 1

    def test_to_dict_structure(self):
        """Test to_dict produces correct structure."""
        result = QAResult(job_id="test_456", language="es")
        result.add_stage_result(StageResult("format", True, {"duration": 300}))
        result.execution_time_seconds = 5.5

        d = result.to_dict()

        assert d["job_id"] == "test_456"
        assert d["language"] == "es"
        assert d["overall_passed"] is True
        assert d["execution_time_seconds"] == 5.5
        assert "stages" in d
        assert "summary" in d
        assert d["summary"]["total_stages"] == 1
        assert d["summary"]["passed_stages"] == 1
