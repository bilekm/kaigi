"""Click CLI entry points for kaigi."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from kaigi import __version__
from kaigi.lib.errors import KaigiError, print_error


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
PROJECT_CONFIG = ".kaigi/workflow.yaml"


def _run_default() -> None:
    """Run default behavior when no subcommand given."""
    from kaigi.commands.conversation import _auto_start_agents
    from kaigi.services.parser import parse_workflow_file
    from kaigi.services.conversation import execute_conversation
    from kaigi.models.workflow import ConversationWorkflow
    from kaigi.lib.errors import workflow_invalid

    config_path = Path.cwd() / PROJECT_CONFIG

    if config_path.exists():
        # Run conversation with project config
        click.echo(f"Using project config: {config_path}")
        _run_conversation(config_path)
    else:
        # No config found - run init wizard
        _run_init_wizard()


def _run_conversation(config_path: Path) -> None:
    """Run a conversation workflow from a config file."""
    from kaigi.commands.conversation import _auto_start_agents
    from kaigi.services.parser import parse_workflow_file
    from kaigi.services.conversation import execute_conversation
    from kaigi.models.workflow import ConversationWorkflow
    from kaigi.lib.errors import workflow_invalid

    try:
        workflow = parse_workflow_file(config_path)

        if not isinstance(workflow, ConversationWorkflow):
            raise workflow_invalid(
                "Project config must be a conversation workflow. "
                "Use 'kaigi run' for pipeline workflows."
            )

        _auto_start_agents(workflow, use_json=False)

        yaml_content = config_path.read_text()
        result = execute_conversation(workflow, yaml_content)

        if result["status"] == "completed":
            click.echo()
            click.echo(f"Conversation completed. Rounds: {result['rounds_completed']}")
            if result.get("consensus_status") == "approved":
                click.echo("Consensus: Approved")
        else:
            click.echo(f"Conversation {result['status']}")

    except KaigiError as e:
        print_error(e, False)
        sys.exit(1)


def _run_init_wizard() -> None:
    """Run the initialization wizard to create project config."""
    from kaigi.services.parser import parse_workflow_file
    from kaigi.models.workflow import ConversationWorkflow

    click.echo("No .kaigi/workflow.yaml found.")
    click.echo()
    click.echo("Choose an option:")
    click.echo("  1. Create a conversation workflow")
    click.echo("  2. Exit")
    click.echo()

    choice = input("Choice [1-2]: ").strip()

    if choice == "1":
        workflow_name = input("Workflow name: ").strip() or "my-conversation"
        agent_refs = input("Agent refs (comma-separated, e.g., claude,copilot): ").strip()
        agent_refs = [a.strip() for a in agent_refs.split(",") if a.strip()] if agent_refs else ["claude"]

        agents = []
        for i, ref in enumerate(agent_refs):
            agents.append({
                "id": ref if i == 0 else f"{ref}-{i+1}",
                "agent": ref,
                "persona": f"You are {ref}."
            })

        topic = input("Discussion topic: ").strip() or "Discuss a topic together"

        config = f"""\
name: {workflow_name}
version: "1.0"
mode: conversation
description: {workflow_name}
agents:
"""
        for agent in agents:
            config += f"""  - id: {agent['id']}
    agent: {agent['agent']}
    persona: "{agent['persona']}"
"""

        config += f"""\
topic: |
  {topic}

max_rounds: 5
consensus_keyword: "AGREED:"
"""

        project_dir = Path.cwd() / ".kaigi"
        project_dir.mkdir(parents=True, exist_ok=True)
        config_path = project_dir / "workflow.yaml"

        if config_path.exists():
            overwrite = input(f"{config_path} exists. Overwrite? [y/N]: ").strip().lower()
            if overwrite != "y":
                click.echo("Aborted.")
                sys.exit(1)

        config_path.write_text(config)
        click.echo()
        click.echo(f"Created: {config_path}")
        click.echo()
        click.echo("Run with: kaigi")

    else:
        click.echo("Exiting.")
        sys.exit(0)


# === Main CLI Group ===

@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="kaigi")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Kaigi (会議) - CLI-based multi-agent collaboration platform."""
    if ctx.invoked_subcommand is None:
        _run_default()


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing config")
def init(force: bool) -> None:
    """Initialize a new kaigi project."""
    project_dir = Path.cwd() / ".kaigi"

    if project_dir.exists() and not force:
        click.echo(f"{project_dir} already exists.")
        click.echo("Use --force to overwrite.")
        sys.exit(1)

    _run_init_wizard()


# === Import Commands from Modules ===
# Workflow commands
from kaigi.commands.workflow import (
    validate,
    run,
    status,
    list_executions,
    output,
    cancel,
    retry,
    cleanup,
)

# Conversation commands
from kaigi.commands.conversation import (
    converse,
    say,
    transcript,
)

# Agent commands (group)
from kaigi.commands.agents import agents

# Register all commands with the CLI
cli.add_command(validate)
cli.add_command(run)
cli.add_command(status)
cli.add_command(list_executions)
cli.add_command(output)
cli.add_command(cancel)
cli.add_command(retry)
cli.add_command(cleanup)

cli.add_command(converse)
cli.add_command(say)
cli.add_command(transcript)

cli.add_command(agents)


if __name__ == "__main__":
    cli()
