"""Workflow execution commands - run, status, list, etc."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from orchestrator.lib.errors import OrchestratorError, print_error


def json_option(f):
    """Add --json option to a command."""
    return click.option(
        "--json",
        "use_json",
        is_flag=True,
        help="Output in JSON format",
    )(f)


@click.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@json_option
def validate(workflow_file: Path, use_json: bool) -> None:
    """Validate a workflow definition without executing."""
    from orchestrator.services.parser import parse_workflow_file
    from orchestrator.models.workflow import ConversationWorkflow

    try:
        workflow = parse_workflow_file(workflow_file)
        is_conversation = isinstance(workflow, ConversationWorkflow)

        if use_json:
            if is_conversation:
                output = {
                    "valid": True,
                    "name": workflow.name,
                    "mode": "conversation",
                    "agents": len(workflow.agents),
                }
            else:
                output = {
                    "valid": True,
                    "name": workflow.name,
                    "mode": "pipeline",
                    "steps": len(workflow.steps),
                }
            click.echo(json.dumps(output))
        else:
            click.echo(f"Workflow '{workflow.name}' is valid.")
            if is_conversation:
                click.echo(f"Mode: conversation")
                click.echo(f"{len(workflow.agents)} agents defined.")
            else:
                click.echo(f"Mode: pipeline")
                click.echo(f"{len(workflow.steps)} steps defined.")
    except OrchestratorError as e:
        print_error(e, use_json)
        sys.exit(2)


@click.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@json_option
@click.option("--quiet", is_flag=True, help="Suppress progress output")
def run(workflow_file: Path, use_json: bool, quiet: bool) -> None:
    """Execute a workflow from a YAML definition file."""
    from orchestrator.services.executor import execute_workflow
    from orchestrator.services.parser import parse_workflow_file

    try:
        workflow = parse_workflow_file(workflow_file)
        yaml_content = workflow_file.read_text()
        result = execute_workflow(workflow, yaml_content, use_json=use_json, quiet=quiet)

        if use_json:
            click.echo(json.dumps(result, indent=2))
        # Human-readable output is printed during execution

        # Exit with appropriate code based on result
        if result.get("status") == "completed":
            sys.exit(0)
        elif result.get("status") == "failed":
            sys.exit(1)
        else:
            sys.exit(1)

    except OrchestratorError as e:
        print_error(e, use_json)
        if e.code.value == "WORKFLOW_INVALID":
            sys.exit(2)
        elif e.code.value == "WORKFLOW_LOCKED":
            sys.exit(3)
        else:
            sys.exit(1)


@click.command()
@click.argument("execution_id", required=False)
@json_option
@click.option("--workflow", help="Filter by workflow name")
def status(execution_id: str | None, use_json: bool, workflow: str | None) -> None:
    """Get status of a workflow execution."""
    from orchestrator.services.store import WorkflowStore
    from orchestrator.lib.errors import execution_not_found

    store = WorkflowStore()

    try:
        if execution_id:
            result = store.get_execution_by_id(execution_id)
            if not result:
                raise execution_not_found(execution_id)
            workflow_id, execution = result
        else:
            result = store.get_latest_execution()
            if not result:
                if use_json:
                    click.echo(json.dumps({"error": "No executions found"}))
                else:
                    click.echo("No executions found.")
                sys.exit(1)
            workflow_id, execution = result

        if use_json:
            output = {
                "execution_id": execution.id,
                "workflow_name": execution.workflow_name,
                "status": execution.status.value,
                "started_at": execution.started_at.isoformat() if execution.started_at else None,
                "ended_at": execution.ended_at.isoformat() if execution.ended_at else None,
                "current_step": execution.steps[execution.current_step_index].step_id
                if execution.current_step_index < len(execution.steps)
                else None,
                "steps": [
                    {
                        "id": s.step_id,
                        "status": s.status.value,
                        "duration": s.duration_seconds,
                    }
                    for s in execution.steps
                ],
            }
            click.echo(json.dumps(output, indent=2))
        else:
            click.echo(f"Execution: {execution.id}")
            click.echo(f"Workflow: {execution.workflow_name}")
            click.echo(f"Status: {execution.status.value}")
            if execution.started_at:
                click.echo(f"Started: {execution.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
            click.echo()
            click.echo("Steps:")
            for i, step in enumerate(execution.steps):
                if step.status.value == "completed":
                    marker = "[x]"
                elif step.status.value == "running":
                    marker = "[>]"
                elif step.status.value == "failed":
                    marker = "[!]"
                else:
                    marker = "[ ]"
                duration = f", {step.duration_seconds:.1f}s" if step.duration_seconds else ""
                click.echo(f"  {marker} {step.step_id} ({step.status.value}{duration})")

    except OrchestratorError as e:
        print_error(e, use_json)
        sys.exit(1)


@click.command("list")
@json_option
@click.option("--limit", default=10, help="Maximum number of executions to show")
@click.option("--status", "filter_status", help="Filter by status")
def list_executions(use_json: bool, limit: int, filter_status: str | None) -> None:
    """List recent workflow executions."""
    from orchestrator.services.store import WorkflowStore
    from orchestrator.models.execution import ExecutionStatus

    store = WorkflowStore()

    status_filter = None
    if filter_status:
        try:
            status_filter = ExecutionStatus(filter_status)
        except ValueError:
            click.echo(f"Invalid status: {filter_status}", err=True)
            sys.exit(1)

    executions = store.list_executions(status=status_filter, limit=limit)

    if use_json:
        output = [
            {
                "execution_id": ex.id,
                "workflow_name": ex.workflow_name,
                "status": ex.status.value,
                "started_at": ex.started_at.isoformat() if ex.started_at else None,
                "duration": ex.duration_seconds,
            }
            for _, ex in executions
        ]
        click.echo(json.dumps(output, indent=2))
    else:
        if not executions:
            click.echo("No executions found.")
            return

        click.echo(f"{'EXECUTION':<12} {'WORKFLOW':<20} {'STATUS':<12} {'STARTED':<20} {'DURATION':<10}")
        for _, ex in executions:
            started = ex.started_at.strftime("%Y-%m-%d %H:%M:%S") if ex.started_at else "N/A"
            duration = f"{ex.duration_seconds:.1f}s" if ex.duration_seconds else "N/A"
            click.echo(f"{ex.id:<12} {ex.workflow_name:<20} {ex.status.value:<12} {started:<20} {duration:<10}")


@click.command()
@click.argument("execution_id")
@click.argument("step_id")
@click.option("--stderr", is_flag=True, help="Show stderr instead of stdout")
@click.option("--tail", type=int, help="Show last N lines only")
def output(execution_id: str, step_id: str, stderr: bool, tail: int | None) -> None:
    """Retrieve the output of a specific step."""
    from orchestrator.services.store import WorkflowStore
    from orchestrator.lib.errors import execution_not_found, step_not_found

    store = WorkflowStore()

    result = store.get_execution_by_id(execution_id)
    if not result:
        print_error(execution_not_found(execution_id), False)
        sys.exit(1)

    workflow_id, execution = result

    step_result = execution.get_step_result(step_id)
    if not step_result:
        print_error(step_not_found(step_id, execution_id), False)
        sys.exit(1)

    content = store.read_step_output(workflow_id, execution_id, step_id, is_stderr=stderr)

    if not content:
        click.echo(f"No {'stderr' if stderr else 'stdout'} output for step '{step_id}'", err=True)
        sys.exit(2)

    if tail:
        lines = content.splitlines()
        content = "\n".join(lines[-tail:])

    click.echo(content, nl=False)


@click.command()
@click.argument("execution_id", required=False)
@json_option
@click.option("--force", is_flag=True, help="Send SIGKILL immediately")
def cancel(execution_id: str | None, use_json: bool, force: bool) -> None:
    """Cancel a running workflow execution."""
    from orchestrator.services.store import WorkflowStore
    from orchestrator.services.executor import cancel_execution
    from orchestrator.lib.errors import execution_not_found

    store = WorkflowStore()

    if execution_id:
        result = store.get_execution_by_id(execution_id)
        if not result:
            print_error(execution_not_found(execution_id), use_json)
            sys.exit(1)
        workflow_id, execution = result
    else:
        result = store.get_running_execution()
        if not result:
            if use_json:
                click.echo(json.dumps({"error": "No running workflow found"}))
            else:
                click.echo("No running workflow found.")
            sys.exit(1)
        workflow_id, execution = result

    try:
        cancel_result = cancel_execution(workflow_id, execution, force=force)
        if use_json:
            click.echo(json.dumps(cancel_result, indent=2))
        else:
            click.echo(f"Workflow cancelled. Partial results preserved.")
    except OrchestratorError as e:
        print_error(e, use_json)
        sys.exit(2)


@click.command()
@click.argument("execution_id", required=False)
@json_option
@click.option("--from-start", is_flag=True, help="Restart from beginning")
def retry(execution_id: str | None, use_json: bool, from_start: bool) -> None:
    """Retry a failed or cancelled workflow."""
    from orchestrator.services.store import WorkflowStore
    from orchestrator.services.executor import retry_execution
    from orchestrator.lib.errors import execution_not_found, not_retryable
    from orchestrator.models.execution import ExecutionStatus

    store = WorkflowStore()

    if execution_id:
        result = store.get_execution_by_id(execution_id)
        if not result:
            print_error(execution_not_found(execution_id), use_json)
            sys.exit(2)
        workflow_id, execution = result
    else:
        # Find latest failed or cancelled
        result = store.get_latest_execution(status=ExecutionStatus.FAILED)
        if not result:
            result = store.get_latest_execution(status=ExecutionStatus.CANCELLED)
        if not result:
            if use_json:
                click.echo(json.dumps({"error": "No failed or cancelled execution found"}))
            else:
                click.echo("No failed or cancelled execution found.")
            sys.exit(2)
        workflow_id, execution = result

    if not execution.is_retryable:
        print_error(not_retryable(execution.id, execution.status.value), use_json)
        sys.exit(2)

    try:
        retry_result = retry_execution(workflow_id, execution, from_start=from_start, use_json=use_json)
        if use_json:
            click.echo(json.dumps(retry_result, indent=2))
        sys.exit(0 if retry_result.get("status") == "completed" else 1)
    except OrchestratorError as e:
        print_error(e, use_json)
        if e.code.value == "WORKFLOW_LOCKED":
            sys.exit(3)
        sys.exit(1)


@click.command()
@json_option
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
@click.option("--force", is_flag=True, help="Delete without confirmation")
def cleanup(use_json: bool, dry_run: bool, force: bool) -> None:
    """Remove expired workflow data (older than 7 days)."""
    from orchestrator.services.cleanup import run_cleanup

    result = run_cleanup(dry_run=dry_run, force=force)

    if use_json:
        click.echo(json.dumps(result, indent=2))
    else:
        count = result.get("deleted_count", 0)
        if dry_run:
            click.echo(f"Would delete {count} executions.")
        else:
            click.echo(f"Deleted {count} executions.")
