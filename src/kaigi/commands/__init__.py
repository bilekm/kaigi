"""CLI commands for the orchestrator.

This package contains the main CLI commands split into modules:
- workflow.py: Pipeline workflow commands (run, status, list, etc.)
- conversation.py: Conversation mode commands (converse, say, transcript)
- agents.py: Agent management commands (start, stop, list, etc.)
"""

from kaigi.commands.workflow import (
    validate,
    run,
    status,
    list_executions,
    output,
    cancel,
    retry,
    cleanup,
)
from kaigi.commands.conversation import (
    converse,
    say,
    transcript,
)
from kaigi.commands.agents import (
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
