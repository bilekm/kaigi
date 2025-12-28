"""Workflow executor with subprocess streaming."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import click

from orchestrator.lib.errors import (
    OrchestratorError,
    agent_failed,
    agent_timeout,
    workflow_locked,
)
from orchestrator.lib.logging import Logger, get_logger
from orchestrator.lib.signals import signal_handler_context
from orchestrator.models.execution import ExecutionRecord, ExecutionStatus, StepStatus
from orchestrator.models.workflow import Workflow
from orchestrator.services.store import WorkflowStore


# Global reference to current process for signal handling
_current_process: asyncio.subprocess.Process | None = None
_cancelled: bool = False


def _handle_signal(signum: int, frame: Any) -> None:
    """Handle termination signals."""
    global _cancelled
    _cancelled = True
    if _current_process and _current_process.returncode is None:
        _current_process.terminate()


def execute_workflow(
    workflow: Workflow,
    yaml_content: str,
    use_json: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Execute a workflow synchronously.

    Args:
        workflow: Parsed workflow to execute.
        yaml_content: Original YAML content for storage.
        use_json: Whether to output JSON format.
        quiet: Whether to suppress progress output.

    Returns:
        Execution result dictionary.

    Raises:
        OrchestratorError: On execution failure.
    """
    return asyncio.run(
        _execute_workflow_async(workflow, yaml_content, use_json, quiet)
    )


