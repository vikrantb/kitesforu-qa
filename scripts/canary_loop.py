"""Continuous canary loop — fires 1-min Cairo horror prompts on a 30-min cadence.

Per /Users/vikrantbhosale/.claude/projects/-Users-vikrantbhosale-gitprojects-kitesforu/memory/reference_live_job_verification_recipe.md

Each iteration:
  1. Playwright sign-in → mint Clerk JWT
  2. POST /v1/create/intake (SSE) — handle plan_complete OR refine-once-with-skip
  3. POST /v1/create/execute → capture job_id
  4. Poll Firestore podcast_jobs/{job_id} every 60s, up to 10 min
  5. On completion: ffprobe the mp3, append to canary-loop-log.md
  6. On stall/failure: append + slack C09QXLV86C8 with diagnostics
  7. Sleep 30 min, repeat (max 24 iterations).

Single-iteration mode for the parent's orchestrator: --once.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from google.cloud import firestore
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_BASE = "https://kitesforu-api-456429114416.us-central1.run.app"
BETA_URL = "https://beta.kitesforu.com"
TEST_EMAIL = "test@kitesforu.com"
TEST_PASSWORD = "PatangUdav.15"
PROJECT_ID = "kitesforu-dev"

PROMPT = "Layla and Amir hear whispers in their Cairo apartment. 1 minute horror, dialogue-heavy."
TARGET_DURATION_MIN = 1.0

POLL_INTERVAL_SEC = 60
MAX_POLL_MINUTES = 10
SLEEP_BETWEEN_ITERATIONS_SEC = 30 * 60  # 30 minutes
MAX_ITERATIONS = 24                      # 12-hour cap

REPORTS_DIR = Path("/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-qa/reports")
LOG_FILE = REPORTS_DIR / "canary-loop-log.md"

SLACK_CHANNEL = "C09QXLV86C8"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


_BROWSER_STATE: dict[str, Any] = {"ctx_dir": None}


def mint_clerk_token() -> str:
    """Sign in to beta via the UI modal (mirrors kitetest auth.ts) + return a fresh JWT.

    Uses a persistent context dir so subsequent calls re-use the cookie jar and skip the modal.
    """
    if _BROWSER_STATE["ctx_dir"] is None:
        _BROWSER_STATE["ctx_dir"] = tempfile.mkdtemp(prefix="canary_clerk_ctx_")
    ctx_dir = _BROWSER_STATE["ctx_dir"]

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(ctx_dir, headless=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BETA_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass

        # Wait for Clerk to hydrate.
        try:
            page.wait_for_function(
                "typeof window.Clerk !== 'undefined' && window.Clerk.loaded === true",
                timeout=60_000,
            )
        except Exception:
            pass

        already = False
        try:
            already = bool(page.evaluate("() => Boolean(window.Clerk && window.Clerk.session)"))
        except Exception:
            already = False

        if not already:
            # UI modal flow — same as kitetest/tests/shared/auth.ts loginUser.
            sign_in_btn = page.locator("button:has-text('Sign In')").first
            sign_in_btn.wait_for(state="visible", timeout=20_000)
            sign_in_btn.click()
            page.wait_for_selector(".cl-modalContent, [data-clerk-component='SignIn']", timeout=15_000)

            email_input = page.locator("#identifier-field, input[name='identifier']").first
            email_input.wait_for(state="visible", timeout=15_000)
            email_input.fill(TEST_EMAIL)

            # Clerk may be one-step (password visible immediately) or two-step (continue then password).
            try:
                password_input = page.locator("#password-field, input[name='password']").first
                password_input.wait_for(state="visible", timeout=4_000)
            except Exception:
                page.locator("button.cl-formButtonPrimary").first.click()
                password_input = page.locator("#password-field, input[name='password']").first
                password_input.wait_for(state="visible", timeout=15_000)
            password_input.fill(TEST_PASSWORD)
            page.locator("button.cl-formButtonPrimary").first.click()

            # Wait for modal close + session.
            try:
                page.wait_for_function(
                    "!document.querySelector('.cl-modalContent, [data-clerk-component=\"SignIn\"]')",
                    timeout=30_000,
                )
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            page.wait_for_function("Boolean(window.Clerk && window.Clerk.session)", timeout=30_000)

        token = None
        last_err: Exception | None = None
        for _ in range(5):
            try:
                token = page.evaluate("async () => await window.Clerk.session.getToken()")
                if token and not (isinstance(token, str) and token.startswith("ERROR:")):
                    break
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5)
        ctx.close()
        if not token:
            raise RuntimeError(f"Clerk getToken failed: {last_err}")
        return token


def parse_sse_to_terminal(resp: requests.Response) -> dict[str, Any]:
    """Iterate an SSE response until we hit a terminal event.

    Returns the first dict whose `type` is one of {plan_complete, questions, error}.
    """
    buf = ""
    for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
        if not chunk:
            continue
        buf += chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            for line in raw.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type")
                if t in {"plan_complete", "questions", "error"}:
                    return ev
    raise RuntimeError("SSE stream ended without a terminal event")


def trigger_job(token: str) -> str:
    """Run intake (optionally refine-skip) + execute. Returns job_id."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # intake
    intake_body = {"initial_text": PROMPT, "target_duration_min": TARGET_DURATION_MIN}
    with requests.post(
        f"{API_BASE}/v1/create/intake",
        headers={**headers, "Accept": "text/event-stream"},
        json=intake_body,
        stream=True,
        timeout=180,
    ) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"intake HTTP {resp.status_code}: {resp.text[:500]}")
        ev = parse_sse_to_terminal(resp)

    if ev.get("type") == "error":
        raise RuntimeError(f"intake error: {ev}")

    session_id = ev.get("session_id")
    if not session_id:
        raise RuntimeError(f"intake terminal event missing session_id: {ev}")

    # Refine loop — keep skipping questions until plan_complete or hard cap.
    refine_attempts = 0
    while ev.get("type") == "questions" and refine_attempts < 5:
        refine_attempts += 1
        # Prefer the first listed option (deterministic) over "skip" — some genres
        # require a constrained answer rather than free-text skip.
        answers = {}
        for q in ev.get("questions", []):
            opts = q.get("options") or []
            answers[q["id"]] = opts[0] if opts else "skip"
        refine_body = {"session_id": session_id, "answers": answers}
        token = mint_clerk_token()
        headers["Authorization"] = f"Bearer {token}"
        with requests.post(
            f"{API_BASE}/v1/create/refine",
            headers={**headers, "Accept": "text/event-stream"},
            json=refine_body,
            stream=True,
            timeout=180,
        ) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"refine HTTP {resp.status_code}: {resp.text[:500]}")
            ev = parse_sse_to_terminal(resp)
    if ev.get("type") != "plan_complete":
        raise RuntimeError(f"refine never reached plan_complete after {refine_attempts} attempts: type={ev.get('type')}")

    # execute
    token = mint_clerk_token()
    headers["Authorization"] = f"Bearer {token}"
    # Test-cost-ladder default: canaries run CHEAP (quality_tier=low ≈ $0.025 vs $0.15+).
    # Escalate deliberately via CANARY_QUALITY_TIER=medium|high (T4 — founder ack required
    # per .claude/rules/test-cost-ladder.md).
    exec_body = {
        "session_id": session_id,
        "generation_mode": "sequential",
        "quality_tier": os.environ.get("CANARY_QUALITY_TIER", "low"),
        "wants_visuals": os.environ.get("CANARY_WANTS_VISUALS", "") == "true",
    }
    resp = requests.post(
        f"{API_BASE}/v1/create/execute",
        headers=headers,
        json=exec_body,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"execute HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    # Current api shape: {content_id, content_type, redirect_url, ...}.
    # content_id == job_id for podcast content (verified 2026-06-02 against beta).
    job_id = data.get("job_id") or data.get("content_id") or data.get("id")
    if not job_id and isinstance(data.get("job"), dict):
        job_id = data["job"].get("id") or data["job"].get("job_id")
    if not job_id and isinstance(data.get("jobs"), list) and data["jobs"]:
        first = data["jobs"][0]
        if isinstance(first, dict):
            job_id = first.get("id") or first.get("job_id")
        elif isinstance(first, str):
            job_id = first
    if not job_id:
        raise RuntimeError(f"execute response missing job_id/content_id: {json.dumps(data)[:500]}")
    return job_id


def fetch_job(db: firestore.Client, job_id: str) -> dict[str, Any] | None:
    snap = db.collection("podcast_jobs").document(job_id).get()
    return snap.to_dict() if snap.exists else None


def watch_job(db: firestore.Client, job_id: str) -> dict[str, Any]:
    """Poll until terminal or timeout. Returns the final job dict + a 'verdict'."""
    deadline = time.time() + MAX_POLL_MINUTES * 60
    last: dict[str, Any] = {}
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SEC)
        doc = fetch_job(db, job_id) or {}
        last = doc
        status = doc.get("status")
        stage = (doc.get("progress") or {}).get("stage")
        pct = (doc.get("progress") or {}).get("pct")
        print(f"  [{utcnow_iso()}] poll job={job_id} status={status} stage={stage} pct={pct}", flush=True)
        if status in {"completed", "failed", "cancelled"}:
            return doc
    last["_timeout"] = True
    return last


