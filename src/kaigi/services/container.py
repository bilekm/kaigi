"""Simple dependency injection container for orchestrator services.

This module provides a lightweight dependency container that makes it easy to:
1. Wire up dependencies without circular imports
2. Swap implementations for testing
3. Share state across components
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar, cast
from dataclasses import dataclass, field

from kaigi.services.event_handler import ConversationEventHandler, NullEventHandler
from kaigi.services.store import WorkflowStore


T = TypeVar("T")


@dataclass
class ServiceContainer:
    """Simple dependency injection container.

    Provides lazy-initialized singleton services with support for
    overriding dependencies (useful for testing).

    Usage:
        container = ServiceContainer()
        store = container.get_store()

        # For testing
        mock_store = Mock()
        container.set_store(mock_store)
    """

    _store: WorkflowStore | None = field(default=None, repr=False)
    _event_handler: ConversationEventHandler | None = field(default=None, repr=False)

    # Factory functions for lazy initialization
    _store_factory: Callable[[], WorkflowStore] | None = field(default=None, repr=False)
    _event_handler_factory: Callable[[], ConversationEventHandler] | None = field(default=None, repr=False)

    def get_store(self) -> WorkflowStore:
        """Get or create the WorkflowStore."""
        if self._store is None:
            if self._store_factory:
                self._store = self._store_factory()
            else:
                self._store = WorkflowStore()
        return self._store

    def set_store(self, store: WorkflowStore) -> None:
        """Set the WorkflowStore (useful for testing)."""
        self._store = store

    def set_store_factory(self, factory: Callable[[], WorkflowStore]) -> None:
        """Set a factory function for creating the WorkflowStore."""
        self._store_factory = factory
        self._store = None  # Reset to force re-initialization

    def get_event_handler(self) -> ConversationEventHandler:
        """Get or create the event handler."""
        if self._event_handler is None:
            if self._event_handler_factory:
                self._event_handler = self._event_handler_factory()
            else:
                self._event_handler = NullEventHandler()
        return self._event_handler

    def set_event_handler(self, handler: ConversationEventHandler) -> None:
        """Set the event handler (useful for testing)."""
        self._event_handler = handler

    def set_event_handler_factory(self, factory: Callable[[], ConversationEventHandler]) -> None:
        """Set a factory function for creating the event handler."""
        self._event_handler_factory = factory
        self._event_handler = None  # Reset to force re-initialization

    def reset(self) -> None:
        """Reset all services to None (useful for testing)."""
        self._store = None
        self._event_handler = None


# Global default container
_default_container: ServiceContainer | None = None


def get_default_container() -> ServiceContainer:
    """Get the global default service container."""
    global _default_container
    if _default_container is None:
        _default_container = ServiceContainer()
    return _default_container


def reset_default_container() -> None:
    """Reset the global default container (useful for testing)."""
    global _default_container
    if _default_container:
        _default_container.reset()
    _default_container = None


class ContainerContext:
    """Context manager for using a temporary container.

    Usage:
        with ContainerContext(test_container):
            # Code here uses test_container
            result = some_function()

        # Original container restored
    """

    def __init__(self, container: ServiceContainer):
        self.container = container
        self._original: ServiceContainer | None = None

    def __enter__(self) -> ServiceContainer:
        global _default_container
        self._original = _default_container
        _default_container = self.container
        return self.container

    def __exit__(self, *args: Any) -> None:
        global _default_container
        _default_container = self._original
