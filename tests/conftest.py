"""Shared pytest fixtures for kaigi tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def kaigi_home(temp_dir, monkeypatch):
    """Set up isolated kaigi home directory for tests."""
    home = temp_dir / ".kaigi"
    home.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(home))
    return home


@pytest.fixture
def sample_workflow_yaml():
    """Return a simple valid workflow YAML string."""
    return """
name: test-workflow
version: "1.0"
steps:
  - id: step1
    command: echo
    args: ["hello"]
  - id: step2
    command: cat
"""


@pytest.fixture
def sample_workflow_file(temp_dir, sample_workflow_yaml):
    """Create a sample workflow file and return its path."""
    workflow_path = temp_dir / "workflow.yaml"
    workflow_path.write_text(sample_workflow_yaml)
    return workflow_path
