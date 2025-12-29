"""Orchestrator - CLI-based workflow orchestrator for agent coordination."""

from importlib.metadata import version

__version__ = version("orchestrator")

# Export key services for convenience
from orchestrator.services.event_handler import (
    ConversationEventHandler,
    NullEventHandler,
)
from orchestrator.services.cli_event_handler import CliEventHandler
from orchestrator.services.container import (
    ServiceContainer,
    get_default_container,
    reset_default_container,
    ContainerContext,
)

__all__ = [
    "ConversationEventHandler",
    "NullEventHandler",
    "CliEventHandler",
    "ServiceContainer",
    "get_default_container",
    "reset_default_container",
    "ContainerContext",
]
