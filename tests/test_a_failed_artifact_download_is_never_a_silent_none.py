"""A job asset that cannot be downloaded RAISES. It is never a silent ``None``.

``Artifact.load`` downloads the episode audio, and every audio check reads ``art.audio_path``. With no
file, each check calls ``skip()`` ("no audio file on artifact"), exactly as for a job that has no audio,
and a scorecard of skips passes. ``_download`` fetched HTTPS through ``urllib``, which fails every
storage.googleapis.com GET with CERTIFICATE_VERIFY_FAILED on a stock macOS python, and swallowed every
exception into ``None``. So on such a machine every graded episode was graded on nothing.

The GET is stubbed at ``requests.get``: offline and $0.
"""
from __future__ import annotations

import pytest
import requests

from kitesforu_qa.harness import artifact as artifact_mod
from kitesforu_qa.harness.artifact import Artifact, ArtifactDownloadError

URL = "https://storage.googleapis.com/kitesforu-dev-podcasts/audio/job/final.mp3"


class _Response:
    def __init__(self, chunks=(b"ID3", b"\x00" * 64), *, status_error=None, fail_after=None):
        self._chunks, self._error, self._fail_after = list(chunks), status_error, fail_after

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def raise_for_status(self):
        if self._error:
            raise self._error

    def iter_content(self, chunk_size):
        for i, chunk in enumerate(self._chunks):
            if self._fail_after is not None and i == self._fail_after:
                raise requests.exceptions.ChunkedEncodingError("connection broken mid-body")
            yield chunk


def _get_returning(resp, seen=None):
    def get(url, timeout, stream):
        if seen is not None:
            seen.append((url, timeout, stream))
        return resp
    return get


def _get_raising(exc):
    def get(url, timeout, stream):
        raise exc
    return get


def test_a_good_download_lands_whole_at_the_path(tmp_path, monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(requests, "get", _get_returning(_Response(), seen))
    dest = tmp_path / "job.audio"
    assert artifact_mod._download(URL, str(dest)) == str(dest)
    assert dest.read_bytes() == b"ID3" + b"\x00" * 64
    assert seen == [(URL, artifact_mod._DOWNLOAD_TIMEOUT_S, True)]
    assert not (tmp_path / "job.audio.part").exists()


@pytest.mark.parametrize("get, cause", [
    (_get_raising(requests.exceptions.SSLError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate")),
     "CERTIFICATE_VERIFY_FAILED"),
    (_get_raising(requests.ConnectionError("Name or service not known")), "ConnectionError"),
    (_get_returning(_Response(status_error=requests.HTTPError("403 Client Error: Forbidden"))), "403"),
    (_get_returning(_Response(chunks=())), "empty"),
    (_get_returning(_Response(fail_after=1)), "ChunkedEncodingError"),
])
def test_a_failed_download_raises_and_leaves_no_file(get, cause, tmp_path, monkeypatch):
    """Each way an HTTPS download fails raises, names its cause, and leaves nothing at the path,
    not even part of the body: a truncated file would be graded as a truncated episode."""
    monkeypatch.setattr(requests, "get", get)
    dest = tmp_path / "job.audio"
    with pytest.raises(ArtifactDownloadError, match=cause):
        artifact_mod._download(URL, str(dest))
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_a_failed_gcs_download_raises_too(tmp_path, monkeypatch):
    from kitesforu_qa.integrations import gcs

    def fail(gcs_uri, local_dir=None):
        raise RuntimeError("Failed to download from GCS: 403 Forbidden")

    monkeypatch.setattr(gcs, "download_from_gcs", fail)
    with pytest.raises(ArtifactDownloadError, match="403 Forbidden"):
        artifact_mod._download("gs://kitesforu-dev-podcasts/audio/job/final.mp3",
                               str(tmp_path / "job.audio"))


class _Client:
    """The kqa client's two calls ``Artifact.load`` may make, for a job whose audio is at ``URL``."""

    def __init__(self, doc):
        self.doc = doc

    def get_job(self, job_id):
        return dict(self.doc)

    def get_job_audio(self, job_id):
        raise requests.ConnectionError("the API is unreachable")


def test_load_never_returns_an_artifact_whose_audio_silently_failed(tmp_path, monkeypatch):
    """The grade cannot go ahead on nothing: ``load`` raises, and only ``download=False`` gives an
    artifact without its audio, because the caller asked for one."""
    monkeypatch.setattr(requests, "get", _get_raising(requests.exceptions.SSLError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")))
    client = _Client({"job_id": "job", "audio_url": URL})
    with pytest.raises(ArtifactDownloadError, match="CERTIFICATE_VERIFY_FAILED"):
        Artifact.load("job", client, temp_dir=str(tmp_path))
    assert Artifact.load("job", client, download=False, temp_dir=str(tmp_path)).audio_path is None


@pytest.mark.parametrize("doc", [
    {"job_id": "job", "audio_url": URL},
    {"job_id": "job", "audio_path": URL},
    {"job_id": "job", "outputs": {"audio_url": URL}},
])
def test_load_takes_the_audio_url_from_the_doc_it_already_has(doc, tmp_path, monkeypatch):
    """``client.get_job_audio`` re-fetches the same doc for the same fields. Its failure was
    swallowed into "no audio", so a doc whose URL sat at the top level graded on nothing."""
    monkeypatch.setattr(requests, "get", _get_returning(_Response()))
    art = Artifact.load("job", _Client(doc), temp_dir=str(tmp_path))
    assert art.audio_path == str(tmp_path / "job.audio")
    assert (tmp_path / "job.audio").read_bytes().startswith(b"ID3")


def test_a_job_with_no_audio_url_still_loads_without_audio(tmp_path, monkeypatch):
    """No URL means no audio to fetch, which is not a failure."""
    monkeypatch.setattr(requests, "get", _get_raising(AssertionError("nothing to download")))
    assert Artifact.load("job", _Client({"job_id": "job"}), temp_dir=str(tmp_path)).audio_path is None
