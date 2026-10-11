"""Pytest configuration and fixtures."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_script():
    """Sample podcast script for testing."""
    return """
Welcome to today's episode of Tech Talk! I'm your host, Sarah.

Today, we're diving into the fascinating world of artificial intelligence
and how it's transforming our daily lives.

First, let's talk about what AI actually is. At its core, artificial
intelligence refers to computer systems designed to perform tasks that
typically require human intelligence.

These tasks include things like recognizing speech, making decisions,
and even creative activities like writing and art.

One of the most exciting developments is in healthcare. AI systems can
now analyze medical images with incredible accuracy, sometimes even
outperforming human doctors in detecting certain conditions.

But it's not all perfect. There are important ethical considerations
we need to address as this technology evolves.

Thank you for listening to today's episode. I hope you learned something
new about AI. Until next time, keep exploring and stay curious!
"""


@pytest.fixture
def sample_request():
    """Sample user request for testing."""
    return "Create a 10-minute podcast about artificial intelligence and its impact on daily life"


@pytest.fixture
def mock_audio_path(temp_dir):
    """Create a mock audio file path (doesn't actually create audio)."""
    audio_path = os.path.join(temp_dir, "test_audio.mp3")
    # Create empty file
    Path(audio_path).touch()
    return audio_path


# ---- the producer (kitesforu-workers), for the verification job's tests (#175 rounds 5-6) ----------------

@pytest.fixture(scope="session")
def workers_src(tmp_path_factory) -> str:
    """``src/`` of a kitesforu-workers tree that carries the quote seam and the no-video reasons: ``WORKERS_SRC``
    when set, else an archive of the first ref that has both (``producer_tree.ref_with_seam``), once per session."""
    import subprocess

    from producer_tree import SEAM, SKIP_REASONS, ref_with_seam, workers_repo

    explicit = os.environ.get("WORKERS_SRC")
    if explicit:
        return explicit
    repo = workers_repo()
    if repo is None:
        pytest.skip("no kitesforu-workers checkout (set KFU_WORKERS_REPO or WORKERS_SRC): SKIPPED, not "
                    "passed. A purchase cannot be priced, nor a wait ended, without the producer.")
    sha = ref_with_seam(repo)
    if sha is None:
        pytest.skip(f"no kitesforu-workers ref carries {SEAM} and {SKIP_REASONS} (#175 depends on that PR): "
                    f"SKIPPED, not passed. Set WORKERS_SRC to a tree that has them.")
    tree = tmp_path_factory.mktemp("workers-with-seam")
    archive = subprocess.run(["git", "-C", str(repo), "archive", sha, "--", "src/workers",
                              "config/model_catalog.csv"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    assert (tree / "config" / "model_catalog.csv").stat().st_size > 10_000, "the extracted catalog is empty"
    print(f"workers_src: from {sha[:12]}")
    return str(tree / "src")


@pytest.fixture(scope="session")
def producer_reasons(workers_src) -> frozenset:
    """The producer's own by-design no-video reasons, read the way the wait reads them: ``verification_job.py
    no-video-reasons`` under the workers interpreter. Never a copy in a test."""
    import subprocess

    from producer_tree import QA, workers_python

    py = workers_python()
    if not py:
        pytest.skip("no kitesforu-workers venv (set KFU_WORKERS_PYTHON): SKIPPED, not passed")
    r = subprocess.run([py, str(QA / "scripts" / "verification_job.py"), "no-video-reasons",
                        f"--workers-src={workers_src}"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    reasons = frozenset(x for x in r.stdout.strip().split(",") if x)
    assert reasons, "the producer named no by-design reason"
    return reasons
