"""One downloader for the job artifacts qa grades: https or gs://, into a file or into memory, or a
typed error.

THE CLASS IT CLOSES. Each grader had its own fetch, and most turned a failed fetch into "no file":
``None``, ``False``, or a skipped pair. Every check that needs the file then calls ``skip()``, exactly
as for a job that has no such file, and a scorecard of skips passes. ``quality_matrix.resolve_audio``
did not fetch HTTPS at all, and 3,154 of the 3,162 completed jobs carry an https
``outputs.audio_url`` (census, 2026-10-05). The ``urllib`` fetches also failed every HTTPS GET on a
python.org framework python whose ``Install Certificates.command`` was never run (here
``/usr/local/bin/python3`` 3.12.3). Apple's ``/usr/bin/python3`` verifies the same URL. The fetch
sites that do not go through here are listed, with the reason each may not, in
``tests/test_every_grader_fetches_through_the_one_downloader.py`` (``ALLOWED``).

THE CONTRACT. :func:`download` returns :class:`Downloaded`, and :func:`fetch_bytes` returns the body;
both raise :class:`DownloadError` on any failure, and neither returns ``None`` or ``False``. Both
schemes give the same guarantees:

* :func:`download` writes ``<local_path>.part`` and renames it onto ``local_path`` only when complete,
  so a failed transfer never leaves a truncated file to be graded as a truncated episode;
* an empty body is refused, and so is a page where media was expected (``media=True``): HTML, JSON,
  XML or any other ``text/*``;
* the size is checked against what the source declared (``Content-Length``, or the blob's size). That
  check does not depend on urllib3 enforcing it. A body that arrived content-encoded is decoded, and
  its declared (encoded) size is not compared;
* every socket operation has a timeout, and the whole transfer, retries included, has a deadline. The
  body is read with urllib3's ``read1``, which returns whatever has arrived, so the deadline is
  checked at every arrival: a trickle is bounded as well as a stall, on both schemes. The deadline can
  be overrun by one read timeout (a read already waiting when it falls due) plus one connect timeout;
* ``gs://`` media is streamed the same way, over an authorized session, from the JSON API, pinned to
  the generation its metadata named (``ifGenerationMatch``): an object rewritten in between is a 412,
  retried, and one deleted in between is a 404, ``not_found``;
* a transient failure (a reset, a timeout, a 5xx, a 408, 412 or 429, a short body) is retried, a
  bounded number of times;
* ``not_found`` on the error is True only when the source says the object does not exist (HTTP 404
  or 410, or no such blob): the one failure a caller may read as an absence.

urllib3 must be 2.2 or newer (``read1``; declared in ``pyproject.toml``). With an older one every
transfer raises, saying so, rather than read in blocks that a trickle can hold open past the deadline.

:class:`Downloaded` carries what the fetch said about the object: its GCS ``generation`` (the
``x-goog-generation`` header, or the blob's) and the bytes on disk. That is the master identity
``harness.painted_timeline_sidecar.FetchedMaster`` holds a producer's stamp to.

COST, for operators: every call downloads the whole object, and a transient failure can download it
again, up to ``ATTEMPTS`` times in all. No provider is called. Master AUDIO runs ~1.08 MB per minute of
speech (HEAD ``Content-Length`` over the speech timeline of the 12 newest completed jobs, 2026-10-07),
~$0.00012 a minute at the GCS internet egress list price of ~$0.12/GiB. The VIDEO master runs ~9.6 MB
per minute (median of the 12 newest completed jobs that surface one, 6.8-13.5; HEAD
``Content-Length`` over ``visual.video_runtime_ms``, 2026-10-08, all 17-111 s long), ~$0.0011 a minute,
about 9x the audio. See ``COST_CHANGELOG.md``. A loop over many jobs takes a :class:`FetchBudget`.
"""

from __future__ import annotations

import io
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

#: Content types that are never media: a login page, an API error body, a redirect notice.
_NOT_MEDIA = ("text/", "application/json", "application/xml", "application/problem+json")


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
    declared_size: int | None    # Content-Length, or the blob's size; None when not comparable


