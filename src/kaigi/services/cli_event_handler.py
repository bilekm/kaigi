"""CLI-based event handler for interactive conversations.

Provides prompt_toolkit support for history navigation, line editing,
automatic bracketed paste mode (multi-line input), and tab completion.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from kaigi.services.event_handler import ConversationEventHandler


class CliEventHandler(ConversationEventHandler):
    """Event handler that outputs to CLI using Click with prompt_toolkit support."""

    def __init__(self, show_agent_output: bool = True, max_output_lines: int = 0):
        """Initialize CLI event handler.

        Args:
            show_agent_output: Whether to display agent responses
            max_output_lines: Maximum lines of agent output to show (0 = unbounded/full output)
        """
        self.show_agent_output = show_agent_output
        self.max_output_lines = max_output_lines
        self._workflow_agents: dict[str, str] = {}  # Maps agent type to role ID
        self._setup_session()

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

    def _setup_session(self) -> None:
        """Configure prompt_toolkit PromptSession for history and tab completion."""
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import FileHistory
        except ImportError:
            # prompt_toolkit not available, will fall back to basic input
            self.session = None
            return

        # History file
        history_dir = Path.home() / ".kaigi"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_file = history_dir / "history"

        # Tab completion for slash commands
        commands = [
            "/help", "/agents", "/quit", "/add", "/remove",
            "/model", "/persona", "/save", "/config", "approve"
        ]
        command_completer = WordCompleter(commands, ignore_case=True)

        # Create session with history and completer
        self.session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=command_completer,
            enable_history_search=True,
        )

    async def _get_input(self, prompt: str = "> ") -> str:
        """Get input from user with prompt_toolkit support.

        Uses prompt_toolkit if available for:
        - Multi-line paste support (automatic bracketed paste detection)
        - History navigation (up/down arrows)
        - Tab completion
        Falls back to basic input() if prompt_toolkit unavailable.

        Args:
            prompt: The prompt string to display

        Returns:
            User input string
        """
        if self.session is not None:
            # Use prompt_toolkit for proper multi-line paste support
            return await self.session.prompt_async(prompt)
        else:
            # Fallback to basic input
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
            click.echo("Mode: Team collaboration")
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
        click.echo("[Type 'approve' to accept, 'approve <agent-id>' to choose executor,")
        click.echo(" or 'approve <agent-id> <model>' to override model, or provide feedback to continue]")

    def on_consensus_too_early(self, current_round: int, min_rounds: int) -> None:
        """Display message when consensus detected too early."""
        click.echo()
        click.secho(
            f"⚠️  Consensus proposal detected in round {current_round}, "
            f"waiting for team review (min {min_rounds} rounds required)...",
            fg="yellow",
        )

    async def prompt_user_approval(self, content: str) -> str:
        """Prompt user to approve consensus."""
        response = await self._get_input("> ")
        return response.strip()

    async def prompt_topic(self, workflow_name: str, agents: list[str], agent_types: dict[str, str] | None = None) -> str | None:
        """Prompt user for topic."""

        # Store the agent type mapping for display in _show_running_agents
        if agent_types:
            self._workflow_agents = agent_types

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
        from kaigi.lib.agent_client import list_running_agents

        click.echo()
        running = list_running_agents()
        if not running:
            click.echo("No agents running.")
        else:
            click.echo("Running agents:")
            for agent in running:
                agent_type = agent['id']  # e.g., 'claude', 'copilot', 'glm'
                pid = agent['pid'] or 'N/A'

                # Get role ID from workflow mapping if available
                role_id = self._workflow_agents.get(agent_type, '')

                if role_id:
                    # Show: role_id - agent_type   PID: xxx
                    # Pad plain text before styling (ANSI codes break padding)
                    role_padded = f"{role_id:<10}"
                    agent_padded = f"{agent_type:<10}"
                    click.echo(f"  {self._agent(role_padded)} - {self._agent(agent_padded)}   PID: {pid}")
                else:
                    # No workflow mapping, just show agent type
                    agent_padded = f"{agent_type:<20}"
                    click.echo(f"  {self._agent(agent_padded)}   PID: {pid}")
        click.echo()

    async def prompt_user_input(self) -> str | None:
        """Prompt user for input between rounds."""
        click.echo(f"[Enter to continue, {self._cmd('/help')} for commands, or type message]")

        user_input = await self._get_input("> ")

        stripped = user_input.strip()

        if stripped.lower() == "quit":
            return "__quit__"

        return stripped if stripped else None

    async def prompt_user_question(
        self,
        question: str,
        options: list[dict[str, str]] | None = None,
    ) -> str:
        """Prompt user a question during agent execution."""
        click.echo()
        click.echo(f"[{self._cmd('Question from agent')}]")
        click.echo(question)

        if options:
            click.echo()
            click.echo("Options:")
            for i, option in enumerate(options, 1):
                label = option.get("label", f"{i}")
                description = option.get("description", "")
                if description:
                    click.echo(f"  {i}. {self._cmd(label)} - {description}")
                else:
                    click.echo(f"  {i}. {self._cmd(label)}")
            click.echo(f"  {len(options) + 1}. Other (type custom response)")
            click.echo()

            while True:
                response = await self._get_input("Choose option (or type custom response): ")
                response = response.strip()

                # Check if it's a number
                try:
                    choice = int(response)
                    if 1 <= choice <= len(options):
                        return options[choice - 1].get("label", str(choice))
                except ValueError:
                    pass

                # Return custom response if not empty
                if response:
                    return response

                click.echo("Please enter a choice or custom response.")
        else:
            # No options, free-form input
            response = await self._get_input("Your response: ")
            return response.strip()

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
