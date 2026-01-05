"""Audio file utilities."""

import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple


def get_audio_duration(audio_path: str) -> Optional[float]:
    """
    Get audio duration in seconds using ffprobe.

    Args:
        audio_path: Path to the audio file

    Returns:
        Duration in seconds, or None if failed
    """
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-show_entries',
            'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def normalize_audio_path(audio_path: str, temp_dir: str = "/tmp/kitesforu-qa") -> str:
    """
    Normalize audio path - download from GCS if needed.

    Args:
        audio_path: Local path or GCS URI
        temp_dir: Directory for temporary files

    Returns:
        Local file path
    """
    if audio_path.startswith("gs://"):
        from ..integrations.gcs import download_from_gcs
        return download_from_gcs(audio_path, temp_dir)
    return audio_path


def convert_to_wav(
    input_path: str,
    output_path: Optional[str] = None,
    sample_rate: int = 16000,
) -> str:
    """
    Convert audio to WAV format for processing.

    Args:
        input_path: Input audio file path
        output_path: Output WAV path (optional)
        sample_rate: Target sample rate

    Returns:
        Path to WAV file
    """
    if output_path is None:
        base = Path(input_path).stem
        output_path = f"/tmp/{base}_converted.wav"

    cmd = [
        'ffmpeg', '-y', '-i', input_path,
        '-ar', str(sample_rate), '-ac', '1',
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Audio conversion failed: {result.stderr}")

    return output_path


def get_audio_info(audio_path: str) -> dict:
    """
    Get comprehensive audio file information.

    Args:
        audio_path: Path to the audio file

    Returns:
        Dictionary with audio metadata
    """
    import json

    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_format', '-show_streams', audio_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return {"error": "Failed to get audio info"}

    data = json.loads(result.stdout)
    format_info = data.get('format', {})
    streams = data.get('streams', [])

    audio_stream = None
    for stream in streams:
        if stream.get('codec_type') == 'audio':
            audio_stream = stream
            break

    return {
        "format": format_info.get('format_name'),
        "duration": float(format_info.get('duration', 0)),
        "bitrate": int(format_info.get('bit_rate', 0)),
        "size_bytes": int(format_info.get('size', 0)),
        "codec": audio_stream.get('codec_name') if audio_stream else None,
        "sample_rate": int(audio_stream.get('sample_rate', 0)) if audio_stream else 0,
        "channels": int(audio_stream.get('channels', 0)) if audio_stream else 0,
    }


def cleanup_temp_files(temp_dir: str = "/tmp/kitesforu-qa") -> int:
    """
    Clean up temporary files older than 1 hour.

    Args:
        temp_dir: Directory to clean

    Returns:
        Number of files deleted
    """
    import time

    if not os.path.exists(temp_dir):
        return 0

    count = 0
    current_time = time.time()
    max_age = 3600  # 1 hour

    for filename in os.listdir(temp_dir):
        filepath = os.path.join(temp_dir, filename)
        if os.path.isfile(filepath):
            file_age = current_time - os.path.getmtime(filepath)
            if file_age > max_age:
                try:
                    os.remove(filepath)
                    count += 1
                except Exception:
                    pass

    return count
