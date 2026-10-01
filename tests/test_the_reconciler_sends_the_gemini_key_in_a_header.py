"""The catalog reconciler sends the Gemini key in a header, never in the URL.

A URL is what a proxy, a curl error and a log line carry. The same key reached 578 Cloud Run log
lines through ``?key=`` URLs in the 7 days to 2026-10-01 (workers #3243, api #886,
course-workers #213). ``probe_google_genai`` built the same URL shape.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "model_catalog_reconcile.py"
FAKE_KEY = "AIza" + "Fake0" * 7


def _load():
    spec = importlib.util.spec_from_file_location("model_catalog_reconcile", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_gemini_probe_sends_the_key_in_a_header(monkeypatch):
    module = _load()
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        body = {"models": [{"name": "models/gemini-2.5-flash"}]}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr(module, "_secret", lambda _name: FAKE_KEY)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    ids, control_ok, _note = module.probe_google_genai()

    assert ids == {"gemini-2.5-flash"} and control_ok, "the probe still parses the model list"
    (cmd,) = calls
    url = cmd[-1]
    assert url.startswith("https://generativelanguage.googleapis.com/v1beta/models")
    assert FAKE_KEY not in url and "key=" not in url, url
    assert ["-H", f"x-goog-api-key: {FAKE_KEY}"] == cmd[cmd.index("-H"):cmd.index("-H") + 2]
