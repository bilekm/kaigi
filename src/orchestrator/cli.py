"""Click CLI entry points for orchestrator."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from orchestrator import __version__
from orchestrator.lib.errors import OrchestratorError, print_error


# Common options
def json_option(f):
    """Add --json option to a command."""
    return click.option(
        "--json",
        "use_json",
        is_flag=True,
        help="Output in JSON format",
    )(f)


class Context:
    """CLI context object."""

    def __init__(self, use_json: bool = False):
        self.use_json = use_json


pass_context = click.make_pass_decorator(Context, ensure=True)


# Default project config location
PROJECT_CONFIG = ".orchestrator/workflow.yaml"


def _run_default() -> None:
    """Run default behavior when no subcommand given."""
    config_path = Path.cwd() / PROJECT_CONFIG

    if config_path.exists():
        # Run conversation with project config
        click.echo(f"Using project config: {config_path}")
        _run_conversation(config_path)
    else:
        # No config found - run init wizard
        click.echo("No project config found.")
        click.echo()
        if click.confirm("Create a new orchestrator project?", default=True):
            _run_init_wizard()
        else:
            click.echo()
            click.echo("Usage:")
            click.echo("  orchestrator init              Create project config")
            click.echo("  orchestrator converse <file>   Run specific workflow")
            click.echo("  orchestrator --help            Show all commands")


def _run_conversation(config_path: Path) -> None:
    """Run a conversation from config file."""
    from orchestrator.services.parser import parse_workflow_file
    from orchestrator.services.conversation import execute_conversation
    from orchestrator.models.workflow import ConversationWorkflow

    try:
        workflow = parse_workflow_file(config_path)

        if not isinstance(workflow, ConversationWorkflow):
            click.echo("Error: Project config must be a conversation workflow.")
            click.echo("Use 'orchestrator run' for pipeline workflows.")
            sys.exit(1)

        yaml_content = config_path.read_text()
        result = execute_conversation(workflow, yaml_content, interactive=True)

        if result["status"] == "completed":
            click.echo()
            click.echo(f"Conversation completed. Rounds: {result['rounds_completed']}")
    except OrchestratorError as e:
        print_error(e, use_json=False)
        sys.exit(1)


def _run_init_wizard() -> None:
    """Interactive wizard to create project config."""
    from orchestrator.lib.settings import load_agent_settings

    click.echo()
    click.echo("=== Orchestrator Project Setup ===")
    click.echo()

    # Get available agents
    settings = load_agent_settings()
    available_agents = settings.list_agents()

    if not available_agents:
        click.echo("No agents configured. Run 'orchestrator agents init' first.")
        sys.exit(1)

    click.echo(f"Available agents: {', '.join(available_agents)}")
    click.echo()

    # Get project name
    default_name = Path.cwd().name
    name = click.prompt("Project name", default=default_name)

    # Get topic
    click.echo()
    click.echo("What should the agents discuss/work on?")
    topic = click.prompt("Topic")

    # Select agents
    click.echo()
    click.echo("Select agents (comma-separated, min 2):")
    for i, agent in enumerate(available_agents, 1):
        config = settings.get_agent(agent)
        desc = config.description if config else ""
        click.echo(f"  {agent}: {desc}")

    default_agents = ",".join(available_agents[:2]) if len(available_agents) >= 2 else ",".join(available_agents)
    agents_input = click.prompt("Agents", default=default_agents)
    selected_agents = [a.strip() for a in agents_input.split(",") if a.strip()]

    if len(selected_agents) < 2:
        click.echo("Error: At least 2 agents required.")
        sys.exit(1)

    # Validate agents exist
    for agent in selected_agents:
        if agent not in available_agents:
            click.echo(f"Error: Agent '{agent}' not found in settings.")
            sys.exit(1)

    # Get personas
    click.echo()
    click.echo("Define personas for each agent (or press Enter for default):")
    personas = {}
    for agent in selected_agents:
        default_persona = f"You are {agent}."
        persona = click.prompt(f"  {agent} persona", default=default_persona)
        personas[agent] = persona

    # Max rounds
    click.echo()
    max_rounds = click.prompt("Max rounds", default=5, type=int)

    # Build config
    agents_yaml = ""
    for agent in selected_agents:
        agents_yaml += f"""
  - id: {agent}
    agent: {agent}
    persona: "{personas[agent]}"