def ffprobe_duration(mp3_url: str) -> tuple[float | None, int | None]:
    """Returns (duration_sec, file_size_bytes), or (None,None) on failure."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name
        urllib.request.urlretrieve(mp3_url, tmp_path)
        size = os.path.getsize(tmp_path)
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
            capture_output=True, text=True, timeout=60,
        )
        os.unlink(tmp_path)
        if out.returncode != 0:
            return None, size
        return float(out.stdout.strip()), size
    except Exception as e:  # noqa: BLE001
        print(f"  ffprobe failed: {e}", flush=True)
        return None, None


def append_log(line: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text(
            "# Canary Loop Log\n\n"
            "Continuous 30-min canary watching pipeline health. 1-min Cairo horror prompt each iteration.\n\n"
            "| timestamp | iter | outcome | job_id | wall_clock | mp3_size | duration_sec | note |\n"
            "|-----------|------|---------|--------|------------|----------|---------------|------|\n"
        )
    with LOG_FILE.open("a") as fh:
        fh.write(line + "\n")


def slack_alert(message: str) -> None:
    """Post to slack channel. Uses the MCP path via stdout signaling — best-effort."""
    # We don't have direct slack MCP access from this script — print a structured
    # marker the parent agent will pick up from our stdout.
    banner = "\n" + "=" * 8 + " SLACK ALERT (channel " + SLACK_CHANNEL + ") " + "=" * 8 + "\n"
    print(banner + message + "\n" + "=" * 60, flush=True)


def diagnose_terminal(doc: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Extract verdict + diagnostic fields from the final firestore doc."""
    status = doc.get("status")
    is_timeout = bool(doc.get("_timeout"))
    audio = doc.get("audio") or {}
    mp3_url = audio.get("mp3_url")
    stage_audio = (doc.get("stages") or {}).get("job-audio") or {}
    result = stage_audio.get("result") or {}
    if not mp3_url:
        mp3_url = result.get("audio_url")
    phase_timing = stage_audio.get("phase_timing") or {}
    heartbeat_phase = stage_audio.get("heartbeat_phase")
    failure_reason = doc.get("failure_reason") or stage_audio.get("failure_reason")
    created_at = doc.get("created_at")
    updated_at = doc.get("updated_at")

    def to_ms(ts):
        if ts is None:
            return None
        if hasattr(ts, "timestamp"):
            return int(ts.timestamp() * 1000)
        return None

    wall_ms = None
    c_ms, u_ms = to_ms(created_at), to_ms(updated_at)
    if c_ms and u_ms:
        wall_ms = u_ms - c_ms

    return {
        "job_id": job_id,
        "status": status,
        "is_timeout": is_timeout,
        "mp3_url": mp3_url,
        "phase_timing": phase_timing,
        "heartbeat_phase": heartbeat_phase,
        "failure_reason": failure_reason,
        "wall_ms": wall_ms,
        "progress": doc.get("progress"),
    }


