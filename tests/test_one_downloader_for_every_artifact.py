"""The shared downloader (``kitesforu_qa.integrations.download``): a file, or a typed error.

Every guarantee here is exercised over a real socket: a local HTTP server on 127.0.0.1 that stalls,
trickles, truncates, fails transiently, refuses, or serves HTML. ``gs://`` runs through a fake blob
in place of ``google.cloud.storage``. Offline and $0.
"""
from __future__ import annotations

import inspect
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from kitesforu_qa.integrations import download as dl
from kitesforu_qa.integrations import gcs
from kitesforu_qa.integrations.download import DownloadError, download

GEN = 1759660800123456
BODY = b"ID3" + bytes(range(256)) * 16          # 4,099 bytes of "audio"
FAST = {"attempts": 3, "backoff_s": (0.0, 0.0)}


class _Handler(BaseHTTPRequestHandler):
    """One behaviour per path. ``server.hits`` counts the requests each path received."""

    def log_message(self, *_args):  # keep pytest output clean
        pass

    def _head(self, code, ctype="audio/mpeg", length=None, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):  # noqa: N802 — the http.server API
        path = self.path.split("?", 1)[0]
        hits = self.server.hits
        hits[path] = hits.get(path, 0) + 1
        if path == "/ok.mp3":
            self._head(200, length=len(BODY), extra={"x-goog-generation": str(GEN)})
            self.wfile.write(BODY)
        elif path == "/redirect.mp3":
            self._head(302, ctype="text/html", length=0, extra={"Location": "/ok.mp3"})
        elif path in ("/missing.mp3", "/gone.mp3", "/forbidden.mp3", "/always503.mp3"):
            code = {"/missing.mp3": 404, "/gone.mp3": 410, "/forbidden.mp3": 403,
                    "/always503.mp3": 503}[path]
            self._head(code, ctype="text/plain", length=2)
            self.wfile.write(b"no")
        elif path == "/login":
            page = b"<html><body>sign in</body></html>"
            self._head(200, ctype="text/html; charset=utf-8", length=len(page))
            self.wfile.write(page)
        elif path == "/empty.mp3":
            self._head(200, length=0)
        elif path == "/flaky.mp3":
            if hits[path] == 1:
                self._head(503, ctype="text/plain", length=2)
                self.wfile.write(b"no")
            else:
                self._head(200, length=len(BODY), extra={"x-goog-generation": str(GEN)})
                self.wfile.write(BODY)
        elif path == "/short.mp3":
            self._head(200, length=1000)
            self.wfile.write(b"x" * 500)
            self.wfile.flush()
            self.close_connection = True
        elif path == "/stall.mp3":
            self._head(200, length=1000)
            self.wfile.write(b"x" * 100)
            self.wfile.flush()
            time.sleep(3)
        elif path == "/trickle.mp3":
            self._head(200, length=100_000)
            for _ in range(60):
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.1)
        else:
            self._head(404, ctype="text/plain", length=0)


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.hits = {}
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _left(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


# ── https ─────────────────────────────────────────────────────────────────────────────────────

def test_a_good_download_lands_whole_and_names_the_object(server, tmp_path):
    srv, base = server
    got = download(f"{base}/ok.mp3", str(tmp_path / "ep.mp3"))
    assert (got.path, got.size, got.generation) == (str(tmp_path / "ep.mp3"), len(BODY), GEN)
    assert got.content_type == "audio/mpeg"
    assert (tmp_path / "ep.mp3").read_bytes() == BODY and _left(tmp_path) == ["ep.mp3"]


def test_a_redirect_reports_the_object_it_ended_on(server, tmp_path):
    srv, base = server
    got = download(f"{base}/redirect.mp3", str(tmp_path / "ep.mp3"))
    assert (got.size, got.generation) == (len(BODY), GEN)


@pytest.mark.parametrize("path, not_found, transient, cause", [
    ("/missing.mp3", True, False, "HTTP 404"),
    ("/gone.mp3", True, False, "HTTP 410"),
    ("/forbidden.mp3", False, False, "403"),
    ("/login", False, False, "an HTML page"),
    ("/empty.mp3", False, False, "empty"),
    ("/always503.mp3", False, True, "HTTP 503"),
    ("/short.mp3", False, True, "IncompleteRead|truncated, 500 of 1000 bytes"),
])
def test_a_failed_download_raises_names_its_cause_and_leaves_no_file(
        server, tmp_path, path, not_found, transient, cause):
    srv, base = server
    with pytest.raises(DownloadError, match=cause) as err:
        download(f"{base}{path}", str(tmp_path / "ep.mp3"), **FAST)
    assert (err.value.not_found, err.value.transient) == (not_found, transient)
    assert _left(tmp_path) == []        # no file, and no .part: a truncated episode is never graded


def test_a_transient_failure_is_retried_and_recovers(server, tmp_path):
    srv, base = server
    got = download(f"{base}/flaky.mp3", str(tmp_path / "ep.mp3"), **FAST)
    assert got.size == len(BODY) and srv.hits["/flaky.mp3"] == 2


def test_retries_are_bounded(server, tmp_path):
    srv, base = server
    with pytest.raises(DownloadError):
        download(f"{base}/always503.mp3", str(tmp_path / "ep.mp3"), **FAST)
    assert srv.hits["/always503.mp3"] == 3


@pytest.mark.parametrize("path", ["/missing.mp3", "/forbidden.mp3", "/login", "/empty.mp3"])
def test_a_permanent_failure_is_not_retried(server, tmp_path, path):
    srv, base = server
    with pytest.raises(DownloadError):
        download(f"{base}{path}", str(tmp_path / "ep.mp3"), **FAST)
    assert srv.hits[path] == 1


@pytest.mark.timeout(20)
def test_a_stall_ends_at_the_read_timeout(server, tmp_path):
    srv, base = server
    t0 = time.monotonic()
    with pytest.raises(DownloadError) as err:
        download(f"{base}/stall.mp3", str(tmp_path / "ep.mp3"), timeout=(1.0, 0.5), attempts=1)
    assert time.monotonic() - t0 < 2.5 and err.value.transient, err.value
    assert _left(tmp_path) == []


@pytest.mark.timeout(20)
def test_a_trickle_ends_at_the_deadline(server, tmp_path):
    """One byte every 0.1 s never trips a 1 s read timeout. Only the deadline ends it."""
    srv, base = server
    t0 = time.monotonic()
    with pytest.raises(DownloadError, match="deadline"):
        download(f"{base}/trickle.mp3", str(tmp_path / "ep.mp3"), timeout=(1.0, 1.0),
                 deadline_s=1.0, attempts=1)
    assert time.monotonic() - t0 < 4.0
    assert _left(tmp_path) == []


class _Read1Raw:
    """A urllib3 2.x body that does not enforce Content-Length: 500 bytes, then EOF."""

    def __init__(self):
        self._left = [b"x" * 500]

    def read1(self, amt, decode_content=True):
        return self._left.pop(0) if self._left else b""


@pytest.mark.parametrize("raw", [object(), "read1"], ids=["urllib3-1.x-no-read1", "read1-no-enforcement"])
def test_the_declared_size_is_checked_without_urllib3s_help(tmp_path, monkeypatch, raw):
    """urllib3 1.x does not enforce Content-Length. A body short of it is refused here anyway, on
    both read paths."""
    calls = []

    class Short:
        status_code = 200
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "1000"}

        def __init__(self):
            self.raw = _Read1Raw() if raw == "read1" else raw

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"x" * 500

    def get(url, timeout, stream):
        calls.append(url)
        return Short()

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(DownloadError, match="truncated, 500 of 1000 bytes"):
        download("https://storage.googleapis.com/b/ep.mp3", str(tmp_path / "ep.mp3"), **FAST)
    assert len(calls) == 3 and _left(tmp_path) == []