"""

    config_content = f"""# Orchestrator Project Config
# Created by init wizard

name: {name}
version: "1.0"
mode: conversation
description: "{topic[:50]}"

agents:{agents_yaml}
topic: |
  {topic}

max_rounds: {max_rounds}
turn_order: round_robin
consensus_keyword: "AGREED:"
"""

    # Create config
    config_dir = Path.cwd() / ".orchestrator"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "workflow.yaml"

    config_path.write_text(config_content)

    click.echo()
    click.echo(f"Created: {config_path}")
    click.echo()

    if click.confirm("Start conversation now?", default=True):
        click.echo()
        _run_conversation(config_path)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="orchestrator")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Orchestrator - CLI-based workflow orchestrator for agent coordination.

    Run without arguments to start a conversation using project config,
    or run 'orchestrator init' to create a new project.
    """
    ctx.ensure_object(Context)

    # If no subcommand, run default behavior
    if ctx.invoked_subcommand is None:
        _run_default()


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing config")
def init(force: bool) -> None:
    """Initialize a new orchestrator project in current directory.

    Creates .orchestrator/workflow.yaml with an interactive wizard.
    """
    config_path = Path.cwd() / PROJECT_CONFIG

    if config_path.exists() and not force:
        click.echo(f"Project config already exists: {config_path}")
        click.echo("Use --force to overwrite.")
        sys.exit(1)

    _run_init_wizard()


@cli.command()
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


@cli.command()
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


@cli.command()
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


@cli.command("list")
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


@cli.command()
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


@cli.command()
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


@cli.command()
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


@cli.command()
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


# === Conversation Mode Commands ===


@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@json_option
@click.option(
    "--non-interactive", is_flag=True, help="Run without user prompts (auto-continue)"
)
@click.option(
    "--no-auto-start", is_flag=True, help="Don't auto-start missing agents"
)
def converse(workflow_file: Path, use_json: bool, non_interactive: bool, no_auto_start: bool) -> None:
    """Start a conversation between AI agents.

    The workflow file must have mode: conversation.
    Agents will discuss the topic until consensus is reached or max_rounds.

    By default, any agents not already running will be started automatically
    in the background. Use --no-auto-start to disable this behavior.

    In interactive mode (default), you can:
    - Press Enter to continue to next round
    - Type a message to inject into the conversation
    - Type 'quit' to end early
    - Type 'approve' when consensus is reached
    """
    from orchestrator.services.parser import parse_workflow_file
    from orchestrator.services.conversation import execute_conversation
    from orchestrator.models.workflow import ConversationWorkflow
    from orchestrator.lib.errors import workflow_invalid

    try:
        workflow = parse_workflow_file(workflow_file)

        if not isinstance(workflow, ConversationWorkflow):
            raise workflow_invalid(
                "Workflow must have mode: conversation. Use 'orchestrator run' for pipeline workflows."
            )

        # Auto-start missing agents
        if not no_auto_start:
            _auto_start_agents(workflow, use_json)

        yaml_content = workflow_file.read_text()
        result = execute_conversation(
            workflow=workflow,
            yaml_content=yaml_content,
            interactive=not non_interactive,
        )

        if use_json:
            click.echo(json.dumps(result, indent=2))

        if result.get("status") == "completed":
            if result.get("consensus_status") == "approved":
                sys.exit(0)
            else:
                sys.exit(0)  # Completed but no consensus
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


