"""CLI entry point for KitesForU QA tool."""

import json
import sys
from pathlib import Path
from typing import Optional

import click

from .config import QAConfig, set_config
from .integrations.gcs import download_from_gcs
from .integrations.kitesforu_api import KitesForUClient
from .models.language import Language
from .pipeline import QAPipeline
from .utils.reporting import format_results, generate_report


@click.group()
@click.version_option(version="0.1.0", prog_name="kqa")
def cli():
    """KitesForU Audio Quality Assurance CLI.

    Run QA checks on podcast audio files to validate quality,
    pronunciation, naturalness, content, and voice matching.
    """
    pass


@cli.command()
@click.option('--audio', required=True, help='Audio file path or GCS URI (gs://...)')
@click.option('--request', required=True, help='Original user request/topic')
@click.option('--script', default=None, help='Path to script file (optional)')
@click.option('--job-id', default=None, help='Job ID for tracking')
@click.option('--language', default='en', help='Language code (en, es, hi, etc.)')
@click.option('--topic', default='', help='Podcast topic for voice matching')
@click.option('--expected-duration', default=None, type=float, help='Expected duration in seconds')
@click.option('--expected-hosts', default=1, type=int, help='Number of expected hosts')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.option('--output', '-o', default=None, help='Output file path (JSON)')
@click.option('--format', 'output_format', default='json', type=click.Choice(['json', 'markdown', 'html']))
def run(
    audio: str,
    request: str,
    script: Optional[str],
    job_id: Optional[str],
    language: str,
    topic: str,
    expected_duration: Optional[float],
    expected_hosts: int,
    verbose: bool,
    output: Optional[str],
    output_format: str,
):
    """Run QA pipeline on a single podcast.

    Examples:

        kqa run --audio gs://bucket/audio.mp3 --request "Create podcast about AI"

        kqa run --audio /local/audio.mp3 --script /local/script.txt --request "..."
    """
    click.echo(f"🔍 Running QA on: {audio}")

    # Load script if provided
    script_text = None
    if script:
        script_path = Path(script)
        if not script_path.exists():
            click.echo(f"❌ Script file not found: {script}", err=True)
            sys.exit(1)
        script_text = script_path.read_text()

    # Parse language from request JSON if present
    effective_language = language
    try:
        request_data = json.loads(request)
        if isinstance(request_data, dict):
            # Extract language from request JSON (supports BCP-47 codes like "hi-IN")
            req_lang = request_data.get("language", "")
            if req_lang:
                # Normalize BCP-47 to short code (e.g., "hi-IN" -> "hi")
                effective_language = req_lang.split("-")[0] if "-" in req_lang else req_lang
    except (json.JSONDecodeError, TypeError):
        # Request is plain text, use language option
        pass

    # Get language enum
    try:
        lang = Language(effective_language)
    except ValueError:
        click.echo(f"❌ Unsupported language: {effective_language}", err=True)
        click.echo(f"   Supported: {', '.join(l.value for l in Language)}")
        sys.exit(1)

    # Run pipeline
    pipeline = QAPipeline(language=lang, verbose=verbose)

    try:
        results = pipeline.run(
            audio_path=audio,
            user_request=request,
            script=script_text,
            topic=topic or request,
            job_id=job_id,
            expected_duration=expected_duration,
            expected_hosts=expected_hosts,
        )
    except Exception as e:
        click.echo(f"❌ Pipeline failed: {e}", err=True)
        if verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    # Display results
    click.echo(format_results(results, verbose=verbose))

    # Save to file if requested
    if output:
        report = generate_report(results, output_path=output, format=output_format)
        click.echo(f"\n📄 Results saved to: {output}")

    # Exit with appropriate code
    if results.passed:
        click.echo("\n✅ QA PASSED")
        sys.exit(0)
    else:
        click.echo("\n❌ QA FAILED")
        sys.exit(1)


