"""The catalog reconciler's credentials never ride in a URL or in curl's argv.

A URL is what a proxy, a curl error and a log line carry. The same Gemini key reached 578 Cloud Run
log lines through ``?key=`` URLs in the 7 days to 2026-10-01 (workers #3243, api #886,
course-workers #213). ``probe_google_genai`` built the same URL shape. A process's arguments are
readable by any local user (``ps``) while it runs, so the headers go to curl on stdin.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "model_catalog_reconcile.py"
FAKE_KEY = "AIza" + "Fake0" * 7
GENAI = "https://generativelanguage.googleapis.com/v1beta/models"


def _load():
    spec = importlib.util.spec_from_file_location("model_catalog_reconcile", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(monkeypatch, module, pages):
    """Stub curl: record (argv, stdin) per call and answer with the next page."""
    calls: list[tuple[list[str], str]] = []

    def fake_run(cmd, input=None, **_kwargs):  # noqa: A002 — subprocess.run's own name
        calls.append((cmd, input or ""))
        body = pages[min(len(calls), len(pages)) - 1]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr(module, "_secret", lambda _name: FAKE_KEY)
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return calls


def test_the_gemini_probe_sends_the_key_on_stdin_never_in_the_url_or_argv(monkeypatch):
    module = _load()
    calls = _record(monkeypatch, module, [{"models": [{"name": "models/gemini-2.5-flash"}]}])

    ids, control_ok, _note = module.probe_google_genai()

    assert ids == {"gemini-2.5-flash"} and control_ok, "the probe still parses the model list"
    ((cmd, stdin),) = calls
    # The whole URL is pinned: the page size this probe relies on is part of it (#182 review).
    assert cmd[-1] == f"{GENAI}?pageSize=200"
    assert not any(FAKE_KEY in arg for arg in cmd), cmd
    assert cmd[cmd.index("--config") + 1] == "-"
    assert f'header = "x-goog-api-key: {FAKE_KEY}"' in stdin.splitlines()


def test_the_gemini_probe_reads_every_page(monkeypatch):
    # #182 review (latency NIT): the probe never read `nextPageToken`, so a model past the first
    # page would read as a phantom and be proposed for retirement.
    module = _load()
    calls = _record(monkeypatch, module, [
        {"models": [{"name": "models/gemini-a"}], "nextPageToken": "tok2"},
        {"models": [{"name": "models/gemini-b"}]},
    ])
    ids, control_ok, _note = module.probe_google_genai()
    assert ids == {"gemini-a", "gemini-b"} and control_ok
    assert [c[-1] for c, _ in calls] == [f"{GENAI}?pageSize=200", f"{GENAI}?pageSize=200&pageToken=tok2"]


def test_no_probe_puts_a_credential_in_argv(monkeypatch):
    module = _load()
    calls = _record(monkeypatch, module, [{"data": [], "models": [], "voices": []}])
    monkeypatch.setattr(module.subprocess, "run", _with_token(module.subprocess.run))
    for probe in (module.probe_anthropic, module.probe_openai, module.probe_google_genai,
                  module.probe_google_tts, module.probe_elevenlabs):
        probe()
    curls = [(cmd, stdin) for cmd, stdin in calls if cmd and cmd[0] == "curl"]
    assert len(curls) == 5, curls
    for cmd, stdin in curls:
        assert not any(FAKE_KEY in arg or "ya29.FAKE" in arg for arg in cmd), cmd
        assert FAKE_KEY in stdin or "ya29.FAKE" in stdin, stdin


def _with_token(recording_run):
    """`gcloud auth print-access-token` answers a fake token; every other call is recorded."""
    def run(cmd, *args, **kwargs):
        if cmd[:3] == ["gcloud", "auth", "print-access-token"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="ya29.FAKE\n", stderr="")
        return recording_run(cmd, *args, **kwargs)
    return run


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs the real curl binary")
def test_real_curl_sends_the_stdin_headers():
    """`--config -` is only a fix if the installed curl sends those headers: one real request to a
    127.0.0.1 server. $0, no network."""
    seen: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — the stdlib's name
            seen["key"] = self.headers.get("x-goog-api-key")
            seen["path"] = self.path
            body = b'{"models": [{"name": "models/gemini-local"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 — the stdlib's signature
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        module = _load()
        url = f"http://127.0.0.1:{server.server_port}/v1beta/models?pageSize=200"
        d = module._get(url, {"x-goog-api-key": FAKE_KEY + '"q\\'})
    finally:
        thread.join(timeout=10)
        server.server_close()
    assert d == {"models": [{"name": "models/gemini-local"}]}
    assert seen == {"key": FAKE_KEY + '"q\\', "path": "/v1beta/models?pageSize=200"}
