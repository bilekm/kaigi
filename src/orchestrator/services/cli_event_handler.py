"""CLI-based event handler for interactive conversations.

Provides readline support for history navigation, line editing,
and tab completion.
"""

from __future__ import annotations

import asyncio
import atexit
from pathlib import Path

import click

from orchestrator.services.event_handler import ConversationEventHandler


class CliEventHandler(ConversationEventHandler):
    """Event handler that outputs to CLI using Click with readline support."""

    def __init__(self, show_agent_output: bool = True, max_output_lines: int = 0):
        """Initialize CLI event handler.

        Args:
            show_agent_output: Whether to display agent responses
            max_output_lines: Maximum lines of agent output to show (0 = unbounded/full output)
        """
        self.show_agent_output = show_agent_output
        self.max_output_lines = max_output_lines
        self._setup_readline()

    # Color helper methods
    def _cmd(self, text: str) -> str:
        """Format command text in bright yellow."""
        return click.style(text, fg="bright_yellow")

    def _agent(self, text: str) -> str:
        """Format agent name in green."""
        return click.style(text, fg="green")

    def _error(self, text: str) -> str:
        """Format error text in red."""
        return click.style(text, fg="red")

    def _round(self, text: str) -> str:
        """Format round number in cyan."""
        return click.style(text, fg="cyan")

    def _consensus(self, text: str) -> str:
        """Format consensus text in green bold."""
        return click.style(text, fg="green", bold=True)

    def _setup_readline(self) -> None:
        """Configure readline for history and tab completion."""
        try:
            import readline
        except ImportError:
            return  # readline not available

        # History file
        history_file = Path.home() / ".orchestrator" / "history"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        if history_file.exists():
            try:
                readline.read_history_file(str(history_file))
            except OSError:
                pass

        # Save history on exit
        def save_history():
            try:
                readline.set_history_length(1000)
                readline.write_history_file(str(history_file))
            except OSError:
                pass
        atexit.register(save_history)

        # Tab completion for slash commands
        commands = [
            "/help", "/agents", "/quit", "/add", "/remove",
            "/model", "/persona", "/save", "/config", "approve"
        ]

        def completer(text: str, state: int) -> str | None:
            options = [c for c in commands if c.startswith(text)]
            if state < len(options):
                return options[state]
            return None

        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")

    async def _get_input(self, prompt: str = "> ") -> str:
        """Get input from user with readline support.

        Args:
            prompt: The prompt string to display

        Returns:
            User input string
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input, prompt)

    def on_conversation_start(
        self,
        workflow_name: str,
        topic: str,
        agents: list[str],
        collaboration: str,
        lead: str | None = None,
    ) -> None:
        """Display conversation startup info."""
        click.echo(f"Starting conversation: {workflow_name}")
        click.echo(f"Topic: {topic[:100]}...")
        if collaboration == "orchestrated":
            click.echo(f"Mode: Orchestrated (Lead: {self._agent(lead or '')})")
            # Filter out lead from agents list for display
            team = [a for a in agents if a != lead]
            click.echo(f"Team: {', '.join(self._agent(a) for a in team)}")
        else:
            click.echo(f"Mode: Team collaboration")
            click.echo(f"Agents: {', '.join(self._agent(a) for a in agents)}")
        click.echo()

    def on_round_start(self, round_num: int) -> None:
        """Display round header."""
        click.echo(f"--- Round {self._round(str(round_num))} ---")

    def on_agent_turn_start(self, agent_id: str) -> None:
        """Show agent is thinking."""
        click.echo(f"  [{self._agent(agent_id)}] thinking...", nl=False)

    def on_agent_turn_complete(
        self,
        agent_id: str,
        duration: float,
        output: str,
    ) -> None:
        """Show agent response."""
        click.echo(f" done ({duration:.1f}s)")

        if not self.show_agent_output:
            return

        # Show full or truncated response
        if self.max_output_lines == 0:
            # Show full output
            click.echo(output)
        else:
            # Show truncated response
            lines = output.split("\n")
            for i, line in enumerate(lines[:self.max_output_lines]):
                click.echo(f"    {line}")
            if len(lines) > self.max_output_lines:
                remaining = len(lines) - self.max_output_lines
                click.echo(f"    ... ({remaining} more lines)")
        click.echo()

    def on_agent_turn_error(
        self,
        agent_id: str,
        error: str,
    ) -> None:
        """Show agent error."""
        click.echo(f" {self._error('ERROR')}: {error}")

    def on_consensus_reached(self, content: str) -> None:
        """Display consensus banner."""
        click.echo()
        click.secho("=" * 50, fg="green")
        click.secho("CONSENSUS REACHED", fg="green", bold=True)
        click.secho("=" * 50, fg="green")
        if content:
            click.echo(content)
        click.secho("=" * 50, fg="green")
        click.echo("[Type 'approve' to accept, or provide feedback to continue]")

    async def prompt_user_approval(self, content: str) -> bool:
        """Prompt user to approve consensus."""
        response = await self._get_input("> ")

        if response.lower() == "approve":
            return True

        return False

    async def prompt_topic(self, workflow_name: str, agents: list[str]) -> str | None:
        """Prompt user for topic."""
        from orchestrator.lib.agent_client import list_running_agents

        click.echo(f"Starting conversation: {workflow_name}")
        click.echo(f"Agents: {', '.join(self._agent(a) for a in agents)}")
        click.echo()
        click.echo(f"Enter topic/prompt ({self._cmd('/help')} for commands, 'quit' to exit):")

        while True:
            topic = await self._get_input("> ")
            topic = topic.strip()

            if topic.lower() in ("quit", "/quit"):
                return None
            if topic == "/help":
                self._show_topic_help()
                continue
            if topic == "/agents":
                self._show_running_agents()
                continue
            if topic.startswith("/"):
                click.echo(f"Unknown command: {self._cmd(topic)}. Type {self._cmd('/help')} for available commands.")
                continue
            if topic:
                return topic

            click.echo("Please enter a topic or command.")

    def _show_topic_help(self) -> None:
        """Show help for topic prompt."""
        click.echo()
        click.echo("Available commands:")
        click.echo(f"  {self._cmd('/agents')}    Show running agent servers")
        click.echo(f"  {self._cmd('/help')}      Show this help")
        click.echo(f"  {self._cmd('/quit')}      Exit")
        click.echo()

    def _show_running_agents(self) -> None:
        """Show running agents."""
        from orchestrator.lib.agent_client import list_running_agents

        click.echo()
        running = list_running_agents()
        if not running:
            click.echo("No agents running.")
        else:
            click.echo("Running agents:")
            for agent in running:
                click.echo(f"  {self._agent(agent['id']):<20} PID: {agent['pid'] or 'N/A':<10}")
        click.echo()

    async def prompt_user_input(self) -> str | None:
        """Prompt user for input between rounds."""
        click.echo(f"[Enter to continue, {self._cmd('/help')} for commands, or type message]")

        user_input = await self._get_input("> ")

        stripped = user_input.strip()

        if stripped.lower() == "quit":
            return "__quit__"

        return stripped if stripped else None

    def on_command_result(self, command: str, result: str) -> None:
        """Display command result."""
        click.echo(result)

    def on_conversation_complete(
        self,
        status: str,
        rounds: int,
        consensus_status: str | None = None,
    ) -> None:
        """Display completion message."""
        pass  # CliEventHandler doesn't need special completion output

    def on_max_rounds_reached(self, max_rounds: int) -> None:
        """Display max rounds message."""
        click.echo(f"\n{self._error('Max rounds reached without consensus.')}.")
