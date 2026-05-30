"""Unit tests for D21 L5.4 live kqa pin (verify_live.py).

These mock Firestore + GCS — no real network calls. The contracts pinned:

  - load_job_context() returns None gracefully when the doc / user_id
    is missing (a cron rollout shouldn't crash on a half-built job).
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

from kitesforu_qa.verify_live import (
    JobAudioContext,
    LiveVerifyResult,
    _resolve_job_gcs_prefix,
    load_job_context,
    verify_job_live,
)


# ---------------------------------------------------------------------------
# GCS path resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    def test_default_bucket_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("KFU_PUBLIC_BUCKET", "test-bucket")
        prefix = _resolve_job_gcs_prefix(user_id="u123", job_id="j456")
        assert prefix == "gs://test-bucket/v1/podcasts/u123/j456/"

    def test_custom_bucket_arg_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("KFU_PUBLIC_BUCKET", "env-bucket")
        prefix = _resolve_job_gcs_prefix(
            user_id="u", job_id="j", bucket="arg-bucket",
        )
        assert prefix == "gs://arg-bucket/v1/podcasts/u/j/"

    def test_public_prefix_trims_trailing_slash(self) -> None:
        prefix = _resolve_job_gcs_prefix(
            user_id="u", job_id="j", bucket="b", public_prefix="prod/",
        )
        assert "prod/podcasts" in prefix


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

    def test_returns_none_when_user_id_missing(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(
                doc_data={"genre": "horror"},
            )
            assert load_job_context("j1") is None

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

    def test_audio_uri_built_from_user_and_job(self) -> None:
        with patch("google.cloud.firestore.Client") as mock_cls:
            mock_cls.return_value = self._make_mock_client(doc_data={
                "user_id": "u-abc",
            })
            ctx = load_job_context("job-xyz")
            assert ctx is not None
            assert "podcasts/u-abc/job-xyz/audio.mp3" in (
                ctx.audio_gcs_uri or ""
            )
            assert "podcasts/u-abc/job-xyz/speech_only.mp3" in (
                ctx.speech_only_gcs_uri or ""
            )


# ---------------------------------------------------------------------------
# JobAudioContext / LiveVerifyResult shape
# ---------------------------------------------------------------------------


class TestDataclassShape:
    def test_job_context_to_dict_has_all_fields(self) -> None:
        ctx = JobAudioContext(
            job_id="j", user_id="u", genre="horror",
            expected_duration_s=300.0,
            audio_gcs_uri="gs://b/audio.mp3",
            speech_only_gcs_uri="gs://b/speech.mp3",
            notes=["note"],
        )
        d = ctx.to_dict()
        assert d["job_id"] == "j"
        assert d["user_id"] == "u"
        assert d["genre"] == "horror"
        assert d["expected_duration_s"] == 300.0
        assert d["audio_gcs_uri"] == "gs://b/audio.mp3"
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
            audio_gcs_uri="gs://b/audio.mp3",
            speech_only_gcs_uri="gs://b/speech.mp3",
        )
        with patch(
            "kitesforu_qa.verify_live.load_job_context", return_value=ctx,
        ), patch(
            "kitesforu_qa.verify_live._gcs_download", return_value=False,
        ):
            result = verify_job_live("j")
            assert result.report is None
            assert result.error is not None
            assert "could not download" in result.error

    def test_speech_only_optional_legacy_job(self, tmp_path) -> None:
        """A pre-PR-737 job without speech_only.mp3 should still grade
        (loudness + duration axes; STOI/SMR/music_presence skipped)."""
        ctx = JobAudioContext(
            job_id="legacy", user_id="u", genre="horror",
            expected_duration_s=10.0,
            audio_gcs_uri="gs://b/audio.mp3",
            speech_only_gcs_uri="gs://b/speech.mp3",
        )

        def fake_download(uri: str, dest_path: str) -> bool:
            # First call (audio) succeeds; second (speech) misses.
            if "audio" in uri:
                # Drop a 1-byte placeholder so verify_audio_quality
                # advances past the existence check. The actual
                # ListenTestReport will return FAIL on "decode failed"
                # but that's fine — we're testing the orchestration
                # didn't crash on the missing speech_only, not the
                # report's exact verdict.
                Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
                Path(dest_path).write_bytes(b"x")
                return True
            return False

        with patch(
            "kitesforu_qa.verify_live.load_job_context", return_value=ctx,
        ), patch(
            "kitesforu_qa.verify_live._gcs_download",
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
                "speech_only.mp3 not in GCS" in n
                for n in result.context.notes
            )


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
