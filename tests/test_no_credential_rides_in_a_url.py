"""No credential rides in a URL that this codebase builds.

A URL is what httpx logs at INFO (``HTTP Request: POST <url>``), what a proxy records and what an
``HTTPStatusError`` prints. The Gemini key in ``?key=`` reached 578 Cloud Run log lines in the 7 days
to 2026-10-01 (workers #3243; the command is in ``workers.common.log_redaction``). This is
kitesforu-qa's copy of the scan workers, api and course-workers carry (qa #182 review): the
reconciler built the same ``?key=`` URL here until #182.

It reads every module under ``scripts`` and ``src`` with ``ast`` (comments and docstrings cannot trip it) and finds
three shapes of URL credential:
  * an f-string whose literal part ends in ``?key=`` / ``&api_key=`` / ``&token=`` ... and is
    followed by a value: ``f"{base}?key={k}"``;
  * a string literal ending that way and concatenated: ``"...?key=" + k``;
  * a ``params={...}`` dict with a credential-named key: ``client.get(url, params={"key": k})``.

The allowlist is the URL credentials that remain, each with the reason it has no header form or has
not moved yet. Adding a line to it is a decision, written down here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIRS = (ROOT / "scripts", ROOT / "src")

_CRED_NAMES = ("key", "api_key", "apikey", "api-key", "access_token", "token", "client_secret")
_ENDS_IN_CRED = re.compile(r"[?&](" + "|".join(map(re.escape, _CRED_NAMES)) + r")=$", re.IGNORECASE)

#: (path relative to the repo, credential parameter) -> why it is still a URL credential. Keyed by
#: the parameter, not the line. Empty: none remains in qa. Adding a line is a decision, written here.
ALLOWED: dict = {}


def url_credential_sites(tree: ast.AST) -> list[tuple[int, str]]:
    """``(line, parameter)`` for each place ``tree`` builds a URL that carries a credential."""
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = node.values
            for literal, value in zip(parts, parts[1:]):
                if (
                    isinstance(literal, ast.Constant)
                    and isinstance(literal.value, str)
                    and _ENDS_IN_CRED.search(literal.value)
                    and isinstance(value, ast.FormattedValue)
                ):
                    sites.append((node.lineno, _ENDS_IN_CRED.search(literal.value).group(1).lower()))
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Add)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and _ENDS_IN_CRED.search(node.left.value)
        ):
            sites.append((node.lineno, _ENDS_IN_CRED.search(node.left.value).group(1).lower()))
        elif isinstance(node, ast.keyword) and node.arg == "params" and isinstance(node.value, ast.Dict):
            for key in node.value.keys:
                if isinstance(key, ast.Constant) and str(key.value).lower() in _CRED_NAMES:
                    sites.append((key.lineno, str(key.value).lower()))
    return sorted(set(sites))


def _all_sites() -> dict[tuple[str, str], list[str]]:
    """``(path, parameter)`` -> the ``path:line`` of each site."""
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(p for d in DIRS for p in d.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for line, name in url_credential_sites(ast.parse(path.read_text(encoding="utf-8"), filename=rel)):
            found.setdefault((rel, name), []).append(f"{rel}:{line}")
    return found


def test_no_new_url_credential_exists_in_src():
    found = _all_sites()
    unexpected = {site: where for site, where in found.items() if site not in ALLOWED}
    assert not unexpected, (
        "A credential is built into a URL. Send it in a header (Google: `x-goog-api-key`), or add "
        f"the site to ALLOWED with the reason it cannot move: {unexpected}"
    )


def test_every_allowed_site_still_exists():
    """A stale-allowlist check: each allowed site must still be found. If one moved to a header,
    delete its line here."""
    found = _all_sites()
    assert set(ALLOWED) <= set(found), f"stale ALLOWED entries: {set(ALLOWED) - set(found)}"


def test_the_scan_reads_the_tree():
    """With an empty allowlist the zero above is only a claim about the scan if the scan READ
    something: the reconciler alone proves it parses (its pre-#182 line was the one site)."""
    files = [p for d in DIRS for p in d.rglob("*.py")]
    assert len(files) >= 50, len(files)
    assert ROOT / "scripts" / "model_catalog_reconcile.py" in files


@pytest.mark.parametrize(
    "source",
    [
        'url = f"https://generativelanguage.googleapis.com/v1beta/models?key={k}"',
        'url = f"{base}:generateContent?key={self.api_key}"',
        'url = "https://example.com/v1?q=1&api_key=" + k',
        'client.get(url, params={"key": k, "q": "moon"})',
        'url = f"{base}?q={q}&token={t}"',
    ],
)
def test_the_scan_finds_each_shape(source):
    assert [line for line, _ in url_credential_sites(ast.parse(source))] == [1]


@pytest.mark.parametrize(
    "source",
    [
        '"""Never send `?key=`: the URL is logged."""',
        "# url = f'{base}?key={k}'",
        'headers = {"x-goog-api-key": k}',
        'msg = f"cache key={k}"',
        'client.get(url, params={"q": "moon", "pageSize": 1})',
    ],
)
def test_the_scan_ignores_what_is_not_a_url_credential(source):
    assert url_credential_sites(ast.parse(source)) == []
