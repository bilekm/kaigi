"""ExecutionRecord and StepResult Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Status of a workflow execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Status of a step execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepResult(BaseModel):
    """Result of a single step execution."""

    step_id: str
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    exit_code: int | None = None
    output_path: str | None = None
    error_path: str | None = None
    error_message: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        """Calculate duration in seconds."""
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds()
        return None

    def start(self) -> None:
        """Mark step as started."""
        self.status = StepStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)

    def complete(self, exit_code: int, output_path: str, error_path: str) -> None:
        """Mark step as completed."""
        self.status = StepStatus.COMPLETED if exit_code == 0 else StepStatus.FAILED
        self.ended_at = datetime.now(timezone.utc)
        self.exit_code = exit_code
        self.output_path = output_path
        self.error_path = error_path

    def fail(self, error_message: str, exit_code: int | None = None) -> None:
        """Mark step as failed."""
        self.status = StepStatus.FAILED
        self.ended_at = datetime.now(timezone.utc)
        self.error_message = error_message
        if exit_code is not None:
            self.exit_code = exit_code

    def skip(self) -> None:
        """Mark step as skipped."""
        self.status = StepStatus.SKIPPED


class ExecutionRecord(BaseModel):
    """Tracks a single workflow run."""

    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    workflow_id: str
    workflow_name: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    current_step_index: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    steps: list[StepResult] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        """Calculate duration in seconds."""
        if self.started_at and self.ended_at:
            return (self.ended_at - self.started_at).total_seconds()
        elif self.started_at:
            return (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return None

    @property
    def current_step(self) -> StepResult | None:
        """Get the currently executing step."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_retryable(self) -> bool:
        """Check if execution can be retried."""
        return self.status in (ExecutionStatus.FAILED, ExecutionStatus.CANCELLED)

    def start(self) -> None:
        """Mark execution as started."""
        self.status = ExecutionStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)

    def complete(self) -> None:
        """Mark execution as completed successfully."""
        self.status = ExecutionStatus.COMPLETED
        self.ended_at = datetime.now(timezone.utc)

    def fail(self) -> None:
        """Mark execution as failed."""
        self.status = ExecutionStatus.FAILED
        self.ended_at = datetime.now(timezone.utc)

    def cancel(self) -> None:
        """Mark execution as cancelled."""
        self.status = ExecutionStatus.CANCELLED
        self.ended_at = datetime.now(timezone.utc)

    def get_step_result(self, step_id: str) -> StepResult | None:
        """Get step result by ID."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def get_failed_step_index(self) -> int | None:
        """Get the index of the first failed step."""
        for i, step in enumerate(self.steps):
            if step.status == StepStatus.FAILED:
                return i
        return None

    @classmethod
    def from_workflow(cls, workflow_id: str, workflow_name: str, step_ids: list[str]) -> ExecutionRecord:
        """Create a new execution record from workflow metadata."""
        return cls(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            steps=[StepResult(step_id=step_id) for step_id in step_ids],
        )
