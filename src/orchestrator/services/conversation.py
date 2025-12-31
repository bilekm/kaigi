"""Conversation executor for multi-agent discussions."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import signal
import yaml
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
from orchestrator.services.event_handler import ConversationEventHandler, NullEventHandler
from orchestrator.services.store import WorkflowStore
from orchestrator.services.tools import CapabilityChecker, ToolCall, ToolExecutor, ToolResult


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
        event_handler: ConversationEventHandler | None = None,
    ):
        self.workflow = workflow
        self.yaml_content = yaml_content
        self.store = store or WorkflowStore()
        self.event_handler = event_handler or NullEventHandler()
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

            # Load context files if preload_context is enabled
            # By default, we use on-demand exploration (agents have tools like Read, Grep, etc.)
            # Setting preload_context: true in workflow enables pre-loading for large codebases
            if self.workflow.preload_context and self.workflow.context_files:
                record.context_files_content = self._load_context_files()
                self.logger.info(
                    "Context files loaded",
                    count=len(record.context_files_content),
                    total_bytes=sum(len(v) for v in record.context_files_content.values()),
                )
            else:
                # Default: on-demand exploration philosophy
                # record.context_files_content stays empty
                pass

            # Prompt for topic if not provided
            topic_was_prompted = False
            if not self.workflow.topic:
                agent_ids = [a.id for a in self.workflow.agents]
                # Build agent type mapping: {agent_type: role_id}
                # e.g., {'claude': 'reviewer', 'copilot': 'architect', 'glm': 'analyst'}
                agent_types = {}
                for agent in self.workflow.agents:
                    agent_type = agent.agent if agent.agent else agent.id
                    agent_types[agent_type] = agent.id

                topic = await self.event_handler.prompt_topic(
                    self.workflow.name, agent_ids, agent_types
                )
                if topic is None:
                    record.cancel()
                    self.store.save_conversation(workflow_id, record)
                    return self._format_result(record)
                self.workflow.topic = topic
                record.topic = topic
                topic_was_prompted = True
                click.echo()  # Preserve spacing for CLI handler

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

                # Notify event handler of conversation start
                if not topic_was_prompted:
                    self.event_handler.on_conversation_start(
                        workflow_name=self.workflow.name,
                        topic=self.workflow.topic,
                        agents=agent_ids,
                        collaboration=self.workflow.collaboration,
                        lead=self.workflow.lead,
                    )

                # Main loop
                while record.current_round < self.workflow.max_rounds:
                    if _cancelled:
                        record.cancel()
                        self.store.save_conversation(workflow_id, record)
                        break

                    record.current_round += 1

                    self.event_handler.on_round_start(record.current_round)

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
                            # Enforce min_rounds before allowing consensus
                            if record.current_round < self.workflow.min_rounds:
                                # Consensus detected but too early
                                self.event_handler.on_consensus_too_early(
                                    record.current_round,
                                    self.workflow.min_rounds,
                                )
                                # Continue - don't break, let other agents respond
                            else:
                                record.consensus_status = ConsensusStatus.AGREED
                                self._extract_consensus_content(record)
                                break

                    # After round, check if consensus reached
                    if record.consensus_status == ConsensusStatus.AGREED:
                        self.event_handler.on_consensus_reached(record.consensus_content or "")
                        approved = await self.event_handler.prompt_user_approval(
                            record.consensus_content or ""
                        )
                        if approved:
                            record.consensus_status = ConsensusStatus.APPROVED
                            # Enter execution phase - have first agent execute the agreed changes
                            click.echo()
                            click.echo("=" * 50)
                            click.echo("EXECUTION PHASE")
                            click.echo("=" * 50)

                            # Get first agent to execute the plan
                            first_agent = self.workflow.agents[0]
                            click.echo(f"Asking {first_agent.id} to execute the agreed changes...")

                            # Build execution prompt
                            exec_prompt = self._build_execution_prompt(record, first_agent)
                            record.add_message(MessageRole.SYSTEM, exec_prompt)

                            # Execute WITH write permission, using execution prompt
                            await self._agent_turn(
                                record, first_agent, workflow_id,
                                with_write_permission=True,
                                execution_prompt=exec_prompt,
                            )
                            self.store.save_conversation(workflow_id, record)

                            record.complete()
                            self.store.save_conversation(workflow_id, record)
                            break
                        else:
                            record.consensus_status = ConsensusStatus.REJECTED
                            # Continue discussion

                    # Prompt user for input between rounds
                    if not _cancelled:
                        user_input = await self.event_handler.prompt_user_input()
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
                        self.event_handler.on_max_rounds_reached(self.workflow.max_rounds)

                record.ended_at = datetime.now(timezone.utc)
                self.store.save_conversation(workflow_id, record)

                return self._format_result(record)

        finally:
            self.store.release_global_lock()

    def _parse_tool_calls(self, text: str) -> list[ToolCall]:
        """Parse tool calls from agent output.

        Looks for JSON-formatted tool calls in the text.
        Format: {"type": "tool_call", "id": "t1", "tool": "read_file", "args": {...}}

        Supports multiple formats:
        1. Strict JSON-per-line (original behavior)
        2. Multiline JSON objects
        3. Markdown code blocks (```json ... ```)

        Args:
            text: Agent output text

        Returns:
            List of parsed ToolCall objects
        """
        # Strip thinking blocks from agent output (defensive measure)
        # Some agents output <thinking>...</thinking> blocks which can confuse parsing
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)

        tool_calls = []

        # Try strict JSON-per-line parsing first (fast path)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                if isinstance(data, dict) and data.get("type") == "tool_call":
                    tool_call = ToolCall(**data)
                    tool_calls.append(tool_call)
            except (json.JSONDecodeError, ValueError):
                # Not valid JSON or not a tool call, continue
                continue

        # If we found tool calls, return them (success on fast path)
        if tool_calls:
            return tool_calls

        # Fallback: Regex extraction for multiline JSON and markdown blocks
        # Pattern matches: {"type": "tool_call", ...}
        # Handles multiline by matching content between braces
        tool_call_pattern = re.compile(
            r'\{[^{}]*"type"\s*:\s*"tool_call"[^{}]*\}',
            re.DOTALL
        )

        # Also try to extract from markdown code blocks
        markdown_pattern = re.compile(
            r'```(?:json)?\s*\n(\{[^{}]*"type"\s*:\s*"tool_call"[^{}]*\})\s*```',
            re.DOTALL
        )

        # Try markdown extraction first
        for match in markdown_pattern.finditer(text):
            json_str = match.group(1)
            try:
                data = json.loads(json_str)
                tool_call = ToolCall(**data)
                tool_calls.append(tool_call)
            except (json.JSONDecodeError, ValueError):
                continue

        # If still no matches, try raw regex extraction
        if not tool_calls:
            for match in tool_call_pattern.finditer(text):
                json_str = match.group(0)
                try:
                    data = json.loads(json_str)
                    tool_call = ToolCall(**data)
                    tool_calls.append(tool_call)
                except (json.JSONDecodeError, ValueError):
                    continue

        return tool_calls

    async def _execute_tool_loop(
        self,
        agent: ConversationAgent,
        initial_prompt: str,
        with_write_permission: bool = False,
    ) -> str:
        """Execute agent with tool support loop.

        Implements the ReAct pattern:
        1. Send prompt to agent
        2. Parse tool calls from response
        3. Check capabilities and execute tools
        4. Feed tool results back to agent
        5. Repeat until agent produces final response (no tool calls)

        Args:
            agent: The agent to execute
            initial_prompt: Initial prompt to send
            with_write_permission: Whether to grant write permissions

        Returns:
            Final agent response (text only, no tool calls)
        """
        # Initialize tool executor and capability checker
        workspace_root = Path.cwd()
        tool_executor = ToolExecutor(workspace_root)

        # Adjust permissions based on write_permission flag
        permissions = agent.permissions.copy()
        if with_write_permission and "write:*" not in permissions:
            permissions.append("write:*")

        capability_checker = CapabilityChecker(permissions, workspace_root)

        # Start with initial prompt
        current_prompt = initial_prompt
        max_tool_iterations = 10  # Prevent infinite loops

        for iteration in range(max_tool_iterations):
            # Execute agent
            output = await self._execute_agent(agent, current_prompt, with_write_permission=with_write_permission)

            # Parse tool calls from output
            tool_calls = self._parse_tool_calls(output)

            if not tool_calls:
                # No tool calls - this is the final response
                return output

            # Process tool calls
            tool_results = []
            for tool_call in tool_calls:
                self.logger.info(
                    "Tool call requested",
                    agent_id=agent.id,
                    tool=tool_call.tool,
                    tool_id=tool_call.id,
                )

                # Special handling for ask_user tool (always allowed, no capability check)
                if tool_call.tool == "ask_user":
                    question = tool_call.args.get("question", "")
                    options = tool_call.args.get("options")

                    try:
                        response = await self.event_handler.prompt_user_question(question, options)
                        result = ToolResult(
                            id=tool_call.id,
                            status="ok",
                            content=response,
                        )
                        self.logger.info(
                            "User question answered",
                            agent_id=agent.id,
                            question=question[:100],
                        )
                    except Exception as e:
                        result = ToolResult(
                            id=tool_call.id,
                            status="error",
                            content=f"Failed to get user input: {e}",
                        )
                        self.logger.error(
                            "User question failed",
                            agent_id=agent.id,
                            error=str(e),
                        )
                    tool_results.append(result)
                    continue

                # Special handling for spawn_agent tool
                if tool_call.tool == "spawn_agent":
                    agent_type = tool_call.args.get("agent_type", "general")
                    prompt = tool_call.args.get("prompt", "")

                    try:
                        # Load subagent configuration
                        subagent_config = self._load_subagent_config(agent_type)

                        # Create temporary ConversationAgent
                        from orchestrator.models.workflow import ConversationAgent

                        subagent = ConversationAgent(
                            id=f"sub_{agent_type}_{uuid4().hex[:8]}",
                            agent=subagent_config.get("agent"),
                            persona=subagent_config.get("persona", ""),
                            permissions=subagent_config.get("permissions", ["read:*"]),
                            model=subagent_config.get("model"),
                        )

                        self.logger.info(
                            "Spawning subagent",
                            agent_type=agent_type,
                            subagent_id=subagent.id,
                        )

                        # Execute subagent with tool support (read-only by default)
                        subagent_response = await self._execute_agent(
                            subagent,
                            prompt,
                            with_write_permission=False,  # Subagents are read-only by default
                        )

                        result = ToolResult(
                            id=tool_call.id,
                            status="ok",
                            content=subagent_response,
                        )
                        self.logger.info(
                            "Subagent completed",
                            agent_type=agent_type,
                            subagent_id=subagent.id,
                            response_length=len(subagent_response),
                        )
                    except Exception as e:
                        result = ToolResult(
                            id=tool_call.id,
                            status="error",
                            content=f"Subagent execution failed: {e}",
                        )
                        self.logger.error(
                            "Subagent failed",
                            agent_type=agent_type,
                            error=str(e),
                        )
                    tool_results.append(result)
                    continue

                # Check capability for other tools
                decision, reason = capability_checker.check_capability(tool_call)

                if decision == "denied":
                    # Capability denied
                    result = ToolResult(
                        id=tool_call.id,
                        status="error",
                        content=f"Permission denied: {reason}",
                    )
                    self.logger.warning(
                        "Tool call denied",
                        agent_id=agent.id,
                        tool=tool_call.tool,
                        reason=reason,
                    )
                else:
                    # Execute tool
                    result = await tool_executor.execute(tool_call)

                tool_results.append(result)

            # Build next prompt with tool results
            results_text = "\n".join(
                result.model_dump_json() for result in tool_results
            )
            current_prompt = f"Tool results:\n{results_text}\n\nContinue your response:"

        # Max iterations reached - return last output
        self.logger.warning(
            "Tool loop max iterations reached",
            agent_id=agent.id,
            iterations=max_tool_iterations,
        )
        return output

    async def _agent_turn(
        self,
        record: ConversationRecord,
        agent: ConversationAgent,
        workflow_id: str,
        with_write_permission: bool = False,
        execution_prompt: str | None = None,
    ) -> None:
        """Execute a single agent's turn.

        Args:
            record: The conversation record
            agent: The agent to execute
            workflow_id: The workflow ID
            with_write_permission: Whether to grant write permissions
            execution_prompt: If provided, use this prompt directly instead of
                            building from conversation history (for execution phase)
        """
        turn = TurnResult(agent_id=agent.id)
        turn.start()
        record.turn_results.append(turn)

        self.event_handler.on_agent_turn_start(agent.id)

        # Use execution prompt if provided, otherwise build from conversation history
        if execution_prompt is not None:
            prompt = execution_prompt
        else:
            prompt = self._build_prompt(record, agent)

        # Execute agent command with tool support
        try:
            output = await self._execute_tool_loop(agent, prompt, with_write_permission=with_write_permission)

            # Add response as message
            msg = record.add_message(MessageRole.AGENT, output, agent_id=agent.id)
            turn.complete(msg.id)

            duration = turn.duration_seconds or 0
            self.logger.info(
                "Agent responded",
                agent_id=agent.id,
                duration=duration,
            )

            self.event_handler.on_agent_turn_complete(agent.id, duration, output)

        except asyncio.TimeoutError:
            turn.fail(f"Timeout after {agent.timeout}s")

            self.logger.error("Agent timeout", agent_id=agent.id, timeout=agent.timeout)

            self.event_handler.on_agent_turn_error(agent.id, "TIMEOUT")

            raise agent_timeout(agent.id, agent.timeout)

        except OrchestratorError:
            raise

        except Exception as e:
            turn.fail(str(e))

            self.logger.error("Agent error", agent_id=agent.id, error=str(e))

            self.event_handler.on_agent_turn_error(agent.id, str(e))

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
        with_write_permission: bool = False,
    ) -> str:
        """Execute agent command and return output.

        Tries persistent agent first (via Unix socket), falls back to spawning.

        Args:
            agent: The agent to execute
            prompt: The prompt to send
            with_write_permission: If True, add --permission-mode acceptEdits to args
        """
        # If write permission requested, must use spawn mode (persistent agents
        # were started without write permission)
        if with_write_permission:
            return await self._execute_agent_spawn(agent, prompt, with_write_permission=True)

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
        with_write_permission: bool = False,
    ) -> str:
        """Execute agent by spawning a new process.

        Args:
            agent: The agent to execute
            prompt: The prompt to send
            with_write_permission: If True, add --permission-mode acceptEdits to args
        """
        global _current_process

        # Resolve agent configuration
        command, args_template, agent_env, timeout, model = self._resolve_agent(agent)

        # Note: /model command only works in interactive mode, not with -p flag
        # For spawn mode, ignore the model setting (agent uses its default)
        effective_prompt = prompt

        # Replace {{prompt}} placeholder in args
        args = [arg.replace("{{prompt}}", effective_prompt) for arg in args_template]

        # Add write permission if requested (for execution phase)
        if with_write_permission:
            # Insert --permission-mode acceptEdits before -p flag
            for i, arg in enumerate(args):
                if arg == "-p" and i + 1 < len(args):
                    # Insert permission mode before the prompt flag
                    args.insert(i, "--permission-mode")
                    args.insert(i + 1, "acceptEdits")
                    break

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
                    history=history,
                    team_list=team_list,
                    min_rounds=self.workflow.min_rounds,
                )
            else:
                return build_team_member_prompt(
                    agent_id=agent.id,
                    persona=agent.persona,
                    topic=self.workflow.topic,
                    lead_id=self.workflow.lead or "",
                    history=history,
                    min_rounds=self.workflow.min_rounds,
                )
        else:
            # Team mode: equal collaboration
            return build_team_prompt(
                agent_id=agent.id,
                persona=agent.persona,
                topic=self.workflow.topic,
                consensus_keyword=self.workflow.consensus_keyword,
                history=history,
                min_rounds=self.workflow.min_rounds,
            )

    def _build_execution_prompt(
        self,
        record: ConversationRecord,
        agent: ConversationAgent,
    ) -> str:
        """Build the prompt for execution phase after consensus approval.

        The first agent is asked to execute the agreed changes.
        """
        consensus = record.consensus_content or ""

        return f"""EXECUTION PHASE

