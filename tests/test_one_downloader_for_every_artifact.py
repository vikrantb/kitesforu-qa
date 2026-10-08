"""The shared downloader (``kitesforu_qa.integrations.download``): a file, or a typed error.

Every guarantee here is exercised over a real socket: a local HTTP server on 127.0.0.1 that stalls,
trickles, truncates, fails transiently, refuses, encodes, redirects, or serves a page. ``gs://`` reads
its metadata from a fake blob in place of ``google.cloud.storage``, and streams its media from the same
local server, through the same read loop as https. Offline and $0.
"""
from __future__ import annotations

import gzip
import inspect
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from kitesforu_qa.integrations import download as dl
from kitesforu_qa.integrations import gcs
from kitesforu_qa.integrations.download import DownloadError, FetchBudget, download, fetch_bytes

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
        elif path == "/hop.mp3":   # an earlier hop that names a generation, then a final one that does not
            self._head(302, ctype="text/html", length=0,
                       extra={"Location": "/nogen.mp3", "x-goog-generation": "111"})
        elif path == "/nogen.mp3":
            self._head(200, length=len(BODY))
            self.wfile.write(BODY)
        elif path == "/gzip.mp3":
            packed = gzip.compress(BODY)
            self._head(200, length=len(packed), extra={"Content-Encoding": "gzip"})
            self.wfile.write(packed)
        elif path in ("/error.json", "/error.xml", "/note.txt"):
            ctype = {"/error.json": "application/json", "/error.xml": "application/xml",
                     "/note.txt": "text/plain; charset=utf-8"}[path]
            self._head(200, ctype=ctype, length=len(b"{}"))
            self.wfile.write(b"{}")
        elif path == "/precondition.mp3":
            if hits[path] == 1:
                self._head(412, ctype="application/json", length=2)
                self.wfile.write(b"{}")
            else:
                self._head(200, length=len(BODY))
                self.wfile.write(BODY)
        elif path == "/trickle8k.mp3":   # 8 KiB of body takes ~25 s: one byte every 3 ms
            self._head(200, length=100_000)
            for _ in range(10_000):
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.003)
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
    ("/login", False, False, "text/html; charset=utf-8 body, not media"),
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


def _short_response(raw):
    class Short:
        status_code = 200
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "1000"}

        def __init__(self):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def raise_for_status(self):
            pass

    return Short()


def test_without_read1_every_download_raises_and_names_urllib3(tmp_path, monkeypatch):
    """urllib3 before 2.2 has no ``read1``, and a block read lets a trickle outrun the deadline (the
    round-2 critic forced that path and the trickle test went red). ``pyproject.toml`` requires 2.2;
    a machine that has an older one gets an error naming it, never a silent fallback."""
    calls = []
    monkeypatch.setattr(requests, "get", lambda url, timeout, stream: calls.append(url)
                        or _short_response(object()))
    with pytest.raises(DownloadError, match="has no read1.*urllib3>=2.2") as err:
        download("https://storage.googleapis.com/b/ep.mp3", str(tmp_path / "ep.mp3"), **FAST)
    assert len(calls) == 1 and not err.value.transient and _left(tmp_path) == []


@pytest.mark.parametrize("raw", ["read1"], ids=["read1-no-enforcement"])
def test_the_declared_size_is_checked_without_urllib3s_help(tmp_path, monkeypatch, raw):
    """urllib3 does not always enforce Content-Length. A body short of it is refused here anyway."""
    calls = []

    def get(url, timeout, stream):
        calls.append(url)
        return _short_response(_Read1Raw())

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


def test_only_the_final_responses_generation_names_the_object(server, tmp_path):
    """A generation on an earlier redirect hop names some other object (#184's rule, kept now that
    the downloader replaces curl's headers)."""
    srv, base = server
    got = download(f"{base}/hop.mp3", str(tmp_path / "ep.mp3"))
    assert (got.size, got.generation) == (len(BODY), None) and srv.hits["/nogen.mp3"] == 1


def test_an_encoded_body_is_decoded_and_not_held_to_its_encoded_length(server, tmp_path):
    """``Content-Length`` counts the gzip bytes on the wire; the file holds the decoded ones."""
    srv, base = server
    got = download(f"{base}/gzip.mp3", str(tmp_path / "ep.mp3"))
    assert got.size == len(BODY) and (tmp_path / "ep.mp3").read_bytes() == BODY


