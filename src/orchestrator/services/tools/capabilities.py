"""Capability checking and permission validation."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Literal

from orchestrator.services.tools.models import ToolCall


class CapabilityChecker:
    """Validates tool calls against agent permissions."""

    def __init__(self, permissions: list[str], workspace_root: Path | str):
        """Initialize capability checker.

        Args:
            permissions: List of permission patterns (e.g., ["read:*", "write:src/**"])
            workspace_root: Workspace root for path validation
        """
        self.permissions = permissions
        self.workspace_root = Path(workspace_root).resolve()

    def check_capability(
        self, tool_call: ToolCall
    ) -> tuple[Literal["granted", "denied"], str]:
        """Check if a tool call is allowed.

        Args:
            tool_call: The tool call to validate

        Returns:
            Tuple of (decision, reason)
            decision: "granted" or "denied"
            reason: Human-readable explanation
        """
        tool = tool_call.tool
        args = tool_call.args

        if tool == "read_file":
            return self._check_read_file(args.get("path", ""))
        elif tool == "write_file":
            return self._check_write_file(args.get("path", ""))
        elif tool == "exec_bash":
            return self._check_exec_bash(args.get("command", ""))
        else:
            return ("denied", f"Unknown tool: {tool}")

    def _check_read_file(self, path: str) -> tuple[Literal["granted", "denied"], str]:
        """Check read permission for a file path."""
        if not path:
            return ("denied", "No path specified")

        # Check against permission patterns
        for perm in self.permissions:
            if perm.startswith("read:"):
                pattern = perm[5:]  # Remove "read:" prefix
                if self._match_path_pattern(path, pattern):
                    return ("granted", f"Matched permission: {perm}")

        return ("denied", f"No read permission for: {path}")

    def _check_write_file(
        self, path: str
    ) -> tuple[Literal["granted", "denied"], str]:
        """Check write permission for a file path."""
        if not path:
            return ("denied", "No path specified")

        # Check against permission patterns
        for perm in self.permissions:
            if perm.startswith("write:"):
                pattern = perm[6:]  # Remove "write:" prefix
                if self._match_path_pattern(path, pattern):
                    return ("granted", f"Matched permission: {perm}")

        return ("denied", f"No write permission for: {path}")

    def _check_exec_bash(
        self, command: str
    ) -> tuple[Literal["granted", "denied"], str]:
        """Check bash execution permission for a command."""
        if not command:
            return ("denied", "No command specified")

        # Check against permission patterns
        for perm in self.permissions:
            if perm.startswith("bash:"):
                pattern = perm[5:]  # Remove "bash:" prefix

                # Pattern matching for commands
                # "bash:*" allows all commands
                # "bash:git *" allows only git commands
                if pattern == "*":
                    return ("granted", "Full bash access")

                # Match command prefix
                if command.strip().startswith(pattern.split()[0]):
                    return ("granted", f"Matched permission: {perm}")

        return ("denied", f"No bash permission for: {command[:50]}")

    def _match_path_pattern(self, path: str, pattern: str) -> bool:
        """Check if a path matches a permission pattern.

        Supports:
        - Wildcards: * (any files in directory)
        - Recursive: ** (any files in directory tree)
        - Exact match

        Args:
            path: File path to check
            pattern: Permission pattern

        Returns:
            True if path matches pattern
        """
        # Normalize paths
        try:
            # Handle both absolute and relative paths
            if Path(path).is_absolute():
                check_path = Path(path)
            else:
                check_path = self.workspace_root / path

            # Convert pattern to absolute if needed
            if Path(pattern).is_absolute():
                pattern_path = Path(pattern)
            else:
                pattern_path = self.workspace_root / pattern

            # Use glob-style matching
            # Convert to relative paths for pattern matching
            try:
                relative_check = check_path.relative_to(self.workspace_root)
                relative_pattern = pattern_path.relative_to(self.workspace_root)
            except ValueError:
                # Paths not relative to workspace - try direct match
                return fnmatch.fnmatch(str(check_path), str(pattern_path))

            # Match using fnmatch with glob patterns
            return fnmatch.fnmatch(
                str(relative_check), str(relative_pattern)
            ) or fnmatch.fnmatch(str(check_path), pattern)

        except Exception:
            # Fallback to simple string matching
            return fnmatch.fnmatch(path, pattern)


def format_capability_request(tool_call: ToolCall) -> str:
    """Format a capability request message for display.

    Args:
        tool_call: The tool call requesting capability

    Returns:
        Formatted string like "NEED: read_file(src/main.py)"
    """
    tool = tool_call.tool
    args = tool_call.args

    if tool == "read_file":
        path = args.get("path", "?")
        return f"NEED: read_file({path})"
    elif tool == "write_file":
        path = args.get("path", "?")
        return f"NEED: write_file({path})"
    elif tool == "exec_bash":
        cmd = args.get("command", "?")
        # Truncate long commands
        if len(cmd) > 50:
            cmd = cmd[:47] + "..."
        return f"NEED: exec_bash({cmd})"
    else:
        return f"NEED: {tool}({args})"


def format_capability_response(
    decision: Literal["granted", "denied"], reason: str
) -> str:
    """Format a capability response message.

    Args:
        decision: "granted" or "denied"
        reason: Explanation for the decision

    Returns:
        Formatted string like "GRANTED: Matched permission: read:*"
    """
    return f"{decision.upper()}: {reason}"
