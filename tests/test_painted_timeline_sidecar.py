"""Reading the producer's painted-timeline sidecar: each step fails on its own and says which one did.

``kitesforu_qa.harness.painted_timeline_sidecar`` resolves ``visual.painted_timeline_uri``, fetches
the bytes and hands them to workers' ``parse_v1``. Every test here stubs the GET or the parser, so
the file is offline and $0. Reading the golden sidecar through the real ``parse_v1`` is pinned in
``tests/test_delivered_timeline.py``.
"""
from __future__ import annotations

import json
import subprocess
import sys

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


# ── workers' parse_v1, loaded from its FILE, never through the workers package ─────────────────

_RAISING_INIT = 'raise RuntimeError("a workers package __init__ ran")\n'


def _tree(root, module_source, *, raising_inits=True):
    """A workers ``src`` tree holding only ``painted_timeline.py``. Every package ``__init__`` raises,
    so loading the module through the package would fail."""
    visuals = root / "workers" / "stages" / "visuals"
    visuals.mkdir(parents=True)
    if raising_inits:
        for pkg in (root / "workers", root / "workers" / "stages", visuals):
            (pkg / "__init__.py").write_text(_RAISING_INIT)
    (visuals / "painted_timeline.py").write_text(module_source)
    return root


def test_parse_v1_is_loaded_from_its_file_and_no_package_init_runs(tmp_path, monkeypatch):
    """The real ``workers/__init__.py`` imports ``BaseWorker`` and ``kitesforu_schemas`` and re-classes
    every logger in the process (~1.0 s, 806 modules, measured on #3257 ``bfa00e734``). The parser
    needs none of it."""
    src = _tree(tmp_path / "src", "def parse_v1(data):\n    return ('parsed', data)\n")
    monkeypatch.setenv("WORKERS_SRC", str(src))
    parse_v1, why = sc.load_parse_v1()
    assert why is None and parse_v1 is not None, why
    assert parse_v1(b"{}") == ("parsed", b"{}")
    assert parse_v1.__module__.startswith("_kitesforu_qa_workers_painted_timeline_")