def test_a_certificate_failure_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def get(url, timeout, stream):
        calls.append(url)
        raise requests.exceptions.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(DownloadError, match="CERTIFICATE_VERIFY_FAILED") as err:
        download("https://storage.googleapis.com/b/ep.mp3", str(tmp_path / "ep.mp3"), **FAST)
    assert len(calls) == 1 and not err.value.transient


@pytest.mark.parametrize("uri", ["", "ftp://b/ep.mp3", "/tmp/not-a-uri.mp3", "visuals/j/ep.mp3"])
def test_anything_but_https_or_gs_raises(uri, tmp_path):
    with pytest.raises(DownloadError, match="not an https:// or gs:// URI"):
        download(uri, str(tmp_path / "ep.mp3"))


def test_the_defaults_bound_every_wait():
    """Literal values, so a default that drops to None (no timeout, no deadline) goes red."""
    params = inspect.signature(download).parameters
    assert params["timeout"].default == (10.0, 60.0)
    assert params["deadline_s"].default == 900.0
    assert params["attempts"].default == 3
    assert all(math.isfinite(t) and t > 0 for t in dl.TIMEOUT_S)


# ── gs:// ─────────────────────────────────────────────────────────────────────────────────────

class _Blob:
    """What ``download`` asks of a ``google.cloud.storage`` blob."""

    def __init__(self, data=BODY, *, generation=GEN, content_type="audio/mpeg", size=None,
                 fail=None, chunks=1, pause=0.0):
        self.data, self.generation, self.content_type = data, generation, content_type
        self.size = len(data) if size is None else size
        self.fail, self.chunks, self.pause, self.calls = list(fail or []), chunks, pause, []

    def download_to_file(self, fh, timeout, if_generation_match):
        self.calls.append((timeout, if_generation_match))
        if self.fail:
            raise self.fail.pop(0)
        step = max(1, math.ceil(len(self.data) / self.chunks))
        for i in range(0, len(self.data), step):
            fh.write(self.data[i:i + step])
            time.sleep(self.pause)