def run_one(db: firestore.Client, iteration: int) -> dict[str, Any]:
    """One end-to-end canary iteration."""
    print(f"\n=== [{utcnow_iso()}] iter {iteration} START ===", flush=True)
    started_at = time.time()
    job_id: str | None = None
    try:
        token = mint_clerk_token()
        job_id = trigger_job(token)
        print(f"  triggered job_id={job_id}", flush=True)
        final = watch_job(db, job_id)
        diag = diagnose_terminal(final, job_id)

        if diag["status"] == "completed" and not diag["is_timeout"]:
            duration_sec, mp3_size = (None, None)
            if diag["mp3_url"]:
                duration_sec, mp3_size = ffprobe_duration(diag["mp3_url"])
            # Prefer wall-clock from script-start (most honest).
            wall_sec = time.time() - started_at
            note = "clean"
            ok = bool(mp3_size and mp3_size > 100_000 and duration_sec and 40 <= duration_sec <= 90)
            if not ok:
                note = f"completed_but_mp3_off (size={mp3_size}, dur={duration_sec})"
            append_log(
                f"| {utcnow_iso()} | {iteration} | {'PASS' if ok else 'WARN'} | {job_id} | "
                f"{wall_sec:.0f}s | {mp3_size or 'n/a'} | {duration_sec or 'n/a'} | {note} |"
            )
            print(f"=== iter {iteration} OUTCOME={'PASS' if ok else 'WARN'} ===", flush=True)
            return {"outcome": "pass" if ok else "warn", "job_id": job_id, "diag": diag,
                    "duration_sec": duration_sec, "mp3_size": mp3_size, "wall_sec": wall_sec}

        # FAILURE PATH
        wall_sec = time.time() - started_at
        outcome = "stall" if diag["is_timeout"] else "fail"
        note = f"status={diag['status']} timeout={diag['is_timeout']} reason={diag['failure_reason']} hb_phase={diag['heartbeat_phase']}"
        append_log(
            f"| {utcnow_iso()} | {iteration} | {outcome.upper()} | {job_id} | "
            f"{wall_sec:.0f}s | n/a | n/a | {note} |"
        )
        # Pull last 20 log lines for the job.
        log_tail = pull_log_tail(job_id)
        slack_alert(
            f":rotating_light: canary iter {iteration} {outcome.upper()}\n"
            f"job_id: `{job_id}`\n"
            f"status: `{diag['status']}` timeout: `{diag['is_timeout']}`\n"
            f"failure_reason: `{diag['failure_reason']}`\n"
            f"last heartbeat_phase: `{diag['heartbeat_phase']}`\n"
            f"phase_timing: ```{json.dumps(diag['phase_timing'], default=str)[:500]}```\n"
            f"wall_clock: {wall_sec:.0f}s (limit {MAX_POLL_MINUTES*60}s)\n"
            f"log tail:\n```{log_tail[:1500]}```\n"
            f"Hypothesis: {classify_failure(diag, log_tail)}"
        )
        print(f"=== iter {iteration} OUTCOME={outcome.upper()} ===", flush=True)
        return {"outcome": outcome, "job_id": job_id, "diag": diag, "log_tail": log_tail}

    except Exception as e:  # noqa: BLE001
        wall_sec = time.time() - started_at
        append_log(
            f"| {utcnow_iso()} | {iteration} | TRIGGER_ERR | {job_id or 'n/a'} | "
            f"{wall_sec:.0f}s | n/a | n/a | exception: {str(e)[:200]} |"
        )
        slack_alert(
            f":rotating_light: canary iter {iteration} TRIGGER_ERR\n"
            f"job_id: `{job_id or 'n/a'}`\n"
            f"exception: ```{str(e)[:1000]}```\n"
            f"Hypothesis: token mint / SSE intake / execute endpoint regression"
        )
        print(f"=== iter {iteration} OUTCOME=TRIGGER_ERR: {e} ===", flush=True)
        return {"outcome": "trigger_err", "error": str(e)}


