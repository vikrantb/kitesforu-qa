"""Reading the producer's painted-timeline sidecar: each step fails on its own and says which one did.

``kitesforu_qa.harness.painted_timeline_sidecar`` resolves ``visual.painted_timeline_uri``, fetches
the bytes and hands them to workers' ``parse_v1``. Every test here stubs the GET or the parser, so
the file is offline and $0. Reading the golden sidecar through the real ``parse_v1`` is pinned in
``tests/test_delivered_timeline.py``.
"""
from __future__ import annotations

import json

import pytest
import requests

from kitesforu_qa.harness import painted_timeline_sidecar as sc

URI = "gs://kitesforu-dev-podcasts/visuals/job/painted_timeline.json"
PUBLIC = "https://storage.googleapis.com/kitesforu-dev-podcasts/visuals/job/painted_timeline.json"
DOC = {"visual": {"painted_timeline_uri": URI}}
BODY = json.dumps({"version": 1, "master_ms": 1000, "windows": [], "close": {}}).encode()


def _never(*_args, **_kwargs):
    raise AssertionError("must not be called")


#: Stands in for workers' ``parse_v1`` where the parser is not what is under test: like it, it takes
#: the body bytes and raises ``ValueError`` (``JSONDecodeError``) on anything that is not JSON.
_parse = json.loads


@pytest.mark.parametrize("doc", [None, {}, {"visual": None}, {"visual": {}},
                                 {"visual": {"painted_timeline_uri": "  "}},
                                 {"visual": {"painted_timeline_uri": 7}}])
def test_a_doc_that_names_no_sidecar_is_absent_and_costs_nothing(doc, monkeypatch):
    """Every master assembled before the sidecar. Absent is not a failure: nothing is rejected,
    nothing is imported and nothing is fetched."""
    monkeypatch.setattr(sc, "load_parse_v1", _never)
    read = sc.read_sidecar(doc, fetch=_never)
    assert (read.status, read.why_unread, read.bytes_read, read.parsed) == ("absent", None, 0, None)


def test_without_parse_v1_the_sidecar_is_not_fetched(monkeypatch):
    """qa has no parser of its own, so bytes it cannot parse would be egress for nothing."""
    monkeypatch.setattr(sc, "load_parse_v1",
                        lambda: (None, "ModuleNotFoundError: No module named 'workers'"))
    read = sc.read_sidecar(DOC, fetch=_never)
    assert (read.status, read.uri, read.bytes_read) == ("parser_unavailable", URI, 0)
    assert read.why_unread == "parser_unavailable: ModuleNotFoundError: No module named 'workers'"


@pytest.mark.parametrize("uri, url", [
    (URI, PUBLIC),
    (PUBLIC, PUBLIC),
    ("gs://kitesforu-dev-podcasts", None),
    ("gs://kitesforu-dev-podcasts/", None),
    ("visuals/job/painted_timeline.json", None),
    ("http://storage.googleapis.com/b/painted_timeline.json", None),
])
def test_the_uri_resolves_to_its_public_https_form_or_to_nothing(uri, url):
    assert sc.public_url(uri) == url


def test_an_unresolvable_uri_is_not_fetched():
    doc = {"visual": {"painted_timeline_uri": "visuals/job/painted_timeline.json"}}
    read = sc.read_sidecar(doc, fetch=_never, parse=_parse)
    assert read.status == "uri_unresolvable" and read.bytes_read == 0, read


def test_the_fetch_goes_to_the_public_url_and_counts_the_bytes():
    seen: list[str] = []

    def fetch(url):
        seen.append(url)
        return BODY

    read = sc.read_sidecar(DOC, fetch=fetch, parse=_parse)
    assert seen == [PUBLIC]
    assert (read.status, read.why_unread, read.bytes_read) == ("read", None, len(BODY))
    assert read.parsed["version"] == 1


def test_the_bytes_reach_parse_v1_as_they_arrived():
    """qa does not decode the sidecar: ``parse_v1`` takes the body bytes (#3257 ``bfa00e734``), so
    the producer's parser owns the JSON decoding too."""
    handed: list[object] = []

    def parse(body):
        handed.append(body)
        return _parse(body)

    sc.read_sidecar(DOC, fetch=lambda url: BODY, parse=parse)
    assert handed == [BODY] and isinstance(handed[0], bytes)


def _raises(exc):
    def f(*_args, **_kwargs):
        raise exc
    return f


@pytest.mark.parametrize("fetch, parse, status, detail", [
    (_raises(requests.ConnectionError("refused")), _parse, "fetch_failed", "ConnectionError: refused"),
    (lambda url: b"<html>not found</html>", _parse, "parse_failed", "JSONDecodeError"),
    (lambda url: BODY, _raises(ValueError("unknown version 2")), "parse_failed",
     "ValueError: unknown version 2"),
    (lambda url: BODY, lambda obj: None, "parse_failed", "parse_v1 returned None"),
])
def test_each_failed_step_says_which_step_failed(fetch, parse, status, detail):
    read = sc.read_sidecar(DOC, fetch=fetch, parse=parse)
    assert read.status == status and read.parsed is None, read
    assert detail in (read.why_unread or ""), read.why_unread
    assert read.bytes_read == (0 if status == "fetch_failed" else len(fetch(PUBLIC)))


class _Raw:
    def __init__(self, size: int):
        self.size, self.asked = size, None

    def read(self, amt, decode_content=False):
        self.asked = amt
        return b"x" * min(amt, self.size)


class _Response:
    def __init__(self, size: int, status_error: Exception | None = None):
        self.raw, self._error = _Raw(size), status_error

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def raise_for_status(self):
        if self._error:
            raise self._error


@pytest.mark.parametrize("size, ok", [(3000, True), (sc.MAX_SIDECAR_BYTES, True),
                                      (sc.MAX_SIDECAR_BYTES + 1, False), (40_000_000, False)])
def test_the_get_streams_and_stops_at_the_cap(size, ok, monkeypatch):
    """A URI that points at the 40 MB master by mistake costs one megabyte, not the video."""
    calls = []

    def get(url, timeout, stream):
        calls.append((url, timeout, stream))
        resp = _Response(size)
        calls.append(resp.raw)
        return resp

    monkeypatch.setattr(requests, "get", get)
    if ok:
        assert len(sc._https_get(PUBLIC)) == size
    else:
        with pytest.raises(ValueError, match="exceeds"):
            sc._https_get(PUBLIC)
    assert calls[0] == (PUBLIC, sc.FETCH_TIMEOUT_S, True)
    assert calls[1].asked == sc.MAX_SIDECAR_BYTES + 1


def test_an_http_error_is_a_failed_fetch(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout, stream: _Response(
        10, requests.HTTPError("404 Client Error")))
    read = sc.read_sidecar(DOC, fetch=None, parse=_parse)
    assert read.status == "fetch_failed" and "HTTPError: 404" in (read.why_unread or ""), read
