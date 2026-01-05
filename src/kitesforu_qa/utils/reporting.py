"""Report generation utilities."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..models.results import QAResult


def format_results(results: QAResult, verbose: bool = False) -> str:
    """
    Format QA results for console display.

    Args:
        results: QAResult object
        verbose: Include detailed output

    Returns:
        Formatted string for display
    """
    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("📊 QA RESULTS")
    lines.append("=" * 60)

    if results.job_id:
        lines.append(f"Job ID: {results.job_id}")
    lines.append(f"Language: {results.language}")
    lines.append(f"Execution Time: {results.execution_time_seconds:.1f}s")
    lines.append("")

    for stage_name, stage_result in results.stages.items():
        passed = stage_result.passed
        icon = "✅" if passed else "❌"
        lines.append(f"{icon} Stage: {stage_name.upper()}")

        if verbose or not passed:
            for key, value in stage_result.data.items():
                if key not in ('passed', 'issues'):
                    lines.append(f"   {key}: {value}")

            if stage_result.issues:
                for issue in stage_result.issues:
                    lines.append(f"   ⚠️ {issue}")

            if stage_result.error:
                lines.append(f"   ❌ Error: {stage_result.error}")

        lines.append("")

    lines.append("=" * 60)

    if results.passed:
        lines.append("✅ OVERALL: PASSED")
    else:
        lines.append("❌ OVERALL: FAILED")
        if results.all_issues:
            lines.append("")
            lines.append("Issues Found:")
            for issue in results.all_issues:
                lines.append(f"  • {issue}")

    lines.append("=" * 60)

    return "\n".join(lines)


def generate_report(
    results: QAResult,
    output_path: Optional[str] = None,
    format: str = "json",
) -> str:
    """
    Generate QA report in specified format.

    Args:
        results: QAResult object
        output_path: Path to save report (optional)
        format: Output format ('json', 'markdown', 'html')

    Returns:
        Report content as string
    """
    if format == "json":
        content = json.dumps(results.to_dict(), indent=2)
    elif format == "markdown":
        content = _generate_markdown_report(results)
    elif format == "html":
        content = _generate_html_report(results)
    else:
        raise ValueError(f"Unsupported format: {format}")

    if output_path:
        Path(output_path).write_text(content)

    return content


def _generate_markdown_report(results: QAResult) -> str:
    """Generate markdown format report."""
    lines = []
    lines.append("# QA Report")
    lines.append("")
    lines.append(f"**Generated**: {results.timestamp}")
    if results.job_id:
        lines.append(f"**Job ID**: {results.job_id}")
    lines.append(f"**Language**: {results.language}")
    lines.append(f"**Overall Status**: {'✅ PASSED' if results.passed else '❌ FAILED'}")
    lines.append("")

    lines.append("## Stage Results")
    lines.append("")

    for stage_name, stage_result in results.stages.items():
        icon = "✅" if stage_result.passed else "❌"
        lines.append(f"### {icon} {stage_name.title()}")
        lines.append("")

        if stage_result.data:
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            for key, value in stage_result.data.items():
                if key not in ('passed', 'issues'):
                    lines.append(f"| {key} | {value} |")
            lines.append("")

        if stage_result.issues:
            lines.append("**Issues:**")
            for issue in stage_result.issues:
                lines.append(f"- {issue}")
            lines.append("")

        if stage_result.error:
            lines.append(f"**Error:** {stage_result.error}")
            lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total Stages**: {len(results.stages)}")
    lines.append(f"- **Passed**: {sum(1 for s in results.stages.values() if s.passed)}")
    lines.append(f"- **Failed**: {len(results.failed_stages)}")
    lines.append(f"- **Execution Time**: {results.execution_time_seconds:.1f}s")

    return "\n".join(lines)


def _generate_html_report(results: QAResult) -> str:
    """Generate HTML format report."""
    overall_status = "PASSED" if results.passed else "FAILED"
    status_color = "#28a745" if results.passed else "#dc3545"

    stages_html = []
    for stage_name, stage_result in results.stages.items():
        icon = "✅" if stage_result.passed else "❌"
        bg_color = "#d4edda" if stage_result.passed else "#f8d7da"

        metrics_html = ""
        if stage_result.data:
            rows = []
            for key, value in stage_result.data.items():
                if key not in ('passed', 'issues'):
                    rows.append(f"<tr><td><b>{key}</b></td><td>{value}</td></tr>")
            if rows:
                metrics_html = f"<table>{''.join(rows)}</table>"

        issues_html = ""
        if stage_result.issues:
            items = [f"<li>{issue}</li>" for issue in stage_result.issues]
            issues_html = f"<ul>{''.join(items)}</ul>"

        stages_html.append(f"""
        <div style="background: {bg_color}; padding: 15px; margin: 10px 0; border-radius: 5px;">
            <h3>{icon} {stage_name.title()}</h3>
            {metrics_html}
            {issues_html}
        </div>
        """)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>QA Report - {results.job_id or 'Unknown'}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            h1 {{ color: #333; }}
            table {{ border-collapse: collapse; width: 100%; }}
            td, th {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        </style>
    </head>
    <body>
        <h1>QA Report</h1>
        <p><b>Generated:</b> {results.timestamp}</p>
        <p><b>Job ID:</b> {results.job_id or 'N/A'}</p>
        <p><b>Language:</b> {results.language}</p>
        <p style="color: {status_color}; font-size: 1.5em;"><b>{overall_status}</b></p>

        <h2>Stage Results</h2>
        {''.join(stages_html)}

        <h2>Summary</h2>
        <ul>
            <li>Total Stages: {len(results.stages)}</li>
            <li>Passed: {sum(1 for s in results.stages.values() if s.passed)}</li>
            <li>Failed: {len(results.failed_stages)}</li>
            <li>Execution Time: {results.execution_time_seconds:.1f}s</li>
        </ul>
    </body>
    </html>
    """
