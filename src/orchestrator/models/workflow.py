"""Workflow and AgentStep Pydantic models."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


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


# === Conversation Mode Models ===


class ConversationAgent(BaseModel):
    """An AI agent participating in a conversation.

    Can be configured inline (command + args) or by reference (agent name from settings).
    """

    id: Annotated[str, Field(min_length=1, max_length=50)]

    # Reference to configured agent (from agents.yaml)
    agent: str | None = None

    # Inline configuration (used if 'agent' not specified)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=MIN_TIMEOUT, le=MAX_TIMEOUT)

    # Model selection (e.g., "gemini-3-pro-preview", "claude-sonnet-4-5")
    model: str | None = None

    # Agent personality for the conversation
    persona: str = ""

    # Permissions/capabilities granted to this agent
    # Format: list of permission patterns like "read:*", "write:src/**/*.py", "bash:git *"
    permissions: list[str] = Field(default_factory=lambda: ["read:*"])

    @field_validator("id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        """Validate agent ID format."""
        if not STEP_ID_PATTERN.match(v):
            raise ValueError(
                f"Agent ID must be 1-50 alphanumeric characters with hyphens/underscores, got: {v}"
            )
        return v

    def is_reference(self) -> bool:
        """Check if this agent is a reference to settings."""
        return self.agent is not None

    def get_resolved_command(self) -> str:
        """Get the command, resolving from settings if needed."""
        if self.command:
            return self.command
        raise ValueError(f"Agent '{self.id}' has no command configured")

    def get_resolved_args(self) -> list[str]:
        """Get the args, resolving from settings if needed."""
        return self.args

    def get_resolved_env(self) -> dict[str, str]:
        """Get the env, resolving from settings if needed."""
        return self.env


class ConversationWorkflow(BaseModel):
    """A conversation-mode workflow where agents discuss a topic."""

    name: Annotated[str, Field(min_length=1, max_length=100)]
    version: str = "1.0"
    mode: Literal["conversation"] = "conversation"
    description: str = ""

    agents: Annotated[list[ConversationAgent], Field(min_length=2)]
    topic: str = ""  # Optional: if empty, prompt user at runtime

    # Collaboration mode
    # - "team": All agents equal, round-robin, consensus-based (default)
    # - "orchestrated": One lead agent assigns tasks to others
    collaboration: Literal["team", "orchestrated"] = "team"
    lead: str | None = None  # Agent ID of lead (required for orchestrated)

    max_rounds: int = Field(default=10, ge=1, le=100)
    min_rounds: int = Field(default=2, ge=1, le=100)  # Minimum rounds before consensus allowed
    turn_order: Literal["round_robin", "flexible"] = "round_robin"
    context_files: list[str] = Field(default_factory=list)  # Glob patterns
    preload_context: bool = Field(default=False)  # Whether to pre-load context files into prompt

    consensus_keyword: str = "AGREED:"  # How agents signal agreement

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

    @field_validator("agents")
    @classmethod
    def validate_unique_agent_ids(
        cls, agents: list[ConversationAgent]
    ) -> list[ConversationAgent]:
        """Ensure all agent IDs are unique."""
        ids = [a.id for a in agents]
        duplicates = [id for id in ids if ids.count(id) > 1]
        if duplicates:
            raise ValueError(f"Duplicate agent IDs found: {set(duplicates)}")
        return agents

    @model_validator(mode="after")
    def validate_lead_agent(self) -> Self:
        """Validate lead agent for orchestrated mode."""
        if self.collaboration == "orchestrated":
            if not self.lead:
                raise ValueError(
                    "Lead agent must be specified for orchestrated collaboration. "
                    "Add 'lead: <agent-id>' to your workflow."
                )
            agent_ids = [a.id for a in self.agents]
            if self.lead not in agent_ids:
                raise ValueError(
                    f"Lead agent '{self.lead}' not found. "
                    f"Available agents: {', '.join(agent_ids)}"
                )
        return self

    @model_validator(mode="after")
    def validate_round_limits(self) -> Self:
        """Validate min_rounds does not exceed max_rounds."""
        if self.min_rounds > self.max_rounds:
            raise ValueError(
                f"min_rounds ({self.min_rounds}) cannot exceed "
                f"max_rounds ({self.max_rounds})"
            )
        return self

    def get_agent(self, agent_id: str) -> ConversationAgent | None:
        """Get an agent by ID."""
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        return None

    def get_lead_agent(self) -> ConversationAgent | None:
        """Get the lead agent for orchestrated mode."""
        if self.lead:
            return self.get_agent(self.lead)
        return None

    def get_team_agents(self) -> list[ConversationAgent]:
        """Get non-lead agents (for orchestrated mode)."""
        if self.collaboration == "orchestrated" and self.lead:
            return [a for a in self.agents if a.id != self.lead]
        return self.agents
