"""Google Cloud Storage integration.

Downloads go through ``integrations.download.download``, which owns the guarantees every artifact
fetch shares (a ``.part`` file renamed when complete, an empty or non-media body refused, the declared
size checked, a deadline that bounds a trickle, a bounded retry, a typed error). This module owns the
GCS side of it: the one client constructor, the blob lookup, and the authorized session and media URL
that a ``gs://`` body is streamed through.

There is deliberately no "does this object exist" helper that answers False on any error: a 403 or a
missing credential would read as "not there". ``get_blob(uri) is None`` is the existence check; it
raises on anything but a missing object.
"""

import functools
import os
from typing import Any
from urllib.parse import quote, urlparse

from ..config import get_config

#: Read-only access is all qa's downloads need.
_READ_SCOPE = "https://www.googleapis.com/auth/devstorage.read_only"
_MEDIA_BASE = "https://storage.googleapis.com/download/storage/v1"


def storage_client() -> Any:
    """The one ``google.cloud.storage`` client constructor for qa."""
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage not installed. "
            "Install with: pip install google-cloud-storage"
        ) from exc
    return storage.Client(project=get_config().gcs_project)


@functools.lru_cache(maxsize=1)
def authorized_session() -> Any:
    """An HTTP session carrying qa's application-default credentials, read-only, for streaming an
    object's media (``integrations.download``). One per process: the token is fetched once and
    refreshed by the session when it expires."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=[_READ_SCOPE])
    return AuthorizedSession(credentials)


def split_gs_uri(gcs_uri: str) -> tuple[str, str]:
    """``gs://bucket/object`` as ``(bucket, object)``. ValueError for anything else."""
    parsed = urlparse(gcs_uri)
    bucket, blob_path = parsed.netloc, parsed.path.lstrip("/")
    if parsed.scheme != "gs" or not bucket or not blob_path:
        raise ValueError(f"Invalid GCS URI: {gcs_uri}")
    return bucket, blob_path


def media_url(gcs_uri: str, *, if_generation_match: Any = None) -> str:
    """The JSON API media URL of ``gs://bucket/object``. With ``if_generation_match``, GCS serves the
    object only while it is still that generation (412 otherwise; 404 once it is deleted)."""
    bucket, blob_path = split_gs_uri(gcs_uri)
    url = f"{_MEDIA_BASE}/b/{quote(bucket, safe='')}/o/{quote(blob_path, safe='')}?alt=media"
    if if_generation_match is not None:
        url += f"&ifGenerationMatch={int(if_generation_match)}"
    return url


#: ``get_blob``'s default: the library's own retry policy.
LIBRARY_RETRY = object()


def get_blob(gcs_uri: str, *, timeout: Any = None, retry: Any = LIBRARY_RETRY) -> Any:
    """The blob ``gcs_uri`` names, with its metadata loaded (generation, size, content type), or
    None when no such object exists. ``retry=None`` turns the library's own retry off (the
    downloader retries inside its deadline instead)."""
    bucket, blob_path = split_gs_uri(gcs_uri)
    extra = {} if retry is LIBRARY_RETRY else {"retry": retry}
    return storage_client().bucket(bucket).get_blob(blob_path, timeout=timeout, **extra)


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

    Raises ValueError for anything but ``gs://bucket/object``, ImportError without
    google-cloud-storage, and RuntimeError when the upload fails.
    """
    bucket_name, blob_path = split_gs_uri(gcs_uri)
    client = storage_client()
    try:
        client.bucket(bucket_name).blob(blob_path).upload_from_filename(local_path)
    except Exception as e:
        raise RuntimeError(f"Failed to upload to GCS: {e}") from e
    return gcs_uri
