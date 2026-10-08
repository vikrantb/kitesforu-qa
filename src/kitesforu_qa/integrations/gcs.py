"""Google Cloud Storage integration.

Downloads go through ``integrations.download.download``, which owns the guarantees every artifact
fetch shares (a ``.part`` file renamed when complete, an empty or HTML body refused, the declared size
checked, a deadline, a bounded retry, a typed error). This module owns the GCS side of it: the one
client constructor and the blob lookup.
"""

import os
from typing import Any
from urllib.parse import urlparse

from ..config import get_config


def storage_client() -> Any:
    """The one ``google.cloud.storage`` client constructor for qa's reads."""
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage not installed. "
            "Install with: pip install google-cloud-storage"
        ) from exc
    return storage.Client(project=get_config().gcs_project)


def split_gs_uri(gcs_uri: str) -> tuple[str, str]:
    """``gs://bucket/object`` as ``(bucket, object)``. ValueError for anything else."""
    parsed = urlparse(gcs_uri)
    bucket, blob_path = parsed.netloc, parsed.path.lstrip("/")
    if parsed.scheme != "gs" or not bucket or not blob_path:
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    return bucket, blob_path


def get_blob(gcs_uri: str, *, timeout: Any = None) -> Any:
    """The blob ``gcs_uri`` names, with its metadata loaded (generation, size, content type), or
    None when no such object exists."""
    bucket, blob_path = split_gs_uri(gcs_uri)
    return storage_client().bucket(bucket).get_blob(blob_path, timeout=timeout)


def download_from_gcs(
    gcs_uri: str,
    local_dir: str | None = None,
) -> str:
    """
    Download file from Google Cloud Storage into a DIRECTORY.

    Args:
        gcs_uri: GCS URI (gs://bucket/path/to/file.mp3)
        local_dir: Local directory to save file (the file keeps the object's base name)

    Returns:
        Local file path

    Raises ``integrations.download.DownloadError`` (a RuntimeError) on any failure. A caller that
    has a file path should call ``integrations.download.download`` itself.
    """
    from .download import download

    local_dir = local_dir or get_config().temp_dir
    _, blob_path = split_gs_uri(gcs_uri)
    return download(gcs_uri, os.path.join(local_dir, os.path.basename(blob_path))).path


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
