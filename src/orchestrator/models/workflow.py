"""Workflow and AgentStep Pydantic models."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


# Validation patterns
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
STEP_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,49}$")

# Default timeout in seconds (5 minutes)
DEFAULT_TIMEOUT = 300

# Timeout bounds
MIN_TIMEOUT = 1
MAX_TIMEOUT = 86400  # 24 hours


class AgentStep(BaseModel):
    """A single agent invocation within a workflow."""

    id: Annotated[str, Field(min_length=1, max_length=50)]
    command: Annotated[str, Field(min_length=1)]
    args: list[str] = Field(default_factory=list)
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=MIN_TIMEOUT, le=MAX_TIMEOUT)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_step_id(cls, v: str) -> str:
        """Validate step ID format."""
        if not STEP_ID_PATTERN.match(v):
            raise ValueError(
                f"Step ID must be 1-50 alphanumeric characters with hyphens/underscores, got: {v}"
            )
        return v


class Workflow(BaseModel):
    """A named, ordered sequence of agent steps defined in YAML."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    version: str = "1.0"
    description: str = ""
    steps: Annotated[list[AgentStep], Field(min_length=1)]

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate workflow name format."""
        if not NAME_PATTERN.match(v):
            raise ValueError(
                f"Workflow name must be 1-100 alphanumeric characters with hyphens/underscores, "
                f"got: {v}"
            )
        return v

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        """Validate version format."""
        if v not in ("1.0",):
            raise ValueError(f"Unsupported workflow version: {v}. Supported: 1.0")
        return v

    @field_validator("steps")
    @classmethod
    def validate_unique_step_ids(cls, steps: list[AgentStep]) -> list[AgentStep]:
        """Ensure all step IDs are unique."""
        ids = [step.id for step in steps]
        duplicates = [id for id in ids if ids.count(id) > 1]
        if duplicates:
            raise ValueError(f"Duplicate step IDs found: {set(duplicates)}")
        return steps

    def get_step(self, step_id: str) -> AgentStep | None:
        """Get a step by ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_step_index(self, step_id: str) -> int | None:
        """Get the index of a step by ID."""
        for i, step in enumerate(self.steps):
            if step.id == step_id:
                return i
        return None
