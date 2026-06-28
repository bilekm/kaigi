"""Error types and formatting for kaigi."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Standard error codes for kaigi errors."""

    AGENT_FAILED = "AGENT_FAILED"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_RATE_LIMITED = "AGENT_RATE_LIMITED"
    WORKFLOW_INVALID = "WORKFLOW_INVALID"
    WORKFLOW_LOCKED = "WORKFLOW_LOCKED"
    EXECUTION_NOT_FOUND = "EXECUTION_NOT_FOUND"
    STEP_NOT_FOUND = "STEP_NOT_FOUND"
    NOT_RETRYABLE = "NOT_RETRYABLE"
    IO_ERROR = "IO_ERROR"
    CONVERSATION_ERROR = "CONVERSATION_ERROR"
    NO_CONSENSUS = "NO_CONSENSUS"


@dataclass
class KaigiError(Exception):
    """Base error class for kaigi errors."""

    code: ErrorCode
    message: str
    source: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for JSON output."""
        result = {
            "error_code": self.code.value,
            "message": self.message,
        }
        if self.source:
            result["source"] = self.source
        if self.details:
            result["details"] = self.details
        return result

    def to_json(self) -> str:
        """Convert error to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def format_human(self) -> str:
        """Format error for human-readable output."""
        lines = [f"Error: {self.message}"]
        if self.source:
            lines.append(f"  Source: {self.source}")
        if self.details:
            for key, value in self.details.items():
                if key == "stderr" and value:
                    lines.append(f"\n  stderr output:")
                    for line in str(value).strip().split("\n"):
                        lines.append(f"    {line}")
                else:
                    lines.append(f"  {key}: {value}")
        return "\n".join(lines)


def format_error(error: KaigiError, use_json: bool = False) -> str:
    """Format an error for output."""
    if use_json:
        return error.to_json()
    return error.format_human()


def print_error(error: KaigiError, use_json: bool = False) -> None:
    """Print an error to stderr."""
    print(format_error(error, use_json), file=sys.stderr)


# Convenience functions for creating common errors
def agent_failed(
    agent_id: str,
    exit_code: int,
    stderr: str = "",
    workflow_name: str = "",
    execution_id: str = "",
) -> KaigiError:
    """Create an AGENT_FAILED error."""
    source_parts = []
    if workflow_name:
        source_parts.append(f"workflow:{workflow_name}")
    if execution_id:
        source_parts.append(f"execution:{execution_id}")
    source_parts.append(f"step:{agent_id}")

    return KaigiError(
        code=ErrorCode.AGENT_FAILED,
        message=f"Agent '{agent_id}' exited with code {exit_code}",
        source="/".join(source_parts),
        details={"exit_code": exit_code, "stderr": stderr} if stderr else {"exit_code": exit_code},
    )


def agent_timeout(agent_id: str, timeout_seconds: int) -> KaigiError:
    """Create an AGENT_TIMEOUT error."""
    return KaigiError(
        code=ErrorCode.AGENT_TIMEOUT,
        message=f"Agent '{agent_id}' exceeded timeout of {timeout_seconds} seconds",
        details={"timeout_seconds": timeout_seconds},
    )


def workflow_invalid(message: str, details: dict[str, Any] | None = None) -> KaigiError:
    """Create a WORKFLOW_INVALID error."""
    return KaigiError(
        code=ErrorCode.WORKFLOW_INVALID,
        message=message,
        details=details or {},
    )


def workflow_locked() -> KaigiError:
    """Create a WORKFLOW_LOCKED error."""
    return KaigiError(
        code=ErrorCode.WORKFLOW_LOCKED,
        message="Another workflow is already running. Only one workflow can run at a time.",
    )


def execution_not_found(execution_id: str) -> KaigiError:
    """Create an EXECUTION_NOT_FOUND error."""
    return KaigiError(
        code=ErrorCode.EXECUTION_NOT_FOUND,
        message=f"Execution '{execution_id}' not found",
        details={"execution_id": execution_id},
    )


def step_not_found(step_id: str, execution_id: str) -> KaigiError:
    """Create a STEP_NOT_FOUND error."""
    return KaigiError(
        code=ErrorCode.STEP_NOT_FOUND,
        message=f"Step '{step_id}' not found in execution '{execution_id}'",
        details={"step_id": step_id, "execution_id": execution_id},
    )


def not_retryable(execution_id: str, status: str) -> KaigiError:
    """Create a NOT_RETRYABLE error."""
    return KaigiError(
        code=ErrorCode.NOT_RETRYABLE,
        message=f"Execution '{execution_id}' is not in a retryable state (current: {status})",
        details={"execution_id": execution_id, "status": status},
    )


def io_error(message: str, path: str = "") -> KaigiError:
    """Create an IO_ERROR error."""
    return KaigiError(
        code=ErrorCode.IO_ERROR,
        message=message,
        details={"path": path} if path else {},
    )


def conversation_error(
    message: str, details: dict[str, Any] | None = None
) -> KaigiError:
    """Create a CONVERSATION_ERROR error."""
    return KaigiError(
        code=ErrorCode.CONVERSATION_ERROR,
        message=message,
        details=details or {},
    )


def no_consensus(execution_id: str, rounds: int) -> KaigiError:
    """Create a NO_CONSENSUS error."""
    return KaigiError(
        code=ErrorCode.NO_CONSENSUS,
        message=f"Conversation '{execution_id}' ended after {rounds} rounds without consensus",
        details={"execution_id": execution_id, "rounds": rounds},
    )


def agent_rate_limited(agent_id: str, reset_time: str = "") -> KaigiError:
    """Create an AGENT_RATE_LIMITED error."""
    message = f"Agent '{agent_id}' hit rate limit"
    if reset_time:
        message += f" (resets {reset_time})"
    return KaigiError(
        code=ErrorCode.AGENT_RATE_LIMITED,
        message=message,
        details={"agent_id": agent_id, "reset_time": reset_time} if reset_time else {"agent_id": agent_id},
    )


# Rate limit detection patterns for different agents
RATE_LIMIT_PATTERNS = [
    # Claude
    r"You've hit your limit",
    r"rate limit",
    r"resets?\s*\d+[ap]m",
    r"resets?\s*\d+:\d+",
    r"quota exceeded",
    # OpenAI/Copilot
    r"Rate limit reached",
    r"Too many requests",
    r"429",
    # Generic
    r"limit exceeded",
    r"try again later",
    r"throttl",
]


def detect_rate_limit(output: str) -> tuple[bool, str]:
    """Detect if output indicates a rate limit error.

    Args:
        output: Agent output or error message

    Returns:
        Tuple of (is_rate_limited, reset_time_if_found)
    """
    import re

    for pattern in RATE_LIMIT_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            # Try to extract reset time
            reset_match = re.search(r'resets?\s*(\d+[ap]m|\d+:\d+)', output, re.IGNORECASE)
            reset_time = reset_match.group(1) if reset_match else ""
            return True, reset_time

    return False, ""
