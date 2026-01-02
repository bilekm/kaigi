"""会議 (Kaigi) - CLI-based multi-agent collaboration platform."""

from importlib.metadata import version

__version__ = version("kaigi")


def get_version() -> str:
    """Return the current version of the kaigi package."""
    return __version__


# Export key services for convenience
from kaigi.services.event_handler import (
    ConversationEventHandler,
    NullEventHandler,
)
from kaigi.services.cli_event_handler import CliEventHandler
from kaigi.services.container import (
    ServiceContainer,
    get_default_container,
    reset_default_container,
    ContainerContext,
)

__all__ = [
    "get_version",
    "ConversationEventHandler",
    "NullEventHandler",
    "CliEventHandler",
    "ServiceContainer",
    "get_default_container",
    "reset_default_container",
    "ContainerContext",
]
