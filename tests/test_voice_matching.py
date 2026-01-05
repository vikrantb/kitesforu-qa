"""Tests for voice matching stage."""

import pytest

from kitesforu_qa.stages.voice_matching import (
    _detect_placeholder_names,
    _check_topic_voice_match,
    PLACEHOLDER_PATTERNS,
)


class TestPlaceholderDetection:
    """Test cases for placeholder name detection."""

    def test_host_1_detection(self):
        """Test detection of 'host 1' placeholder."""
        transcript = "Hello and welcome! Host 1 will start the show."
        issues = _detect_placeholder_names(transcript)
        assert len(issues) > 0
        assert any("host" in issue.lower() for issue in issues)

    def test_speaker_2_detection(self):
        """Test detection of 'speaker 2' placeholder."""
        transcript = "Now let's hear from Speaker 2 about this topic."
        issues = _detect_placeholder_names(transcript)
        assert len(issues) > 0

    def test_host_one_detection(self):
        """Test detection of 'host one' spelled out."""
        transcript = "Host one begins the episode with an introduction."
        issues = _detect_placeholder_names(transcript)
        assert len(issues) > 0

    def test_no_placeholder(self):
        """Test that valid names don't trigger false positives."""
        transcript = "Hello, I'm Sarah and welcome to the show!"
        issues = _detect_placeholder_names(transcript)
        assert len(issues) == 0

    def test_multiple_placeholders(self):
        """Test detection of multiple placeholder names."""
        transcript = "Host 1 starts, then Host 2 joins the discussion."
        issues = _detect_placeholder_names(transcript)
        # Should detect at least one
        assert len(issues) >= 1


class TestTopicVoiceMatch:
    """Test cases for topic-voice matching."""

    def test_seniors_topic_mature_voice(self):
        """Test that seniors topic with mature voice is OK."""
        issues = _check_topic_voice_match("Retirement planning for seniors", "mature")
        assert len(issues) == 0

    def test_seniors_topic_young_voice(self):
        """Test that seniors topic with young voice is flagged."""
        issues = _check_topic_voice_match("Retirement planning for seniors", "young")
        assert len(issues) > 0
        assert any("senior" in issue.lower() for issue in issues)

    def test_teen_topic_young_voice(self):
        """Test that teen topic with young voice is OK."""
        issues = _check_topic_voice_match("College tips for teens", "young")
        assert len(issues) == 0

    def test_teen_topic_mature_voice(self):
        """Test that teen topic with mature voice is flagged."""
        issues = _check_topic_voice_match("High school life for teens", "mature")
        assert len(issues) > 0

    def test_neutral_topic_any_voice(self):
        """Test that neutral topics don't flag any voice type."""
        issues = _check_topic_voice_match("Technology trends 2024", "mature")
        assert len(issues) == 0

        issues = _check_topic_voice_match("Technology trends 2024", "young")
        assert len(issues) == 0

    def test_unknown_voice_age(self):
        """Test handling of unknown voice age."""
        issues = _check_topic_voice_match("Retirement tips", "unknown")
        # Unknown should not trigger issues
        assert len(issues) == 0
