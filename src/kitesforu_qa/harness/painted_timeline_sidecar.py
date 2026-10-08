"""The producer's painted-timeline sidecar, read one way: its URI, its bytes, then workers' own parse.

Workers #3257 writes what the assembler painted (contract v1: the windows and the close) to a JSON
object next to the master, ``visuals/<job>/painted_timeline.json``, which is public-read like the
master. The doc records where it is as ``visual.painted_timeline_uri``. Nothing else on the doc
carries the timeline.

THE SCHEMA HAS ONE PARSER, AND IT IS THE PRODUCER'S: ``parse_v1`` in kitesforu-workers
``src/workers/stages/visuals/painted_timeline.py`` (#3257, merged as ``2d707598c``). It takes the
sidecar's bytes as they arrive, so qa does not even decode the JSON. It returns pydantic models that
ignore unknown fields, and raises ``ValueError`` on anything that is not v1.

WHICH COPY OF IT RUNS. By default, the PINNED GIT OBJECT ``origin/main:<that path>`` of the workers
repository (``git show``), never a working tree: the shared workers checkout sits on whatever branch
a peer last checked out, and a qa worktree has no sibling checkout at all, so a working-tree default
read nothing wherever the gate actually runs. The repository is ``WORKERS_REPO``, else the sibling of
qa's canonical checkout (found through git's common directory, so a worktree resolves it too); the
ref is ``WORKERS_REF``. ``WORKERS_SRC`` (a workers ``src`` tree) overrides both. When none of them
has the file, the reader says so (``parser_unavailable``, and ``delivered_timeline.stamp_note`` puts
it in every reader's headline output) and falls back to its estimate, labelled as one
(``DeliveredTimeline.source == "estimated"``).

The source is executed as a private module. It is never imported as
``workers.stages.visuals.painted_timeline``: that runs ``workers/__init__.py``, which imports
``workers.base.BaseWorker`` and ``kitesforu_schemas`` and re-classes every logger in the process
(measured on bfa00e734: ~1.0 s and 806 modules in a fresh process). The module itself needs only
``json``, ``logging``, ``typing`` and pydantic, and has no relative import.

THE MASTER IT DESCRIBES. The sidecar and its master sit at fixed paths, both overwritten in place,
master first, so a pass that dies between the two leaves an older sidecar beside a newer master, and
a re-assembly over the same audio keeps the same length. The producer therefore records the uploaded
master blob's ``master_generation`` and ``master_size`` (two optional v1 fields). :class:`FetchedMaster`
is the same pair for the master the reader actually fetched, as ``integrations.download`` reports it:
the GCS generation of that fetch and the size of the bytes on disk. Every field that both sides carry
is compared, and any mismatch means the stamp describes another master (``DeliveredTimeline``
rejects it as ``stale_master``).

Each step can fail on its own, and :class:`SidecarRead` says which one did:

* ``absent``: the doc names no sidecar. That is every master assembled before the sidecar existed,
  so it is not a failure, and nothing is reported as rejected.
* ``parser_unavailable``: ``parse_v1`` could not be loaded; ``detail`` says from where it was asked.
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
import os
import subprocess
import sys
import types
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


#: Where ``painted_timeline.py`` lives inside a workers checkout.
PAINTED_TIMELINE_IN_REPO = "src/workers/stages/visuals/painted_timeline.py"
#: The workers ref read by default. A git OBJECT, never a working tree: which branch a peer has
#: checked out in the shared workers checkout must not decide which parser runs.
WORKERS_REF_DEFAULT = "origin/main"


def workers_src_override() -> str | None:
    """``WORKERS_SRC``: an explicit workers ``src`` tree, which wins over the git object."""
    return os.environ.get("WORKERS_SRC") or None


def workers_repo() -> Path:
    """The workers git repository: ``WORKERS_REPO``, else the sibling of qa's CANONICAL checkout. A
    qa worktree has no sibling of its own, so the canonical checkout is found through git's common
    directory."""
    if os.environ.get("WORKERS_REPO"):
        return Path(os.environ["WORKERS_REPO"])
    try:
        out = subprocess.run(["git", "-C", str(_QA_ROOT), "rev-parse", "--path-format=absolute",
                              "--git-common-dir"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).parent.parent / "kitesforu-workers"
    except (OSError, subprocess.SubprocessError):
        pass
    return _QA_ROOT.parent / "kitesforu-workers"


def workers_ref() -> str:
    return os.environ.get("WORKERS_REF") or WORKERS_REF_DEFAULT


def workers_tree() -> str:
    """A workers ``src`` tree on disk, for code that must IMPORT workers modules (the renderer oracle
    in the tests): the override, else the workers repo's working tree."""
    return workers_src_override() or str(workers_repo() / "src")


