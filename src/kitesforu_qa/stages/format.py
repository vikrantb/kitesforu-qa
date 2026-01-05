"""Stage 1: Audio format validation using ffprobe."""

import json
import subprocess
from typing import Optional

from ..models.results import StageResult


def check_audio_format(
    audio_path: str,
    expected_duration: Optional[float] = None,
    duration_tolerance: float = 0.05,  # 5% tolerance
    min_sample_rate: int = 22050,
    min_bitrate: int = 96000,
) -> StageResult:
    """
    Check if audio file is valid and meets format requirements.

    Args:
        audio_path: Path to the audio file
        expected_duration: Expected duration in seconds (optional)
        duration_tolerance: Tolerance for duration check (0.05 = 5%)
        min_sample_rate: Minimum sample rate in Hz
        min_bitrate: Minimum bitrate in bps

    Returns:
        StageResult with format validation results
    """
    issues = []

    try:
        # Run ffprobe to get audio info
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return StageResult(
                stage_name="format",
                passed=False,
                error=f"File is corrupted or unreadable: {result.stderr}",
            )

        data = json.loads(result.stdout)
        format_info = data.get('format', {})
        streams = data.get('streams', [])

        # Find audio stream
        audio_stream = None
        for stream in streams:
            if stream.get('codec_type') == 'audio':
                audio_stream = stream
                break

        if not audio_stream:
            return StageResult(
                stage_name="format",
                passed=False,
                error="No audio stream found in file",
            )

        # Extract metadata
        format_name = format_info.get('format_name', 'unknown')
        duration = float(format_info.get('duration', 0))
        bitrate = int(format_info.get('bit_rate', 0))
        sample_rate = int(audio_stream.get('sample_rate', 0))
        channels = int(audio_stream.get('channels', 0))
        codec = audio_stream.get('codec_name', 'unknown')

        # Validate format
        valid_formats = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac']
        if not any(fmt in format_name.lower() for fmt in valid_formats):
            issues.append(f"Unsupported format: {format_name}")

        # Validate sample rate
        if sample_rate < min_sample_rate:
            issues.append(
                f"Sample rate too low: {sample_rate}Hz (min: {min_sample_rate}Hz)"
            )

        # Validate bitrate (only for lossy formats)
        if bitrate > 0 and bitrate < min_bitrate and 'mp3' in format_name.lower():
            issues.append(
                f"Bitrate too low: {bitrate}bps (min: {min_bitrate}bps)"
            )

        # Validate duration if expected
        if expected_duration is not None and duration > 0:
            tolerance = expected_duration * duration_tolerance
            if abs(duration - expected_duration) > tolerance:
                issues.append(
                    f"Duration mismatch: {duration:.1f}s "
                    f"(expected: {expected_duration:.1f}s ±{duration_tolerance*100:.0f}%)"
                )

        # Check for very short audio (likely error)
        if duration < 5:
            issues.append(f"Audio too short: {duration:.1f}s (min: 5s)")

        passed = len(issues) == 0

        return StageResult(
            stage_name="format",
            passed=passed,
            data={
                "valid": True,
                "format": format_name,
                "codec": codec,
                "duration_seconds": duration,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "channels": channels,
            },
            issues=issues,
        )

    except subprocess.TimeoutExpired:
        return StageResult(
            stage_name="format",
            passed=False,
            error="ffprobe timed out - file may be too large or corrupted",
        )
    except json.JSONDecodeError as e:
        return StageResult(
            stage_name="format",
            passed=False,
            error=f"Failed to parse ffprobe output: {e}",
        )
    except FileNotFoundError:
        return StageResult(
            stage_name="format",
            passed=False,
            error="ffprobe not found - please install FFmpeg",
        )
    except Exception as e:
        return StageResult(
            stage_name="format",
            passed=False,
            error=f"Unexpected error: {e}",
        )
