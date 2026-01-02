"""Structured JSON logging for the orchestrator."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class LogLevel(str, Enum):
    """Log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Logger:
    """Structured JSON logger that writes to stderr."""

    def __init__(
        self,
        workflow_id: str | None = None,
        step_id: str | None = None,
        enabled: bool = True,
    ):
        self.workflow_id = workflow_id
        self.step_id = step_id
        self.enabled = enabled

    def with_context(
        self,
        workflow_id: str | None = None,
        step_id: str | None = None,
    ) -> Logger:
        """Create a new logger with additional context."""
        return Logger(
            workflow_id=workflow_id or self.workflow_id,
            step_id=step_id or self.step_id,
            enabled=self.enabled,
        )

    def _log(
        self,
        level: LogLevel,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Write a log entry to stderr."""
        if not self.enabled:
            return

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.value,
            "message": message,
        }

        if self.workflow_id:
            entry["workflow_id"] = self.workflow_id
        if self.step_id:
            entry["step_id"] = self.step_id
        if context:
            entry["context"] = context

        print(json.dumps(entry), file=sys.stderr)

    def debug(self, message: str, **context: Any) -> None:
        """Log a debug message."""
        self._log(LogLevel.DEBUG, message, context if context else None)

    def info(self, message: str, **context: Any) -> None:
        """Log an info message."""
        self._log(LogLevel.INFO, message, context if context else None)

    def warning(self, message: str, **context: Any) -> None:
        """Log a warning message."""
        self._log(LogLevel.WARNING, message, context if context else None)

    def error(self, message: str, **context: Any) -> None:
        """Log an error message."""
        self._log(LogLevel.ERROR, message, context if context else None)


# Default logger instance
_default_logger: Logger | None = None


def get_logger() -> Logger:
    """Get the default logger instance."""
    global _default_logger
    if _default_logger is None:
        _default_logger = Logger()
    return _default_logger


def set_logger(logger: Logger) -> None:
    """Set the default logger instance."""
    global _default_logger
    _default_logger = logger


def disable_logging() -> None:
    """Disable logging globally."""
    global _default_logger
    _default_logger = Logger(enabled=False)
