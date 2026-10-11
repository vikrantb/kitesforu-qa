"""Where the tests find the PRODUCER: a kitesforu-workers tree and the interpreter that imports it.

The verification job prices a purchase with workers functions and ends a wait only on the workers' own by-design
no-video reasons (kitesforu-qa #175 rounds 5-6). Both live in the kitesforu-workers PR "a purchased hero clip is
quoted by the code that selects it". These helpers find a tree that HAS them: ``WORKERS_SRC`` when set, else the
first of kitesforu-workers ``origin/main`` and that PR's branch whose tree carries both files. Tests that need a
tree SKIP only when no checkout has one, and say so. Offline and $0.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

QA = Path(__file__).resolve().parents[1]
WORKSPACE_DEFAULT = Path("/Users/vikrantbhosale/gitprojects/kitesforu/kitesforu-workers")
SEAM = "src/workers/stages/visuals/hero_clip_choice.py"
SKIP_REASONS = "src/workers/stages/visuals/video_skip_reasons.py"
SEAM_BRANCH = "feat/a-purchase-is-quoted-by-the-code-that-selects-it"


def workers_repo() -> Optional[Path]:
    for cand in (os.environ.get("KFU_WORKERS_REPO"), QA.parent / "kitesforu-workers", WORKSPACE_DEFAULT):
        if cand and (Path(cand) / ".git").exists():
            return Path(cand)
    return None


def ref_with_seam(repo: Path) -> Optional[str]:
    """The first of origin/main and the seam branch whose tree carries the quote seam AND the no-video reasons,
    as a full SHA."""
    for ref in ("origin/main", SEAM_BRANCH, f"origin/{SEAM_BRANCH}"):
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                             capture_output=True, text=True).stdout.strip()
        if sha and all(subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}:{path}"],
                                      capture_output=True).returncode == 0 for path in (SEAM, SKIP_REASONS)):
            return sha
    return None


def workers_python() -> Optional[str]:
    explicit = os.environ.get("KFU_WORKERS_PYTHON")
    if explicit:
        return explicit
    repo = workers_repo()
    cand = repo / ".venv" / "bin" / "python" if repo else None
    return str(cand) if cand and cand.exists() else None