def _auto_start_agents(workflow, use_json: bool) -> None:
    """Auto-start any agents that aren't already running."""
    from orchestrator.lib.agent_client import is_agent_running
    from orchestrator.lib.settings import get_agent_config
    from orchestrator.lib.agent_server import get_socket_path
    import os
    import time

    started = []

    for agent in workflow.agents:
        agent_id = agent.agent or agent.id

        if is_agent_running(agent_id):
            continue

        # Get agent config
        config = get_agent_config(agent_id)
        if not config:
            if not use_json:
                click.echo(f"Warning: Agent '{agent_id}' not found in settings, will spawn on demand")
            continue

        if not use_json:
            click.echo(f"Starting agent '{agent_id}' in background...")

        # Start in background (double-fork daemon)
        pid = os.fork()
        if pid == 0:
            os.setsid()
            pid2 = os.fork()
            if pid2 == 0:
                # Grandchild - run the server
                from orchestrator.lib.agent_server import run_agent_server

                sys.stdin.close()
                with open("/dev/null", "r") as null:
                    os.dup2(null.fileno(), 0)

                log_path = get_socket_path(agent_id).with_suffix(".log")
                with open(log_path, "w") as log:
                    os.dup2(log.fileno(), 1)
                    os.dup2(log.fileno(), 2)

                run_agent_server(agent_id, config.command, env=config.env)
                sys.exit(0)
            else:
                os._exit(0)
        else:
            os.waitpid(pid, 0)
            started.append(agent_id)

    # Wait for agents to be ready
    if started:
        if not use_json:
            click.echo("Waiting for agents to initialize...")

        max_wait = 30  # seconds
        start_time = time.time()

        while time.time() - start_time < max_wait:
            all_ready = True
            for agent_id in started:
                if not is_agent_running(agent_id):
                    all_ready = False
                    break

            if all_ready:
                break

            time.sleep(0.5)

        if not use_json:
            ready_count = sum(1 for aid in started if is_agent_running(aid))
            click.echo(f"Started {ready_count}/{len(started)} agents")
            click.echo()


@cli.command()
@click.argument("message")
@click.argument("execution_id", required=False)
@json_option
def say(message: str, execution_id: str | None, use_json: bool) -> None:
    """Inject a message into an active conversation.

    Use this to contribute to a running conversation from another terminal.
    If execution_id is not provided, targets the most recent running conversation.
    """
    from orchestrator.services.store import WorkflowStore
    from orchestrator.services.conversation import inject_user_message
    from orchestrator.lib.errors import execution_not_found

    store = WorkflowStore()

    try:
        if execution_id:
            result = store.get_conversation_by_id(execution_id)
            if not result:
                raise execution_not_found(execution_id)
            workflow_id, _ = result
        else:
            result = store.get_running_conversation()
            if not result:
                if use_json:
                    click.echo(json.dumps({"error": "No running conversation"}))
                else:
                    click.echo("No running conversation found.")
                sys.exit(1)
            workflow_id, record = result
            execution_id = record.id

        inject_result = inject_user_message(workflow_id, execution_id, message)

        if use_json:
            click.echo(json.dumps(inject_result, indent=2))
        else:
            click.echo(f"Message added to conversation {execution_id}")

    except OrchestratorError as e:
        print_error(e, use_json)
        sys.exit(1)


