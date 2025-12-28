"""Conversation executor for multi-agent discussions."""

from __future__ import annotations

import asyncio
import glob
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import click

from orchestrator.lib.errors import (
    OrchestratorError,
    agent_failed,
    agent_timeout,
    conversation_error,
    workflow_invalid,
    workflow_locked,
)
from orchestrator.lib.logging import get_logger
from orchestrator.lib.prompts import (
    build_lead_prompt,
    build_orchestrated_system_message,
    build_team_member_prompt,
    build_team_prompt,
    build_team_system_message,
    format_context_files,
)
from orchestrator.lib.settings import get_agent_config
from orchestrator.lib.signals import signal_handler_context
from orchestrator.models.execution import (
    ConversationRecord,
    ConsensusStatus,
    ExecutionStatus,
    MessageRole,
    StepStatus,
    TurnResult,
)
from orchestrator.models.workflow import ConversationAgent, ConversationWorkflow
from orchestrator.services.store import WorkflowStore


# Global reference for signal handling
_current_process: asyncio.subprocess.Process | None = None
_cancelled: bool = False


def _handle_signal(signum: int, frame: Any) -> None:
    """Handle termination signals."""
    global _cancelled
    _cancelled = True
    if _current_process and _current_process.returncode is None:
        _current_process.terminate()


