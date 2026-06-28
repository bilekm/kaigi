"""Keyboard input handling for non-blocking key detection.

Provides ESC key detection during agent execution without blocking.
Uses a background thread to monitor stdin in raw mode.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import threading
import tty
from contextlib import contextmanager
from typing import Callable, Generator

# ESC key code
ESC_KEY = 27


class KeyboardMonitor:
    """Monitor keyboard for ESC key press in background thread.

    Usage:
        monitor = KeyboardMonitor(on_esc=lambda: set_paused())
        monitor.start()
        # ... do work ...
        monitor.stop()

    Or use as context manager:
        with KeyboardMonitor(on_esc=callback):
            # ... do work ...
    """

    def __init__(self, on_esc: Callable[[], None]):
        """Initialize keyboard monitor.

        Args:
            on_esc: Callback function to call when ESC is pressed
        """
        self.on_esc = on_esc
        self._running = False
        self._thread: threading.Thread | None = None
        self._old_settings: list | None = None

    def start(self) -> None:
        """Start monitoring keyboard in background thread."""
        if self._running:
            return

        # Only monitor if stdin is a TTY
        if not sys.stdin.isatty():
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop monitoring keyboard."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None

    def _monitor_loop(self) -> None:
        """Background thread loop to monitor for ESC key."""
        # Save terminal settings
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
        except (termios.error, ValueError, OSError):
            # Not a terminal or error getting settings
            return

        try:
            # Set terminal to raw mode (but don't echo)
            tty.setcbreak(fd)

            while self._running:
                # Check if input is available (non-blocking)
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    try:
                        ch = sys.stdin.read(1)
                        if ch and ord(ch) == ESC_KEY:
                            self.on_esc()
                    except (IOError, OSError):
                        break
        except (termios.error, ValueError, OSError):
            pass
        finally:
            # Restore terminal settings
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            except (termios.error, ValueError, OSError):
                pass

    def __enter__(self) -> "KeyboardMonitor":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.stop()


@contextmanager
def esc_monitor(callback: Callable[[], None]) -> Generator[KeyboardMonitor, None, None]:
    """Context manager for ESC key monitoring.

    Args:
        callback: Function to call when ESC is pressed

    Example:
        with esc_monitor(lambda: print("ESC pressed!")):
            # ... do work ...
    """
    monitor = KeyboardMonitor(on_esc=callback)
    monitor.start()
    try:
        yield monitor
    finally:
        monitor.stop()


def is_terminal() -> bool:
    """Check if stdin is a terminal (supports keyboard input)."""
    return sys.stdin.isatty()