@cli.command()
@click.argument("execution_id", required=False)
@click.option("--round", "round_num", type=int, help="Show only specific round")
@json_option
def transcript(execution_id: str | None, round_num: int | None, use_json: bool) -> None:
    """Show the full conversation transcript."""
    from orchestrator.services.store import WorkflowStore
    from orchestrator.lib.errors import execution_not_found

    store = WorkflowStore()

    try:
        if execution_id:
            result = store.get_conversation_by_id(execution_id)
            if not result:
                raise execution_not_found(execution_id)
            workflow_id, record = result
        else:
            # Find latest conversation
            conversations = store.list_conversations(limit=1)
            if not conversations:
                if use_json:
                    click.echo(json.dumps({"error": "No conversations found"}))
                else:
                    click.echo("No conversations found.")
                sys.exit(1)
            workflow_id, record = conversations[0]

        if use_json:
            messages = []
            for msg in record.messages:
                if round_num is not None and msg.round_number != round_num:
                    continue
                messages.append({
                    "id": msg.id,
                    "role": msg.role.value,
                    "agent_id": msg.agent_id,
                    "content": msg.content,
                    "round": msg.round_number,
                    "timestamp": msg.timestamp.isoformat(),
                })
            output = {
                "execution_id": record.id,
                "workflow_name": record.workflow_name,
                "topic": record.topic,
                "status": record.status.value,
                "consensus_status": record.consensus_status.value,
                "rounds": record.current_round,
                "messages": messages,
            }
            click.echo(json.dumps(output, indent=2))
        else:
            click.echo(f"Conversation: {record.id}")
            click.echo(f"Workflow: {record.workflow_name}")
            click.echo(f"Topic: {record.topic[:80]}...")
            click.echo(f"Status: {record.status.value} | Consensus: {record.consensus_status.value}")
            click.echo(f"Rounds: {record.current_round}/{record.max_rounds}")
            click.echo()
            click.echo("=" * 60)
            click.echo("TRANSCRIPT")
            click.echo("=" * 60)

            current_round = -1
            for msg in record.messages:
                if round_num is not None and msg.round_number != round_num:
                    continue

                if msg.round_number != current_round:
                    current_round = msg.round_number
                    if current_round > 0:
                        click.echo()
                        click.echo(f"--- Round {current_round} ---")

                if msg.role.value == "system":
                    click.echo(f"\n[SYSTEM] {msg.content}")
                elif msg.role.value == "user":
                    click.echo(f"\n[USER/LEAD] {msg.content}")
                else:
                    click.echo(f"\n[{msg.agent_id}] {msg.content}")

            click.echo()
            click.echo("=" * 60)

            if record.consensus_content:
                click.echo("\nCONSENSUS:")
                click.echo(record.consensus_content)

    except OrchestratorError as e:
        print_error(e, use_json)
        sys.exit(1)


# === Agent Management Commands ===


@cli.group()
def agents() -> None:
    """Manage AI agent configurations."""
    pass