def _named(name):
    return type(name, (Exception,), {})


def _gcs(monkeypatch, blob):
    looked = []

    def get_blob(uri, *, timeout=None):
        looked.append(uri)
        return blob

    monkeypatch.setattr(gcs, "get_blob", get_blob)
    return looked


def test_a_gs_download_lands_at_the_file_path_pinned_to_its_generation(tmp_path, monkeypatch):
    """The old gs:// path treated the file path as a directory and wrote ``<path>/<object>``."""
    blob = _Blob()
    _gcs(monkeypatch, blob)
    got = download("gs://kitesforu-dev-podcasts/audio/job/final.mp3", str(tmp_path / "job.audio"))
    assert (tmp_path / "job.audio").is_file() and (tmp_path / "job.audio").read_bytes() == BODY
    assert (got.size, got.generation, got.content_type) == (len(BODY), GEN, "audio/mpeg")
    assert blob.calls == [((10.0, 60.0), GEN)]
    assert _left(tmp_path) == ["job.audio"]


@pytest.mark.parametrize("blob, cause, not_found", [
    (None, "no such object", True),
    (_Blob(b""), "empty", False),
    (_Blob(b"<html>sign in</html>", content_type="text/html"), "an HTML page", False),
])
def test_a_gs_download_that_fails_raises(tmp_path, monkeypatch, blob, cause, not_found):
    _gcs(monkeypatch, blob)
    with pytest.raises(DownloadError, match=cause) as err:
        download("gs://b/audio/job/final.mp3", str(tmp_path / "job.audio"), **FAST)
    assert err.value.not_found is not_found and _left(tmp_path) == []


def test_a_short_gs_body_is_truncated_and_retried(tmp_path, monkeypatch):
    blob = _Blob(b"x" * 500, size=1000)
    _gcs(monkeypatch, blob)
    with pytest.raises(DownloadError, match="truncated, 500 of 1000 bytes"):
        download("gs://b/audio/job/final.mp3", str(tmp_path / "job.audio"), **FAST)
    assert len(blob.calls) == 3 and _left(tmp_path) == []


def test_a_transient_gs_failure_is_retried_and_a_permanent_one_is_not(tmp_path, monkeypatch):
    blob = _Blob(fail=[_named("ServiceUnavailable")("503")])
    _gcs(monkeypatch, blob)
    assert download("gs://b/a.mp3", str(tmp_path / "a.mp3"), **FAST).size == len(BODY)
    assert len(blob.calls) == 2
    blob = _Blob(fail=[_named("Forbidden")("403 storage.objects.get denied")])
    _gcs(monkeypatch, blob)
    with pytest.raises(DownloadError, match="403"):
        download("gs://b/b.mp3", str(tmp_path / "b.mp3"), **FAST)
    assert len(blob.calls) == 1


@pytest.mark.timeout(20)
def test_a_gs_trickle_ends_at_the_deadline(tmp_path, monkeypatch):
    _gcs(monkeypatch, _Blob(b"x" * 100, chunks=100, pause=0.05))
    with pytest.raises(DownloadError, match="deadline"):
        download("gs://b/a.mp3", str(tmp_path / "a.mp3"), deadline_s=0.5, attempts=1)
    assert _left(tmp_path) == []


def test_download_from_gcs_keeps_its_directory_contract(tmp_path, monkeypatch):
    _gcs(monkeypatch, _Blob())
    path = gcs.download_from_gcs("gs://b/audio/job/final.mp3", str(tmp_path))
    assert path == str(tmp_path / "final.mp3") and (tmp_path / "final.mp3").is_file()
    _gcs(monkeypatch, None)
    with pytest.raises(DownloadError):
        gcs.download_from_gcs("gs://b/audio/job/other.mp3", str(tmp_path))
