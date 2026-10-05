"""The producer's painted-timeline sidecar, read one way: its URI, its bytes, then workers' own parse.

Workers #3257 writes what the assembler painted (contract v1: the windows and the close) to a JSON
object next to the master, ``visuals/<job>/painted_timeline.json``, which is public-read like the
master. The doc records where it is as ``visual.painted_timeline_uri``. Nothing else on the doc
carries the timeline.

THE SCHEMA HAS ONE PARSER, AND IT IS THE PRODUCER'S: ``parse_v1`` in kitesforu-workers
``src/workers/stages/visuals/painted_timeline.py`` (#3257 ``bfa00e734``). It takes the sidecar's
bytes as they arrive, so qa does not even decode the JSON. It returns pydantic models that ignore
unknown fields, and raises ``ValueError`` on anything that is not v1.

It is loaded BY FILE PATH from ``WORKERS_SRC`` (default: the sibling ``kitesforu-workers/src``
checkout), with ``importlib.util.spec_from_file_location`` as qa loads its own scripts. It is never
imported as ``workers.stages.visuals.painted_timeline``: that runs ``workers/__init__.py``, which
imports ``workers.base.BaseWorker`` and ``kitesforu_schemas`` and re-classes every logger in the
process (measured on bfa00e734: ~1.0 s and 806 modules in a fresh process). The module itself needs
only ``json``, ``logging``, ``typing`` and pydantic, and has no relative import. If it cannot be
loaded, the sidecar is not fetched at all, and the reader falls back to its own estimate, labelled as
one (``DeliveredTimeline.source == "estimated"``).

THE MASTER IT DESCRIBES. The sidecar and its master sit at fixed paths, both overwritten in place,
master first, so a pass that dies between the two leaves an older sidecar beside a newer master, and
a re-assembly over the same audio keeps the same length. The producer therefore records the uploaded
master blob's ``master_generation`` and ``master_size`` (two optional v1 fields). :class:`FetchedMaster`
is the same pair for the master the reader actually fetched: the ``x-goog-generation`` header of that
GET and the size of the bytes on disk. When both pairs are complete and differ, the stamp describes
another master (``DeliveredTimeline`` rejects it as ``stale_master``).

Each step can fail on its own, and :class:`SidecarRead` says which one did:

* ``absent``: the doc names no sidecar. That is every master assembled before the sidecar existed,
  so it is not a failure, and nothing is reported as rejected.
* ``parser_unavailable``: ``parse_v1`` could not be imported.
* ``uri_unresolvable``: the URI is neither ``gs://bucket/object`` nor ``https://``. The producer
  writes the public ``https://storage.googleapis.com/...`` form (``playable_url.gs_to_public_https``).
* ``fetch_failed``: the GET failed, or the body exceeded ``MAX_SIDECAR_BYTES``. A URI that points at
  the master MP4 by mistake stops there instead of downloading the whole video.
* ``parse_failed``: the body is not JSON, or ``parse_v1`` refused it or returned nothing.
* ``read``: ``parsed`` holds what ``parse_v1`` returned.

``bytes_read`` counts the body bytes received, so a census can account for its GCS egress.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The doc field that names the sidecar, on ``visual``.
SIDECAR_URI_KEY = "painted_timeline_uri"

#: A v1 window is about 200 bytes of JSON, so this allows several thousand windows. The cap exists
#: so a URI that points at the master by mistake costs a megabyte, not the whole video.
MAX_SIDECAR_BYTES = 1 << 20
FETCH_TIMEOUT_S = 15

READ = "read"
ABSENT = "absent"
PARSER_UNAVAILABLE = "parser_unavailable"
URI_UNRESOLVABLE = "uri_unresolvable"
FETCH_FAILED = "fetch_failed"
PARSE_FAILED = "parse_failed"

_GCS_PUBLIC = "https://storage.googleapis.com/"
_QA_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class SidecarRead:
    """The outcome of reading one job's sidecar. ``parsed`` is set only when ``status == "read"``."""

    status: str
    uri: str | None = None
    parsed: Any = None
    bytes_read: int = 0
    detail: str | None = None

    @property
    def why_unread(self) -> str | None:
        """Why the producer's timeline is not available. None when it was read, and None when the
        doc names no sidecar, because a master assembled before the sidecar is not a failure."""
        if self.status in (READ, ABSENT):
            return None
        return f"{self.status}: {self.detail}" if self.detail else self.status


def sidecar_uri(doc: Mapping[str, Any] | None) -> str | None:
    visual = (doc or {}).get("visual") or {}
    uri = visual.get(SIDECAR_URI_KEY) if isinstance(visual, Mapping) else None
    return uri.strip() if isinstance(uri, str) and uri.strip() else None