def pull_log_tail(job_id: str, max_lines: int = 30) -> str:
    """Best-effort gcloud logging tail for the job_id, last 15m."""
    try:
        cmd = [
            "gcloud", "logging", "read",
            f"resource.type=cloud_run_revision AND (jsonPayload.job_id=\"{job_id}\" OR textPayload:\"{job_id}\")",
            "--project", PROJECT_ID,
            "--freshness", "15m",
            "--limit", str(max_lines),
            "--format", "value(timestamp,resource.labels.service_name,textPayload,jsonPayload.message)",
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return out.stdout.strip() or out.stderr.strip()
    except Exception as e:  # noqa: BLE001
        return f"(log pull failed: {e})"


def classify_failure(diag: dict[str, Any], log_tail: str) -> str:
    fr = (diag.get("failure_reason") or "").lower()
    hb = (diag.get("heartbeat_phase") or "").lower()
    if "stale_no_heartbeat" in fr or "never_heartbeat" in fr:
        return "R2 watchdog reaped a stalled job — worker stopped heartbeating. Inspect last heartbeat_phase + worker logs."
    if diag.get("is_timeout"):
        if hb:
            return f"Watcher 10-min timeout while still running at phase={hb}. Likely a long-running LLM call or new stall in that phase."
        return "Watcher 10-min timeout with no heartbeat_phase — job may have never started TTS or worker dispatch broke."
    if "validation" in log_tail.lower():
        return "Pydantic validation error in pipeline — recent schema bump may not be propagated."
    if "rate" in log_tail.lower() or "429" in log_tail:
        return "Provider rate-limit (likely LLM/TTS). Check failover chain firing."
    return "Unclassified — inspect log tail + Firestore stages.* for last-write phase."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Single iteration then exit")
    parser.add_argument("--iter-start", type=int, default=1, help="Starting iteration number")
    parser.add_argument("--max", type=int, default=MAX_ITERATIONS, help="Max iterations before exit")
    parser.add_argument("--sleep-sec", type=int, default=SLEEP_BETWEEN_ITERATIONS_SEC)
    args = parser.parse_args()

    db = firestore.Client(project=PROJECT_ID)

    if args.once:
        run_one(db, args.iter_start)
        return 0

    passes = 0
    fails = 0
    walls: list[float] = []
    for i in range(args.iter_start, args.iter_start + args.max):
        res = run_one(db, i)
        if res["outcome"] == "pass":
            passes += 1
            if res.get("wall_sec"):
                walls.append(res["wall_sec"])
        else:
            fails += 1
        # Heartbeat every 6
        if i % 6 == 0:
            avg = sum(walls)/len(walls) if walls else 0.0
            print(
                f"\n>>> CANARY HEARTBEAT @ iter {i}: passes={passes} fails={fails} "
                f"avg_wall={avg:.0f}s (n={len(walls)})\n",
                flush=True,
            )
        # Don't sleep after final iteration.
        if i < args.iter_start + args.max - 1:
            print(f"  sleeping {args.sleep_sec}s before next iter...", flush=True)
            time.sleep(args.sleep_sec)

    avg = sum(walls)/len(walls) if walls else 0.0
    print(
        f"\n>>> CANARY LOOP COMPLETE: passes={passes} fails={fails} "
        f"avg_wall={avg:.0f}s (n={len(walls)})",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
