"""Conversation mode commands - converse, say, transcript."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from orchestrator.lib.errors import OrchestratorError, print_error
from orchestrator.commands.utils import _auto_start_agents


def json_option(f):
    """Add --json option to a command."""
    return click.option(
        "--json",
        "use_json",
        is_flag=True,
        help="Output in JSON format",
    )(f)


def converse_command() -> click.Command:
    """Create the converse command (as a function for easier registration)."""
    @click.command()
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
                event_handler=None if non_interactive else None,  # Will default to CliEventHandler
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

    return converse


@click.command()
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


@click.command()
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


# Export converse as a function to allow lazy evaluation
converse = converse_command()