def read_workers_file(relpath: str) -> tuple[bytes, str]:
    """``relpath`` (from the workers repo root) as bytes, and where it came from. From the
    ``WORKERS_SRC`` override's checkout when one is set; else from the pinned git object
    ``<workers_ref()>:<relpath>``. Raises ``FileNotFoundError`` when neither has it."""
    override = workers_src_override()
    if override:
        path = Path(override).parent / relpath
        if not path.is_file():
            raise FileNotFoundError(f"WORKERS_SRC={override} has no {relpath}")
        return path.read_bytes(), f"WORKERS_SRC={override}"
    repo, ref = workers_repo(), workers_ref()
    try:
        shown = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{relpath}"],
                               capture_output=True, timeout=30)
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", ref],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FileNotFoundError(f"cannot read {ref}:{relpath} in {repo}: {exc}") from exc
    if shown.returncode != 0:
        why = shown.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["git show failed"]
        raise FileNotFoundError(f"{repo} has no {ref}:{relpath} ({why[0]}); set WORKERS_SRC to a "
                                f"workers src tree that has it, or WORKERS_REPO/WORKERS_REF")
    return shown.stdout, f"{repo.name} {ref} ({commit.stdout.strip() or '?'})"


#: One loaded parser per SOURCE ASKED FOR (the override, else the repo and ref), so a process reads
#: and runs workers' file once and a cached call forks nothing: the key is built from the environment
#: alone. A failure is not cached: a fixed tree or a fetched ref is picked up on the next call.
_PARSE_V1: dict[tuple[str, str, str], Callable[[Any], Any]] = {}


def _source_asked_for() -> tuple[str, str, str]:
    return (workers_src_override() or "", os.environ.get("WORKERS_REPO") or "", workers_ref())


def load_parse_v1() -> tuple[Callable[[Any], Any] | None, str | None]:
    """workers' ``parse_v1``, or ``(None, why)`` when it cannot be loaded.

    The source is read with :func:`read_workers_file`: the ``WORKERS_SRC`` override, else the pinned
    git object ``origin/main:src/workers/stages/visuals/painted_timeline.py`` of the workers repo. It
    is executed as a private module, registered in ``sys.modules`` before it runs (pydantic resolves
    the models' annotations through it), never as a ``workers`` package module."""
    asked = _source_asked_for()
    if asked in _PARSE_V1:
        return _PARSE_V1[asked], None
    try:
        source, origin = read_workers_file(PAINTED_TIMELINE_IN_REPO)
    except FileNotFoundError as exc:
        return None, f"FileNotFoundError: {exc}"
    # One module name per source asked for: two sources holding the same text never share a module.
    name = ("_kitesforu_qa_workers_painted_timeline_"
            + hashlib.sha1(repr(asked).encode() + source).hexdigest()[:12])
    try:
        module = types.ModuleType(name)
        module.__file__ = f"<{origin}>/{PAINTED_TIMELINE_IN_REPO}"
        sys.modules[name] = module
        exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 — workers' own source
        parse_v1 = module.parse_v1
    except Exception as exc:  # any failure means there is no parser here, and the caller says so
        sys.modules.pop(name, None)
        return None, f"{type(exc).__name__}: {exc}"
    _PARSE_V1[asked] = parse_v1
    return parse_v1, None


@dataclass(frozen=True)
class FetchedMaster:
    """The master the reader actually fetched: its GCS generation (the ``x-goog-generation`` of that
    GET, or the blob's) and the size of the bytes on disk, as ``integrations.download.Downloaded``
    reports them. Either is None when it is not known."""

    generation: int | None
    size: int | None

    @property
    def complete(self) -> bool:
        return self.generation is not None and self.size is not None


def _https_get(url: str) -> bytes:
    """The body, capped at ``MAX_SIDECAR_BYTES``. Uses ``requests`` (a declared dependency), which
    carries its own CA bundle: ``urllib`` on a python.org framework python whose ``Install
    Certificates.command`` was never run (here ``/usr/local/bin/python3`` 3.12.3) fails every
    storage.googleapis.com GET with CERTIFICATE_VERIFY_FAILED."""
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