def public_url(uri: str) -> str | None:
    """``gs://bucket/object`` as its public HTTPS form; an ``https://`` URL as it is; else None."""
    if uri.startswith("gs://"):
        bucket, _, obj = uri[len("gs://"):].partition("/")
        return f"{_GCS_PUBLIC}{bucket}/{obj}" if bucket and obj else None
    return uri if uri.startswith("https://") else None


def workers_src() -> str:
    return os.environ.get("WORKERS_SRC") or str(_QA_ROOT.parent / "kitesforu-workers" / "src")


def painted_timeline_path() -> Path:
    return Path(workers_src()) / "workers" / "stages" / "visuals" / "painted_timeline.py"


#: One loaded module per file, so each process runs it once. A failure is not cached: a fixed tree is
#: picked up on the next call.
_PARSE_V1: dict[str, Callable[[Any], Any]] = {}


def load_parse_v1() -> tuple[Callable[[Any], Any] | None, str | None]:
    """workers' ``parse_v1``, loaded from its file, or ``(None, why)`` when this checkout of workers
    does not have it. The module is registered in ``sys.modules`` under a private name before it runs
    (pydantic resolves the models' annotations through it), never as a ``workers`` package module."""
    path = painted_timeline_path()
    key = str(path.resolve())
    if key in _PARSE_V1:
        return _PARSE_V1[key], None
    if not path.is_file():
        return None, f"FileNotFoundError: {path}"
    name = "_kitesforu_qa_workers_painted_timeline_" + hashlib.sha1(key.encode()).hexdigest()[:12]
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        parse_v1 = module.parse_v1
    except Exception as exc:  # any failure means there is no parser here, and the caller says so
        sys.modules.pop(name, None)
        return None, f"{type(exc).__name__}: {exc}"
    _PARSE_V1[key] = parse_v1
    return parse_v1, None


@dataclass(frozen=True)
class FetchedMaster:
    """The master the reader actually fetched: the ``x-goog-generation`` header of that GET and the
    size of the bytes on disk. Either is None when it is not known."""

    generation: int | None
    size: int | None

    @property
    def complete(self) -> bool:
        return self.generation is not None and self.size is not None


def fetched_master(headers: str, local_path: str) -> FetchedMaster:
    """The fetched master from the raw response headers of its GET (``curl -D``) and the local file.
    The LAST ``x-goog-generation`` wins: with ``-L`` every redirect hop writes its own block."""
    generation = None
    for line in headers.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "x-goog-generation":
            try:
                generation = int(value.strip())
            except ValueError:
                generation = None
    try:
        size: int | None = os.path.getsize(local_path)
    except OSError:
        size = None
    return FetchedMaster(generation, size)


def _https_get(url: str) -> bytes:
    """The body, capped at ``MAX_SIDECAR_BYTES``. Uses ``requests`` (a declared dependency), because
    ``urllib`` on a stock macOS python fails every storage.googleapis.com GET with
    CERTIFICATE_VERIFY_FAILED, and ``requests`` carries its own CA bundle."""
    import requests

    with requests.get(url, timeout=FETCH_TIMEOUT_S, stream=True) as resp:
        resp.raise_for_status()
        body = resp.raw.read(MAX_SIDECAR_BYTES + 1, decode_content=True)
    if len(body) > MAX_SIDECAR_BYTES:
        raise ValueError(f"body exceeds {MAX_SIDECAR_BYTES} bytes, so it is not a painted timeline")
    return body


def read_sidecar(doc: Mapping[str, Any] | None, *,
                 fetch: Callable[[str], bytes] | None = None,
                 parse: Callable[[Any], Any] | None = None) -> SidecarRead:
    """Read the sidecar ``doc`` names. ``fetch`` and ``parse`` replace the GET and workers'
    ``parse_v1`` (tests only). ``parse`` receives the body bytes, as ``parse_v1`` does."""
    uri = sidecar_uri(doc)
    if uri is None:
        return SidecarRead(ABSENT)
    if parse is None:
        parse, why = load_parse_v1()
        if parse is None:
            return SidecarRead(PARSER_UNAVAILABLE, uri, detail=why)
    url = public_url(uri)
    if url is None:
        return SidecarRead(URI_UNRESOLVABLE, uri, detail=uri)
    try:
        body = (fetch or _https_get)(url)
    except Exception as exc:  # the read failed; the reader falls back and reports why
        return SidecarRead(FETCH_FAILED, uri, detail=f"{type(exc).__name__}: {exc}")
    try:
        parsed = parse(body)
    except Exception as exc:  # not JSON, or parse_v1 refused it
        return SidecarRead(PARSE_FAILED, uri, bytes_read=len(body),
                           detail=f"{type(exc).__name__}: {exc}")
    if parsed is None:
        return SidecarRead(PARSE_FAILED, uri, bytes_read=len(body), detail="parse_v1 returned None")
    return SidecarRead(READ, uri, parsed=parsed, bytes_read=len(body))
