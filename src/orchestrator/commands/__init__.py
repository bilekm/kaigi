"""CLI commands for the orchestrator.

This package contains the main CLI commands split into modules:
- workflow.py: Pipeline workflow commands (run, status, list, etc.)
- conversation.py: Conversation mode commands (converse, say, transcript)
- agents.py: Agent management commands (start, stop, list, etc.)
"""

from orchestrator.commands.workflow import (
    validate,
    run,
    status,
    list_executions,
    output,
    cancel,
    retry,
    cleanup,
)
from orchestrator.commands.conversation import (
    converse,
    say,
    transcript,
)
from orchestrator.commands.agents import (
    agents,
)

__all__ = [
    # Workflow commands
    "validate",
    "run",
    "status",
    "list_executions",
    "output",
    "cancel",
    "retry",
    "cleanup",
    # Conversation commands
    "converse",
    "say",
    "transcript",
    # Agent commands
    "agents",
]
