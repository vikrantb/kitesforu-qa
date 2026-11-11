"""No committed file in this public repo carries an account id: `user_` then 20 or more base62 characters.

The account-id half of kitesforu-decider's tests/test_the_repo_holds_no_identifiers.py. Its job-id, email and hex
shapes are not enforced here yet: this repo still holds some, and removing them is a separate change.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ID = re.compile(r"user_[A-Za-z0-9]{20,}")
# The positive control: a scan that does not reach these files has scanned the wrong population.
KNOWN_COMMITTED_FILES = {"README.md", "pyproject.toml", "tests/test_audio_overhaul_pipeline_invariants.py"}
TOOL_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})


def committed_files(root: Path) -> list[str]:
    """The files git would commit under root; in a tree exported without git metadata, every file but tool caches."""
    if (root / ".git").exists():
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
        )
        return sorted({name for name in listing.stdout.decode("utf-8").split("\0") if name})
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not TOOL_CACHE_DIRS.intersection(path.relative_to(root).parts)
    )


def test_no_committed_file_holds_an_account_id():
    files = committed_files(REPO_ROOT)
    assert KNOWN_COMMITTED_FILES <= set(files), "the scan missed files it must reach"
    holding = [name for name in files
               if (REPO_ROOT / name).is_file()
               and ACCOUNT_ID.search((REPO_ROOT / name).read_text(encoding="utf-8", errors="ignore"))]
    assert holding == [], f"account ids in committed files: {holding}"
