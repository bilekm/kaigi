"""Tool policy configuration loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from kaigi.lib.logging import get_logger


class ToolPolicy(BaseModel):
    """Tool execution policy configuration."""

    # Default permissions for all agents (if not specified per-agent)
    default_permissions: list[str] = Field(
        default_factory=lambda: ["read:*"],
        description="Default permissions granted to agents",
    )

    # Blocked paths (never allow access, even with permissions)
    blocked_paths: list[str] = Field(
        default_factory=list,
        description="Paths that are always blocked (e.g., .env, credentials.json)",
    )

    # Allowed bash commands (allowlist mode)
    allowed_bash_commands: list[str] | None = Field(
        default=None,
        description="If specified, only these commands are allowed (e.g., ['git *', 'npm *'])",
    )

    # Max file size limits
    max_read_size_mb: int = Field(
        default=10,
        description="Maximum file size for read operations (MB)",
    )

    max_write_size_mb: int = Field(
        default=10,
        description="Maximum file size for write operations (MB)",
    )

    # Bash timeout
    bash_timeout_seconds: int = Field(
        default=30,
        description="Default timeout for bash commands (seconds)",
    )


def load_tool_policy(policy_path: Path | str | None = None) -> ToolPolicy:
    """Load tool policy from configuration file.

    Searches in order:
    1. Provided path (if given)
    2. .kaigi/tool_policy.yaml (project-specific)
    3. ~/.kaigi/tool_policy.yaml (global)
    4. Default policy (if no files found)

    Args:
        policy_path: Optional explicit path to policy file

    Returns:
        ToolPolicy configuration
    """
    logger = get_logger()

    # Build search paths
    search_paths = []

    if policy_path:
        search_paths.append(Path(policy_path))

    # Project-specific policy
    project_policy = Path.cwd() / ".kaigi" / "tool_policy.yaml"
    search_paths.append(project_policy)

    # Global policy
    global_policy = Path.home() / ".kaigi" / "tool_policy.yaml"
    search_paths.append(global_policy)

    # Try to load from each path
    for path in search_paths:
        if path.exists():
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}

                policy = ToolPolicy(**data)

                logger.info(
                    "Loaded tool policy",
                    path=str(path),
                    default_permissions=policy.default_permissions,
                    blocked_paths=policy.blocked_paths,
                )

                return policy

            except Exception as e:
                logger.error(
                    "Failed to load tool policy",
                    path=str(path),
                    error=str(e),
                )
                # Continue to next path

    # No policy found - use defaults
    logger.info("No tool policy found, using defaults")
    return ToolPolicy()


def create_default_policy_file(path: Path | str) -> None:
    """Create a default tool_policy.yaml file.

    Args:
        path: Path to create the file at
    """
    policy_path = Path(path)
    policy_path.parent.mkdir(parents=True, exist_ok=True)

    default_content = """# Tool Policy Configuration
# Controls what tools agents can use and how

# Default permissions for all agents (unless overridden in workflow)
default_permissions:
  - "read:*"                  # Read any file in workspace
  # - "write:src/**/*.py"     # Write to Python files in src/
  # - "bash:git *"            # Run git commands

# Paths that are always blocked (security)
blocked_paths:
  - ".env"
  - ".env.*"
  - "**/.env"
  - "**/credentials.json"
  - "**/*.key"
  - "**/*.pem"
  - "~/.ssh/*"

# Bash command allowlist (if specified, only these are allowed)
# If null/empty, all commands are allowed (subject to agent permissions)
allowed_bash_commands: null
# Examples:
# - "git *"
# - "npm *"
# - "pytest *"

# File size limits (MB)
max_read_size_mb: 10
max_write_size_mb: 10

# Bash timeout (seconds)
bash_timeout_seconds: 30
"""

    policy_path.write_text(default_content)

    logger = get_logger()
    logger.info("Created default tool policy", path=str(policy_path))
