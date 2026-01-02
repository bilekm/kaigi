"""Signal handling utilities for graceful shutdown."""

from __future__ import annotations

import signal
from contextlib import contextmanager
from typing import Any, Callable, Generator

# Type alias for signal handlers
SignalHandler = Callable[[int, Any], None]


@contextmanager
def signal_handler_context(
    handler: SignalHandler,
    signals: tuple[int, ...] = (signal.SIGTERM, signal.SIGINT),
) -> Generator[None, None, None]:
    """Context manager that temporarily installs signal handlers.

    Restores original handlers on exit, preventing signal handler pollution
    when this module is used as a library.

    Args:
        handler: The signal handler function to install.
        signals: Tuple of signals to handle (default: SIGTERM, SIGINT).

    Example:
        with signal_handler_context(_handle_signal):
            # ... code that may receive signals ...
            pass
        # Original handlers are restored here
    """
    original_handlers: dict[int, Any] = {}

    # Install new handlers
    for sig in signals:
        original_handlers[sig] = signal.signal(sig, handler)

    try:
        yield
    finally:
        # Restore original handlers
        for sig, original in original_handlers.items():
            signal.signal(sig, original)