class _DeadlineWriter:
    """The sink being written, refusing to grow past the transfer's deadline. The read loop
    (:func:`_receive`) writes whatever each ``read1`` returned, so the deadline is checked at every
    arrival of data; a stall between arrivals is bounded by the read timeout."""

    def __init__(self, fh: BinaryIO, deadline: float, uri: str) -> None:
        self._fh, self.deadline, self._uri = fh, deadline, uri

    def write(self, data: bytes) -> int:
        if time.monotonic() > self.deadline:
            raise DownloadError(f"{self._uri}: the download outran its deadline", uri=self._uri)
        return self._fh.write(data)


def download(uri: str, local_path: str, *, media: bool = True,
             timeout: tuple[float, float] = TIMEOUT_S, deadline_s: float = DEADLINE_S,
             attempts: int = ATTEMPTS,
             backoff_s: tuple[float, ...] = BACKOFF_S) -> Downloaded:
    """Download ``uri`` (``https://``, ``http://`` or ``gs://``) to the file ``local_path``."""
    uri = str(uri or "").strip()
    once = _scheme(uri)
    os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
    part = f"{local_path}.part"

    def attempt(deadline: float) -> Downloaded:
        try:
            with open(part, "wb") as fh:
                fetched = once(uri, _DeadlineWriter(fh, deadline, uri), timeout, None)
            size = os.path.getsize(part)
            _accept(uri, size, fetched, media)
            os.replace(part, local_path)
            return Downloaded(uri, local_path, size, fetched.generation, fetched.content_type)
        finally:
            if os.path.exists(part):
                os.remove(part)

    return _with_retries(uri, attempt, deadline_s=deadline_s, attempts=attempts, backoff_s=backoff_s)


def fetch_bytes(uri: str, *, cap: int, media: bool = False,
                timeout: tuple[float, float] = TIMEOUT_S, deadline_s: float = 60.0,
                attempts: int = ATTEMPTS, backoff_s: tuple[float, ...] = BACKOFF_S) -> bytes:
    """The body of ``uri``, in memory, with :func:`download`'s guarantees. A body over ``cap`` bytes
    raises (not retried) after at most ``cap + 1`` bytes were read, so a URI that names a master by
    mistake costs the cap, not the video."""
    uri = str(uri or "").strip()
    once = _scheme(uri)

    def attempt(deadline: float) -> bytes:
        buf = io.BytesIO()
        fetched = once(uri, _DeadlineWriter(buf, deadline, uri), timeout, cap)
        body = buf.getvalue()
        _accept(uri, len(body), fetched, media)
        return body

    return _with_retries(uri, attempt, deadline_s=deadline_s, attempts=attempts, backoff_s=backoff_s)


def _with_retries(uri: str, attempt: Callable[[float], Any], *, deadline_s: float, attempts: int,
                  backoff_s: tuple[float, ...]) -> Any:
    deadline = time.monotonic() + deadline_s
    error: DownloadError | None = None
    for n in range(max(1, attempts)):
        try:
            return attempt(deadline)
        except Exception as exc:  # every failure is classified, then raised or retried
            error = exc if isinstance(exc, DownloadError) else _classify(uri, exc)
        if not error.transient or n + 1 >= max(1, attempts):
            break
        wait = backoff_s[min(n, len(backoff_s) - 1)] if backoff_s else 0.0
        if time.monotonic() + wait >= deadline:
            break
        time.sleep(wait)
    assert error is not None
    raise error