@cli.command()
@click.option('--topic', required=True, help='Podcast topic')
@click.option('--duration', default=10, type=int, help='Duration in minutes')
@click.option('--style', default='Explainer', help='Podcast style (Explainer, Storytelling, Interview, Motivational)')
@click.option('--language', default='en-US', help='BCP 47 language code (e.g., en-US, hi-IN, es-ES)')
@click.option('--api-url', envvar='KITESFORU_API_URL', default='https://api.kitesforu.com', help='API URL')
@click.option('--api-key', envvar='KITESFORU_API_KEY', help='API key')
@click.option('--wait-timeout', default=600, type=int, help='Max wait time in seconds')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.option('--output', '-o', default=None, help='Output file path')
def e2e(
    topic: str,
    duration: int,
    style: str,
    language: str,
    api_url: str,
    api_key: str,
    wait_timeout: int,
    verbose: bool,
    output: Optional[str],
):
    """Run end-to-end test: create podcast via API then run QA.

    This command creates a new podcast job via the KitesForU API,
    waits for it to complete, then runs full QA on the result.

    Examples:

        kqa e2e --topic "The future of AI" --duration 10

        kqa e2e --topic "Tech news" --language hi-IN --api-url https://api.example.com
    """
    click.echo(f"🚀 Starting E2E test for: {topic}")

    if not api_key:
        click.echo("❌ KITESFORU_API_KEY not set", err=True)
        sys.exit(1)

    # Create client
    client = KitesForUClient(base_url=api_url, api_key=api_key)

    # Health check
    if verbose:
        click.echo("  Checking API health...")
    if not client.health_check():
        click.echo(f"⚠️  API health check failed, continuing anyway...")

    # Create job
    click.echo("  📝 Creating podcast request...")
    try:
        job = client.create_job(
            topic=topic,
            duration_min=duration,
            style=style,
            language=language,
        )
        job_id = job.get('job_id') or job.get('id')
        click.echo(f"  ✓ Job created: {job_id}")
    except Exception as e:
        click.echo(f"❌ Failed to create job: {e}", err=True)
        sys.exit(1)

    # Wait for completion
    click.echo(f"  ⏳ Waiting for completion (max {wait_timeout}s)...")
    try:
        completed_job = client.wait_for_completion(
            job_id=job_id,
            timeout=wait_timeout,
        )
    except TimeoutError:
        click.echo(f"❌ Job did not complete within {wait_timeout}s", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error waiting for job: {e}", err=True)
        sys.exit(1)

    status = completed_job.get('status', '').lower()
    if status not in ('completed', 'complete', 'done'):
        click.echo(f"  ❌ Job failed: {completed_job.get('error', 'Unknown error')}")
        sys.exit(1)

    click.echo(f"  ✓ Job completed!")

    # Get audio and script
    audio_url = client.get_job_audio(job_id)
    script = client.get_job_script(job_id)

    if not audio_url:
        click.echo("❌ No audio URL in completed job", err=True)
        sys.exit(1)

    # Run QA
    click.echo("  🔍 Running QA pipeline...")
    try:
        lang = Language(language)
    except ValueError:
        lang = Language.ENGLISH

    pipeline = QAPipeline(language=lang, verbose=verbose)
    results = pipeline.run(
        audio_path=audio_url,
        user_request=topic,
        script=script,
        job_id=job_id,
    )

    # Display results
    click.echo(format_results(results, verbose=True))

    if output:
        generate_report(results, output_path=output)
        click.echo(f"\n📄 Results saved to: {output}")

    if results.passed:
        click.echo("\n✅ E2E TEST PASSED")
        sys.exit(0)
    else:
        click.echo("\n❌ E2E TEST FAILED")
        sys.exit(1)


@cli.command()
@click.option('--input', 'input_file', required=True, help='Input CSV file with jobs')
@click.option('--output', 'output_dir', required=True, help='Output directory for results')
@click.option('--parallel', default=1, type=int, help='Number of parallel workers')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def batch(
    input_file: str,
    output_dir: str,
    parallel: int,
    verbose: bool,
):
    """Run QA on multiple podcasts from CSV file.

    Input CSV format:
        job_id,audio_path,request,language
        job_123,gs://bucket/audio.mp3,"Create podcast about AI",en
        job_456,gs://bucket/audio2.mp3,"Tech news",en

    Examples:

        kqa batch --input jobs.csv --output ./results/

        kqa batch --input jobs.csv --output ./results/ --parallel 4
    """
    import csv
    from concurrent.futures import ThreadPoolExecutor, as_completed

    click.echo(f"📦 Batch processing: {input_file}")

    # Read input CSV
    try:
        with open(input_file) as f:
            reader = csv.DictReader(f)
            jobs = list(reader)
    except Exception as e:
        click.echo(f"❌ Failed to read input file: {e}", err=True)
        sys.exit(1)

    if not jobs:
        click.echo("❌ No jobs found in input file", err=True)
        sys.exit(1)

    click.echo(f"  Found {len(jobs)} jobs to process")

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Process jobs
    results_summary = {"passed": 0, "failed": 0, "errors": 0}

    def process_job(job_data: dict) -> tuple:
        job_id = job_data.get('job_id', 'unknown')
        try:
            lang_str = job_data.get('language', 'en')
            try:
                lang = Language(lang_str)
            except ValueError:
                lang = Language.ENGLISH

            pipeline = QAPipeline(language=lang, verbose=False)
            result = pipeline.run(
                audio_path=job_data['audio_path'],
                user_request=job_data.get('request', ''),
                job_id=job_id,
            )

            # Save individual result
            result_path = output_path / f"{job_id}.json"
            generate_report(result, output_path=str(result_path))

            return (job_id, result.passed, None)

        except Exception as e:
            return (job_id, False, str(e))

    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {executor.submit(process_job, job): job for job in jobs}

        for future in as_completed(futures):
            job_id, passed, error = future.result()

            if error:
                results_summary["errors"] += 1
                icon = "💥"
                status = f"ERROR: {error}"
            elif passed:
                results_summary["passed"] += 1
                icon = "✅"
                status = "PASSED"
            else:
                results_summary["failed"] += 1
                icon = "❌"
                status = "FAILED"

            if verbose or not passed:
                click.echo(f"  {icon} {job_id}: {status}")

    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("📊 BATCH SUMMARY")
    click.echo("=" * 60)
    click.echo(f"  Total:  {len(jobs)}")
    click.echo(f"  Passed: {results_summary['passed']}")
    click.echo(f"  Failed: {results_summary['failed']}")
    click.echo(f"  Errors: {results_summary['errors']}")
    click.echo(f"\n  Results saved to: {output_dir}")

    if results_summary["failed"] > 0 or results_summary["errors"] > 0:
        sys.exit(1)
    sys.exit(0)


@cli.command()
@click.option('--script', required=True, help='Path to script file')
@click.option('--request', required=True, help='Original user request')
@click.option('--duration', default=10, type=int, help='Target duration in minutes')
@click.option('--quick', is_flag=True, help='Run only quick checks (free, no LLM)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def check_script(script: str, request: str, duration: int, quick: bool, verbose: bool):
    """Check script quality before audio generation.

    This is a lightweight check that can be run before spending
    money on audio generation.

    Examples:

        kqa check-script --script /path/to/script.txt --request "Create podcast about AI"
    """
    click.echo(f"📝 Checking script quality...")

    script_path = Path(script)
    if not script_path.exists():
        click.echo(f"❌ Script file not found: {script}", err=True)
        sys.exit(1)

    script_text = script_path.read_text()

    if quick:
        # Run only free quick checks
        from .stages.content import quick_script_checks
        issues = quick_script_checks(script_text, target_duration_min=duration)
        if issues:
            click.echo("\n❌ Script has issues:")
            for issue in issues:
                click.echo(f"  • {issue}")
            sys.exit(1)
        else:
            click.echo("\n✅ Quick checks passed!")
            click.echo("   (Run without --quick for full LLM evaluation)")
            sys.exit(0)

    pipeline = QAPipeline(verbose=verbose)
    result = pipeline.run_content_only(
        script=script_text,
        user_request=request,
        target_duration_min=duration,
    )

    if result.passed:
        click.echo("\n✅ Script looks good!")
        if result.data.get('scores'):
            click.echo("\nScores:")
            for dim, score in result.data['scores'].items():
                click.echo(f"  {dim}: {score}/10")
        sys.exit(0)
    else:
        if result.error:
            click.echo(f"\n⚠️  {result.error}")
            click.echo("   (Quick checks passed, but LLM evaluation failed)")
            sys.exit(1)
        click.echo("\n❌ Script has issues:")
        for issue in result.issues:
            click.echo(f"  • {issue}")
        sys.exit(1)


@cli.command("verify-live")
@click.option("--job-id", required=True, help="Job id to verify (Firestore key)")
@click.option(
    "--genre-override", default=None,
    help="Override the architect-committed genre (e.g. 'horror' / 'bedtime')",
)
@click.option(
    "--output", "-o", default=None,
    help="Write the full JSON report to this path (otherwise stdout)",
)
@click.option(
    "--exit-on-fail/--no-exit-on-fail", default=True,
    help="Exit non-zero when verdict=FAIL (useful for CI / cron alerting)",
)
def verify_live(
    job_id: str,
    genre_override: Optional[str],
    output: Optional[str],
    exit_on_fail: bool,
):
    """D21 L5.4 — download a live job's mp3 + speech_only and grade
    it against the per-genre listen-test profile.

    Closes the verifier loop the way L5.2 + L5.3 intended:
      1. pull (genre, duration, GCS paths) from Firestore
      2. download audio.mp3 + speech_only.mp3 to a tempdir
      3. run verify_audio_quality(genre=<architect-committed>)
      4. print the verdict (PASS / WARN / FAIL)
      5. exit non-zero on FAIL (so a Cloud Scheduler cron alerts)
    """
    # Imports kept lazy — the live module pulls google-cloud-firestore
    # which the non-live CLI commands shouldn't need to load.
    from .profiles import get_profile
    from .verify_live import verify_job_live

    profile_override = (
        get_profile(genre_override) if genre_override else None
    )
    result = verify_job_live(job_id, profile_override=profile_override)
    payload = result.to_dict()

    if output:
        Path(output).write_text(
            json.dumps(payload, indent=2), encoding="utf-8",
        )
        click.echo(f"Wrote report to {output}")
    else:
        click.echo(json.dumps(payload, indent=2))

    verdict = payload.get("verdict", "ERROR")
    if verdict == "PASS":
        click.echo(f"\n✅ {job_id}: PASS")
    elif verdict == "WARN":
        click.echo(f"\n⚠️  {job_id}: WARN")
    else:
        click.echo(f"\n❌ {job_id}: {verdict}")
        if exit_on_fail:
            sys.exit(1)


if __name__ == '__main__':
    cli()
