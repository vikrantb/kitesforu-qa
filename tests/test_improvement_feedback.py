"""Tests for improvement feedback generation."""

import pytest
from kitesforu_qa.models.results import QAResult, StageResult
from kitesforu_qa.utils.improvement_feedback import generate_improvement_feedback


class TestImprovementFeedback:
    """Tests for improvement feedback generation."""

    def test_no_feedback_when_all_passed(self):
        """No suggestions when all stages pass."""
        result = QAResult(language="en")
        result.add_stage_result(StageResult(
            stage_name="format",
            passed=True,
            data={"valid": True}
        ))
        result.add_stage_result(StageResult(
            stage_name="pronunciation",
            passed=True,
            data={"word_error_rate": 0.05}
        ))

        feedback = result.get_improvement_feedback()

        assert feedback["has_suggestions"] is False
        assert feedback["priority"] == "none"
        assert len(feedback["suggestions"]) == 0

    def test_high_wer_generates_feedback(self):
        """High word error rate generates pronunciation feedback."""
        result = QAResult(language="en", user_request="Test podcast")
        result.add_stage_result(StageResult(
            stage_name="pronunciation",
            passed=False,
            data={"word_error_rate": 0.15},
            issues=["Word error rate 15.0% exceeds threshold 10.0%"]
        ))

        feedback = result.get_improvement_feedback()

        assert feedback["has_suggestions"] is True
        assert feedback["priority"] == "high"
        assert any(s["stage"] == "pronunciation" for s in feedback["suggestions"])

    def test_low_mos_generates_feedback(self):
        """Low MOS score generates quality feedback."""
        result = QAResult(language="en")
        result.add_stage_result(StageResult(
            stage_name="quality",
            passed=False,
            data={"mos_score": 2.5},
            issues=["MOS score 2.5 below threshold 3.5"]
        ))

        feedback = result.get_improvement_feedback()

        assert feedback["has_suggestions"] is True
        assert any(s["stage"] == "quality" for s in feedback["suggestions"])

    def test_content_issues_generate_feedback(self):
        """Content issues generate script improvement feedback."""
        result = QAResult(language="en", user_request="Create AI podcast")
        result.add_stage_result(StageResult(
            stage_name="content",
            passed=False,
            data={
                "scores": {"topic": 5, "structure": 4, "engagement": 6},
                "overall_score": 5.0,
                "red_flags": ["Script is too short"],
                "quick_check_issues": ["Script too short: 50 words"]
            },
            issues=["Content score 5.0 below threshold 7.0"]
        ))

        feedback = result.get_improvement_feedback()

        assert feedback["has_suggestions"] is True
        # Should have multiple content-related suggestions
        content_suggestions = [s for s in feedback["suggestions"] if s["stage"] == "content"]
        assert len(content_suggestions) >= 2

    def test_monotone_prosody_generates_feedback(self):
        """Monotone voice generates prosody feedback."""
        result = QAResult(language="en")
        result.add_stage_result(StageResult(
            stage_name="prosody",
            passed=False,
            data={"is_monotone": True, "pitch_variation_hz": 10.0},
            issues=["Voice is monotone"]
        ))

        feedback = result.get_improvement_feedback()

        assert feedback["has_suggestions"] is True
        prosody_suggestions = [s for s in feedback["suggestions"] if s["stage"] == "prosody"]
        assert len(prosody_suggestions) >= 1
        # Check for new structure: target and modification
        assert "target" in prosody_suggestions[0] or "modification" in prosody_suggestions[0]

    def test_placeholder_detected_generates_feedback(self):
        """Placeholder names generate voice matching feedback."""
        result = QAResult(language="en")
        result.add_stage_result(StageResult(
            stage_name="voice_matching",
            passed=False,
            data={"placeholder_detected": True},
            issues=["Placeholder name detected: Host 1"]
        ))

        feedback = result.get_improvement_feedback()

        assert feedback["has_suggestions"] is True
        voice_suggestions = [s for s in feedback["suggestions"] if s["stage"] == "voice_matching"]
        assert len(voice_suggestions) >= 1

    def test_improvement_prompt_generated(self):
        """Improvement prompt is generated for failed results."""
        result = QAResult(language="en", user_request="Create podcast about AI")
        result.add_stage_result(StageResult(
            stage_name="content",
            passed=False,
            data={"overall_score": 5.0},
            issues=["Content score too low"]
        ))

        feedback = result.get_improvement_feedback()

        assert "improvement_prompt" in feedback
        # New structure: targets system components for modification
        assert "# System Component Modifications Required" in feedback["improvement_prompt"]
        assert "Create podcast about AI" in feedback["improvement_prompt"]

    def test_to_dict_includes_feedback(self):
        """QAResult.to_dict() includes feedback when failed."""
        result = QAResult(language="en")
        result.add_stage_result(StageResult(
            stage_name="quality",
            passed=False,
            data={"mos_score": 2.0},
            issues=["Low quality"]
        ))

        data = result.to_dict(include_feedback=True)

        assert "improvement_feedback" in data
        assert data["improvement_feedback"]["has_suggestions"] is True

    def test_to_dict_excludes_feedback_when_passed(self):
        """QAResult.to_dict() excludes feedback when all passed."""
        result = QAResult(language="en")
        result.add_stage_result(StageResult(
            stage_name="quality",
            passed=True,
            data={"mos_score": 4.5}
        ))

        data = result.to_dict(include_feedback=True)

        assert "improvement_feedback" not in data

    def test_priority_levels(self):
        """Priority is correctly determined from suggestion priorities."""
        result = QAResult(language="en")

        # Add format issue (medium priority)
        result.add_stage_result(StageResult(
            stage_name="format",
            passed=False,
            data={"sample_rate": 16000, "valid": True},
            issues=["Low sample rate"]
        ))

        feedback = result.get_improvement_feedback()

        # Should have at least medium priority
        assert feedback["priority"] in ["medium", "high", "critical"]

    def test_summary_generated(self):
        """Summary text is generated."""
        result = QAResult(language="en")
        result.add_stage_result(StageResult(
            stage_name="quality",
            passed=False,
            data={"mos_score": 2.0},
            issues=["Low quality"]
        ))

        feedback = result.get_improvement_feedback()

        assert "summary" in feedback
        # New summary format: "Modify {files}: {priorities}" or "All quality checks passed"
        assert "modify" in feedback["summary"].lower() or "passed" in feedback["summary"].lower()
