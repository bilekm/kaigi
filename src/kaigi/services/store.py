"""File-based state store for workflow executions."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from kaigi.lib.errors import execution_not_found, io_error, workflow_locked
from kaigi.lib.logging import get_logger
from kaigi.models.execution import (
    ConversationRecord,
    ExecutionRecord,
    ExecutionStatus,
)
from kaigi.models.workflow import ConversationWorkflow, Workflow


def _atomic_write(path: Path, content: str) -> None:
    """Write content to file atomically using temp file + rename.

    This prevents corruption if the process crashes during write.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content)
        os.replace(tmp_path, path)  # Atomic on POSIX
    except Exception:
        # Clean up temp file on failure
        tmp_path.unlink(missing_ok=True)
        raise


def get_kaigi_home() -> Path:
    """Get the kaigi home directory."""
    home = os.environ.get("KAIGI_HOME")
    if home:
        return Path(home)
    return Path.home() / ".kaigi"


def ensure_kaigi_home() -> Path:
    """Ensure kaigi home directory exists."""
    home = get_kaigi_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


class WorkflowStore:
    """File-based store for workflows and executions."""

    def __init__(self, base_path: Path | None = None):
        self.base_path = base_path or ensure_kaigi_home()
        self.workflows_path = self.base_path / "workflows"
        self.workflows_path.mkdir(parents=True, exist_ok=True)
        self._global_lock_path = self.base_path / "kaigi.lock"
        self._logger = get_logger()

    # === Global Lock (Sequential Execution) ===

    def check_stale_lock(self) -> str | None:
        """Check for and clean up stale locks from crashed processes.

        Returns:
            The execution ID that was holding the stale lock, or None.
        """
        if not self._global_lock_path.exists():
            return None

        execution_id = self.get_lock_holder()
        if not execution_id:
            # Lock file exists but is empty or unreadable - clean it up
            self.release_global_lock()
            return None

        # Check if the execution is still running
        result = self.get_execution_by_id(execution_id)
        if result:
            _, execution = result
            if execution.status == ExecutionStatus.RUNNING:
                # Execution claims to be running - check if it's stale
                # (e.g., started more than 24 hours ago with no updates)
                if execution.started_at:
                    age = (datetime.now(timezone.utc) - execution.started_at).total_seconds()
                    if age > 86400:  # 24 hours
                        # Definitely stale
                        execution.status = ExecutionStatus.FAILED
                        self.save_execution(result[0], execution)
                        self.release_global_lock()
                        return execution_id
                    # Still might be running, don't clean up
                    return None
            else:
                # Execution finished but lock wasn't released - clean up
                self.release_global_lock()
                return execution_id
        else:
            # Execution doesn't exist - orphaned lock
            self.release_global_lock()
            return execution_id

        return None

    def acquire_global_lock(self, execution_id: str) -> bool:
        """Acquire global lock for sequential execution.

        Returns:
            True if lock acquired, False if already locked.
        """
        # First, check for stale locks
        self.check_stale_lock()

        if self._global_lock_path.exists():
            return False
        try:
            self._global_lock_path.write_text(execution_id)
            return True
        except OSError:
            return False

    def release_global_lock(self) -> None:
        """Release global lock."""
        try:
            self._global_lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def get_lock_holder(self) -> str | None:
        """Get the execution ID holding the global lock."""
        if self._global_lock_path.exists():
            try:
                return self._global_lock_path.read_text().strip()
            except OSError:
                return None
        return None

    def is_locked(self) -> bool:
        """Check if global lock is held."""
        return self._global_lock_path.exists()

    # === Workflow Storage ===

    def get_workflow_path(self, workflow_id: str) -> Path:
        """Get path to workflow directory."""
        return self.workflows_path / workflow_id

    def save_workflow(self, workflow_id: str, workflow: Workflow, yaml_content: str) -> Path:
        """Save workflow definition.

        Returns:
            Path to saved workflow.yaml file.
        """
        workflow_dir = self.get_workflow_path(workflow_id)
        workflow_dir.mkdir(parents=True, exist_ok=True)

        workflow_file = workflow_dir / "workflow.yaml"
        workflow_file.write_text(yaml_content)

        return workflow_file

    # === Execution Storage ===

    def get_execution_path(self, workflow_id: str, execution_id: str) -> Path:
        """Get path to execution directory."""
        return self.get_workflow_path(workflow_id) / "executions" / execution_id

    def get_steps_path(self, workflow_id: str, execution_id: str) -> Path:
        """Get path to steps output directory."""
        return self.get_execution_path(workflow_id, execution_id) / "steps"

    def create_execution(self, workflow_id: str, execution: ExecutionRecord) -> Path:
        """Create a new execution record.

        Returns:
            Path to execution directory.
        """
        exec_path = self.get_execution_path(workflow_id, execution.id)
        exec_path.mkdir(parents=True, exist_ok=True)

        steps_path = exec_path / "steps"
        steps_path.mkdir(exist_ok=True)

        self.save_execution(workflow_id, execution)
        return exec_path

    def save_execution(self, workflow_id: str, execution: ExecutionRecord) -> None:
        """Save execution record to disk atomically."""
        exec_path = self.get_execution_path(workflow_id, execution.id)
        exec_file = exec_path / "execution.json"
        _atomic_write(exec_file, execution.model_dump_json(indent=2))

    def load_execution(self, workflow_id: str, execution_id: str) -> ExecutionRecord:
        """Load execution record from disk."""
        exec_path = self.get_execution_path(workflow_id, execution_id)
        exec_file = exec_path / "execution.json"

        if not exec_file.exists():
            raise execution_not_found(execution_id)

        try:
            data = json.loads(exec_file.read_text())
            return ExecutionRecord.model_validate(data)
        except (json.JSONDecodeError, OSError) as e:
            raise io_error(f"Cannot read execution file: {e}", str(exec_file))

    def _load_execution_safe(
        self, workflow_id: str, execution_id: str
    ) -> ExecutionRecord | None:
        """Load execution record with defensive error handling.

        Returns None and logs error if file is corrupted.
        """
        exec_path = self.get_execution_path(workflow_id, execution_id)
        exec_file = exec_path / "execution.json"

        if not exec_file.exists():
            return None

        try:
            data = json.loads(exec_file.read_text())
            return ExecutionRecord.model_validate(data)
        except (json.JSONDecodeError, ValidationError, OSError) as e:
            self._logger.error(
                "Corrupted execution file",
                path=str(exec_file),
                error=str(e),
            )
            return None

    def get_execution_by_id(self, execution_id: str) -> tuple[str, ExecutionRecord] | None:
        """Find execution by ID across all workflows.

        Returns:
            Tuple of (workflow_id, ExecutionRecord) or None if not found.
        """
        for workflow_dir in self.workflows_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            executions_dir = workflow_dir / "executions"
            if not executions_dir.exists():
                continue
            exec_dir = executions_dir / execution_id
            if exec_dir.exists():
                execution = self._load_execution_safe(workflow_dir.name, execution_id)
                if execution:
                    return (workflow_dir.name, execution)
        return None

    def list_executions(
        self,
        workflow_id: str | None = None,
        status: ExecutionStatus | None = None,
        limit: int = 10,
    ) -> list[tuple[str, ExecutionRecord]]:
        """List executions, optionally filtered.

        Returns:
            List of (workflow_id, ExecutionRecord) tuples, sorted by start time descending.
        """
        results: list[tuple[str, ExecutionRecord, datetime | None]] = []

        workflow_dirs = (
            [self.get_workflow_path(workflow_id)]
            if workflow_id
            else list(self.workflows_path.iterdir())
        )

        for workflow_dir in workflow_dirs:
            if not workflow_dir.is_dir():
                continue
            executions_dir = workflow_dir / "executions"
            if not executions_dir.exists():
                continue

            for exec_dir in executions_dir.iterdir():
                if not exec_dir.is_dir():
                    continue
                execution = self._load_execution_safe(workflow_dir.name, exec_dir.name)
                if execution and (status is None or execution.status == status):
                    results.append((workflow_dir.name, execution, execution.started_at))

        # Sort by start time, most recent first
        results.sort(key=lambda x: x[2] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        return [(wid, ex) for wid, ex, _ in results[:limit]]

    def get_latest_execution(
        self,
        workflow_id: str | None = None,
        status: ExecutionStatus | None = None,
    ) -> tuple[str, ExecutionRecord] | None:
        """Get the most recent execution."""
        executions = self.list_executions(workflow_id=workflow_id, status=status, limit=1)
        return executions[0] if executions else None

    def get_running_execution(self) -> tuple[str, ExecutionRecord] | None:
        """Get the currently running execution, if any."""
        return self.get_latest_execution(status=ExecutionStatus.RUNNING)

    # === Step Output Files ===

    def get_step_output_path(
        self, workflow_id: str, execution_id: str, step_id: str, is_stderr: bool = False
    ) -> Path:
        """Get path to step output file."""
        steps_path = self.get_steps_path(workflow_id, execution_id)
        suffix = ".err" if is_stderr else ".out"
        return steps_path / f"{step_id}{suffix}"

    def get_step_temp_output_path(
        self, workflow_id: str, execution_id: str, step_id: str
    ) -> Path:
        """Get path to temporary step output file."""
        steps_path = self.get_steps_path(workflow_id, execution_id)
        return steps_path / f"{step_id}.out.tmp"

    def read_step_output(
        self, workflow_id: str, execution_id: str, step_id: str, is_stderr: bool = False
    ) -> str:
        """Read step output content."""
        path = self.get_step_output_path(workflow_id, execution_id, step_id, is_stderr)
        if not path.exists():
            return ""
        return path.read_text()

    # === Cleanup ===

    def delete_execution(self, workflow_id: str, execution_id: str) -> None:
        """Delete an execution and all its files."""
        exec_path = self.get_execution_path(workflow_id, execution_id)
        if exec_path.exists():
            shutil.rmtree(exec_path)

    def find_stale_executions(self, max_age_days: int = 7) -> Iterator[tuple[str, str, datetime]]:
        """Find executions older than max_age_days.

        Yields:
            Tuples of (workflow_id, execution_id, ended_at).
        """
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_days * 24 * 60 * 60)

        for workflow_dir in self.workflows_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            executions_dir = workflow_dir / "executions"
            if not executions_dir.exists():
                continue

            for exec_dir in executions_dir.iterdir():
                if not exec_dir.is_dir():
                    continue
                try:
                    execution = self.load_execution(workflow_dir.name, exec_dir.name)
                    if execution.ended_at and execution.ended_at.timestamp() < cutoff:
                        yield (workflow_dir.name, exec_dir.name, execution.ended_at)
                except Exception:
                    # If we can't load it, check mtime
                    exec_file = exec_dir / "execution.json"
                    if exec_file.exists():
                        mtime = exec_file.stat().st_mtime
                        if mtime < cutoff:
                            yield (
                                workflow_dir.name,
                                exec_dir.name,
                                datetime.fromtimestamp(mtime, tz=timezone.utc),
                            )

    # === Conversation Storage ===

    def save_conversation_workflow(
        self, workflow_id: str, workflow: ConversationWorkflow, yaml_content: str
    ) -> Path:
        """Save conversation workflow definition.

        Returns:
            Path to saved workflow.yaml file.
        """
        workflow_dir = self.get_workflow_path(workflow_id)
        workflow_dir.mkdir(parents=True, exist_ok=True)

        workflow_file = workflow_dir / "workflow.yaml"
        workflow_file.write_text(yaml_content)

        return workflow_file

    def create_conversation(
        self, workflow_id: str, record: ConversationRecord
    ) -> Path:
        """Create a new conversation record.

        Returns:
            Path to conversation directory.
        """
        exec_path = self.get_execution_path(workflow_id, record.id)
        exec_path.mkdir(parents=True, exist_ok=True)

        self.save_conversation(workflow_id, record)
        return exec_path

    def save_conversation(self, workflow_id: str, record: ConversationRecord) -> None:
        """Save conversation record to disk atomically."""
        exec_path = self.get_execution_path(workflow_id, record.id)
        exec_path.mkdir(parents=True, exist_ok=True)
        conv_file = exec_path / "conversation.json"
        _atomic_write(conv_file, record.model_dump_json(indent=2))

    def load_conversation(
        self, workflow_id: str, execution_id: str
    ) -> ConversationRecord:
        """Load conversation record from disk."""
        exec_path = self.get_execution_path(workflow_id, execution_id)
        conv_file = exec_path / "conversation.json"

        if not conv_file.exists():
            raise execution_not_found(execution_id)

        try:
            data = json.loads(conv_file.read_text())
            return ConversationRecord.model_validate(data)
        except (json.JSONDecodeError, OSError) as e:
            raise io_error(f"Cannot read conversation file: {e}", str(conv_file))

    def _load_conversation_safe(
        self, workflow_id: str, execution_id: str
    ) -> ConversationRecord | None:
        """Load conversation record with defensive error handling.

        Returns None and logs error if file is corrupted.
        """
        exec_path = self.get_execution_path(workflow_id, execution_id)
        conv_file = exec_path / "conversation.json"

        if not conv_file.exists():
            return None

        try:
            data = json.loads(conv_file.read_text())
            return ConversationRecord.model_validate(data)
        except (json.JSONDecodeError, ValidationError, OSError) as e:
            self._logger.error(
                "Corrupted conversation file",
                path=str(conv_file),
                error=str(e),
            )
            return None

    def get_conversation_by_id(
        self, execution_id: str
    ) -> tuple[str, ConversationRecord] | None:
        """Find conversation by ID across all workflows.

        Returns:
            Tuple of (workflow_id, ConversationRecord) or None if not found.
        """
        for workflow_dir in self.workflows_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            executions_dir = workflow_dir / "executions"
            if not executions_dir.exists():
                continue
            exec_dir = executions_dir / execution_id
            conv_file = exec_dir / "conversation.json"
            if conv_file.exists():
                record = self._load_conversation_safe(workflow_dir.name, execution_id)
                if record:
                    return (workflow_dir.name, record)
        return None

    def get_running_conversation(self) -> tuple[str, ConversationRecord] | None:
        """Get the currently running conversation, if any."""
        for workflow_dir in self.workflows_path.iterdir():
            if not workflow_dir.is_dir():
                continue
            executions_dir = workflow_dir / "executions"
            if not executions_dir.exists():
                continue

            for exec_dir in executions_dir.iterdir():
                if not exec_dir.is_dir():
                    continue
                conv_file = exec_dir / "conversation.json"
                if conv_file.exists():
                    record = self._load_conversation_safe(workflow_dir.name, exec_dir.name)
                    if record and record.status == ExecutionStatus.RUNNING:
                        return (workflow_dir.name, record)
        return None

    def list_conversations(
        self,
        workflow_id: str | None = None,
        status: ExecutionStatus | None = None,
        limit: int = 10,
    ) -> list[tuple[str, ConversationRecord]]:
        """List conversations, optionally filtered.

        Returns:
            List of (workflow_id, ConversationRecord) tuples, sorted by start time.
        """
        results: list[tuple[str, ConversationRecord, datetime | None]] = []

        workflow_dirs = (
            [self.get_workflow_path(workflow_id)]
            if workflow_id
            else list(self.workflows_path.iterdir())
        )

        for workflow_dir in workflow_dirs:
            if not workflow_dir.is_dir():
                continue
            executions_dir = workflow_dir / "executions"
            if not executions_dir.exists():
                continue

            for exec_dir in executions_dir.iterdir():
                if not exec_dir.is_dir():
                    continue
                conv_file = exec_dir / "conversation.json"
                if not conv_file.exists():
                    continue
                record = self._load_conversation_safe(workflow_dir.name, exec_dir.name)
                if record and (status is None or record.status == status):
                    results.append((workflow_dir.name, record, record.started_at))

        # Sort by start time, most recent first
        results.sort(
            key=lambda x: x[2] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        return [(wid, rec) for wid, rec, _ in results[:limit]]