@agents.command("list")
@json_option
def agents_list(use_json: bool) -> None:
    """List all configured AI agents."""
    from orchestrator.lib.settings import (
        list_available_agents,
        get_global_settings_path,
        get_project_settings_path,
    )

    agents = list_available_agents()

    if use_json:
        output = {
            "agents": [
                {
                    "name": name,
                    "command": config.command,
                    "description": config.description,
                    "timeout": config.timeout,
                }
                for name, config in agents
            ],
            "global_settings": str(get_global_settings_path()),
            "project_settings": str(get_project_settings_path()) if get_project_settings_path() else None,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        global_path = get_global_settings_path()
        project_path = get_project_settings_path()

        click.echo("Agent Settings:")
        click.echo(f"  Global: {global_path}")
        if project_path:
            click.echo(f"  Project: {project_path}")
        click.echo()

        if not agents:
            click.echo("No agents configured.")
            click.echo()
            click.echo("Create ~/.orchestrator/agents.yaml with:")
            click.echo()
            click.echo("  agents:")
            click.echo("    claude:")
            click.echo('      command: claude')
            click.echo('      args: ["-p", "{{prompt}}", "--output-format", "text"]')
            return

        click.echo(f"{'NAME':<15} {'COMMAND':<15} {'DESCRIPTION':<40}")
        click.echo("-" * 70)
        for name, config in agents:
            desc = config.description[:40] if config.description else ""
            click.echo(f"{name:<15} {config.command:<15} {desc:<40}")


@agents.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing settings")
@click.option("--project", is_flag=True, help="Create in .orchestrator/ (project-level)")
def agents_init(force: bool, project: bool) -> None:
    """Create a default agents.yaml settings file.

    By default creates global settings in ~/.orchestrator/agents.yaml.
    Use --project to create project-level settings in .orchestrator/agents.yaml.
    Project settings override global settings.
    """
    from orchestrator.lib.settings import get_global_settings_path, ensure_global_settings_dir

    if project:
        # Create in current directory
        project_dir = Path.cwd() / ".orchestrator"
        project_dir.mkdir(parents=True, exist_ok=True)
        settings_path = project_dir / "agents.yaml"
    else:
        ensure_global_settings_dir()
        settings_path = get_global_settings_path()

    if settings_path.exists() and not force:
        click.echo(f"Settings file already exists: {settings_path}")
        click.echo("Use --force to overwrite.")
        sys.exit(1)

    default_settings = '''\
# Orchestrator Agent Settings
# Configure AI agents that can be used in conversation workflows.
#
# Usage in workflow.yaml:
#   agents:
#     - id: my-agent
#       agent: claude    # Reference by name
#       model: opus-4    # Override model (optional)
#       persona: "..."
#
# Environment variables can be referenced with ${VAR_NAME} syntax.

agents:
  # Claude Code CLI
  claude:
    command: claude
    args: ["-p", "{{prompt}}", "--output-format", "text"]
    timeout: 300
    description: "Claude Code CLI"
    # model: claude-sonnet-4-5  # Default model (optional)

  # GitHub Copilot CLI
  # Requires: npm install -g @github/copilot && GitHub auth
  copilot:
    command: copilot
    args: ["-p", "{{prompt}}"]
    timeout: 300
    description: "GitHub Copilot CLI"
    # model: gemini-3-pro-preview  # Select model

  # GLM via Z.AI (OpenAI-compatible endpoint)
  # Requires: export ZAI_API_KEY=your-key
  # glm:
  #   command: claude
  #   args: ["-p", "{{prompt}}", "--output-format", "text"]
  #   env:
  #     ANTHROPIC_BASE_URL: "https://api.z.ai/api/coding/paas/v4"
  #     ANTHROPIC_AUTH_TOKEN: "${ZAI_API_KEY}"
  #   timeout: 300
  #   description: "GLM-4.7 via Z.AI"
'''

    settings_path.write_text(default_settings)
    click.echo(f"Created: {settings_path}")
    click.echo()
    if project:
        click.echo("Project-level settings created.")
        click.echo("These override global settings (~/.orchestrator/agents.yaml).")
    else:
        click.echo("Configure your agents by editing the file.")
        click.echo("Use --project to create project-specific settings.")


@agents.command("show")
@click.argument("name")
@json_option
def agents_show(name: str, use_json: bool) -> None:
    """Show details for a specific agent."""
    from orchestrator.lib.settings import get_agent_config

    config = get_agent_config(name)

    if not config:
        if use_json:
            click.echo(json.dumps({"error": f"Agent '{name}' not found"}))
        else:
            click.echo(f"Agent '{name}' not found.")
        sys.exit(1)

    if use_json:
        output = {
            "name": name,
            "command": config.command,
            "args": config.args,
            "env": {k: "***" if "TOKEN" in k or "KEY" in k else v for k, v in config.env.items()},
            "timeout": config.timeout,
            "description": config.description,
            "model": config.model,
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(f"Agent: {name}")
        click.echo(f"  Command: {config.command}")
        click.echo(f"  Args: {' '.join(config.args)}")
        if config.model:
            click.echo(f"  Model: {config.model}")
        click.echo(f"  Timeout: {config.timeout}s")
        if config.description:
            click.echo(f"  Description: {config.description}")
        if config.env:
            click.echo("  Environment:")
            for k, v in config.env.items():
                # Mask sensitive values
                display_v = "***" if "TOKEN" in k or "KEY" in k else v
                click.echo(f"    {k}: {display_v}")


# === Persistent Agent Commands ===


def _detect_terminal_emulator() -> list[str] | None:
    """Detect available terminal emulator."""
    import shutil

    # Common terminal emulators in order of preference
    terminals = [
        (["gnome-terminal", "--"], "gnome-terminal"),
        (["konsole", "-e"], "konsole"),
        (["xfce4-terminal", "-e"], "xfce4-terminal"),
        (["xterm", "-e"], "xterm"),
        (["terminator", "-e"], "terminator"),
        (["alacritty", "-e"], "alacritty"),
        (["kitty", "--"], "kitty"),
    ]

    for cmd, name in terminals:
        if shutil.which(name):
            return cmd

    return None


def _start_agent_in_terminal(agent_id: str, command: str, env: dict[str, str]) -> bool:
    """Start an agent in a new visible terminal window."""
    import subprocess
    import shlex

    terminal_cmd = _detect_terminal_emulator()
    if not terminal_cmd:
        click.echo("Error: No terminal emulator found.")
        click.echo("Install gnome-terminal, konsole, xterm, or similar.")
        return False

    # Build the command to run in the terminal
    # We need to run the orchestrator agents start command in foreground
    agent_cmd = f"orchestrator agents start {agent_id}"

    # Build full command
    full_cmd = terminal_cmd + [agent_cmd]

    try:
        # Start the terminal (it will run independently)
        subprocess.Popen(
            full_cmd,
            env={**dict(os.environ), **env},
            start_new_session=True,
        )
        return True
    except Exception as e:
        click.echo(f"Error starting terminal: {e}")
        return False


@agents.command("start")
@click.argument("name")
@click.option("--id", "agent_id", help="Custom agent ID (default: same as name)")
@click.option("--background", "-b", is_flag=True, help="Run in background (daemonize)")
@click.option("--terminal", "-t", is_flag=True, help="Open in new visible terminal window")
def agents_start(name: str, agent_id: str | None, background: bool, terminal: bool) -> None:
    """Start a persistent agent server.

    Starts the agent in interactive mode and exposes it via Unix socket.
    Other processes can then communicate with it instantly.

    Examples:

        # Start Claude agent in foreground (current terminal)
        orchestrator agents start claude

        # Start in background (hidden)
        orchestrator agents start claude --background

        # Start in new visible terminal window
        orchestrator agents start claude --terminal

        # Start with custom ID
        orchestrator agents start claude --id claude-1
    """
    from orchestrator.lib.settings import get_agent_config
    from orchestrator.lib.agent_client import is_agent_running

    config = get_agent_config(name)
    if not config:
        click.echo(f"Agent '{name}' not found in settings.")
        click.echo("Run 'orchestrator agents list' to see available agents.")
        sys.exit(1)

    agent_id = agent_id or name

    if is_agent_running(agent_id):
        click.echo(f"Agent '{agent_id}' is already running.")
        click.echo("Use 'orchestrator agents stop {agent_id}' to stop it first.")
        sys.exit(1)

    # Build command - for interactive mode, we don't use the prompt args
    # Just start the CLI without -p flag
    command = config.command
    env = config.env

    if terminal:
        # Open in new terminal window
        click.echo(f"Opening terminal for agent '{agent_id}'...")

        if _start_agent_in_terminal(agent_id, command, env):
            click.echo(f"Terminal opened for '{agent_id}'")
            # Wait a moment and check if it started
            import time
            time.sleep(3)
            if is_agent_running(agent_id):
                click.echo(f"Agent '{agent_id}' is running.")
            else:
                click.echo(f"Agent may still be initializing. Check the terminal window.")
        else:
            sys.exit(1)

    elif background:
        # Daemonize
        import os
        import subprocess
        from orchestrator.lib.agent_server import get_socket_path, get_pid_path

        click.echo(f"Starting agent '{agent_id}' in background...")

        # Fork and run the server
        pid = os.fork()
        if pid == 0:
            # Child - become session leader and fork again
            os.setsid()
            pid2 = os.fork()
            if pid2 == 0:
                # Grandchild - run the server
                import sys
                from orchestrator.lib.agent_server import run_agent_server

                # Redirect stdio
                sys.stdin.close()
                with open("/dev/null", "r") as null:
                    os.dup2(null.fileno(), 0)

                log_path = get_socket_path(agent_id).with_suffix(".log")
                with open(log_path, "w") as log:
                    os.dup2(log.fileno(), 1)
                    os.dup2(log.fileno(), 2)

                run_agent_server(agent_id, command, env=env)
                sys.exit(0)
            else:
                # Second parent - exit
                os._exit(0)
        else:
            # First parent - wait for child
            os.waitpid(pid, 0)

            # Wait a moment for server to start
            import time
            time.sleep(2)

            if is_agent_running(agent_id):
                socket_path = get_socket_path(agent_id)
                click.echo(f"Agent '{agent_id}' started.")
                click.echo(f"Socket: {socket_path}")
            else:
                log_path = get_socket_path(agent_id).with_suffix(".log")
                click.echo(f"Agent failed to start. Check log: {log_path}")
                sys.exit(1)
    else:
        # Foreground - run directly
        from orchestrator.lib.agent_server import run_agent_server, get_socket_path

        click.echo(f"Starting agent '{agent_id}' in foreground...")
        click.echo(f"Socket: {get_socket_path(agent_id)}")
        click.echo("Press Ctrl+C to stop.")
        click.echo()

        try:
            run_agent_server(agent_id, command, env=env)
        except KeyboardInterrupt:
            click.echo("\nAgent stopped.")


@agents.command("start-all")
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--background", "-b", is_flag=True, help="Run all in background (default)")
@click.option("--terminal", "-t", is_flag=True, help="Open each agent in visible terminal")
def agents_start_all(workflow_file: Path | None, background: bool, terminal: bool) -> None:
    """Start all agents defined in a workflow file.

    If no workflow file is specified, uses .orchestrator/workflow.yaml.

    Examples:

        # Start all agents in background (default)
        orchestrator agents start-all

        # Start all agents in visible terminals
        orchestrator agents start-all --terminal

        # Start agents from specific workflow
        orchestrator agents start-all examples/my-workflow.yaml --terminal
    """
    from orchestrator.services.parser import parse_workflow_file
    from orchestrator.models.workflow import ConversationWorkflow
    from orchestrator.lib.settings import get_agent_config
    from orchestrator.lib.agent_client import is_agent_running
    from orchestrator.lib.agent_server import get_socket_path
    import time

    # Default to project config
    if workflow_file is None:
        workflow_file = Path.cwd() / ".orchestrator" / "workflow.yaml"
        if not workflow_file.exists():
            click.echo("No workflow file specified and .orchestrator/workflow.yaml not found.")
            click.echo("Usage: orchestrator agents start-all <workflow-file>")
            sys.exit(1)

    try:
        workflow = parse_workflow_file(workflow_file)
    except Exception as e:
        click.echo(f"Error parsing workflow: {e}")
        sys.exit(1)

    if not isinstance(workflow, ConversationWorkflow):
        click.echo("Workflow must be a conversation workflow (mode: conversation)")
        sys.exit(1)

    click.echo(f"Workflow: {workflow.name}")
    click.echo(f"Agents: {', '.join(a.agent or a.id for a in workflow.agents)}")
    click.echo()

    started = []
    skipped = []
    failed = []

    for agent in workflow.agents:
        agent_id = agent.agent or agent.id

        if is_agent_running(agent_id):
            click.echo(f"  [{agent_id}] Already running")
            skipped.append(agent_id)
            continue

        config = get_agent_config(agent_id)
        if not config:
            click.echo(f"  [{agent_id}] Not found in settings - skipped")
            failed.append(agent_id)
            continue

        if terminal:
            click.echo(f"  [{agent_id}] Opening terminal...")
            if _start_agent_in_terminal(agent_id, config.command, config.env):
                started.append(agent_id)
            else:
                failed.append(agent_id)
        else:
            # Background mode (default)
            click.echo(f"  [{agent_id}] Starting in background...")

            pid = os.fork()
            if pid == 0:
                os.setsid()
                pid2 = os.fork()
                if pid2 == 0:
                    from orchestrator.lib.agent_server import run_agent_server

                    sys.stdin.close()
                    with open("/dev/null", "r") as null:
                        os.dup2(null.fileno(), 0)

                    log_path = get_socket_path(agent_id).with_suffix(".log")
                    with open(log_path, "w") as log:
                        os.dup2(log.fileno(), 1)
                        os.dup2(log.fileno(), 2)

                    run_agent_server(agent_id, config.command, env=config.env)
                    sys.exit(0)
                else:
                    os._exit(0)
            else:
                os.waitpid(pid, 0)
                started.append(agent_id)

    # Wait for agents to be ready
    if started and not terminal:
        click.echo()
        click.echo("Waiting for agents to initialize...")

        max_wait = 30
        start_time = time.time()

        while time.time() - start_time < max_wait:
            ready = sum(1 for aid in started if is_agent_running(aid))
            if ready == len(started):
                break
            time.sleep(0.5)

        click.echo()

    # Summary
    click.echo("Summary:")
    if started:
        ready_count = sum(1 for aid in started if is_agent_running(aid))
        click.echo(f"  Started: {ready_count}/{len(started)}")
    if skipped:
        click.echo(f"  Already running: {len(skipped)}")
    if failed:
        click.echo(f"  Failed: {len(failed)}")

    # Show running agents
    click.echo()
    click.echo("Running agents:")
    for agent in workflow.agents:
        agent_id = agent.agent or agent.id
        status = "running" if is_agent_running(agent_id) else "not running"
        click.echo(f"  {agent_id}: {status}")


@agents.command("stop")
@click.argument("agent_id", required=False)
@click.option("--all", "stop_all", is_flag=True, help="Stop all running agents")
def agents_stop(agent_id: str | None, stop_all: bool) -> None:
    """Stop a running persistent agent.

    Examples:

        # Stop specific agent
        orchestrator agents stop claude

        # Stop all running agents
        orchestrator agents stop --all
    """
    from orchestrator.lib.agent_client import stop_agent, stop_all_agents, list_running_agents

    if stop_all:
        count = stop_all_agents()
        click.echo(f"Stopped {count} agent(s).")
        return

    if not agent_id:
        # Show running agents and ask
        running = list_running_agents()
        if not running:
            click.echo("No agents running.")
            return

        click.echo("Running agents:")
        for agent in running:
            click.echo(f"  {agent['id']} (PID: {agent['pid']})")
        click.echo()
        click.echo("Specify an agent ID or use --all to stop all.")
        return

    from orchestrator.lib.agent_client import is_agent_running

    if not is_agent_running(agent_id):
        click.echo(f"Agent '{agent_id}' is not running.")
        sys.exit(1)

    if stop_agent(agent_id):
        click.echo(f"Agent '{agent_id}' stopped.")
    else:
        click.echo(f"Failed to stop agent '{agent_id}'.")
        sys.exit(1)


@agents.command("running")
@json_option
def agents_running(use_json: bool) -> None:
    """List running persistent agent servers."""
    from orchestrator.lib.agent_client import list_running_agents

    running = list_running_agents()

    if use_json:
        click.echo(json.dumps(running, indent=2))
        return

    if not running:
        click.echo("No agents running.")
        click.echo()
        click.echo("Start an agent with: orchestrator agents start <name>")
        return

    click.echo(f"{'AGENT ID':<20} {'PID':<10} {'SOCKET'}")
    click.echo("-" * 70)
    for agent in running:
        click.echo(f"{agent['id']:<20} {agent['pid'] or 'N/A':<10} {agent['socket']}")


@agents.command("ping")
@click.argument("agent_id")
def agents_ping(agent_id: str) -> None:
    """Test connectivity to a running agent."""
    import asyncio
    from orchestrator.lib.agent_client import AgentClient, AgentNotRunning

    async def do_ping():
        client = AgentClient(agent_id)
        try:
            await client.connect()
            if await client.ping():
                click.echo(f"Agent '{agent_id}' is responsive.")
                return True
            else:
                click.echo(f"Agent '{agent_id}' did not respond.")
                return False
        except AgentNotRunning as e:
            click.echo(f"Agent '{agent_id}' is not running: {e}")
            return False
        finally:
            await client.close()

    success = asyncio.run(do_ping())
    sys.exit(0 if success else 1)


@agents.command("test")
@click.argument("agent_id")
@click.argument("message")
def agents_test(agent_id: str, message: str) -> None:
    """Send a test message to a running agent.

    Examples:

        orchestrator agents test claude "Hello, how are you?"
    """
    import asyncio
    from orchestrator.lib.agent_client import send_prompt, AgentNotRunning

    async def do_test():
        try:
            def on_chunk(chunk: str) -> None:
                click.echo(chunk, nl=False)

            click.echo(f"Sending to '{agent_id}'...")
            click.echo("-" * 40)

            response = await send_prompt(agent_id, message, on_chunk=on_chunk)

            click.echo()
            click.echo("-" * 40)
            click.echo(f"Response: {len(response)} chars")
            return True

        except AgentNotRunning as e:
            click.echo(f"Error: {e}")
            return False
        except TimeoutError as e:
            click.echo(f"Timeout: {e}")
            return False

    success = asyncio.run(do_test())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    cli()
