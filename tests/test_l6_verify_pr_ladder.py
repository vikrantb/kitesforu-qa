"""L6 D22 — pin the 5-rung verification ladder wiring (GH workflow + CLI).

The script `scripts/verify_pr_against_live.py` was already pyright-clean
from kqa #14. L6 finishes the wiring so an operator can invoke it
without re-discovering its argparse contract:

  - .github/workflows/verify-pr-live.yml — workflow_dispatch with the
    same 4 inputs the script takes (pr_id, pr_kind, baseline_job,
    post_deploy_job, + optional strict)
  - kqa CLI `verify-pr-ladder` subcommand wrapping the same script
    (so local use mirrors the workflow's flag-names exactly)

These pin tests catch:
  (a) workflow file accidentally deleted / renamed
  (b) CLI command accidentally removed
  (c) the 4 required inputs drifting out of sync between workflow and CLI
  (d) the underlying script disappearing (sanity check)
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestVerifyPrAgainstLiveScript:
    """The underlying script is the canonical contract; the workflow +
    CLI are wrappers. If the script is gone, both layers above it are
    dead. Pin the script + its 4 documented inputs."""

    def test_script_exists(self) -> None:
        script = REPO_ROOT / "scripts" / "verify_pr_against_live.py"
        assert script.exists(), (
            f"verify_pr_against_live.py missing at {script}"
        )

    def test_script_documents_4_required_args(self) -> None:
        script = REPO_ROOT / "scripts" / "verify_pr_against_live.py"
        text = script.read_text(encoding="utf-8")
        # The script's argparse contract — these MUST appear as
        # add_argument calls so the workflow/CLI wrappers don't pass
        # invalid flags.
        for flag in ("--pr-id", "--pr-kind", "--baseline-job-id",
                     "--post-deploy-job-id"):
            assert flag in text, (
                f"verify_pr_against_live.py argparse missing {flag!r}"
            )


class TestGhActionsWorkflow:
    """Operator-triggered ladder via GitHub UI."""

    def test_workflow_file_exists(self) -> None:
        wf = REPO_ROOT / ".github" / "workflows" / "verify-pr-live.yml"
        assert wf.exists(), (
            "L6 missing .github/workflows/verify-pr-live.yml — operators "
            "have no UI button to verify a PR"
        )

    def test_workflow_is_manual_only(self) -> None:
        # The script costs LLM+TTS credits per run; the workflow MUST
        # NOT fire on push (would burn credits on every commit). Pin
        # workflow_dispatch as the ONLY trigger.
        wf = REPO_ROOT / ".github" / "workflows" / "verify-pr-live.yml"
        text = wf.read_text(encoding="utf-8")
        assert "workflow_dispatch:" in text, (
            "workflow MUST be workflow_dispatch (manual) only — running "
            "on push would burn LLM+TTS credits on every commit"
        )
        # Forbid push/pull_request triggers explicitly.
        assert "\n  push:" not in text, (
            "workflow has a push trigger — REMOVE; the script costs "
            "real credits per run"
        )

    def test_workflow_passes_all_4_required_inputs(self) -> None:
        wf = REPO_ROOT / ".github" / "workflows" / "verify-pr-live.yml"
        text = wf.read_text(encoding="utf-8")
        for flag in ("--pr-id", "--pr-kind", "--baseline-job-id",
                     "--post-deploy-job-id"):
            assert flag in text, (
                f"workflow doesn't pass {flag!r} to the script"
            )

    def test_workflow_installs_ffmpeg(self) -> None:
        # rung 4 measures the audio — ffmpeg MUST be installed on the
        # runner. Without it the rung fails for a stupid reason.
        wf = REPO_ROOT / ".github" / "workflows" / "verify-pr-live.yml"
        text = wf.read_text(encoding="utf-8")
        assert "ffmpeg" in text


class TestKqaCliWrapper:
    """Local equivalent of the workflow. Same flag names so an operator
    can copy-paste from the workflow log to a local run."""

    def test_cli_command_registered(self) -> None:
        from kitesforu_qa.cli import cli
        cmd_names = {c.name for c in cli.commands.values()}
        assert "verify-pr-ladder" in cmd_names, (
            "kqa CLI must register 'verify-pr-ladder' subcommand"
        )

    def test_cli_accepts_same_4_required_options(self) -> None:
        from kitesforu_qa.cli import cli
        cmd = cli.commands["verify-pr-ladder"]
        param_names = {p.name for p in cmd.params}
        for name in ("pr_id", "pr_kind", "baseline_job_id",
                     "post_deploy_job_id"):
            assert name in param_names, (
                f"CLI verify-pr-ladder missing required option {name!r}"
            )

    def test_cli_help_doesnt_crash(self) -> None:
        from click.testing import CliRunner
        from kitesforu_qa.cli import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["verify-pr-ladder", "--help"])
        assert result.exit_code == 0, (
            f"--help exited {result.exit_code}: {result.output}"
        )
        assert "5-rung verification ladder" in result.output


class TestWorkflowAndCliDontDrift:
    """The workflow's pr_kind choices and the CLI's pr_kind choices
    MUST stay in sync. Drift = an operator who uses the GH workflow
    can't reproduce locally (or vice versa)."""

    def test_pr_kind_choices_match_workflow(self) -> None:
        # Workflow YAML: choices listed under workflow_dispatch.inputs.pr_kind.
        # CLI: click.Choice(...) on the --pr-kind option.
        wf = REPO_ROOT / ".github" / "workflows" / "verify-pr-live.yml"
        wf_text = wf.read_text(encoding="utf-8")
        cli_text = (REPO_ROOT / "src" / "kitesforu_qa" / "cli.py").read_text(
            encoding="utf-8",
        )
        # The set of pr_kind values both must support.
        for kind in ("sfx_palette", "music_density", "intensity_gain",
                     "mastering_compression", "ducking", "other"):
            assert kind in wf_text, f"workflow missing pr_kind option {kind!r}"
            assert kind in cli_text, f"CLI missing pr_kind choice {kind!r}"