class ConversationExecutor:
    """Executes conversation-mode workflows."""

    def __init__(
        self,
        workflow: ConversationWorkflow,
        yaml_content: str,
        store: WorkflowStore | None = None,
        interactive: bool = True,
    ):
        self.workflow = workflow
        self.yaml_content = yaml_content
        self.store = store or WorkflowStore()
        self.interactive = interactive
        self.logger = get_logger()

    def execute(self) -> dict[str, Any]:
        """Execute the conversation synchronously."""
        return asyncio.run(self._execute_async())

    async def _execute_async(self) -> dict[str, Any]:
        """Main conversation execution loop."""
        global _cancelled
        _cancelled = False

        workflow_id = str(uuid4())[:8]

        # Acquire lock
        execution_id = str(uuid4())[:8]
        if not self.store.acquire_global_lock(execution_id):
            raise workflow_locked()

        try:
            # Initialize
            self.store.save_conversation_workflow(
                workflow_id, self.workflow, self.yaml_content
            )

            record = ConversationRecord.from_workflow(
                workflow_id=workflow_id,
                workflow_name=self.workflow.name,
                topic=self.workflow.topic,
                max_rounds=self.workflow.max_rounds,
            )
            record.id = execution_id

            # Load context files
            record.context_files_content = self._load_context_files()

            # Add system message with topic
            agent_ids = [a.id for a in self.workflow.agents]
            if self.workflow.collaboration == "orchestrated":
                lead_id = self.workflow.lead or ""
                team_ids = [a.id for a in self.workflow.get_team_agents()]
                system_msg = build_orchestrated_system_message(
                    self.workflow.topic, lead_id, team_ids
                )
            else:
                system_msg = build_team_system_message(self.workflow.topic, agent_ids)
            record.add_message(MessageRole.SYSTEM, system_msg)

            self.store.create_conversation(workflow_id, record)

            # Use context manager for signal handlers (restores on exit)
            with signal_handler_context(_handle_signal):
                # Start
                record.start()
                self.store.save_conversation(workflow_id, record)

                self.logger.info(
                    "Conversation started",
                    workflow_name=self.workflow.name,
                    agents=agent_ids,
                )

                if self.interactive:
                    click.echo(f"Starting conversation: {self.workflow.name}")
                    click.echo(f"Topic: {self.workflow.topic[:100]}...")
                    if self.workflow.collaboration == "orchestrated":
                        click.echo(f"Mode: Orchestrated (Lead: {self.workflow.lead})")
                        team_ids = [a.id for a in self.workflow.get_team_agents()]
                        click.echo(f"Team: {', '.join(team_ids)}")
                    else:
                        click.echo(f"Mode: Team collaboration")
                        click.echo(f"Agents: {', '.join(agent_ids)}")
                    click.echo()

                # Main loop
                while record.current_round < self.workflow.max_rounds:
                    if _cancelled:
                        record.cancel()
                        self.store.save_conversation(workflow_id, record)
                        break

                    record.current_round += 1

                    if self.interactive:
                        click.echo(f"--- Round {record.current_round} ---")

                    # Determine turn order based on collaboration mode
                    if self.workflow.collaboration == "orchestrated":
                        # Orchestrated: lead speaks first, then team members
                        lead = self.workflow.get_lead_agent()
                        team = self.workflow.get_team_agents()
                        turn_agents = [lead] + team if lead else team
                    else:
                        # Team mode: round-robin all agents
                        turn_agents = self.workflow.agents

                    # Each agent takes a turn
                    for agent in turn_agents:
                        if _cancelled or agent is None:
                            break

                        await self._agent_turn(record, agent, workflow_id)
                        self.store.save_conversation(workflow_id, record)

                        # Check for consensus/decision after each turn
                        if self._check_consensus(record, agent):
                            record.consensus_status = ConsensusStatus.AGREED
                            self._extract_consensus_content(record)
                            break

                    # After round, check if consensus reached
                    if record.consensus_status == ConsensusStatus.AGREED:
                        if self.interactive:
                            approved = await self._prompt_user_approval(record)
                            if approved:
                                record.consensus_status = ConsensusStatus.APPROVED
                                record.complete()
                                self.store.save_conversation(workflow_id, record)
                                break
                            else:
                                record.consensus_status = ConsensusStatus.REJECTED
                                # Continue discussion

                    # Prompt user for input between rounds
                    if self.interactive and not _cancelled:
                        user_input = await self._prompt_user_input()
                        if user_input == "__quit__":
                            record.cancel()
                            self.store.save_conversation(workflow_id, record)
                            break
                        elif user_input:
                            record.add_message(MessageRole.USER, user_input)
                            self.store.save_conversation(workflow_id, record)

                # Finalize
                if record.status == ExecutionStatus.RUNNING:
                    if record.current_round >= self.workflow.max_rounds:
                        record.complete()
                        if self.interactive:
                            click.echo("\nMax rounds reached without consensus.")

                record.ended_at = datetime.now(timezone.utc)
                self.store.save_conversation(workflow_id, record)

                return self._format_result(record)

        finally:
            self.store.release_global_lock()

    async def _agent_turn(
        self,
        record: ConversationRecord,
        agent: ConversationAgent,
        workflow_id: str,
    ) -> None:
        """Execute a single agent's turn."""
        turn = TurnResult(agent_id=agent.id)
        turn.start()
        record.turn_results.append(turn)

        if self.interactive:
            click.echo(f"  [{agent.id}] thinking...", nl=False)

        # Build prompt
        prompt = self._build_prompt(record, agent)

        # Execute agent command
        try:
            output = await self._execute_agent(agent, prompt)

            # Add response as message
            msg = record.add_message(MessageRole.AGENT, output, agent_id=agent.id)
            turn.complete(msg.id)

            duration = turn.duration_seconds or 0
            self.logger.info(
                "Agent responded",
                agent_id=agent.id,
                duration=duration,
            )

            if self.interactive:
                click.echo(f" done ({duration:.1f}s)")
                # Show truncated response
                display = output[:300] + "..." if len(output) > 300 else output
                for line in display.split("\n")[:5]:
                    click.echo(f"    {line}")
                if output.count("\n") > 5:
                    click.echo("    ...")
                click.echo()

        except asyncio.TimeoutError:
            turn.fail(f"Timeout after {agent.timeout}s")

            self.logger.error("Agent timeout", agent_id=agent.id, timeout=agent.timeout)

            if self.interactive:
                click.echo(" TIMEOUT")

            raise agent_timeout(agent.id, agent.timeout)

        except OrchestratorError:
            raise

        except Exception as e:
            turn.fail(str(e))

            self.logger.error("Agent error", agent_id=agent.id, error=str(e))

            if self.interactive:
                click.echo(f" ERROR: {e}")

            raise conversation_error(f"Agent '{agent.id}' failed: {e}")

    def _resolve_agent(self, agent: ConversationAgent) -> tuple[str, list[str], dict[str, str], int, str | None]:
        """Resolve agent configuration from settings if needed.

        Returns:
            Tuple of (command, args, env, timeout, model)
        """
        if agent.is_reference():
            # Load from settings
            config = get_agent_config(agent.agent)
            if not config:
                raise workflow_invalid(
                    f"Agent '{agent.agent}' not found in settings. "
                    f"Add it to ~/.orchestrator/agents.yaml"
                )
            # Merge: settings provide base, workflow can override
            command = agent.command or config.command
            args = agent.args if agent.args else config.args
            env = {**config.env, **agent.env}  # Workflow env overrides settings
            timeout = agent.timeout if agent.timeout != 300 else config.timeout
            model = agent.model or config.model  # Workflow model overrides settings
            return command, args, env, timeout, model
        else:
            # Inline configuration
            if not agent.command:
                raise workflow_invalid(
                    f"Agent '{agent.id}' has no command. "
                    f"Either specify 'command' or 'agent' (reference to settings)."
                )
            return agent.command, agent.args, agent.env, agent.timeout, agent.model

    async def _execute_agent(
        self,
        agent: ConversationAgent,
        prompt: str,
    ) -> str:
        """Execute agent command and return output.

        Tries persistent agent first (via Unix socket), falls back to spawning.
        """
        # Check for persistent agent first
        agent_id = agent.agent or agent.id  # Use settings reference or ID

        try:
            from orchestrator.lib.agent_client import is_agent_running, send_prompt

            if is_agent_running(agent_id):
                self.logger.info(
                    "Using persistent agent",
                    agent_id=agent_id,
                )

                # Get model for persistent agent
                _, _, _, timeout, model = self._resolve_agent(agent)

                # Build prompt with model if specified
                effective_prompt = prompt
                if model:
                    effective_prompt = f"/model {model}\n{prompt}"

                # Send via IPC
                return await send_prompt(agent_id, effective_prompt, timeout=timeout)

        except ImportError:
            pass  # Agent client not available
        except Exception as e:
            self.logger.warning(
                "Persistent agent failed, falling back to spawn",
                agent_id=agent_id,
                error=str(e),
            )

        # Fall back to spawning new process
        return await self._execute_agent_spawn(agent, prompt)

    async def _execute_agent_spawn(
        self,
        agent: ConversationAgent,
        prompt: str,
    ) -> str:
        """Execute agent by spawning a new process."""
        global _current_process

        # Resolve agent configuration
        command, args_template, agent_env, timeout, model = self._resolve_agent(agent)

        # Prepend /model command if model is specified
        effective_prompt = prompt
        if model:
            effective_prompt = f"/model {model}\n{prompt}"

        # Replace {{prompt}} placeholder in args
        args = [arg.replace("{{prompt}}", effective_prompt) for arg in args_template]
        cmd = [command] + args

        env = os.environ.copy()
        env.update(agent_env)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _current_process = process

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise
        finally:
            _current_process = None

        if process.returncode != 0:
            raise agent_failed(
                agent.id,
                process.returncode,
                stderr.decode(),
            )

        return stdout.decode().strip()

    def _build_prompt(
        self,
        record: ConversationRecord,
        agent: ConversationAgent,
    ) -> str:
        """Build the prompt for an agent's turn."""
        context_str = format_context_files(record.context_files_content)
        history = record.get_conversation_history()

        if self.workflow.collaboration == "orchestrated":
            # Orchestrated mode: different prompts for lead vs team members
            if agent.id == self.workflow.lead:
                team_list = ", ".join(a.id for a in self.workflow.get_team_agents())
                return build_lead_prompt(
                    agent_id=agent.id,
                    persona=agent.persona,
                    topic=self.workflow.topic,
                    consensus_keyword=self.workflow.consensus_keyword,
                    context_files=context_str,
                    history=history,
                    team_list=team_list,
                )
            else:
                return build_team_member_prompt(
                    agent_id=agent.id,
                    persona=agent.persona,
                    topic=self.workflow.topic,
                    lead_id=self.workflow.lead or "",
                    context_files=context_str,
                    history=history,
                )
        else:
            # Team mode: equal collaboration
            return build_team_prompt(
                agent_id=agent.id,
                persona=agent.persona,
                topic=self.workflow.topic,
                consensus_keyword=self.workflow.consensus_keyword,
                context_files=context_str,
                history=history,
            )

    # Context file limits to prevent DoS
    MAX_CONTEXT_FILES = 50
    MAX_CONTEXT_BYTES_TOTAL = 10 * 1024 * 1024  # 10MB
    MAX_CONTEXT_BYTES_PER_FILE = 1 * 1024 * 1024  # 1MB

    def _load_context_files(self) -> dict[str, str]:
        """Load content from context file patterns with safety limits.

        Limits:
        - Max 50 files
        - Max 1MB per file
        - Max 10MB total
        """
        files: dict[str, str] = {}
        total_bytes = 0

        for pattern in self.workflow.context_files:
            for path in glob.glob(pattern, recursive=True):
                # Check file count limit
                if len(files) >= self.MAX_CONTEXT_FILES:
                    self.logger.warning(
                        "Context file limit reached",
                        limit=self.MAX_CONTEXT_FILES,
                        pattern=pattern,
                    )
                    break

                if not os.path.isfile(path):
                    continue

                try:
                    # Check file size before reading
                    file_size = os.path.getsize(path)
                    if file_size > self.MAX_CONTEXT_BYTES_PER_FILE:
                        self.logger.warning(
                            "Skipping large context file",
                            path=path,
                            size=file_size,
                            limit=self.MAX_CONTEXT_BYTES_PER_FILE,
                        )
                        continue

                    # Check total size limit
                    if total_bytes + file_size > self.MAX_CONTEXT_BYTES_TOTAL:
                        self.logger.warning(
                            "Context total size limit reached",
                            total=total_bytes,
                            limit=self.MAX_CONTEXT_BYTES_TOTAL,
                        )
                        break

                    with open(path, "r") as f:
                        content = f.read(self.MAX_CONTEXT_BYTES_PER_FILE)
                        files[path] = content
                        total_bytes += len(content)

                except Exception:
                    pass

            # Break outer loop if limits reached
            if len(files) >= self.MAX_CONTEXT_FILES:
                break
            if total_bytes >= self.MAX_CONTEXT_BYTES_TOTAL:
                break

        return files

    def _check_consensus(
        self,
        record: ConversationRecord,
        current_agent: ConversationAgent | None = None,
    ) -> bool:
        """Check if consensus/decision has been reached.

        - Team mode: All agents must include the consensus keyword
        - Orchestrated mode: Only the lead's decision matters
        """
        if self.workflow.collaboration == "orchestrated":
            # In orchestrated mode, only check if lead has made a decision
            if current_agent and current_agent.id == self.workflow.lead:
                # Check if lead's last message contains consensus keyword
                for msg in reversed(record.messages):
                    if msg.agent_id == self.workflow.lead:
                        return self.workflow.consensus_keyword in msg.content
            return False
        else:
            # Team mode: all agents must agree
            return record.check_consensus(
                [a.id for a in self.workflow.agents],
                self.workflow.consensus_keyword,
            )

    def _extract_consensus_content(self, record: ConversationRecord) -> None:
        """Extract the consensus content from messages."""
        keyword = self.workflow.consensus_keyword
        for msg in reversed(record.messages):
            if msg.role == MessageRole.AGENT and keyword in msg.content:
                # Extract content after the keyword
                idx = msg.content.find(keyword)
                record.consensus_content = msg.content[idx:].strip()
                return

    async def _prompt_user_input(self) -> str | None:
        """Prompt user for input between rounds."""
        click.echo("[Enter to continue, /help for commands, or type message]")

        # Use asyncio-compatible input
        loop = asyncio.get_event_loop()
        user_input = await loop.run_in_executor(None, input, "> ")

        stripped = user_input.strip()

        if stripped.lower() == "quit":
            return "__quit__"

        # Handle slash commands
        if stripped.startswith("/"):
            handled = self._handle_command(stripped)
            if handled == "__quit__":
                return "__quit__"
            # Command was handled, prompt again
            return await self._prompt_user_input()

        return stripped if stripped else None

    def _handle_command(self, command: str) -> str | None:
        """Handle slash commands during conversation.

        Returns "__quit__" to quit, None otherwise.
        """
        parts = command.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "/help":
            self._cmd_help()
        elif cmd == "/agents":
            self._cmd_agents()
        elif cmd == "/add":
            if len(parts) < 2:
                click.echo("Usage: /add <agent-name> [persona]")
            else:
                agent_name = parts[1]
                persona = parts[2] if len(parts) > 2 else ""
                self._cmd_add(agent_name, persona)
        elif cmd == "/remove":
            if len(parts) < 2:
                click.echo("Usage: /remove <agent-id>")
            else:
                self._cmd_remove(parts[1])
        elif cmd == "/model":
            if len(parts) < 3:
                click.echo("Usage: /model <agent-id> <model-name>")
            else:
                self._cmd_model(parts[1], parts[2])
        elif cmd == "/persona":
            if len(parts) < 3:
                click.echo("Usage: /persona <agent-id> <new-persona>")
            else:
                self._cmd_persona(parts[1], parts[2])
        elif cmd == "/save":
            self._cmd_save()
        elif cmd == "/config":
            if len(parts) > 1 and parts[1] == "show":
                self._cmd_config_show()
            else:
                self._cmd_config()
        elif cmd == "/quit":
            return "__quit__"
        else:
            click.echo(f"Unknown command: {cmd}. Type /help for available commands.")

        return None

    def _cmd_help(self) -> None:
        """Show available commands."""
        click.echo()
        click.echo("Session commands (temporary):")
        click.echo("  /agents                       List current agents")
        click.echo("  /add <agent> [persona]        Add agent from settings")
        click.echo("  /remove <agent-id>            Remove agent from conversation")
        click.echo("  /model <agent-id> <model>     Change agent's model")
        click.echo("  /persona <agent-id> <text>    Update agent's persona")
        click.echo()
        click.echo("Config commands (permanent):")
        click.echo("  /save                         Save agents to project config")
        click.echo("  /config                       Show config file locations")
        click.echo("  /config show                  Display project config")
        click.echo()
        click.echo("Other:")
        click.echo("  /help                         Show this help")
        click.echo("  /quit                         End conversation")
        click.echo()

    def _cmd_agents(self) -> None:
        """List current agents in conversation."""
        click.echo()
        click.echo("Current agents:")
        for agent in self.workflow.agents:
            model_str = f" (model: {agent.model})" if agent.model else ""
            ref_str = f" -> {agent.agent}" if agent.agent else ""
            click.echo(f"  {agent.id}{ref_str}{model_str}")
            if agent.persona:
                # Show truncated persona
                persona_display = agent.persona[:60] + "..." if len(agent.persona) > 60 else agent.persona
                click.echo(f"    persona: {persona_display}")
        click.echo()

    def _cmd_add(self, agent_name: str, persona: str) -> None:
        """Add an agent from settings to the conversation."""
        # Check if agent exists in settings
        config = get_agent_config(agent_name)
        if not config:
            click.echo(f"Agent '{agent_name}' not found in settings.")
            click.echo("Available agents: ", nl=False)
            from orchestrator.lib.settings import load_agent_settings
            settings = load_agent_settings()
            click.echo(", ".join(settings.list_agents()))
            return

        # Check for duplicate ID
        agent_id = agent_name
        existing_ids = [a.id for a in self.workflow.agents]
        if agent_id in existing_ids:
            # Generate unique ID
            counter = 2
            while f"{agent_name}-{counter}" in existing_ids:
                counter += 1
            agent_id = f"{agent_name}-{counter}"

        # Create new agent
        new_agent = ConversationAgent(
            id=agent_id,
            agent=agent_name,
            persona=persona or f"You are {agent_name}.",
        )
        self.workflow.agents.append(new_agent)

        click.echo(f"Added agent '{agent_id}' (using {agent_name})")

    def _cmd_remove(self, agent_id: str) -> None:
        """Remove an agent from the conversation."""
        if len(self.workflow.agents) <= 2:
            click.echo("Cannot remove agent: minimum 2 agents required.")
            return

        for i, agent in enumerate(self.workflow.agents):
            if agent.id == agent_id:
                self.workflow.agents.pop(i)
                click.echo(f"Removed agent '{agent_id}'")
                return

        click.echo(f"Agent '{agent_id}' not found.")
        click.echo("Current agents: " + ", ".join(a.id for a in self.workflow.agents))

    def _cmd_model(self, agent_id: str, model: str) -> None:
        """Change an agent's model."""
        for agent in self.workflow.agents:
            if agent.id == agent_id:
                old_model = agent.model or "(default)"
                agent.model = model
                click.echo(f"Changed {agent_id} model: {old_model} -> {model}")
                return

        click.echo(f"Agent '{agent_id}' not found.")

    def _cmd_persona(self, agent_id: str, persona: str) -> None:
        """Update an agent's persona."""
        for agent in self.workflow.agents:
            if agent.id == agent_id:
                agent.persona = persona
                click.echo(f"Updated {agent_id} persona.")
                return

        click.echo(f"Agent '{agent_id}' not found.")

    def _cmd_save(self) -> None:
        """Save current agents to project config file."""
        import yaml

        project_dir = Path.cwd() / ".orchestrator"
        project_dir.mkdir(parents=True, exist_ok=True)
        config_path = project_dir / "agents.yaml"

        # Build agents config from current workflow agents
        agents_config: dict[str, dict] = {}

        for agent in self.workflow.agents:
            agent_data: dict[str, Any] = {}

            if agent.agent:
                # It's a reference - get base config
                base_config = get_agent_config(agent.agent)
                if base_config:
                    agent_data["command"] = base_config.command
                    agent_data["args"] = base_config.args
                    if base_config.env:
                        agent_data["env"] = base_config.env
                    agent_data["timeout"] = base_config.timeout
                    if base_config.description:
                        agent_data["description"] = base_config.description
            else:
                # Inline config
                if agent.command:
                    agent_data["command"] = agent.command
                if agent.args:
                    agent_data["args"] = agent.args
                if agent.env:
                    agent_data["env"] = agent.env
                agent_data["timeout"] = agent.timeout

            # Add overrides
            if agent.model:
                agent_data["model"] = agent.model
            if agent.persona:
                agent_data["persona"] = agent.persona

            agents_config[agent.id] = agent_data

        # Write to file
        config_content = {
            "# Orchestrator Project Agents": None,
            "# Saved from conversation session": None,
            "agents": agents_config,
        }

        # Use yaml to write properly
        yaml_content = "# Orchestrator Project Agents\n"
        yaml_content += "# Saved from conversation session\n\n"
        yaml_content += yaml.dump({"agents": agents_config}, default_flow_style=False, sort_keys=False)

        config_path.write_text(yaml_content)
        click.echo(f"Saved {len(agents_config)} agents to: {config_path}")

    def _cmd_config(self) -> None:
        """Show config file locations."""
        from orchestrator.lib.settings import get_global_settings_path, get_project_settings_path

        click.echo()
        click.echo("Config file locations:")

        global_path = get_global_settings_path()
        exists = "exists" if global_path.exists() else "not found"
        click.echo(f"  Global:  {global_path} ({exists})")

        project_path = get_project_settings_path()
        if project_path:
            click.echo(f"  Project: {project_path} (active)")
        else:
            project_default = Path.cwd() / ".orchestrator" / "agents.yaml"
            click.echo(f"  Project: {project_default} (not created)")
            click.echo()
            click.echo("Use /save to create project config from current agents.")
        click.echo()

    def _cmd_config_show(self) -> None:
        """Display project config file contents."""
        from orchestrator.lib.settings import get_project_settings_path

        project_path = get_project_settings_path()
        if not project_path or not project_path.exists():
            click.echo("No project config found.")
            click.echo("Use /save to create one from current agents.")
            return

        click.echo(f"\n--- {project_path} ---")
        click.echo(project_path.read_text())
        click.echo("---")

    async def _prompt_user_approval(self, record: ConversationRecord) -> bool:
        """Prompt user to approve consensus."""
        click.echo()
        click.echo("=" * 50)
        click.echo("CONSENSUS REACHED")
        click.echo("=" * 50)
        if record.consensus_content:
            click.echo(record.consensus_content)
        click.echo("=" * 50)
        click.echo("[Type 'approve' to accept, or provide feedback to continue]")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, input, "> ")

        if response.lower() == "approve":
            return True

        if response.strip():
            record.add_message(MessageRole.USER, f"Feedback: {response}")

        return False

    def _format_result(self, record: ConversationRecord) -> dict[str, Any]:
        """Format result for output."""
        return {
            "execution_id": record.id,
            "workflow_id": record.workflow_id,
            "workflow_name": record.workflow_name,
            "status": record.status.value,
            "consensus_status": record.consensus_status.value,
            "consensus_content": record.consensus_content,
            "rounds_completed": record.current_round,
            "message_count": len(record.messages),
            "duration_seconds": record.duration_seconds,
        }


def inject_user_message(
    workflow_id: str,
    execution_id: str,
    message: str,
) -> dict[str, Any]:
    """Inject a user message into an active conversation."""
    store = WorkflowStore()

    record = store.load_conversation(workflow_id, execution_id)
    if record.status != ExecutionStatus.RUNNING:
        raise conversation_error(
            f"Cannot inject message: conversation is {record.status.value}"
        )

    msg = record.add_message(MessageRole.USER, message)
    store.save_conversation(workflow_id, record)

    return {"status": "message_added", "message_id": msg.id}


def execute_conversation(
    workflow: ConversationWorkflow,
    yaml_content: str,
    interactive: bool = True,
) -> dict[str, Any]:
    """Execute a conversation workflow.

    Convenience function for CLI usage.
    """
    executor = ConversationExecutor(
        workflow=workflow,
        yaml_content=yaml_content,
        interactive=interactive,
    )
    return executor.execute()
