"""Tests for format validation stage."""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from kitesforu_qa.stages.format import check_audio_format


class TestCheckAudioFormat:
    """Test cases for check_audio_format function."""

    def test_valid_audio_format(self):
        """Test with valid audio metadata."""
        mock_output = '''
{
    "format": {
        "format_name": "mp3",
        "duration": "612.5",
        "bit_rate": "192000"
    },
    "streams": [
        {
            "codec_type": "audio",
            "codec_name": "mp3",
            "sample_rate": "44100",
            "channels": 2
        }
    ]
}
'''
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )

            result = check_audio_format("/fake/audio.mp3")

            assert result.passed is True
            assert result.data["valid"] is True
            assert result.data["format"] == "mp3"
            assert result.data["duration_seconds"] == 612.5
            assert result.data["sample_rate"] == 44100
            assert len(result.issues) == 0

    def test_corrupted_file(self):
        """Test with corrupted/unreadable file."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error reading file"
            )

            result = check_audio_format("/fake/corrupted.mp3")

            assert result.passed is False
            assert "corrupted" in result.error.lower() or "unreadable" in result.error.lower()

    def test_low_sample_rate(self):
        """Test detection of low sample rate."""
        mock_output = '''
{
    "format": {
        "format_name": "mp3",
        "duration": "60.0",
        "bit_rate": "192000"
    },
    "streams": [
        {
            "codec_type": "audio",
            "codec_name": "mp3",
            "sample_rate": "16000",
            "channels": 1
        }
    ]
}
'''
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )

            result = check_audio_format("/fake/audio.mp3", min_sample_rate=22050)

            assert result.passed is False
            assert any("sample rate" in issue.lower() for issue in result.issues)

    def test_too_short_audio(self):
        """Test detection of very short audio."""
        mock_output = '''
{
    "format": {
        "format_name": "mp3",
        "duration": "2.5",
        "bit_rate": "192000"
    },
    "streams": [
        {
            "codec_type": "audio",
            "codec_name": "mp3",
            "sample_rate": "44100",
            "channels": 2
        }
    ]
}
'''
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )

            result = check_audio_format("/fake/short.mp3")

            assert result.passed is False
            assert any("too short" in issue.lower() for issue in result.issues)

    def test_duration_mismatch(self):
        """Test duration validation against expected."""
        mock_output = '''
{
    "format": {
        "format_name": "mp3",
        "duration": "300.0",
        "bit_rate": "192000"
    },
    "streams": [
        {
            "codec_type": "audio",
            "codec_name": "mp3",
            "sample_rate": "44100",
            "channels": 2
        }
    ]
}
'''
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )

            # Expected 600s but got 300s
            result = check_audio_format("/fake/audio.mp3", expected_duration=600)

            assert result.passed is False
            assert any("duration" in issue.lower() for issue in result.issues)

    def test_ffprobe_not_found(self):
        """Test handling when ffprobe is not installed."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("ffprobe not found")

            result = check_audio_format("/fake/audio.mp3")

            assert result.passed is False
            assert "ffprobe" in result.error.lower() or "ffmpeg" in result.error.lower()