class FetchBudget:
    """A run's limit on downloading, for a grader that fetches one job's artifacts after another.

    Each download is bounded on its own, but a degraded edge or a dead link makes EVERY job run to its
    own deadline in turn: 400 jobs at ~970 s each is days, and the run is right but never finishes.
    So a run gets a wall-clock budget, and a breaker that opens after ``max_consecutive`` failed
    downloads in a row (a missing object, ``not_found``, is the source answering, so it resets the
    streak).
    Once either trips, :meth:`download` raises at once, naming why, without a request; the caller
    records the job as unscored with that cause, as it does any failed download."""

    def __init__(self, wall_s: float, *, max_consecutive: int = 3,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock, self._wall_s = clock, wall_s
        self._end = clock() + wall_s
        self._max = max_consecutive
        self._streak = 0
        self.spent: str | None = None

    def download(self, uri: str, local_path: str, **kwargs: Any) -> Downloaded:
        if self.spent is None and self._clock() >= self._end:
            self.spent = f"the run's download budget of {self._wall_s:.0f} s is spent"
        if self.spent is not None:
            raise DownloadError(f"{uri}: not fetched: {self.spent}", uri=uri)
        kwargs["deadline_s"] = min(kwargs.get("deadline_s", DEADLINE_S), self._end - self._clock())
        try:
            got = download(uri, local_path, **kwargs)
        except DownloadError as exc:
            self._streak = 0 if exc.not_found else self._streak + 1
            if self._streak >= self._max:
                self.spent = f"{self._streak} downloads failed in a row, the last: {exc}"
            raise
        self._streak = 0
        return got


def _scheme(uri: str) -> Callable[[str, _DeadlineWriter, tuple[float, float], int | None], _Fetched]:
    if uri.startswith(("https://", "http://")):
        return _http_once
    if uri.startswith("gs://"):
        return _gcs_once
    raise DownloadError(f"cannot download {uri!r}: not an https:// or gs:// URI", uri=uri)


def _accept(uri: str, size: int, fetched: _Fetched, media: bool) -> None:
    if size == 0:
        raise DownloadError(f"{uri}: the body was empty", uri=uri)
    ctype = (fetched.content_type or "").lower()
    if media and ctype.startswith(_NOT_MEDIA):
        raise DownloadError(f"{uri}: a {fetched.content_type} body, not media", uri=uri)
    if fetched.declared_size is not None and size != fetched.declared_size:
        raise DownloadError(f"{uri}: truncated, {size} of {fetched.declared_size} bytes",
                            uri=uri, transient=True)


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _receive(uri: str, resp: Any, out: _DeadlineWriter, cap: int | None) -> tuple[Any, bool]:
    """Check the response's status, then stream its body into ``out`` with ``read1``. Returns the
    headers and whether the body arrived content-encoded."""
    code = resp.status_code
    if code in (404, 410):
        raise DownloadError(f"{uri}: HTTP {code}", uri=uri, not_found=True)
    if code in (408, 412, 429) or code >= 500:
        raise DownloadError(f"{uri}: HTTP {code}", uri=uri, transient=True)
    resp.raise_for_status()
    # ``read1`` returns whatever has arrived, so the deadline is checked at every arrival. A
    # ``read(n)`` (and ``iter_content``, which uses it) blocks until n bytes come in, as long as a
    # trickle lands a byte inside every read timeout: measured, a 1 MiB read sat through a whole 6 s
    # trickle past a 1 s deadline, and the GCS client's 8 KiB reads through 24.8 s.
    read1 = getattr(resp.raw, "read1", None)
    if read1 is None:
        import urllib3

        raise DownloadError(f"{uri}: urllib3 {urllib3.__version__} has no read1, so the deadline "
                            f"could not bound a trickle; qa needs urllib3>=2.2", uri=uri)
    total = 0
    while True:
        want = _CHUNK if cap is None else min(_CHUNK, cap + 1 - total)
        data = read1(want, decode_content=True)
        if not data:
            break
        total += len(data)
        if cap is not None and total > cap:
            raise DownloadError(f"{uri}: the body exceeds {cap} bytes", uri=uri)
        out.write(data)
    headers = resp.headers
    return headers, (headers.get("Content-Encoding") or "identity").lower() != "identity"


def _http_once(uri: str, out: _DeadlineWriter, timeout: tuple[float, float],
               cap: int | None) -> _Fetched:
    import requests  # a declared dependency, with its own CA bundle

    with requests.get(uri, timeout=timeout, stream=True) as resp:
        headers, encoded = _receive(uri, resp, out, cap)
    # After any redirect these are the FINAL response's headers, so a generation an earlier hop
    # carried never names the object.
    return _Fetched(generation=_int(headers.get("x-goog-generation")),
                    content_type=headers.get("Content-Type"),
                    declared_size=None if encoded else _int(headers.get("Content-Length")))


def _gcs_once(uri: str, out: _DeadlineWriter, timeout: tuple[float, float],
              cap: int | None) -> _Fetched:
    # No library retry inside the attempt: this module's own loop retries, inside the deadline.
    blob = gcs.get_blob(uri, timeout=timeout, retry=None)
    if blob is None:
        raise DownloadError(f"{uri}: no such object", uri=uri, not_found=True)
    url = gcs.media_url(uri, if_generation_match=blob.generation)
    with gcs.authorized_session().get(url, timeout=timeout, stream=True) as resp:
        _, encoded = _receive(uri, resp, out, cap)
    return _Fetched(generation=_int(blob.generation), content_type=blob.content_type,
                    declared_size=None if encoded else _int(blob.size))


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
