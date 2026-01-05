"""Tests for content evaluation stage."""

import pytest

from kitesforu_qa.stages.content import quick_script_checks


class TestQuickScriptChecks:
    """Test cases for quick_script_checks function."""

    def test_good_script(self, sample_script):
        """Test with a well-structured script."""
        # Sample script is ~140 words, use 1-min target (~150 words expected)
        issues = quick_script_checks(sample_script, target_duration_min=1)
        # Should pass - has intro, outro, not too short/long
        assert len(issues) == 0

    def test_empty_script(self):
        """Test with empty script."""
        issues = quick_script_checks("", target_duration_min=10)
        assert len(issues) > 0
        assert any("empty" in issue.lower() for issue in issues)

    def test_too_short_script(self):
        """Test with script that's too short for target duration."""
        short_script = "Hello, this is a very short script."
        issues = quick_script_checks(short_script, target_duration_min=10)
        assert len(issues) > 0
        assert any("too short" in issue.lower() for issue in issues)

    def test_too_long_script(self):
        """Test with script that's too long for target duration."""
        # Create a very long script (>1300 words for 1 minute)
        long_script = "Word " * 2000
        issues = quick_script_checks(long_script, target_duration_min=1)
        assert len(issues) > 0
        assert any("too long" in issue.lower() for issue in issues)

    def test_missing_intro(self):
        """Test detection of missing introduction."""
        # No intro words: welcome, hello, today, let's, going to, episode, podcast
        no_intro_script = """
This show is about technology.
We discuss various topics.
That's all for now.
Thank you for listening.
"""
        issues = quick_script_checks(no_intro_script, target_duration_min=1)
        assert any("introduction" in issue.lower() for issue in issues)

    def test_missing_outro(self):
        """Test detection of missing conclusion."""
        no_outro_script = """
Welcome to today's episode!
Let's talk about technology.
We have many topics to cover.
Technology is changing rapidly.
"""
        issues = quick_script_checks(no_outro_script, target_duration_min=1)
        assert any("conclusion" in issue.lower() for issue in issues)

    def test_long_sentences(self):
        """Test detection of overly long sentences (>3 sentences with >35 words)."""
        # Implementation requires MORE than 3 long sentences (>35 words each) to flag
        long_sentence_script = """
Welcome to today's episode where we will be discussing the incredibly fascinating and deeply complex topic of artificial intelligence and machine learning and how these transformative technologies are fundamentally reshaping the way we live work and interact.

This is the second long sentence where we explore the many different ways that technology has fundamentally changed our understanding of what computers can do and how they can help us achieve incredible things in our daily lives.

Here is a third incredibly long and verbose sentence that goes on and on about various topics including data science machine learning neural networks deep learning and all kinds of other fascinating subjects that are transforming industries.

And finally a fourth sentence that is also extremely long and contains many many words about technology innovation disruption transformation and the exciting promising future of computing and artificial intelligence in our rapidly evolving modern society.

Thank you for listening.
"""
        issues = quick_script_checks(long_sentence_script, target_duration_min=1)
        assert any("too long" in issue.lower() and "sentence" in issue.lower() for issue in issues)

    def test_repetitive_content(self):
        """Test detection of repetitive phrases (LLM loop detection)."""
        repetitive_script = """
Welcome to today's episode.
This is a test of the system.
This is a test of the system.
This is a test of the system.
This is a test of the system.
This is a test of the system.
Thank you for listening.
"""
        issues = quick_script_checks(repetitive_script, target_duration_min=1)
        assert any("repetitive" in issue.lower() for issue in issues)