def test_the_module_runs_once_per_process(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    src = _tree(tmp_path / "src", f"open({str(runs)!r}, 'a').write('x')\n"
                                  "def parse_v1(data):\n    return data\n")
    monkeypatch.setenv("WORKERS_SRC", str(src))
    first, _ = sc.load_parse_v1()
    second, _ = sc.load_parse_v1()
    assert first is second and runs.read_text() == "x"


def test_a_tree_without_the_module_has_no_parser(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKERS_SRC", str(tmp_path))
    parse_v1, why = sc.load_parse_v1()
    assert parse_v1 is None and why.startswith("FileNotFoundError"), why


def test_a_module_that_fails_to_load_is_not_cached(tmp_path, monkeypatch):
    src = _tree(tmp_path / "src", "raise ImportError('half-written checkout')\n")
    monkeypatch.setenv("WORKERS_SRC", str(src))
    parse_v1, why = sc.load_parse_v1()
    assert parse_v1 is None and why == "ImportError: half-written checkout", why
    (src / "workers" / "stages" / "visuals" / "painted_timeline.py").write_text(
        "def parse_v1(data):\n    return data\n")
    assert sc.load_parse_v1()[0] is not None


def test_a_failed_load_leaves_no_module_registered(tmp_path, monkeypatch):
    """The module is registered in ``sys.modules`` before it runs (pydantic needs it); a source that
    raises half-way must not stay registered as if it had loaded."""
    src = _tree(tmp_path / "src", "x = 1\nraise ValueError('half-written')\n")
    monkeypatch.setenv("WORKERS_SRC", str(src))
    before = {n for n in sys.modules if n.startswith("_kitesforu_qa_workers_painted_timeline_")}
    assert sc.load_parse_v1() == (None, "ValueError: half-written")
    after = {n for n in sys.modules if n.startswith("_kitesforu_qa_workers_painted_timeline_")}
    assert after == before


# ── by default: the PINNED GIT OBJECT, never a working tree ────────────────────────────────────

_GIT = ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
        "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _git(repo, *args):
    subprocess.run([*_GIT, "-C", str(repo), *args], check=True, capture_output=True)


def _parser_returning(label):
    return f"def parse_v1(data):\n    return {label!r}\n"


def _workers_repo(root, *, main, working_tree=None):
    """A workers repo whose ``origin/main`` holds a ``painted_timeline.py`` returning ``main``, with a
    checked-out local branch and, when given, an uncommitted working-tree copy that differ."""
    root.mkdir()
    _git(root, "init", "-q")
    path = root / sc.PAINTED_TIMELINE_IN_REPO
    path.parent.mkdir(parents=True)
    path.write_text(_parser_returning(main))
    _git(root, "add", sc.PAINTED_TIMELINE_IN_REPO)
    _git(root, "commit", "-q", "-m", "main")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(root, "checkout", "-q", "-b", "a-peers-branch")
    path.write_text(_parser_returning("a peer's branch"))
    _git(root, "commit", "-q", "-am", "branch")
    if working_tree is not None:
        path.write_text(_parser_returning(working_tree))
    return root


@pytest.fixture()
def no_override(monkeypatch):
    for name in ("WORKERS_SRC", "WORKERS_REPO", "WORKERS_REF"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_by_default_parse_v1_is_origin_main_not_the_checked_out_tree(tmp_path, no_override):
    """Round-2 design BLOCK: the default used to be the sibling WORKING TREE, which sits on whatever
    branch a peer checked out (and a qa worktree has no sibling at all), so the default read no
    parser wherever the gate runs. The default is now ``origin/main``'s object: neither the branch
    checked out nor an uncommitted edit can change which parser runs."""
    repo = _workers_repo(tmp_path / "kitesforu-workers", main="origin/main",
                         working_tree="an uncommitted edit")
    no_override.setenv("WORKERS_REPO", str(repo))
    parse_v1, why = sc.load_parse_v1()
    assert why is None and parse_v1(b"{}") == "origin/main", why
    assert sc.workers_tree() == str(repo / "src")       # the oracle's importable tree


def test_workers_ref_names_the_object_to_read(tmp_path, no_override):
    repo = _workers_repo(tmp_path / "kitesforu-workers", main="origin/main")
    no_override.setenv("WORKERS_REPO", str(repo))
    no_override.setenv("WORKERS_REF", "a-peers-branch")
    assert sc.load_parse_v1()[0](b"{}") == "a peer's branch"


def test_the_override_wins_over_the_git_object(tmp_path, no_override):
    repo = _workers_repo(tmp_path / "kitesforu-workers", main="origin/main")
    src = _tree(tmp_path / "src", _parser_returning("the override"), raising_inits=False)
    no_override.setenv("WORKERS_REPO", str(repo))
    no_override.setenv("WORKERS_SRC", str(src))
    assert sc.load_parse_v1()[0](b"{}") == "the override"
    assert sc.workers_tree() == str(src)


def test_without_the_object_the_parser_is_unavailable_and_says_where_it_looked(tmp_path, no_override):
    no_override.setenv("WORKERS_REPO", str(tmp_path / "not-a-repo"))
    parse_v1, why = sc.load_parse_v1()
    assert parse_v1 is None and why.startswith("FileNotFoundError: "), why
    assert "origin/main:src/workers/stages/visuals/painted_timeline.py" in why and "WORKERS_SRC" in why


def test_a_loaded_parser_is_reused_without_another_fork(tmp_path, no_override):
    """A census builds one timeline per job; only the first load may run git."""
    repo = _workers_repo(tmp_path / "kitesforu-workers", main="origin/main")
    no_override.setenv("WORKERS_REPO", str(repo))
    first, _ = sc.load_parse_v1()
    no_override.setattr(sc.subprocess, "run", _never)
    assert sc.load_parse_v1() == (first, None)


def test_the_default_repo_is_the_canonical_checkouts_sibling_even_from_a_worktree(tmp_path, no_override):
    """qa runs from linked worktrees, which have no sibling checkout of their own: the workers repo is
    the sibling of the CANONICAL checkout, found through git's common directory."""
    qa = tmp_path / "kitesforu-qa"
    qa.mkdir()
    _git(qa, "init", "-q")
    (qa / "README").write_text("qa")
    _git(qa, "add", "README")
    _git(qa, "commit", "-q", "-m", "qa")
    worktree = tmp_path / "elsewhere" / "wt-qa"
    _git(qa, "worktree", "add", "-q", str(worktree))
    no_override.setattr(sc, "_QA_ROOT", worktree)
    assert sc.workers_repo().resolve() == (tmp_path / "kitesforu-workers").resolve()
    no_override.setattr(sc, "_QA_ROOT", tmp_path / "not-a-checkout" / "qa")
    assert sc.workers_repo() == tmp_path / "not-a-checkout" / "kitesforu-workers"


# ── the master the reader fetched ──────────────────────────────────────────────────────────────

def test_the_fetched_master_is_the_gets_generation_and_the_bytes_on_disk(tmp_path):
    body = tmp_path / "v.mp4"
    body.write_bytes(b"\x00" * 2048)
    headers = ("HTTP/1.1 302 Found\r\nLocation: https://storage.googleapis.com/b/v.mp4\r\n"
               "x-goog-generation: 111\r\n\r\n"
               "HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\nX-Goog-Generation: 1759660800123456\r\n\r\n")
    got = sc.fetched_master(headers, str(body))
    assert got == sc.FetchedMaster(1759660800123456, 2048) and got.complete


@pytest.mark.parametrize("headers, generation", [
    ("HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\n\r\n", None),
    ("HTTP/1.1 200 OK\r\nx-goog-generation: not-a-number\r\n\r\n", None),
    ("", None),
])
def test_a_get_without_a_usable_generation_is_incomplete(headers, generation, tmp_path):
    body = tmp_path / "v.mp4"
    body.write_bytes(b"\x00" * 10)
    got = sc.fetched_master(headers, str(body))
    assert got == sc.FetchedMaster(generation, 10) and not got.complete


def test_only_the_final_responses_generation_counts(tmp_path):
    """With ``-L`` every redirect hop writes its own header block. A generation on an earlier hop
    names some other object, so a final block without one is no generation (round-2 critic #4)."""
    body = tmp_path / "v.mp4"
    body.write_bytes(b"\x00" * 10)
    headers = ("HTTP/1.1 302 Found\r\nx-goog-generation: 111\r\nLocation: https://x/v.mp4\r\n\r\n"
               "HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\n\r\n")
    assert sc.fetched_master(headers, str(body)) == sc.FetchedMaster(None, 10)


def test_an_unreadable_generation_clears_one_read_before_it(tmp_path):
    """Within the final block the LAST ``x-goog-generation`` decides, and one that is not a number
    is no generation, never the value read before it."""
    body = tmp_path / "v.mp4"
    body.write_bytes(b"\x00" * 10)
    headers = "HTTP/1.1 200 OK\r\nx-goog-generation: 5\r\nx-goog-generation: junk\r\n\r\n"
    assert sc.fetched_master(headers, str(body)) == sc.FetchedMaster(None, 10)


def test_a_missing_file_has_no_size(tmp_path):
    got = sc.fetched_master("x-goog-generation: 5\r\n", str(tmp_path / "absent.mp4"))
    assert got == sc.FetchedMaster(5, None) and not got.complete
