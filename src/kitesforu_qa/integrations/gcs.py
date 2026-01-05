"""Google Cloud Storage integration."""

import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..config import get_config


def download_from_gcs(
    gcs_uri: str,
    local_dir: Optional[str] = None,
) -> str:
    """
    Download file from Google Cloud Storage.

    Args:
        gcs_uri: GCS URI (gs://bucket/path/to/file.mp3)
        local_dir: Local directory to save file

    Returns:
        Local file path
    """
    config = get_config()

    if local_dir is None:
        local_dir = config.temp_dir

    # Ensure directory exists
    os.makedirs(local_dir, exist_ok=True)

    # Parse GCS URI
    parsed = urlparse(gcs_uri)
    if parsed.scheme != "gs":
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    bucket_name = parsed.netloc
    blob_path = parsed.path.lstrip("/")
    filename = os.path.basename(blob_path)

    local_path = os.path.join(local_dir, filename)

    try:
        from google.cloud import storage

        client = storage.Client(project=config.gcs_project)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        blob.download_to_filename(local_path)

        return local_path

    except ImportError:
        raise ImportError(
            "google-cloud-storage not installed. "
            "Install with: pip install google-cloud-storage"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to download from GCS: {e}")


def upload_to_gcs(
    local_path: str,
    gcs_uri: str,
) -> str:
    """
    Upload file to Google Cloud Storage.

    Args:
        local_path: Local file path
        gcs_uri: GCS URI (gs://bucket/path/to/file.mp3)

    Returns:
        GCS URI of uploaded file
    """
    config = get_config()

    # Parse GCS URI
    parsed = urlparse(gcs_uri)
    if parsed.scheme != "gs":
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")

    bucket_name = parsed.netloc
    blob_path = parsed.path.lstrip("/")

    try:
        from google.cloud import storage

        client = storage.Client(project=config.gcs_project)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        blob.upload_from_filename(local_path)

        return gcs_uri

    except ImportError:
        raise ImportError(
            "google-cloud-storage not installed. "
            "Install with: pip install google-cloud-storage"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to upload to GCS: {e}")


def gcs_file_exists(gcs_uri: str) -> bool:
    """
    Check if file exists in GCS.

    Args:
        gcs_uri: GCS URI

    Returns:
        True if file exists
    """
    config = get_config()

    parsed = urlparse(gcs_uri)
    if parsed.scheme != "gs":
        return False

    bucket_name = parsed.netloc
    blob_path = parsed.path.lstrip("/")

    try:
        from google.cloud import storage

        client = storage.Client(project=config.gcs_project)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        return blob.exists()

    except Exception:
        return False