## Current Permission State
**PHASE: EXECUTION** (WRITE-ENABLED)
- You CAN: read files, write files, edit code, execute commands
- The team reached consensus and the user approved
- You are now authorized to implement the agreed changes

**Consensus:**
{consensus}

**Your task:**
1. Analyze what needs to be done based on the consensus
2. Use the available tools to make the changes
3. Report back on what you did

**Available Tools:**

You have access to the following tools. To use a tool, output a JSON object on its own line:

1. **read_file** - Read file contents
   Usage: {{"type": "tool_call", "id": "t1", "tool": "read_file", "args": {{"path": "src/file.py"}}}}

2. **write_file** - Write content to a file (creates parent dirs if needed)
   Usage: {{"type": "tool_call", "id": "t2", "tool": "write_file", "args": {{"path": "src/file.py", "content": "..."}}}}

3. **edit_file** - Surgical string replacement in a file
   Usage: {{"type": "tool_call", "id": "t3", "tool": "edit_file", "args": {{"path": "src/file.py", "old_string": "foo", "new_string": "bar", "replace_all": false}}}}

4. **grep** - Search for patterns in files
   Usage: {{"type": "tool_call", "id": "t4", "tool": "grep", "args": {{"pattern": "TODO", "path": "src", "glob_pattern": "*.py", "ignore_case": true}}}}

