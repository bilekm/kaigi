"""YAML workflow parser with validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from orchestrator.lib.errors import workflow_invalid
from orchestrator.models.workflow import Workflow


def parse_workflow_file(path: Path) -> Workflow:
    """Parse a workflow from a YAML file.

    Args:
        path: Path to the workflow YAML file.

    Returns:
        Parsed and validated Workflow object.

    Raises:
        OrchestratorError: If file cannot be read or workflow is invalid.
    """
    if not path.exists():
        raise workflow_invalid(f"Workflow file not found: {path}")

    try:
        content = path.read_text()
    except OSError as e:
        raise workflow_invalid(f"Cannot read workflow file: {e}")

    return parse_workflow_yaml(content, source=str(path))


def parse_workflow_yaml(content: str, source: str = "<string>") -> Workflow:
    """Parse a workflow from YAML content.

    Args:
        content: YAML content string.
        source: Source identifier for error messages.

    Returns:
        Parsed and validated Workflow object.

    Raises:
        OrchestratorError: If YAML is invalid or workflow validation fails.
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise workflow_invalid(f"Invalid YAML in {source}: {e}")

    if not isinstance(data, dict):
        raise workflow_invalid(f"Workflow must be a YAML mapping, got: {type(data).__name__}")

    return parse_workflow_dict(data, source=source)


def parse_workflow_dict(data: dict[str, Any], source: str = "<dict>") -> Workflow:
    """Parse a workflow from a dictionary.

    Args:
        data: Workflow data dictionary.
        source: Source identifier for error messages.

    Returns:
        Parsed and validated Workflow object.

    Raises:
        OrchestratorError: If workflow validation fails.
    """
    try:
        return Workflow.model_validate(data)
    except ValidationError as e:
        errors = []
        for error in e.errors():
            loc = ".".join(str(x) for x in error["loc"])
            msg = error["msg"]
            errors.append(f"  - {loc}: {msg}")
        error_list = "\n".join(errors)
        raise workflow_invalid(
            f"Validation errors in {source}:\n{error_list}",
            details={"validation_errors": e.errors()},
        )


def validate_workflow_file(path: Path) -> tuple[bool, list[str]]:
    """Validate a workflow file without fully parsing it.

    Args:
        path: Path to the workflow YAML file.

    Returns:
        Tuple of (is_valid, list of error messages).
    """
    try:
        workflow = parse_workflow_file(path)
        return True, []
    except Exception as e:
        return False, [str(e)]
