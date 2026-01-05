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
