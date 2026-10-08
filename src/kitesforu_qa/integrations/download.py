"""One downloader for every job artifact qa grades: https or gs://, into a file, or a typed error.

THE CLASS IT CLOSES. Each grader had its own fetch, and most turned a failed fetch into "no file":
``None``, ``False``, or a skipped pair. Every check that needs the file then calls ``skip()``, exactly
as for a job that has no such file, and a scorecard of skips passes. ``quality_matrix.resolve_audio``
did not fetch HTTPS at all, and 3,154 of the 3,162 completed jobs carry an https
``outputs.audio_url`` (census, 2026-10-05). The ``urllib`` fetches also failed every HTTPS GET on a
python.org framework python whose ``Install Certificates.command`` was never run (here
``/usr/local/bin/python3`` 3.12.3). Apple's ``/usr/bin/python3`` verifies the same URL.

THE CONTRACT. :func:`download` returns :class:`Downloaded`, or raises :class:`DownloadError`. It never
returns ``None`` or ``False``. Both schemes give the same guarantees:

* the bytes go to ``<local_path>.part``, renamed onto ``local_path`` only when complete, so a failed
  transfer never leaves a truncated file to be graded as a truncated episode;
* an empty body is refused, and so is an HTML page where media was expected (``media=True``);
* the size is checked against what the source declared (``Content-Length``, or the blob's size). That
  check does not depend on urllib3 2.x enforcing it;
* every socket operation has a timeout, and the whole download, retries included, has a deadline, so
  a trickle is bounded as well as a stall;
* a transient failure (a reset, a timeout, a 5xx, a 408 or 429, a short body, a GCS precondition
  race) is retried, a bounded number of times;
* ``not_found`` on the error is True only when the source says the object does not exist (HTTP 404
  or 410, or no such blob): the one failure a caller may read as an absence.

:class:`Downloaded` carries what the fetch said about the object: its GCS ``generation`` (the
``x-goog-generation`` header, or the blob's) and the bytes on disk. That is the master identity
``harness.painted_timeline_sidecar.FetchedMaster`` holds a producer's stamp to.

COST, for operators: every call downloads the whole object, about 1 MB per minute of mp3 audio (an
estimate). A transient failure can re-download it, up to ``ATTEMPTS`` times. GCS internet egress list
price is ~$0.12/GiB. No provider is called.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, BinaryIO

from . import gcs

#: Seconds to connect, and to wait for each read.
TIMEOUT_S = (10.0, 60.0)
#: Seconds for the whole download, every attempt and backoff included.
DEADLINE_S = 900.0
#: Attempts in all, for a transient failure.
ATTEMPTS = 3
#: Seconds before the second and the third attempt.
BACKOFF_S = (1.0, 3.0)
_CHUNK = 1 << 20


class DownloadError(RuntimeError):
    """A download that did not produce the file. ``not_found`` is True only when the source says the
    object does not exist; ``transient`` marks a failure worth another attempt."""

    def __init__(self, message: str, *, uri: str, not_found: bool = False,
                 transient: bool = False) -> None:
        super().__init__(message)
        self.uri = uri
        self.not_found = not_found
        self.transient = transient


@dataclass(frozen=True)
class Downloaded:
    uri: str
    path: str
    size: int                    # bytes written to ``path``
    generation: int | None       # the GCS object generation of what was fetched, when the source says
    content_type: str | None


@dataclass(frozen=True)
class _Fetched:
    """What one attempt learned about the object, besides its bytes."""

    generation: int | None
    content_type: str | None
    declared_size: int | None    # Content-Length, or the blob's size; None when the source gave none


class _DeadlineWriter:
    """The file being written, refusing to grow past the download's deadline: a trickle that never
    trips a per-read timeout still ends."""

    def __init__(self, fh: BinaryIO, deadline: float, uri: str) -> None:
        self._fh, self._deadline, self._uri = fh, deadline, uri

    def write(self, data: bytes) -> int:
        if time.monotonic() > self._deadline:
            raise DownloadError(f"{self._uri}: the download outran its deadline", uri=self._uri)
        return self._fh.write(data)

    def __getattr__(self, name: str) -> Any:  # tell, flush, seek: whatever the GCS client asks of a file
        return getattr(self._fh, name)


def download(uri: str, local_path: str, *, media: bool = True,
             timeout: tuple[float, float] = TIMEOUT_S, deadline_s: float = DEADLINE_S,
             attempts: int = ATTEMPTS,
             backoff_s: tuple[float, ...] = BACKOFF_S) -> Downloaded:
    """Download ``uri`` (``https://``, ``http://`` or ``gs://``) to the file ``local_path``."""
    uri = str(uri or "").strip()
    once = _scheme(uri)
    os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
    part = f"{local_path}.part"
    deadline = time.monotonic() + deadline_s
    error: DownloadError | None = None
    for attempt in range(max(1, attempts)):
        try:
            with open(part, "wb") as fh:
                fetched = once(uri, _DeadlineWriter(fh, deadline, uri), timeout)
            size = os.path.getsize(part)
            _accept(uri, size, fetched, media)
            os.replace(part, local_path)
            return Downloaded(uri, local_path, size, fetched.generation, fetched.content_type)
        except Exception as exc:  # every failure is classified, then raised or retried
            error = exc if isinstance(exc, DownloadError) else _classify(uri, exc)
        finally:
            if os.path.exists(part):
                os.remove(part)
        if not error.transient or attempt + 1 >= max(1, attempts):
            break
        wait = backoff_s[min(attempt, len(backoff_s) - 1)] if backoff_s else 0.0
        if time.monotonic() + wait >= deadline:
            break
        time.sleep(wait)
    assert error is not None
    raise error


def _scheme(uri: str) -> Callable[[str, _DeadlineWriter, tuple[float, float]], _Fetched]:
    if uri.startswith(("https://", "http://")):
        return _http_once
    if uri.startswith("gs://"):
        return _gcs_once
    raise DownloadError(f"cannot download {uri!r}: not an https:// or gs:// URI", uri=uri)


def _accept(uri: str, size: int, fetched: _Fetched, media: bool) -> None:
    if size == 0:
        raise DownloadError(f"{uri}: the body was empty", uri=uri)
    if media and (fetched.content_type or "").lower().startswith("text/html"):
        raise DownloadError(f"{uri}: an HTML page ({fetched.content_type}), not media", uri=uri)
    if fetched.declared_size is not None and size != fetched.declared_size:
        raise DownloadError(f"{uri}: truncated, {size} of {fetched.declared_size} bytes",
                            uri=uri, transient=True)


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _http_once(uri: str, out: _DeadlineWriter, timeout: tuple[float, float]) -> _Fetched:
    import requests  # a declared dependency, with its own CA bundle

    with requests.get(uri, timeout=timeout, stream=True) as resp:
        code = resp.status_code
        if code in (404, 410):
            raise DownloadError(f"{uri}: HTTP {code}", uri=uri, not_found=True)
        if code in (408, 429) or code >= 500:
            raise DownloadError(f"{uri}: HTTP {code}", uri=uri, transient=True)
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=_CHUNK):
            if chunk:
                out.write(chunk)
        headers = resp.headers
        encoded = (headers.get("Content-Encoding") or "identity").lower() != "identity"
        return _Fetched(generation=_int(headers.get("x-goog-generation")),
                        content_type=headers.get("Content-Type"),
                        declared_size=None if encoded else _int(headers.get("Content-Length")))