5. **glob** - Find files matching a pattern
   Usage: {{"type": "tool_call", "id": "t5", "tool": "glob", "args": {{"pattern": "**/*.py"}}}}

6. **exec_bash** - Execute a bash command in workspace directory
   Usage: {{"type": "tool_call", "id": "t6", "tool": "exec_bash", "args": {{"command": "ls -la"}}}}

7. **ask_user** - Ask the user a question for clarification
   Usage: {{"type": "tool_call", "id": "t7", "tool": "ask_user", "args": {{"question": "Which approach do you prefer?", "options": [{{"label": "A", "description": "..."}}, {{"label": "B", "description": "..."}}]}}}}

8. **spawn_agent** - Spawn a specialized subagent for a task
   Usage: {{"type": "tool_call", "id": "t8", "tool": "spawn_agent", "args": {{"agent_type": "explore", "prompt": "Find all database schema files"}}}}
   Agent types: explore (fast codebase search), plan (architecture design), review (code review), general (any task)

**Tool Response Format:**

After each tool call, you will receive a JSON response:
- Success: {{"type": "tool_result", "id": "t1", "status": "ok", "content": "..."}}
- Error: {{"type": "tool_result", "id": "t1", "status": "error", "content": "..."}}

**Workflow:**
1. Call a tool by outputting the JSON on its own line
2. Wait for the tool result
3. Continue with more tool calls or provide your final response
4. When done, provide a summary starting with "DONE:"

