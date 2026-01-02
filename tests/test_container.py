"""Tests for the dependency injection container."""

import pytest

from kaigi import (
    ServiceContainer,
    get_default_container,
    reset_default_container,
    ContainerContext,
    NullEventHandler,
)
from kaigi.services.store import WorkflowStore
from unittest.mock import Mock


class TestServiceContainer:
    """Tests for ServiceContainer class."""

    def test_init(self):
        """Test container initialization."""
        container = ServiceContainer()
        assert container._store is None
        assert container._event_handler is None

    def test_get_store_creates_default(self):
        """Test that get_store creates a default WorkflowStore."""
        container = ServiceContainer()
        store = container.get_store()

        assert isinstance(store, WorkflowStore)
        # Should return same instance on subsequent calls
        assert container.get_store() is store

    def test_set_store(self):
        """Test setting a custom store."""
        container = ServiceContainer()
        mock_store = Mock(spec=WorkflowStore)

        container.set_store(mock_store)
        result = container.get_store()

        assert result is mock_store

    def test_set_store_factory(self):
        """Test setting a store factory."""
        container = ServiceContainer()
        custom_store = WorkflowStore()

        def factory():
            return custom_store

        container.set_store_factory(factory)
        result = container.get_store()

        assert result is custom_store

    def test_get_event_handler_creates_default(self):
        """Test that get_event_handler creates a default NullEventHandler."""
        container = ServiceContainer()
        handler = container.get_event_handler()

        assert isinstance(handler, NullEventHandler)
        # Should return same instance on subsequent calls
        assert container.get_event_handler() is handler

    def test_set_event_handler(self):
        """Test setting a custom event handler."""
        container = ServiceContainer()
        mock_handler = Mock()

        container.set_event_handler(mock_handler)
        result = container.get_event_handler()

        assert result is mock_handler

    def test_set_event_handler_factory(self):
        """Test setting an event handler factory."""
        container = ServiceContainer()
        custom_handler = NullEventHandler()

        def factory():
            return custom_handler

        container.set_event_handler_factory(factory)
        result = container.get_event_handler()

        assert result is custom_handler

    def test_reset(self):
        """Test resetting the container."""
        container = ServiceContainer()
        container.get_store()
        container.get_event_handler()

        assert container._store is not None
        assert container._event_handler is not None

        container.reset()

        assert container._store is None
        assert container._event_handler is None

    def test_factory_overrides_default(self):
        """Test that factory overrides default creation."""
        container = ServiceContainer()
        custom_store = Mock(spec=WorkflowStore)

        container.set_store_factory(lambda: custom_store)
        result = container.get_store()

        assert result is custom_store


class TestGlobalContainer:
    """Tests for global default container."""

    def setup_method(self):
        """Reset global container before each test."""
        reset_default_container()

    def test_get_default_container_creates(self):
        """Test that get_default_container creates a container."""
        container = get_default_container()

        assert isinstance(container, ServiceContainer)

    def test_get_default_container_same_instance(self):
        """Test that get_default_container returns same instance."""
        container1 = get_default_container()
        container2 = get_default_container()

        assert container1 is container2

    def test_reset_default_container(self):
        """Test resetting the global container."""
        container1 = get_default_container()
        reset_default_container()
        container2 = get_default_container()

        assert container1 is not container2


class TestContainerContext:
    """Tests for ContainerContext manager."""

    def setup_method(self):
        """Reset global container before each test."""
        reset_default_container()

    def test_context_sets_temporary_container(self):
        """Test that context manager temporarily sets container."""
        original = get_default_container()
        temp_container = ServiceContainer()

        with ContainerContext(temp_container) as ctx:
            assert get_default_container() is temp_container
            assert ctx is temp_container

        # Original restored
        assert get_default_container() is original

    def test_context_restores_on_exception(self):
        """Test that context manager restores container even on exception."""
        original = get_default_container()
        temp_container = ServiceContainer()

        with pytest.raises(ValueError):
            with ContainerContext(temp_container):
                raise ValueError("test error")

        # Original should still be restored
        assert get_default_container() is original

    def test_nested_contexts(self):
        """Test nested context managers."""
        original = get_default_container()
        temp1 = ServiceContainer()
        temp2 = ServiceContainer()

        with ContainerContext(temp1):
            assert get_default_container() is temp1

            with ContainerContext(temp2):
                assert get_default_container() is temp2

            assert get_default_container() is temp1

        assert get_default_container() is original
