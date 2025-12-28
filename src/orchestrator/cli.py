"""Click CLI entry points for orchestrator."""

from __future__ import annotations

import json
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
def converse(workflow_file: Path, use_json: bool, non_interactive: bool) -> None:
    """Start a conversation between AI agents.

    The workflow file must have mode: conversation.
    Agents will discuss the topic until consensus is reached or max_rounds.

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


if __name__ == "__main__":
    cli()