**Important:**
- All file paths are relative to the workspace root
- Use read_file before editing to understand the current state
- Use grep and glob for codebase exploration instead of exec_bash
- Use edit_file for surgical edits instead of write_file when making small changes
- Use ask_user when you need clarification from the user
- Use spawn_agent to delegate specialized tasks to subagents
- Make changes incrementally and verify each step
- If a tool fails, the error will explain why

Begin execution now.
"""

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

    def _load_subagent_config(self, agent_type: str) -> dict[str, Any]:
        """Load subagent configuration from subagents.yaml.

        Checks both project-local (.orchestrator/subagents.yaml) and global
        (~/.orchestrator/subagents.yaml) config files. Project config takes
        precedence over global config.

        Args:
            agent_type: The type of subagent to load (e.g., "explore", "plan", "review")

        Returns:
            Dictionary with subagent configuration (model, persona, permissions, agent)

        Raises:
            ValueError: If agent_type is not found in any config file
        """
        import os

        # Paths to check (project takes precedence)
        project_config = Path.cwd() / ".orchestrator" / "subagents.yaml"
        global_config = Path.home() / ".orchestrator" / "subagents.yaml"

        config_data: dict[str, Any] = {}

        # Load global config first (base configuration)
        if global_config.exists():
            try:
                with open(global_config) as f:
                    config_data = yaml.safe_load(f) or {}
            except Exception as e:
                self.logger.warning(
                    "Failed to load global subagent config",
                    path=str(global_config),
                    error=str(e),
                )

        # Overlay project config (overrides global)
        if project_config.exists():
            try:
                with open(project_config) as f:
                    project_data = yaml.safe_load(f) or {}
                    config_data.update(project_data)
            except Exception as e:
                self.logger.warning(
                    "Failed to load project subagent config",
                    path=str(project_config),
                    error=str(e),
                )

        # Get the agent type configuration
        if agent_type not in config_data:
            # Fall back to "general" if available
            if "general" in config_data:
                self.logger.info(
                    "Agent type not found, using general",
                    agent_type=agent_type,
                )
                agent_type = "general"
            else:
                raise ValueError(
                    f"Unknown subagent type: {agent_type}. "
                    f"Available types: {list(config_data.keys())}"
                )

        return config_data[agent_type]

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
        """Prompt user for input between rounds, handling slash commands.

        Delegates the actual prompt to the event handler, then processes
        the input for commands.
        """
        user_input = await self.event_handler.prompt_user_input()

        if not user_input:
            return None

        stripped = user_input.strip()

        # Handle slash commands
        if stripped.startswith("/"):
            handled = self._handle_command(stripped)
            if handled == "__quit__":
                return "__quit__"
            # Command was handled, show result and prompt again
            if handled:
                self.event_handler.on_command_result(stripped, handled)
            return await self._prompt_user_input()

        return stripped if stripped else None

    def _handle_command(self, command: str) -> str | None:
        """Handle slash commands during conversation.

        Returns "__quit__" to quit, result string to display, or None.
        """
        import io
        from contextlib import redirect_stdout

        parts = command.split(maxsplit=2)
        cmd = parts[0].lower()

        # Capture click.echo output
        output = io.StringIO()

        with redirect_stdout(output):
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
                click.echo(f"Unknown command: {click.style(cmd, fg='bright_yellow')}. Type {click.style('/help', fg='bright_yellow')} for available commands.")

        result = output.getvalue()
        return result if result.strip() else None

    def _cmd_help(self) -> None:
        """Show available commands."""
        click.echo()
        click.echo("Session commands (temporary):")
        click.echo(f"  {click.style('/agents', fg='bright_yellow')}                       List current agents")
        click.echo(f"  {click.style('/add <agent> [persona]', fg='bright_yellow')}        Add agent from settings")
        click.echo(f"  {click.style('/remove <agent-id>', fg='bright_yellow')}            Remove agent from conversation")
        click.echo(f"  {click.style('/model <agent-id> <model>', fg='bright_yellow')}     Change agent's model")
        click.echo(f"  {click.style('/persona <agent-id> <text>', fg='bright_yellow')}    Update agent's persona")
        click.echo()
        click.echo("Config commands (permanent):")
        click.echo(f"  {click.style('/save', fg='bright_yellow')}                         Save agents to project config")
        click.echo(f"  {click.style('/config', fg='bright_yellow')}                       Show config file locations")
        click.echo(f"  {click.style('/config show', fg='bright_yellow')}                  Display project config")
        click.echo()
        click.echo("Other:")
        click.echo(f"  {click.style('/help', fg='bright_yellow')}                         Show this help")
        click.echo(f"  {click.style('/quit', fg='bright_yellow')}                         End conversation")
        click.echo()

    def _cmd_agents(self) -> None:
        """List current agents in conversation."""
        click.echo()
        click.echo("Current agents:")
        for agent in self.workflow.agents:
            model_str = f" (model: {agent.model})" if agent.model else ""
            ref_str = f" -> {agent.agent}" if agent.agent else ""
            click.echo(f"  {click.style(agent.id, fg='green')}{ref_str}{model_str}")
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
            click.echo(f"{click.style('ERROR', fg='red')}: Agent '{agent_name}' not found in settings.")
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
            click.echo(f"{click.style('ERROR', fg='red')}: Cannot remove agent: minimum 2 agents required.")
            return

        for i, agent in enumerate(self.workflow.agents):
            if agent.id == agent_id:
                self.workflow.agents.pop(i)
                click.echo(f"Removed agent '{agent_id}'")
                return

        click.echo(f"{click.style('ERROR', fg='red')}: Agent '{agent_id}' not found.")
        click.echo("Current agents: " + ", ".join(click.style(a.id, fg='green') for a in self.workflow.agents))

    def _cmd_model(self, agent_id: str, model: str) -> None:
        """Change an agent's model."""
        for agent in self.workflow.agents:
            if agent.id == agent_id:
                old_model = agent.model or "(default)"
                agent.model = model
                click.echo(f"Changed {click.style(agent_id, fg='green')} model: {old_model} -> {model}")
                return

        click.echo(f"{click.style('ERROR', fg='red')}: Agent '{agent_id}' not found.")

    def _cmd_persona(self, agent_id: str, persona: str) -> None:
        """Update an agent's persona."""
        for agent in self.workflow.agents:
            if agent.id == agent_id:
                agent.persona = persona
                click.echo(f"Updated {click.style(agent_id, fg='green')} persona.")
                return

        click.echo(f"{click.style('ERROR', fg='red')}: Agent '{agent_id}' not found.")

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
            click.echo(f"Use {click.style('/save', fg='bright_yellow')} to create project config from current agents.")
        click.echo()

    def _cmd_config_show(self) -> None:
        """Display project config file contents."""
        from orchestrator.lib.settings import get_project_settings_path

        project_path = get_project_settings_path()
        if not project_path or not project_path.exists():
            click.echo(f"{click.style('ERROR', fg='red')}: No project config found.")
            click.echo(f"Use {click.style('/save', fg='bright_yellow')} to create one from current agents.")
            return

        click.echo(f"\n--- {project_path} ---")
        click.echo(project_path.read_text())
        click.echo("---")

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
    event_handler: ConversationEventHandler | None = None,
) -> dict[str, Any]:
    """Execute a conversation workflow.

    Convenience function for CLI usage.

    Args:
        workflow: The workflow to execute
        yaml_content: Raw YAML content for storage
        event_handler: Event handler for UI (defaults to CliEventHandler)
    """
    # Import here to avoid circular dependency
    if event_handler is None:
        from orchestrator.services.cli_event_handler import CliEventHandler
        from orchestrator.lib.logging import disable_logging

        # Disable JSON logs for cleaner output
        disable_logging()
        event_handler = CliEventHandler()

    executor = ConversationExecutor(
        workflow=workflow,
        yaml_content=yaml_content,
        event_handler=event_handler,
    )
    return executor.execute()
