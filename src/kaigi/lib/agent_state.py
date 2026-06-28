"""Persistent agent state tracking.

Tracks what information has been sent to each agent to avoid
sending duplicate context to agents with persistent memory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


class AgentState:
    """Tracks initialization state for a single agent."""

    def __init__(
        self,
        rules_sent: bool = False,
        project_sent: bool = False,
    ):
        self.rules_sent = rules_sent
        self.project_sent = project_sent

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules_sent": self.rules_sent,
            "project_sent": self.project_sent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentState":
        return cls(
            rules_sent=data.get("rules_sent", False),
            project_sent=data.get("project_sent", False),
        )


class AgentStateStore:
    """Persistent storage for agent initialization states.

    Stores state in .kaigi/.agent_state.yaml to track what context
    has been sent to each agent across kaigi sessions.
    """

    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root or Path.cwd()
        self.state_file = self.project_root / ".kaigi" / ".agent_state.yaml"
        self._workflow_hash: str | None = None
        self._agents: dict[str, AgentState] = {}
        self._load()

    def _load(self) -> None:
        """Load state from disk."""
        if not self.state_file.exists():
            return

        try:
            content = self.state_file.read_text()
            data = yaml.safe_load(content) or {}

            self._workflow_hash = data.get("workflow_hash")
            agents_data = data.get("agents", {})

            for agent_id, agent_data in agents_data.items():
                if isinstance(agent_data, dict):
                    self._agents[agent_id] = AgentState.from_dict(agent_data)
        except (yaml.YAMLError, OSError):
            # If file is corrupted, start fresh
            self._workflow_hash = None
            self._agents = {}

    def _save(self) -> None:
        """Save state to disk."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "workflow_hash": self._workflow_hash,
            "agents": {
                agent_id: state.to_dict()
                for agent_id, state in self._agents.items()
            },
        }

        self.state_file.write_text(yaml.dump(data, default_flow_style=False))

    def get_workflow_hash(self, workflow_content: str) -> str:
        """Compute hash of workflow content."""
        return hashlib.sha256(workflow_content.encode()).hexdigest()[:12]

    def check_workflow_changed(self, workflow_content: str) -> bool:
        """Check if workflow has changed since last run."""
        current_hash = self.get_workflow_hash(workflow_content)
        return self._workflow_hash != current_hash

    def update_workflow_hash(self, workflow_content: str) -> None:
        """Update stored workflow hash."""
        self._workflow_hash = self.get_workflow_hash(workflow_content)
        self._save()

    def get_agent_state(self, agent_id: str) -> AgentState:
        """Get state for an agent, creating if not exists."""
        if agent_id not in self._agents:
            self._agents[agent_id] = AgentState()
        return self._agents[agent_id]

    def mark_rules_sent(self, agent_id: str) -> None:
        """Mark that kaigi rules have been sent to agent."""
        state = self.get_agent_state(agent_id)
        state.rules_sent = True
        self._save()

    def mark_project_sent(self, agent_id: str) -> None:
        """Mark that project context has been sent to agent."""
        state = self.get_agent_state(agent_id)
        state.project_sent = True
        self._save()

    def needs_rules(self, agent_id: str) -> bool:
        """Check if agent needs kaigi rules."""
        return not self.get_agent_state(agent_id).rules_sent

    def needs_project(self, agent_id: str) -> bool:
        """Check if agent needs project context."""
        return not self.get_agent_state(agent_id).project_sent

    def reset(self) -> None:
        """Clear all agent states (force re-initialization)."""
        self._workflow_hash = None
        self._agents = {}
        if self.state_file.exists():
            self.state_file.unlink()

    def reset_agent(self, agent_id: str) -> None:
        """Reset state for a specific agent."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._save()

    def invalidate_all(self) -> None:
        """Invalidate all agents (workflow changed)."""
        for state in self._agents.values():
            state.rules_sent = False
            state.project_sent = False
        self._save()
