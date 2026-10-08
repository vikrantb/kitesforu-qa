"""Unit tests for D21 L5.4 live kqa pin (verify_live.py).

These mock Firestore + GCS — no real network calls. The contracts pinned:

  - load_job_context() reads podcast_jobs, takes the audio URLs from the
    Artifact accessors (the doc names them), and returns None gracefully
    when the doc is missing (a cron rollout shouldn't crash on a
    half-built job).
  - JobAudioContext.to_dict round-trips all fields the operator /
    Slack-post helper reads.
  - verify_job_live() composes load + download + verify and surfaces
    errors as a LiveVerifyResult.error rather than raising.
  - Genre resolution prefers blueprint.genre_module.genre > preferences
    > top-level (so the verifier asks for the bands the architect
    committed to, not the user's original request).
  - Duration resolution prefers outline.duration_min > inputs.duration_min.
  - The CLI 'verify-live' command exists + accepts the right options
    (so future cron / GitHub Actions wiring can call it safely).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from kitesforu_qa.integrations.download import Downloaded, DownloadError
from kitesforu_qa.verify_live import (
    JobAudioContext,
    LiveVerifyResult,
    load_job_context,
    verify_job_live,
)

AUDIO = "https://storage.googleapis.com/kitesforu-podcasts/public/podcasts/u/j/audio.mp3"
SPEECH = "https://storage.googleapis.com/kitesforu-podcasts/public/podcasts/u/j/speech_only.mp3"


# ---------------------------------------------------------------------------
# Firestore lookup (mocked)
# ---------------------------------------------------------------------------


class TestLoadJobContext:
    """The Firestore loader is fail-soft — every missing piece becomes
    a note rather than a crash."""

    def _make_mock_client(self, *, doc_data, exists: bool = True):
        snap = MagicMock()
        snap.exists = exists
        snap.to_dict.return_value = doc_data
        coll = MagicMock()
        coll.document.return_value.get.return_value = snap
        client = MagicMock()
        client.collection.return_value = coll
        return client

    def test_returns_none_when_doc_missing(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(
                doc_data={}, exists=False,
            )
            assert load_job_context("nope") is None

    def test_reads_the_podcast_jobs_collection(self) -> None:
        """It read ``jobs``, which holds 17 stub docs and no job (2026-10-08: ``a6fca205`` exists in
        ``podcast_jobs`` and not in ``jobs``), so every current job came back "could not load"."""
        with patch("google.cloud.firestore.Client") as mock_cls:
            client = self._make_mock_client(doc_data={"user_id": "u1"})
            mock_cls.return_value = client
            assert load_job_context("j1") is not None
            client.collection.assert_called_once_with("podcast_jobs")

    def test_a_doc_without_user_id_still_loads(self) -> None:
        """The URLs come from the doc now, so a missing ``user_id`` no longer blocks a path."""
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(
                doc_data={"genre": "horror", "outputs": {"audio_url": AUDIO}},
            )
            ctx = load_job_context("j1")
            assert ctx is not None and ctx.audio_url == AUDIO and ctx.user_id == ""

    def test_prefers_blueprint_genre_module(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u1",
                "genre": "drama",
                "preferences": {"genre": "comedy"},
                "blueprint": {"genre_module": {"genre": "horror"}},
            })
            ctx = load_job_context("j1")
            assert ctx is not None
            # blueprint.genre_module.genre wins
            assert ctx.genre == "horror"

    def test_falls_back_to_preferences_when_blueprint_silent(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u1",
                "preferences": {"genre": "comedy"},
                "genre": "drama",
            })
            ctx = load_job_context("j1")
            assert ctx is not None
            assert ctx.genre == "comedy"

    def test_falls_back_to_top_level_genre(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u1",
                "genre": "thriller",
            })
            ctx = load_job_context("j1")
            assert ctx is not None
            assert ctx.genre == "thriller"

    def test_uses_default_when_no_genre_anywhere(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u1",
            })
            ctx = load_job_context("j1")
            assert ctx is not None
            assert ctx.genre == "default"
            assert any("genre not found" in n for n in ctx.notes)

    def test_prefers_outline_duration_over_inputs(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u1",
                "outline": {"duration_min": 5.0},
                "inputs": {"duration_min": 2.0},
            })
            ctx = load_job_context("j1")
            assert ctx is not None
            assert ctx.expected_duration_s == 300.0

    def test_duration_falls_back_to_inputs(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u1",
                "inputs": {"duration_min": 3.0},
            })
            ctx = load_job_context("j1")
            assert ctx is not None
            assert ctx.expected_duration_s == 180.0

    def test_the_audio_urls_are_the_ones_the_doc_names(self) -> None:
        """Round-2 design BLOCK: the URIs were built as ``gs://kitesforu-public/v1/podcasts/<u>/<j>/``,
        which matched 0 of 40 sampled current jobs. They are ``Artifact.audio_url`` and
        ``Artifact.speech_only_url`` now."""
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u", "outputs": {"audio_url": AUDIO},
                "audio": {"speech_only_url": SPEECH},
            })
            ctx = load_job_context("j")
            assert ctx is not None and (ctx.audio_url, ctx.speech_only_url) == (AUDIO, SPEECH)
            mock_cls.return_value = self._make_mock_client(doc_data={"user_id": "u"})
            ctx = load_job_context("j")
            assert ctx is not None and (ctx.audio_url, ctx.speech_only_url) == (None, None)


# ---------------------------------------------------------------------------
# JobAudioContext / LiveVerifyResult shape
# ---------------------------------------------------------------------------


class TestDataclassShape:
    def test_job_context_to_dict_has_all_fields(self) -> None:
        ctx = JobAudioContext(
            job_id="j", user_id="u", genre="horror",
            expected_duration_s=300.0,
            audio_url=AUDIO,
            speech_only_url=SPEECH,
            notes=["note"],
        )
        d = ctx.to_dict()
        assert d["job_id"] == "j"
        assert d["user_id"] == "u"
        assert d["genre"] == "horror"
        assert d["expected_duration_s"] == 300.0
        assert (d["audio_url"], d["speech_only_url"]) == (AUDIO, SPEECH)
        assert d["notes"] == ["note"]

    def test_live_result_to_dict_handles_no_report(self) -> None:
        ctx = JobAudioContext(job_id="j", user_id="u")
        result = LiveVerifyResult(context=ctx, report=None, error="oh no")
        d = result.to_dict()
        assert d["report"] is None
        assert d["error"] == "oh no"
        assert d["verdict"] == "ERROR"
        # round-trips through json.dumps (Slack post helper consumes this)
        json.dumps(d)


# ---------------------------------------------------------------------------
# End-to-end orchestration (fully mocked)
# ---------------------------------------------------------------------------


class TestVerifyJobLiveOrchestration:
    def test_returns_error_when_context_load_fails(self) -> None:
        with patch(
            "kitesforu_qa.verify_live.load_job_context", return_value=None,
        ):
            result = verify_job_live("missing")
            assert result.report is None
            assert result.error is not None
            assert "could not load job" in result.error

    def test_returns_error_when_audio_download_fails(self) -> None:
        ctx = JobAudioContext(
            job_id="j", user_id="u", genre="horror",
            expected_duration_s=300.0,
            audio_url=AUDIO,
            speech_only_url=SPEECH,
        )
        def failing(uri: str, dest_path: str, **_kw):
            raise DownloadError(f"{uri}: Forbidden: 403 storage.objects.get denied", uri=uri)

        with patch(
            "kitesforu_qa.verify_live.load_job_context", return_value=ctx,
        ), patch(
            "kitesforu_qa.verify_live.download", side_effect=failing,
        ):
            result = verify_job_live("j")
            assert result.report is None
            assert result.error is not None
            assert "could not download" in result.error
            assert "403" in result.error          # the cause, not a list of guesses

    def test_speech_only_named_but_not_in_storage_still_grades(self, tmp_path) -> None:
        """A job that names speech-only audio that storage says does not exist still grades
        (loudness + duration axes; STOI/SMR/music_presence skipped), and the note says which."""
        ctx = JobAudioContext(
            job_id="legacy", user_id="u", genre="horror",
            expected_duration_s=10.0,
            audio_url=AUDIO,
            speech_only_url=SPEECH,
        )

        def fake_download(uri: str, dest_path: str, **_kw):
            # First call (audio) succeeds; second (speech) is reported missing.
            if uri == AUDIO:
                # Drop a 1-byte placeholder so verify_audio_quality
                # advances past the existence check. The actual
                # ListenTestReport will return FAIL on "decode failed"
                # but that's fine — we're testing the orchestration
                # didn't crash on the missing speech_only, not the
                # report's exact verdict.
                Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
                Path(dest_path).write_bytes(b"x")
                return Downloaded(uri, dest_path, 1, None, "audio/mpeg")
            raise DownloadError(f"{uri}: no such object", uri=uri, not_found=True)

        with patch(
            "kitesforu_qa.verify_live.load_job_context", return_value=ctx,
        ), patch(
            "kitesforu_qa.verify_live.download",
            side_effect=fake_download,
        ):
            result = verify_job_live("legacy")
            # No error — orchestration completed; report has FAIL
            # verdict because the placeholder isn't real audio.
            assert result.error is None
            assert result.report is not None
            assert result.report.verdict in {"PASS", "WARN", "FAIL"}
            # Speech-only-missing note bubbled up to context.notes
            assert any(
                "storage says it does not exist" in n
                for n in result.context.notes
            )

    def test_a_job_that_names_no_speech_only_audio_grades_and_says_so(self, tmp_path) -> None:
        """1,858 of the 3,163 completed jobs name no speech-only audio (2026-10-08). That is what the
        note says, not "legacy"."""
        ctx = JobAudioContext(job_id="j", user_id="u", genre="horror", expected_duration_s=10.0,
                              audio_url=AUDIO, speech_only_url=None)
        seen = []

        def fake_download(uri: str, dest_path: str, **_kw):
            seen.append(uri)
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(dest_path).write_bytes(b"x")
            return Downloaded(uri, dest_path, 1, None, "audio/mpeg")

        with patch("kitesforu_qa.verify_live.load_job_context", return_value=ctx), patch(
                "kitesforu_qa.verify_live.download", side_effect=fake_download):
            result = verify_job_live("j")
        assert result.error is None and seen == [AUDIO]
        assert any("names no speech-only audio" in n for n in result.context.notes)

    def test_an_unreadable_speech_only_file_is_not_a_legacy_job(self, tmp_path) -> None:
        """A 403, an expired credential or a dropped connection on speech_only.mp3 used to be read as
        a legacy job, and four axes were dropped under that label. Only a source that says the
        object does not exist (not_found) is an absence."""
        ctx = JobAudioContext(
            job_id="j", user_id="u", genre="horror",
            expected_duration_s=10.0,
            audio_url=AUDIO,
            speech_only_url=SPEECH,
        )

        def fake_download(uri: str, dest_path: str, **_kw):
            if uri == AUDIO:
                Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
                Path(dest_path).write_bytes(b"x")
                return Downloaded(uri, dest_path, 1, None, "audio/mpeg")
            raise DownloadError(f"{uri}: Forbidden: 403", uri=uri)

        with patch(
            "kitesforu_qa.verify_live.load_job_context", return_value=ctx,
        ), patch(
            "kitesforu_qa.verify_live.download", side_effect=fake_download,
        ):
            result = verify_job_live("j")
            assert result.report is None
            assert "speech_only.mp3" in result.error and "403" in result.error
            assert "not reported missing" in result.error
            assert not any("does not exist" in n for n in result.context.notes)


# ---------------------------------------------------------------------------
# CLI surface — the cron / Cloud Scheduler entry point
# ---------------------------------------------------------------------------


class TestCliSurface:
    def test_verify_live_command_registered(self) -> None:
        # Pin: the CLI must expose 'verify-live' so Cloud Scheduler
        # / GitHub Actions can invoke it without import-side knowledge.
        from kitesforu_qa.cli import cli
        cmd_names = {c.name for c in cli.commands.values()}
        assert "verify-live" in cmd_names, (
            "kqa CLI must register the 'verify-live' command"
        )

    def test_verify_live_accepts_required_options(self) -> None:
        from kitesforu_qa.cli import cli
        cmd = cli.commands["verify-live"]
        param_names = {p.name for p in cmd.params}
        assert "job_id" in param_names
        assert "genre_override" in param_names
        assert "output" in param_names
        assert "exit_on_fail" in param_names

    def test_pipeline_help_does_not_crash(self) -> None:
        from click.testing import CliRunner
        from kitesforu_qa.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["verify-live", "--help"])
        assert result.exit_code == 0
        assert "verify-live" in result.output or "job-id" in result.output