def _gcs_once(uri: str, out: _DeadlineWriter, timeout: tuple[float, float]) -> _Fetched:
    blob = gcs.get_blob(uri, timeout=timeout)
    if blob is None:
        raise DownloadError(f"{uri}: no such object", uri=uri, not_found=True)
    # Pinned to the generation just read, so the bytes and the metadata are one object; a rewrite in
    # between is a precondition failure, which is retried.
    blob.download_to_file(out, timeout=timeout, if_generation_match=blob.generation)
    return _Fetched(generation=_int(blob.generation), content_type=blob.content_type,
                    declared_size=_int(blob.size))


#: Exception class names that are worth another attempt, whichever library raised them.
_TRANSIENT = frozenset({
    "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "ChunkedEncodingError",
    "ProtocolError", "IncompleteRead", "ServiceUnavailable", "InternalServerError", "BadGateway",
    "GatewayTimeout", "TooManyRequests", "DeadlineExceeded", "RetryError", "DataCorruption",
    "PreconditionFailed", "TransportError", "TimeoutError", "ReadTimeoutError",
})
#: Never transient, even where the class derives from one that is (requests' SSLError is a
#: ConnectionError): a certificate the client cannot verify stays unverifiable.
_PERMANENT = frozenset({"SSLError", "Forbidden", "Unauthorized", "InvalidURL", "MissingSchema"})


def _classify(uri: str, exc: Exception) -> DownloadError:
    names = {cls.__name__ for cls in type(exc).__mro__}
    not_found = "NotFound" in names
    transient = not not_found and not (names & _PERMANENT) and bool(names & _TRANSIENT)
    err = DownloadError(f"{uri}: {type(exc).__name__}: {str(exc)[:200]}", uri=uri,
                        not_found=not_found, transient=transient)
    err.__cause__ = exc
    return err
