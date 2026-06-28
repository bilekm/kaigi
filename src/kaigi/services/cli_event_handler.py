"""CLI-based event handler for interactive conversations.

Provides prompt_toolkit support for history navigation, line editing,
automatic bracketed paste mode (multi-line input), and tab completion.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import click

from kaigi.services.event_handler import ConversationEventHandler


class CliEventHandler(ConversationEventHandler):
    """Event handler that outputs to CLI using Click with prompt_toolkit support."""

    def __init__(
        self,
        show_agent_output: bool = True,
        max_output_lines: int = 0,
        enable_color: bool = True,
    ):
        """Initialize CLI event handler.

        Args:
            show_agent_output: Whether to display agent responses
            max_output_lines: Maximum lines of agent output to show (0 = unbounded/full output)
            enable_color: Whether to enable colorized output (default: True)
        """
        self.show_agent_output = show_agent_output
        self.max_output_lines = max_output_lines
        self.enable_color = enable_color and not os.environ.get("NO_COLOR")
        self._workflow_agents: dict[str, str] = {}  # Maps agent type to role ID
        self.pygments_style = self._determine_style()
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

    def _determine_style(self) -> str:
        """Determine Pygments style based on environment or terminal background detection.

        Priority:
        1. KAIGI_STYLE environment variable (explicit override)
        2. KAIGI_BACKGROUND environment variable (light/dark/auto)
        3. Auto-detect terminal background color
        4. Default to 'pastie' (dark theme, better than monokai for code)

        Returns:
            Pygments style name
        """
        # 1. Explicit style override
        style_override = os.environ.get("KAIGI_STYLE")
        if style_override:
            return style_override

        # 2. Background setting (light/dark/auto)
        background = os.environ.get("KAIGI_BACKGROUND", "").lower()

        if background == "light":
            # Better default for light backgrounds
            return "vs"
        elif background == "dark":
            # Better default for dark backgrounds
            return "pastie"
        elif background == "auto":
            # Auto-detect
            detected_bg = self._detect_terminal_background()
            if detected_bg == "light":
                return "vs"
            else:
                # dark or unknown defaults to pastie
                return "pastie"

        # 3. Auto-detect by default
        detected_bg = self._detect_terminal_background()
        if detected_bg == "light":
            return "vs"
        elif detected_bg == "dark":
            return "pastie"

        # 4. Default to dark theme (pastie)
        return "pastie"

    def _detect_terminal_background(self) -> str:
        """Detect terminal background color using terminal queries.

        Returns:
            'light', 'dark', or 'unknown'
        """
        try:
            # Try to read from COLORFGBG environment variable (set by some terminals)
            # This works even when not a TTY, useful for testing
            colorfgbg = os.environ.get("COLORFGBG", "")
            if colorfgbg:
                # Format is usually "fg;bg" or "fg;bg;default_fg;default_bg"
                parts = colorfgbg.split(";")
                if len(parts) >= 2:
                    bg_color = parts[1]
                    # Light backgrounds typically have values >= 8
                    # Dark backgrounds typically have values < 8
                    try:
                        bg_num = int(bg_color)
                        if bg_num >= 8:
                            return "light"
                        else:
                            return "dark"
                    except ValueError:
                        pass

            # If no COLORFGBG, try terminal query (only works in TTY)
            import sys

            if sys.stdout.isatty():
                # Could implement OSC 11 query here in the future
                # For now, just return unknown
                pass

        except Exception:
            pass

        return "unknown"

    def _setup_session(self) -> None:
        """Configure prompt_toolkit PromptSession for history and tab completion."""
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import Completer, Completion
            from prompt_toolkit.history import FileHistory
        except ImportError:
            # prompt_toolkit not available, will fall back to basic input
            self.session = None
            return

        # History file
        history_dir = Path.home() / ".kaigi"
        history_dir.mkdir(parents=True, exist_ok=True)
        history_file = history_dir / "history"

        # Slash commands with descriptions
        self._commands = {
            "/help": "Show available commands",
            "/agents": "List current agents",
            "/quit": "Exit conversation",
            "/clear": "Start new topic (clear history)",
            "/add": "Add agent: /add <agent> [persona]",
            "/remove": "Remove agent: /remove <agent-id>",
            "/model": "Change model: /model <agent-id> <model>",
            "/persona": "Update persona: /persona <agent-id> <text>",
            "/save": "Save agents to project config",
            "/config": "Show config file locations",
        }

        # Custom completer that shows commands when line starts with "/"
        class SlashCommandCompleter(Completer):
            def __init__(self, commands: dict[str, str]):
                self.commands = commands

            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                # Only complete if line starts with "/"
                if not text.startswith("/"):
                    return

                # Find matching commands
                for cmd, description in self.commands.items():
                    if cmd.startswith(text.lower()):
                        yield Completion(
                            cmd,
                            start_position=-len(text),
                            display_meta=description,
                        )

        self._command_completer = SlashCommandCompleter(self._commands)

        # Create session with history and completer
        self.session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=self._command_completer,
            complete_while_typing=True,
            complete_in_thread=True,  # Better async support
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

        # Apply colorization to output
        colored_output = self._colorize_response(output)

        # Show full or truncated response
        if self.max_output_lines == 0:
            # Show full output
            click.echo(colored_output)
        else:
            # Show truncated response
            lines = colored_output.split("\n")
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
        click.echo(
            "[Type 'approve' to accept, 'approve <agent-id>' to choose executor,"
        )
        click.echo(
            " or 'approve <agent-id> <model>' to override model, "
            "or provide feedback to continue]"
        )

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

    async def prompt_topic(
        self,
        workflow_name: str,
        agents: list[str],
        agent_types: dict[str, str] | None = None,
    ) -> str | None:
        """Prompt user for topic."""

        # Store the agent type mapping for display in _show_running_agents
        if agent_types:
            self._workflow_agents = agent_types

        click.echo(f"Starting conversation: {workflow_name}")
        click.echo(f"Agents: {', '.join(self._agent(a) for a in agents)}")
        click.echo()
        click.echo(f"Enter topic/prompt ({self._cmd('/help')} for commands, {self._cmd('/<agent-id> <topic>')} to start with specific agent):")

        while True:
            topic = await self._get_input("> ")
            topic = topic.strip()

            if topic.lower() == "/quit":
                return None
            if topic in ("/", "/help"):
                self._show_topic_help()
                continue
            if topic == "/agents":
                self._show_running_agents()
                continue
            if topic.startswith("/"):
                # Check if it's /agentid <prompt> pattern
                match = re.match(r'^/([a-zA-Z0-9_-]+)\s+(.+)$', topic, re.DOTALL)
                if match:
                    agent_id = match.group(1)
                    # If it matches a valid agent, allow it through
                    if agent_id in agents:
                        return topic

                # Unknown command
                click.echo(
                    f"Unknown command: {self._cmd(topic)}. "
                    f"Type {self._cmd('/')} or {self._cmd('/help')} for available commands."
                )
                continue
            if topic:
                return topic

            click.echo("Please enter a topic or command.")

    def _show_topic_help(self) -> None:
        """Show help for topic prompt."""
        click.echo()
        click.echo("Available commands:")
        click.echo(f"  {self._cmd('/agents')}              Show running agent servers")
        click.echo(f"  {self._cmd('/help')}                Show this help")
        click.echo(f"  {self._cmd('/quit')}                Exit")
        click.echo()
        click.echo("Start conversation with specific agent:")
        click.echo(f"  {self._cmd('/<agent-id> <topic>')}  Start with specified agent")
        click.echo(f"  Example: {self._cmd('/analyst What is the test coverage?')}")
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
                    agent_line = (
                        f"  {self._agent(role_padded)} - "
                        f"{self._agent(agent_padded)}   PID: {pid}"
                    )
                    click.echo(agent_line)
                else:
                    # No workflow mapping, just show agent type
                    agent_padded = f"{agent_type:<20}"
                    click.echo(f"  {self._agent(agent_padded)}   PID: {pid}")
        click.echo()

    def _show_commands(self) -> None:
        """Show available slash commands."""
        click.echo()
        click.echo("Available commands:")
        for cmd, desc in self._commands.items():
            click.echo(f"  {self._cmd(cmd):<20} {desc}")
        click.echo()

    async def prompt_user_input(self) -> str | None:
        """Prompt user for input between rounds."""
        click.echo(f"[Enter to continue, {self._cmd('/')} or {self._cmd('/help')} for commands, or type message]")

        user_input = await self._get_input("> ")

        stripped = user_input.strip()

        # Handle /quit command (must have slash prefix)
        if stripped.lower() == "/quit":
            return "__quit__"

        # Show commands if user types just "/"
        if stripped == "/":
            self._show_commands()
            return await self.prompt_user_input()

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

    async def on_agent_rate_limited(
        self,
        agent_id: str,
        reset_time: str,
    ) -> str:
        """Handle rate limit - prompt user for action."""
        click.echo()
        click.echo(f"{self._error('Rate limit hit for')} {self._agent(agent_id)}")
        if reset_time:
            click.echo(f"  Resets at: {reset_time}")
        click.echo()
        click.echo("Options:")
        click.echo(f"  {self._cmd('skip')}     - Skip this agent for this round")
        click.echo(f"  {self._cmd('wait')}     - Wait and retry")
        click.echo(f"  {self._cmd('replace')}  - Replace with another agent")
        click.echo(f"  {self._cmd('quit')}     - End conversation")
        click.echo()

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.session.prompt(
                    "Choice: ",
                    style=self.prompt_style,
                ).strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            return "quit"

        if response.startswith("replace"):
            # Prompt for replacement agent
            click.echo()
            click.echo("Available agents from settings:")
            try:
                from kaigi.lib.settings import load_agent_settings
                settings = load_agent_settings()
                for name in settings.list_agents():
                    click.echo(f"  - {self._agent(name)}")
            except Exception:
                pass

            try:
                replacement = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.session.prompt(
                        "Replace with: ",
                        style=self.prompt_style,
                    ).strip()
                )
                return f"replace:{replacement}"
            except (EOFError, KeyboardInterrupt):
                return "skip"

        return response if response in ("skip", "wait", "quit") else "skip"

    async def on_paused(self) -> str:
        """Handle pause - prompt user for action."""
        click.echo()
        click.echo(f"{self._cmd('Paused')} - conversation paused after current turn")
        click.echo()
        click.echo("Options:")
        click.echo(f"  {self._cmd('continue')} or Enter - Resume conversation")
        click.echo(f"  {self._cmd('/command')}         - Execute a command (e.g., /help, /clear)")
        click.echo(f"  {self._cmd('quit')}             - End conversation")
        click.echo()

        try:
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.session.prompt(
                    "> ",
                    style=self.prompt_style,
                ).strip()
            )
        except (EOFError, KeyboardInterrupt):
            return "quit"

        if not response or response.lower() == "continue":
            return "continue"
        elif response.lower() == "quit":
            return "quit"
        else:
            # Could be a command or feedback
            return response

    def _colorize_response(self, text: str) -> str:
        """Apply syntax highlighting to markdown code blocks in agent response.

        Uses Pygments to highlight code blocks within markdown.
        Gracefully degrades if Pygments is unavailable.

        Args:
            text: Agent response text that may contain markdown code blocks

        Returns:
            Text with syntax-highlighted code blocks, or original text on failure
        """
        if not self.enable_color:
            return text

        # Try to import pygments
        try:
            from pygments import highlight
            from pygments.formatters import TerminalFormatter
            from pygments.lexers import get_lexer_by_name, guess_lexer
        except ImportError:
            # Pygments not available, return plain text
            return text

        # Pattern for markdown code blocks: ```lang ... ```
        # Matches: ```python\ncode\n``` or ```python\ncode\n``` (with language spec)
        code_block_pattern = re.compile(
            r'```(\w*)\n(.*?)\n```',
            re.DOTALL
        )

        def highlight_code_block(match: re.Match[str]) -> str:
            """Highlight a single code block."""
            lang = match.group(1)
            code = match.group(2)

            if not code.strip():
                return match.group(0)  # Return original if empty

            try:
                # Try to get lexer by language name
                if lang:
                    lexer = get_lexer_by_name(lang, stripall=True)
                else:
                    # No language specified, try to guess
                    lexer = guess_lexer(code)
            except Exception:
                # Language not found or guess failed, use plain text
                lexer = get_lexer_by_name('text', stripall=True)

            # Highlight with terminal formatter using determined style
            try:
                highlighted = highlight(code, lexer, TerminalFormatter(style=self.pygments_style))
                return highlighted.rstrip()
            except Exception:
                # Highlighting failed, return original code block
                return match.group(0)

        # Replace all code blocks with highlighted versions
        highlighted_text = code_block_pattern.sub(highlight_code_block, text)

        return highlighted_text
