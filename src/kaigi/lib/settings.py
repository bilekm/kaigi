"""Agent settings management."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from kaigi.lib.errors import workflow_invalid


# Pattern to match ${ENV_VAR} or $ENV_VAR
ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}|\$([A-Z_][A-Z0-9_]*)")


class AgentConfig(BaseModel):
    """Configuration for an AI agent."""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=300, ge=1, le=86400)
    description: str = ""
    model: str | None = None  # Model to use (e.g., "gemini-3-pro-preview")
    spawn_mode: bool = Field(
        default=False,
        description="Use spawn-per-prompt mode instead of persistent PTY. "
        "Required for agents that don't work well with PTY (claude, copilot, etc.)"
    )

    def resolve_env_vars(self) -> "AgentConfig":
        """Resolve environment variable references in env dict.

        Supports ${VAR} and $VAR syntax.
        """
        resolved_env = {}
        for key, value in self.env.items():
            resolved_env[key] = _expand_env_vars(value)

        return AgentConfig(
            command=self.command,
            args=self.args,
            env=resolved_env,
            timeout=self.timeout,
            description=self.description,
            model=self.model,
            spawn_mode=self.spawn_mode,
        )


class AgentSettings(BaseModel):
    """Collection of agent configurations."""

    agents: dict[str, AgentConfig] = Field(default_factory=dict)

    def get_agent(self, name: str) -> AgentConfig | None:
        """Get agent configuration by name."""
        return self.agents.get(name)

    def list_agents(self) -> list[str]:
        """List all configured agent names."""
        return list(self.agents.keys())

    def merge(self, other: "AgentSettings") -> "AgentSettings":
        """Merge another settings object, with other taking precedence."""
        merged_agents = {**self.agents, **other.agents}
        return AgentSettings(agents=merged_agents)


def _expand_env_vars(value: str) -> str:
    """Expand environment variable references in a string."""

    def replace(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        env_value = os.environ.get(var_name, "")
        if not env_value:
            # Keep original if not found (user might set it later)
            return match.group(0)
        return env_value

    return ENV_VAR_PATTERN.sub(replace, value)


def get_global_settings_path() -> Path:
    """Get path to global agent settings file."""
    orchestrator_home = os.environ.get("ORCHESTRATOR_HOME")
    if orchestrator_home:
        return Path(orchestrator_home) / "agents.yaml"
    return Path.home() / ".kaigi" / "agents.yaml"


def get_project_settings_path() -> Path | None:
    """Get path to project-specific agent settings file.

    Searches current directory and parents for .kaigi/agents.yaml
    """
    current = Path.cwd()

    while current != current.parent:
        project_settings = current / ".kaigi" / "agents.yaml"
        if project_settings.exists():
            return project_settings
        current = current.parent

    # Check current directory directly
    direct_path = Path.cwd() / ".kaigi" / "agents.yaml"
    if direct_path.exists():
        return direct_path

    return None


def load_settings_file(path: Path) -> AgentSettings:
    """Load agent settings from a YAML file."""
    if not path.exists():
        return AgentSettings()

    try:
        content = path.read_text()
        data = yaml.safe_load(content)

        if not data:
            return AgentSettings()

        if not isinstance(data, dict):
            raise workflow_invalid(f"Settings file must be a YAML mapping: {path}")

        agents_data = data.get("agents", {})
        if not isinstance(agents_data, dict):
            raise workflow_invalid(f"'agents' must be a mapping in: {path}")

        agents = {}
        for name, config in agents_data.items():
            if not isinstance(config, dict):
                raise workflow_invalid(
                    f"Agent '{name}' configuration must be a mapping in: {path}"
                )
            try:
                agents[name] = AgentConfig.model_validate(config)
            except Exception as e:
                raise workflow_invalid(
                    f"Invalid agent configuration for '{name}' in {path}: {e}"
                )

        return AgentSettings(agents=agents)

    except yaml.YAMLError as e:
        raise workflow_invalid(f"Invalid YAML in settings file {path}: {e}")


def load_agent_settings() -> AgentSettings:
    """Load agent settings from global and project files.

    Project settings override global settings.
    """
    # Load global settings
    global_path = get_global_settings_path()
    global_settings = load_settings_file(global_path)

    # Load project settings
    project_path = get_project_settings_path()
    if project_path:
        project_settings = load_settings_file(project_path)
        return global_settings.merge(project_settings)

    return global_settings


def get_agent_config(name: str) -> AgentConfig | None:
    """Get a specific agent configuration by name.

    Convenience function that loads settings and retrieves the agent.
    """
    settings = load_agent_settings()
    agent = settings.get_agent(name)
    if agent:
        return agent.resolve_env_vars()
    return None


def list_available_agents() -> list[tuple[str, AgentConfig]]:
    """List all available agents with their configurations."""
    settings = load_agent_settings()
    return [(name, config) for name, config in settings.agents.items()]


def ensure_global_settings_dir() -> Path:
    """Ensure the global settings directory exists."""
    path = get_global_settings_path().parent
    path.mkdir(parents=True, exist_ok=True)
    return path