@pytest.mark.parametrize("path", ["/error.json", "/error.xml", "/note.txt"])
def test_a_page_is_not_media(server, tmp_path, path):
    """Round 2: ``media=True`` refused only HTML; an API error body graded as an episode is the
    same defect."""
    srv, base = server
    with pytest.raises(DownloadError, match="not media"):
        download(f"{base}{path}", str(tmp_path / "ep.mp3"), **FAST)
    assert srv.hits[path] == 1 and _left(tmp_path) == []
    assert fetch_bytes(f"{base}{path}", cap=100) == b"{}"      # a page is fine where one is asked for


@pytest.mark.timeout(20)
def test_no_backoff_sleeps_past_the_deadline(server, tmp_path):
    srv, base = server
    t0 = time.monotonic()
    with pytest.raises(DownloadError, match="HTTP 503"):
        download(f"{base}/always503.mp3", str(tmp_path / "ep.mp3"), deadline_s=1.0, attempts=3,
                 backoff_s=(5.0, 5.0))
    assert time.monotonic() - t0 < 2.0 and srv.hits["/always503.mp3"] == 1


def test_fetch_bytes_has_the_same_guarantees_in_memory(server, tmp_path):
    srv, base = server
    assert fetch_bytes(f"{base}/ok.mp3", cap=len(BODY)) == BODY
    with pytest.raises(DownloadError, match="exceeds 100 bytes"):
        fetch_bytes(f"{base}/ok.mp3", cap=100)
    with pytest.raises(DownloadError, match="HTTP 404") as err:
        fetch_bytes(f"{base}/missing.mp3", cap=100)
    assert err.value.not_found and srv.hits["/ok.mp3"] == 2


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

class _Meta:
    """The metadata ``download`` reads from a ``google.cloud.storage`` blob."""

    def __init__(self, *, generation=GEN, size=None, content_type="audio/mpeg"):
        self.generation, self.content_type = generation, content_type
        self.size = len(BODY) if size is None else size


def _named(name):
    return type(name, (Exception,), {})


def _gs(monkeypatch, base, path, meta=None, *, raises=()):
    """gs:// wired to the local server: ``get_blob`` answers ``meta`` (or raises ``raises`` in turn),
    the media URL is ``<base><path>``, and the session is a plain one. Returns the calls seen."""
    seen = {"get_blob": [], "media_url": []}
    raising = list(raises)

    def get_blob(uri, *, timeout=None, retry=gcs.LIBRARY_RETRY):
        seen["get_blob"].append((uri, timeout, retry))
        if raising:
            raise raising.pop(0)
        return meta

    def media_url(uri, *, if_generation_match=None):
        seen["media_url"].append((uri, if_generation_match))
        return f"{base}{path}"

    monkeypatch.setattr(gcs, "get_blob", get_blob)
    monkeypatch.setattr(gcs, "media_url", media_url)
    monkeypatch.setattr(gcs, "authorized_session", requests.Session)
    return seen


def test_a_gs_download_lands_at_the_file_path_pinned_to_its_generation(server, tmp_path, monkeypatch):
    """The old gs:// path treated the file path as a directory and wrote ``<path>/<object>``."""
    srv, base = server
    seen = _gs(monkeypatch, base, "/ok.mp3", _Meta())
    got = download("gs://kitesforu-dev-podcasts/audio/job/final.mp3", str(tmp_path / "job.audio"))
    assert (tmp_path / "job.audio").read_bytes() == BODY and _left(tmp_path) == ["job.audio"]
    assert (got.size, got.generation, got.content_type) == (len(BODY), GEN, "audio/mpeg")
    assert seen["get_blob"] == [("gs://kitesforu-dev-podcasts/audio/job/final.mp3", (10.0, 60.0), None)]
    assert seen["media_url"] == [("gs://kitesforu-dev-podcasts/audio/job/final.mp3", GEN)]


