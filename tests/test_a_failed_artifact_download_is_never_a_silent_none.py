"""Where the audio lives is read in ONE place, and a grade never proceeds on audio it could not fetch.

``Artifact.audio_url`` is the one reader of where a job's master audio lives. ``Artifact.load`` gets the
URL there, or from the API's ``/audio-url`` (the status snapshot carries none), and fetches it with the
shared downloader: it raises ``DownloadError`` rather than handing the checks an artifact with no audio,
which every audio check would skip as N/A. The GET is stubbed at ``requests.get``: offline and $0.
"""
from __future__ import annotations

import pytest
import requests

from kitesforu_qa.harness.artifact import Artifact
from kitesforu_qa.integrations import download as dl
from kitesforu_qa.integrations.download import Downloaded, DownloadError
from kitesforu_qa.integrations.kitesforu_api import KitesForUClient
from kitesforu_qa.utils import audio as audio_utils

HTTPS = "https://storage.googleapis.com/kitesforu-dev-podcasts/audio/job/final.mp3"
SIGNED = "https://storage.googleapis.com/kitesforu-podcasts/u/job/audio.mp3?X-Goog-Signature=abc"


# ── one reader of where the audio lives ───────────────────────────────────────────────────────

@pytest.mark.parametrize("doc, url", [
    ({"outputs": {"audio_url": HTTPS}, "audio_url": "gs://b/legacy.mp3"}, HTTPS),
    ({"audio_url": "gs://b/legacy.mp3"}, "gs://b/legacy.mp3"),
    ({"stages": {"job-audio": {"result": {"audio_url": HTTPS}}}}, HTTPS),
    ({"outputs": {"audio_url": HTTPS}, "stages": {"job-audio": {"result": {"audio_url": "gs://x"}}}},
     HTTPS),
    ({"audio_path": HTTPS, "audio_gcs_uri": "gs://b/a.mp3", "audio": {"mp3_url": HTTPS}}, None),
    ({}, None),
])
def test_the_audio_url_has_one_reader(doc, url):
    """``outputs.audio_url`` on 3,159 of the 3,162 completed jobs, a legacy top-level ``audio_url`` on
    the other 3, and the audio stage's own result as the last resort. ``audio_path``,
    ``audio_gcs_uri`` and ``audio.mp3_url``, which other readers looked for, are on none."""
    assert Artifact.from_doc(doc).audio_url == url


# ── Artifact.load: the URL, then the downloader ───────────────────────────────────────────────

class _Client:
    """``KitesForUClient``'s two calls ``load`` makes. Its ``get_job`` returns the status snapshot,
    which carries no audio URL; ``get_job_audio`` answers ``/audio-url``."""

    def __init__(self, snapshot=None, audio_url=SIGNED):
        self.snapshot = snapshot if snapshot is not None else {"job_id": "job", "status": "completed"}
        self.audio_url, self.asked = audio_url, []

    def get_job(self, job_id):
        return dict(self.snapshot)

    def get_job_audio(self, job_id):
        self.asked.append(job_id)
        return self.audio_url


def _fetched(seen):
    def fetch(uri, local_path, **_kw):
        seen.append(uri)
        with open(local_path, "wb") as fh:
            fh.write(b"ID3audio")
        return Downloaded(uri, local_path, 8, None, "audio/mpeg")
    return fetch


def test_load_asks_audio_url_when_the_snapshot_has_none(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(dl, "download", _fetched(seen))
    client = _Client()
    art = Artifact.load("job", client, temp_dir=str(tmp_path))
    assert client.asked == ["job"] and seen == [SIGNED]
    assert art.audio_path == str(tmp_path / "job.audio")


def test_load_uses_the_docs_own_url_first(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(dl, "download", _fetched(seen))
    client = _Client(snapshot={"job_id": "job", "outputs": {"audio_url": HTTPS}})
    Artifact.load("job", client, temp_dir=str(tmp_path))
    assert seen == [HTTPS] and client.asked == []


def test_load_raises_when_no_audio_url_can_be_found(tmp_path, monkeypatch):
    """A completed job with no URL in the snapshot and none from /audio-url is not "a job with no
    audio": with download=True it raises."""
    monkeypatch.setattr(dl, "download", lambda *a, **k: pytest.fail("nothing to download"))
    with pytest.raises(DownloadError, match="no audio URL"):
        Artifact.load("job", _Client(audio_url=None), temp_dir=str(tmp_path))
    art = Artifact.load("job", _Client(audio_url=None), download=False, temp_dir=str(tmp_path))
    assert art.audio_path is None


def test_load_raises_when_the_download_fails(tmp_path, monkeypatch):
    def fail(uri, local_path, **_kw):
        raise DownloadError(f"{uri}: SSLError: CERTIFICATE_VERIFY_FAILED", uri=uri)

    monkeypatch.setattr(dl, "download", fail)
    with pytest.raises(DownloadError, match="CERTIFICATE_VERIFY_FAILED"):
        Artifact.load("job", _Client(), temp_dir=str(tmp_path))


# ── the API's /audio-url ──────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")


@pytest.mark.parametrize("status, body, want", [
    (200, {"audio_url": SIGNED, "expires_at": "x", "ttl_minutes": 60, "gcs_path": "u/job/audio.mp3"},
     SIGNED),
    (404, {"detail": "Job has no signable master audio"}, None),
])
def test_get_job_audio_asks_the_audio_url_route(monkeypatch, status, body, want):
    calls = []

    def get(url, headers, timeout):
        calls.append(url)
        return _Resp(status, body)

    monkeypatch.setattr(requests, "get", get)
    client = KitesForUClient(base_url="https://api.example.invalid", api_key="k")
    assert client.get_job_audio("job") == want
    assert calls == ["https://api.example.invalid/v1/podcasts/job/audio-url"]


@pytest.mark.parametrize("answer, transient", [
    (_Resp(503, {}), True),
    (_Resp(401, {}), False),
    (requests.ConnectionError("connection refused"), True),
])
def test_get_job_audio_raises_the_error_load_documents(monkeypatch, tmp_path, answer, transient):
    """Round-2 critic #4: ``Artifact.load`` documents ``DownloadError``, but a 401, a 5xx or a network
    failure on ``/audio-url`` raised ``requests`` errors that a caller catching it would not see."""
    def get(url, headers, timeout):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests, "get", get)
    client = KitesForUClient(base_url="https://api.example.invalid", api_key="k")
    with pytest.raises(DownloadError, match="v1/podcasts/job/audio-url") as err:
        client.get_job_audio("job")
    assert err.value.transient is transient

    class Live:
        def get_job(self, job_id):
            return {"status": "completed"}               # the API snapshot: no audio URL

        get_job_audio = client.get_job_audio

    with pytest.raises(DownloadError):
        Artifact.load("job", Live(), temp_dir=str(tmp_path))


# ── the kqa pipeline's audio input ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("uri", [SIGNED, "gs://kitesforu-dev-podcasts/audio/job/final.mp3"])
def test_normalize_audio_path_fetches_https_and_gs(tmp_path, monkeypatch, uri):
    """An https URL (the API's signed /audio-url) was handed on as if it were a file."""
    seen: list[str] = []
    monkeypatch.setattr(dl, "download", _fetched(seen))
    path = audio_utils.normalize_audio_path(uri, str(tmp_path))
    assert seen == [uri] and path.startswith(str(tmp_path)) and "?" not in path


def test_normalize_audio_path_leaves_a_local_file_alone(tmp_path):
    local = tmp_path / "ep.mp3"
    local.write_bytes(b"x")
    assert audio_utils.normalize_audio_path(str(local), str(tmp_path)) == str(local)