async def _execute_workflow_async(
    workflow: Workflow,
    yaml_content: str,
    use_json: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Execute a workflow asynchronously."""
    global _cancelled
    _cancelled = False

    store = WorkflowStore()
    logger = get_logger()

    # Generate workflow ID
    workflow_id = str(uuid4())[:8]

    # Acquire global lock
    execution_id = str(uuid4())[:8]
    if not store.acquire_global_lock(execution_id):
        raise workflow_locked()

    try:
        # Save workflow and create execution
        store.save_workflow(workflow_id, workflow, yaml_content)

        execution = ExecutionRecord.from_workflow(
            workflow_id=workflow_id,
            workflow_name=workflow.name,
            step_ids=[step.id for step in workflow.steps],
        )
        store.create_execution(workflow_id, execution)

        # Use context manager for signal handlers (restores on exit)
        with signal_handler_context(_handle_signal):
            # Start execution
            execution.start()
            store.save_execution(workflow_id, execution)
            logger.info("Workflow started", workflow_name=workflow.name)

            if not quiet and not use_json:
                click.echo(f"Starting workflow: {workflow.name}")

            # Execute steps
            prev_output_path: Path | None = None

            for i, step in enumerate(workflow.steps):
                if _cancelled:
                    execution.cancel()
                    store.save_execution(workflow_id, execution)
                    break

                execution.current_step_index = i
                step_result = execution.steps[i]
                step_result.start()
                store.save_execution(workflow_id, execution)

                step_logger = logger.with_context(workflow_id=workflow_id, step_id=step.id)
                step_logger.info("Agent started", command=step.command, timeout=step.timeout)

                if not quiet and not use_json:
                    click.echo(f"[{i + 1}/{len(workflow.steps)}] {step.id}... ", nl=False)

                try:
                    exit_code, output_path, error_path = await _execute_step(
                        store=store,
                        workflow_id=workflow_id,
                        execution_id=execution.id,
                        step=step,
                        prev_output_path=prev_output_path,
                        timeout=step.timeout,
                    )

                    step_result.complete(exit_code, str(output_path), str(error_path))
                    store.save_execution(workflow_id, execution)

                    if exit_code != 0:
                        stderr_content = error_path.read_text() if error_path.exists() else ""
                        step_logger.error(
                            "Agent failed",
                            exit_code=exit_code,
                            stderr=stderr_content[:500],
                        )

                        if not quiet and not use_json:
                            click.echo("FAILED")

                        execution.fail()
                        store.save_execution(workflow_id, execution)

                        raise agent_failed(
                            step.id,
                            exit_code,
                            stderr_content,
                            workflow.name,
                            execution.id,
                        )

                    duration = step_result.duration_seconds or 0
                    step_logger.info("Agent completed", duration=duration)

                    if not quiet and not use_json:
                        click.echo(f"done ({duration:.1f}s)")

                    prev_output_path = output_path

                except asyncio.TimeoutError:
                    step_result.fail(f"Timeout after {step.timeout} seconds")
                    store.save_execution(workflow_id, execution)

                    step_logger.error("Agent timeout", timeout=step.timeout)

                    if not quiet and not use_json:
                        click.echo("TIMEOUT")

                    execution.fail()
                    store.save_execution(workflow_id, execution)

                    raise agent_timeout(step.id, step.timeout)

            # Workflow completed successfully
            if execution.status == ExecutionStatus.RUNNING:
                execution.complete()
                store.save_execution(workflow_id, execution)

                total_duration = execution.duration_seconds or 0
                logger.info("Workflow completed", duration=total_duration)

                if not quiet and not use_json:
                    click.echo(f"Workflow completed in {total_duration:.1f}s")

        return _format_execution_result(execution)

    finally:
        store.release_global_lock()


async def _execute_step(
    store: WorkflowStore,
    workflow_id: str,
    execution_id: str,
    step: Any,
    prev_output_path: Path | None,
    timeout: int,
) -> tuple[int, Path, Path]:
    """Execute a single step.

    Returns:
        Tuple of (exit_code, output_path, error_path).
    """
    global _current_process

    # Prepare paths
    output_path = store.get_step_output_path(workflow_id, execution_id, step.id)
    error_path = store.get_step_output_path(workflow_id, execution_id, step.id, is_stderr=True)
    temp_output_path = store.get_step_temp_output_path(workflow_id, execution_id, step.id)

    # Prepare environment
    env = os.environ.copy()
    env.update(step.env)

    # Build command
    cmd = [step.command] + step.args

    # Set up stdin
    stdin_file = None
    if prev_output_path and prev_output_path.exists():
        stdin_file = open(prev_output_path, "rb")
        stdin_source = stdin_file.fileno()
    else:
        stdin_source = asyncio.subprocess.DEVNULL

    # Open output files
    try:
        with open(temp_output_path, "wb") as stdout_file, open(error_path, "wb") as stderr_file:
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=stdin_source,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=env,
                )
                _current_process = process

                try:
                    await asyncio.wait_for(process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    # Graceful shutdown: SIGTERM, then SIGKILL
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                    raise

            finally:
                _current_process = None
    finally:
        if stdin_file is not None:
            stdin_file.close()

    # Rename temp file to final output
    if temp_output_path.exists():
        temp_output_path.rename(output_path)

    return process.returncode or 0, output_path, error_path


def _format_execution_result(execution: ExecutionRecord) -> dict[str, Any]:
    """Format execution result for output."""
    return {
        "execution_id": execution.id,
        "workflow_name": execution.workflow_name,
        "status": execution.status.value,
        "duration_seconds": execution.duration_seconds,
        "steps": [
            {
                "id": step.step_id,
                "status": step.status.value,
                "duration": step.duration_seconds,
            }
            for step in execution.steps
        ],
    }


def cancel_execution(
    workflow_id: str,
    execution: ExecutionRecord,
    force: bool = False,
) -> dict[str, Any]:
    """Cancel a running execution.

    This function signals the current process to terminate.
    The actual termination is handled by the signal handler.
    """
    global _cancelled, _current_process

    store = WorkflowStore()
    logger = get_logger()

    _cancelled = True

    if _current_process and _current_process.returncode is None:
        if force:
            _current_process.kill()
            logger.info("Sent SIGKILL to agent")
        else:
            _current_process.terminate()
            logger.info("Sent SIGTERM to agent")

    # Update execution status
    execution.cancel()
    store.save_execution(workflow_id, execution)
    store.release_global_lock()

    return {
        "execution_id": execution.id,
        "status": "cancelled",
        "message": "Workflow cancelled. Partial results preserved.",
    }


def retry_execution(
    workflow_id: str,
    execution: ExecutionRecord,
    from_start: bool = False,
    use_json: bool = False,
) -> dict[str, Any]:
    """Retry a failed or cancelled execution.

    Args:
        workflow_id: ID of the workflow.
        execution: Previous execution record.
        from_start: If True, restart from the beginning.
        use_json: Whether to output JSON format.

    Returns:
        New execution result dictionary.
    """
    store = WorkflowStore()

    # Load the original workflow
    workflow_path = store.get_workflow_path(workflow_id) / "workflow.yaml"
    yaml_content = workflow_path.read_text()

    from orchestrator.services.parser import parse_workflow_yaml
    workflow = parse_workflow_yaml(yaml_content)

    if from_start:
        # Start fresh
        return execute_workflow(workflow, yaml_content, use_json=use_json, quiet=False)

    # Find the step to resume from
    resume_index = execution.get_failed_step_index()
    if resume_index is None:
        # For cancelled executions, find the last incomplete step
        for i, step in enumerate(execution.steps):
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                resume_index = i
                break

    if resume_index is None:
        resume_index = 0

    # Execute with resume logic
    return asyncio.run(
        _retry_workflow_async(
            workflow=workflow,
            yaml_content=yaml_content,
            workflow_id=workflow_id,
            old_execution=execution,
            resume_index=resume_index,
            use_json=use_json,
        )
    )


async def _retry_workflow_async(
    workflow: Workflow,
    yaml_content: str,
    workflow_id: str,
    old_execution: ExecutionRecord,
    resume_index: int,
    use_json: bool = False,
) -> dict[str, Any]:
    """Retry workflow from a specific step."""
    global _cancelled
    _cancelled = False

    store = WorkflowStore()
    logger = get_logger()

    # Acquire global lock
    execution_id = str(uuid4())[:8]
    if not store.acquire_global_lock(execution_id):
        raise workflow_locked()

    try:
        # Create new execution, copying completed steps from old
        execution = ExecutionRecord.from_workflow(
            workflow_id=workflow_id,
            workflow_name=workflow.name,
            step_ids=[step.id for step in workflow.steps],
        )
        execution.id = execution_id

        # Copy results from completed steps
        for i in range(resume_index):
            old_step = old_execution.steps[i]
            execution.steps[i] = old_step

        store.create_execution(workflow_id, execution)

        # Use context manager for signal handlers (restores on exit)
        with signal_handler_context(_handle_signal):
            execution.start()
            execution.current_step_index = resume_index
            store.save_execution(workflow_id, execution)

            logger.info(
                "Workflow retry started",
                workflow_name=workflow.name,
                resume_from=workflow.steps[resume_index].id,
            )

            if not use_json:
                click.echo(f"Retrying from step '{workflow.steps[resume_index].id}'...")

            # Get previous output path
            prev_output_path: Path | None = None
            if resume_index > 0:
                prev_step = execution.steps[resume_index - 1]
                if prev_step.output_path:
                    prev_output_path = Path(prev_step.output_path)

            # Execute remaining steps
            for i in range(resume_index, len(workflow.steps)):
                if _cancelled:
                    execution.cancel()
                    store.save_execution(workflow_id, execution)
                    break

                step = workflow.steps[i]
                execution.current_step_index = i
                step_result = execution.steps[i]
                step_result.start()
                store.save_execution(workflow_id, execution)

                step_logger = logger.with_context(workflow_id=workflow_id, step_id=step.id)
                step_logger.info("Agent started", command=step.command, timeout=step.timeout)

                if not use_json:
                    click.echo(f"[{i + 1}/{len(workflow.steps)}] {step.id}... ", nl=False)

                try:
                    exit_code, output_path, error_path = await _execute_step(
                        store=store,
                        workflow_id=workflow_id,
                        execution_id=execution.id,
                        step=step,
                        prev_output_path=prev_output_path,
                        timeout=step.timeout,
                    )

                    step_result.complete(exit_code, str(output_path), str(error_path))
                    store.save_execution(workflow_id, execution)

                    if exit_code != 0:
                        stderr_content = error_path.read_text() if error_path.exists() else ""
                        step_logger.error("Agent failed", exit_code=exit_code)

                        if not use_json:
                            click.echo("FAILED")

                        execution.fail()
                        store.save_execution(workflow_id, execution)

                        raise agent_failed(
                            step.id,
                            exit_code,
                            stderr_content,
                            workflow.name,
                            execution.id,
                        )

                    duration = step_result.duration_seconds or 0
                    step_logger.info("Agent completed", duration=duration)

                    if not use_json:
                        click.echo(f"done ({duration:.1f}s)")

                    prev_output_path = output_path

                except asyncio.TimeoutError:
                    step_result.fail(f"Timeout after {step.timeout} seconds")
                    store.save_execution(workflow_id, execution)

                    step_logger.error("Agent timeout", timeout=step.timeout)

                    if not use_json:
                        click.echo("TIMEOUT")

                    execution.fail()
                    store.save_execution(workflow_id, execution)

                    raise agent_timeout(step.id, step.timeout)

            # Workflow completed
            if execution.status == ExecutionStatus.RUNNING:
                execution.complete()
                store.save_execution(workflow_id, execution)

                total_duration = execution.duration_seconds or 0
                logger.info("Workflow completed", duration=total_duration)

                if not use_json:
                    click.echo(f"Workflow completed in {total_duration:.1f}s")

        return _format_execution_result(execution)

    finally:
        store.release_global_lock()