@pytest.mark.parametrize("path, meta, cause, not_found", [
    ("/ok.mp3", None, "no such object", True),
    ("/missing.mp3", _Meta(), "HTTP 404", True),           # deleted between the metadata and the GET
    ("/empty.mp3", _Meta(size=0), "empty", False),
    ("/ok.mp3", _Meta(content_type="text/html"), "not media", False),
])
def test_a_gs_download_that_fails_raises(server, tmp_path, monkeypatch, path, meta, cause, not_found):
    srv, base = server
    _gs(monkeypatch, base, path, meta)
    with pytest.raises(DownloadError, match=cause) as err:
        download("gs://b/audio/job/final.mp3", str(tmp_path / "job.audio"), **FAST)
    assert err.value.not_found is not_found and _left(tmp_path) == []


def test_a_short_gs_body_is_truncated_and_retried(server, tmp_path, monkeypatch):
    srv, base = server
    _gs(monkeypatch, base, "/ok.mp3", _Meta(size=len(BODY) + 1))
    with pytest.raises(DownloadError, match=f"truncated, {len(BODY)} of {len(BODY) + 1} bytes"):
        download("gs://b/audio/job/final.mp3", str(tmp_path / "job.audio"), **FAST)
    assert srv.hits["/ok.mp3"] == 3 and _left(tmp_path) == []


def test_an_object_rewritten_mid_download_is_retried(server, tmp_path, monkeypatch):
    """``ifGenerationMatch``: GCS answers 412 once the object is another generation."""
    srv, base = server
    _gs(monkeypatch, base, "/precondition.mp3", _Meta())
    assert download("gs://b/a.mp3", str(tmp_path / "a.mp3"), **FAST).size == len(BODY)
    assert srv.hits["/precondition.mp3"] == 2


@pytest.mark.parametrize("raised, attempts, not_found", [
    (_named("ServiceUnavailable")("503"), 2, False),          # retried, then fetched
    (_named("Forbidden")("403 storage.objects.get denied"), 1, False),
    (_named("NotFound")("404 bucket kitesforu-gone"), 1, True),
])
def test_a_metadata_failure_is_classified(server, tmp_path, monkeypatch, raised, attempts, not_found):
    srv, base = server
    seen = _gs(monkeypatch, base, "/ok.mp3", _Meta(), raises=[raised])
    if attempts == 2:
        assert download("gs://b/a.mp3", str(tmp_path / "a.mp3"), **FAST).size == len(BODY)
    else:
        with pytest.raises(DownloadError, match=str(raised)) as err:
            download("gs://b/a.mp3", str(tmp_path / "a.mp3"), **FAST)
        assert err.value.not_found is not_found
    assert len(seen["get_blob"]) == attempts


@pytest.mark.timeout(20)
def test_a_gs_trickle_ends_at_the_deadline(server, tmp_path, monkeypatch):
    """Round-2 critic: the GCS client read 8 KiB per write, so a byte every 59 s held one chunk for
    days; measured 24.8 s against a 1 s deadline. The media is streamed through ``read1`` now, from a
    REAL socket that sends a byte every 3 ms: 8 KiB of it takes ~25 s, so a block read of that size
    cannot pass this test."""
    srv, base = server
    _gs(monkeypatch, base, "/trickle8k.mp3", _Meta(size=100_000))
    t0 = time.monotonic()
    with pytest.raises(DownloadError, match="deadline"):
        download("gs://b/a.mp3", str(tmp_path / "a.mp3"), timeout=(1.0, 1.0), deadline_s=1.0,
                 attempts=1)
    assert time.monotonic() - t0 < 4.0 and _left(tmp_path) == []


def test_the_media_url_is_the_json_api_pinned_to_the_generation():
    assert gcs.media_url("gs://kitesforu-dev-podcasts/visuals/j/a b.mp4", if_generation_match=GEN) == (
        "https://storage.googleapis.com/download/storage/v1/b/kitesforu-dev-podcasts/o/"
        f"visuals%2Fj%2Fa%20b.mp4?alt=media&ifGenerationMatch={GEN}")


def test_download_from_gcs_keeps_its_directory_contract(server, tmp_path, monkeypatch):
    srv, base = server
    _gs(monkeypatch, base, "/ok.mp3", _Meta())
    path = gcs.download_from_gcs("gs://b/audio/job/final.mp3", str(tmp_path))
    assert path == str(tmp_path / "final.mp3") and (tmp_path / "final.mp3").is_file()
    _gs(monkeypatch, base, "/ok.mp3", None)
    with pytest.raises(DownloadError):
        gcs.download_from_gcs("gs://b/audio/job/other.mp3", str(tmp_path))


def test_an_upload_goes_through_the_one_client_constructor(tmp_path, monkeypatch):
    """Round-2 design: ``upload_to_gcs`` built its own ``storage.Client``. It uses
    ``gcs.storage_client()`` now; a direct construction would raise here, never reach GCS."""
    from google.cloud import storage

    calls = []

    class Blob:
        def upload_from_filename(self, path):
            calls.append(("upload", path))

    class Bucket:
        def blob(self, name):
            calls.append(("blob", name))
            return Blob()

    class Client:
        def bucket(self, name):
            calls.append(("bucket", name))
            return Bucket()

    def direct(*_a, **_k):
        raise AssertionError("upload_to_gcs built its own storage.Client")

    monkeypatch.setattr(storage, "Client", direct)
    monkeypatch.setattr(gcs, "storage_client", Client)
    local = tmp_path / "a.mp3"
    local.write_bytes(b"x")
    assert gcs.upload_to_gcs(str(local), "gs://never-a-bucket-qa/a/b.mp3") == "gs://never-a-bucket-qa/a/b.mp3"
    assert calls == [("bucket", "never-a-bucket-qa"), ("blob", "a/b.mp3"), ("upload", str(local))]
    assert not hasattr(gcs, "gcs_file_exists")      # an existence check that read a 403 as "absent"


# ── a run's budget ────────────────────────────────────────────────────────────────────────────

class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_the_breaker_opens_after_consecutive_failures_and_a_missing_object_resets_it(
        server, tmp_path):
    """Round-2 latency L1: a degraded link makes every job run to its own deadline, one after another.
    After ``max_consecutive`` failures in a row the run stops downloading, at once, and says why. A
    404 is the source answering, so it resets the streak."""
    srv, base = server
    budget = FetchBudget(3600, max_consecutive=2)
    for path in ("/always503.mp3", "/missing.mp3", "/always503.mp3"):
        with pytest.raises(DownloadError):
            budget.download(f"{base}{path}", str(tmp_path / "ep.mp3"), **FAST)
    assert budget.spent is None                              # the 404 reset the streak to 0, then 1
    with pytest.raises(DownloadError):
        budget.download(f"{base}/always503.mp3", str(tmp_path / "ep.mp3"), **FAST)
    assert budget.spent and "2 downloads failed in a row" in budget.spent
    hits = dict(srv.hits)
    with pytest.raises(DownloadError, match="not fetched: 2 downloads failed in a row"):
        budget.download(f"{base}/ok.mp3", str(tmp_path / "ep.mp3"))
    assert srv.hits == hits                                  # no request once the breaker is open


def test_a_success_resets_the_streak(server, tmp_path):
    srv, base = server
    budget = FetchBudget(3600, max_consecutive=2)
    for path in ("/always503.mp3", "/ok.mp3", "/always503.mp3", "/ok.mp3"):
        try:
            budget.download(f"{base}{path}", str(tmp_path / "ep.mp3"), **FAST)
        except DownloadError:
            pass
    assert budget.spent is None


def test_the_wall_budget_ends_downloading_and_bounds_each_deadline(server, tmp_path, monkeypatch):
    srv, base = server
    clock = _Clock()
    budget = FetchBudget(100, clock=clock)
    asked = []
    monkeypatch.setattr(dl, "download", lambda uri, path, **kw: asked.append(kw["deadline_s"]) or "ok")
    clock.now = 40.0
    assert budget.download(f"{base}/ok.mp3", str(tmp_path / "ep.mp3")) == "ok"
    assert asked == [60.0]                                   # min(DEADLINE_S, what is left of the run)
    clock.now = 100.0
    with pytest.raises(DownloadError, match="budget of 100 s is spent"):
        budget.download(f"{base}/ok.mp3", str(tmp_path / "ep.mp3"))
    assert len(asked) == 1
