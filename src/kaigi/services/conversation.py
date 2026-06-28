"""Conversation executor for multi-agent discussions."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
import yaml

from kaigi.lib.errors import (
    KaigiError,
    agent_failed,
    agent_rate_limited,
    agent_timeout,
    conversation_error,
    detect_rate_limit,
    workflow_invalid,
    workflow_locked,
)
from kaigi.lib.logging import get_logger
from kaigi.lib.agent_state import AgentStateStore
from kaigi.lib.prompts import (
    build_followup_prompt,
    build_lead_prompt,
    build_orchestrated_system_message,
    build_project_prompt,
    build_rules_prompt,
    build_team_member_prompt,
    build_team_prompt,
    build_team_system_message,
)
from kaigi.services.summarizer import (
    apply_summary,
    create_summary,
    should_summarize,
)
from kaigi.lib.settings import get_agent_config
from kaigi.lib.signals import signal_handler_context
from kaigi.models.execution import (
    ConsensusStatus,
    ConversationRecord,
    ExecutionStatus,
    MessageRole,
    TurnResult,
)
from kaigi.models.workflow import ConversationAgent, ConversationWorkflow
from kaigi.services.event_handler import ConversationEventHandler, NullEventHandler
from kaigi.services.store import WorkflowStore
from kaigi.services.tools import CapabilityChecker, ToolCall, ToolExecutor, ToolResult

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
        non_interactive: bool = False,
        decision_only: bool = False,
        persistent_mode: bool = False,
    ):
        self.workflow = workflow
        self.yaml_content = yaml_content
        self.store = store or WorkflowStore()
        self.event_handler = event_handler or NullEventHandler()
        self.non_interactive = non_interactive
        # Advisory/decision mode: stop at the consensus recommendation, never
        # enter the write-enabled execution phase.
        self.decision_only = decision_only
        self.persistent_mode = persistent_mode
        self.logger = get_logger()
        self.starting_agent_id: str | None = None  # Agent to start conversation with
        self._initialized_agents: set[str] = set()  # Agents that received full prompt (session)

        # Persistent agent state tracking (across sessions)
        self._agent_state = AgentStateStore()

        # Check if workflow changed - invalidate all agent states if so
        if self._agent_state.check_workflow_changed(yaml_content):
            self.logger.debug("Workflow changed, invalidating agent states")
            self._agent_state.invalidate_all()
            self._agent_state.update_workflow_hash(yaml_content)

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

                # Parse topic for /agentid <prompt> pattern
                topic, starting_agent = self._parse_starting_agent(topic)

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

                # Direct mode: execute designated agent immediately and exit
                if self.workflow.collaboration == "direct":
                    designated = self.workflow.get_agent(self.workflow.designated_agent or "")
                    if designated:
                        click.echo()
                        click.echo(f"Executing {designated.id} in direct mode...")
                        click.echo()

                        # Execute with write permission using native tools
                        await self._agent_turn(
                            record, designated, workflow_id,
                            with_write_permission=True,
                            use_native_tools=True,
                        )

                        # Save the conversation record (provides built-in memory)
                        record.complete()
                        self.store.save_conversation(workflow_id, record)
                        return self._format_result(record)

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

                    # Rotate turn order if starting agent specified (first round only)
                    if self.starting_agent_id and record.current_round == 1:
                        turn_agents = self._rotate_to_starting_agent(turn_agents, self.starting_agent_id)

                    # Each agent takes a turn
                    for agent in turn_agents:
                        if _cancelled or agent is None:
                            break

                        # Try agent turn, handle rate limits
                        try:
                            await self._agent_turn(record, agent, workflow_id)
                        except KaigiError as e:
                            if e.code.value == "AGENT_RATE_LIMITED":
                                # Handle rate limit - prompt user for action
                                action = await self._handle_rate_limit(
                                    record, agent, e.details.get("reset_time", "")
                                )
                                if action == "quit":
                                    record.cancel()
                                    self.store.save_conversation(workflow_id, record)
                                    return self._format_result(record)
                                elif action == "skip":
                                    continue  # Skip to next agent
                                elif action.startswith("replace:"):
                                    # Replace agent for this turn (one-time)
                                    replacement_id = action.split(":", 1)[1]
                                    replacement = self._get_replacement_agent(replacement_id)
                                    if replacement:
                                        try:
                                            await self._agent_turn(record, replacement, workflow_id)
                                        except KaigiError:
                                            pass  # If replacement also fails, just continue
                                # "wait" - just continue, will retry on next round
                            elif self.non_interactive and e.code.value == "AGENT_FAILED":
                                # Resilience: in unattended mode a single agent failing
                                # (e.g. an API usage/session limit) must not abort the
                                # whole council. Skip it this round; the remaining agents
                                # (and the lead) still proceed and can reach consensus.
                                self.logger.warning(
                                    "Agent failed; skipping this round (non-interactive)",
                                    agent_id=agent.id,
                                    error=str(e),
                                )
                                continue
                            else:
                                raise

                        self.store.save_conversation(workflow_id, record)

                        # Check for consensus/decision after each turn
                        if self._check_consensus(record, agent):
                            record.consensus_status = ConsensusStatus.AGREED
                            self._extract_consensus_content(record)
                            # Don't break - let all agents in this round respond

                    # After round complete, check if summarization is needed
                    # (Triggered by high token count OR consensus reached)
                    if should_summarize(record):
                        self.logger.info(
                            "Conversation exceeds token threshold, creating summary",
                            round=record.current_round,
                        )
                        record = apply_summary(record)
                        self.store.save_conversation(workflow_id, record)

                    # After round, check if consensus reached and enforce min_rounds
                    if record.consensus_status == ConsensusStatus.AGREED:
                        if record.current_round < self.workflow.min_rounds:
                            # Consensus reached too early - reset and continue
                            self.event_handler.on_consensus_too_early(
                                record.current_round,
                                self.workflow.min_rounds,
                            )
                            record.consensus_status = ConsensusStatus.PENDING
                            record.consensus_content = None  # Clear stale content
                        else:
                            # Consensus reached - create summary before user approval
                            self.logger.info(
                                "Consensus reached, creating conversation summary",
                                round=record.current_round,
                            )
                            record = apply_summary(record)
                            self.store.save_conversation(workflow_id, record)
                        # If min_rounds satisfied, proceed to user approval

                    # Prompt for user approval if consensus reached (and min_rounds satisfied)
                    if record.consensus_status == ConsensusStatus.AGREED:
                        self.event_handler.on_consensus_reached(record.consensus_content or "")

                        # Advisory/decision mode: the consensus recommendation IS the
                        # deliverable. Capture it and stop here - never enter the
                        # write-enabled execution phase. Status stays AGREED (consensus
                        # reached, not human-approved/executed).
                        if self.decision_only:
                            record.complete()
                            self.store.save_conversation(workflow_id, record)
                            break

                        # In non-interactive mode, auto-approve consensus
                        if self.non_interactive:
                            user_response = "approve"
                        else:
                            user_response = await self.event_handler.prompt_user_approval(
                                record.consensus_content or ""
                            )

                        # Parse the approval command
                        approved, agent_id_override, model_override = self._parse_approval_command(user_response)

                        if approved:
                            record.consensus_status = ConsensusStatus.APPROVED
                            # Enter execution phase - have designated agent execute the agreed changes
                            click.echo()
                            click.echo("=" * 50)
                            click.echo("EXECUTION PHASE")
                            click.echo("=" * 50)

                            # Agent resolution: runtime override > workflow config > first agent
                            executor_agent = None
                            if agent_id_override:
                                # Runtime override from approval command
                                executor_agent = self.workflow.get_agent(agent_id_override)
                            elif self.workflow.execution.executor_agent:
                                # Workflow config
                                executor_agent = self.workflow.get_agent(self.workflow.execution.executor_agent)

                            if executor_agent is None:
                                # Fallback to first agent
                                executor_agent = self.workflow.agents[0]

                            click.echo(f"Asking {executor_agent.id} to execute the agreed changes...")

                            # Apply model override if specified
                            if model_override:
                                # Create a copy of the agent with model override
                                from copy import copy
                                executor_agent = copy(executor_agent)
                                executor_agent.model = model_override
                                click.echo(f"Using model override: {model_override}")

                            # Build execution prompt
                            exec_prompt = self._build_execution_prompt(record, executor_agent)
                            record.add_message(MessageRole.SYSTEM, exec_prompt)

                            # Execute WITH write permission, using native tools
                            await self._agent_turn(
                                record, executor_agent, workflow_id,
                                with_write_permission=True,
                                execution_prompt=exec_prompt,
                                use_native_tools=True,
                            )
                            self.store.save_conversation(workflow_id, record)

                            # Reset consensus state for next round, keep conversation running
                            record.consensus_status = ConsensusStatus.PENDING
                            record.consensus_content = None
                            self.store.save_conversation(workflow_id, record)
                            # Don't break - let loop continue to prompt user or exit cleanly
                        else:
                            # User provided feedback or invalid approval - continue discussion
                            if user_response:
                                record.add_message(MessageRole.USER, user_response)
                                self.store.save_conversation(workflow_id, record)
                            record.consensus_status = ConsensusStatus.REJECTED

                    # Prompt user for input between rounds (skip in non-interactive mode)
                    if not _cancelled and not self.non_interactive:
                        user_input = await self.event_handler.prompt_user_input()
                        if user_input == "__quit__":
                            record.cancel()
                            self.store.save_conversation(workflow_id, record)
                            break
                        elif user_input == "__new_topic__":
                            # Clear agent state and prompt for new topic
                            self._agent_state.reset()
                            self._initialized_agents.clear()
                            click.echo()
                            click.echo(click.style("Starting new topic...", fg="green"))
                            click.echo()

                            # Prompt for new topic
                            agent_ids = [a.id for a in self.workflow.agents]
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
                                break

                            # Parse topic for /agentid <prompt> pattern
                            topic, starting_agent = self._parse_starting_agent(topic)

                            # Update workflow and record
                            self.workflow.topic = topic
                            record.topic = topic
                            record.current_round = 0
                            record.consensus_status = ConsensusStatus.PENDING
                            record.consensus_content = None
                            record.messages.clear()

                            # Add new system message with topic
                            if self.workflow.collaboration == "orchestrated":
                                lead_id = self.workflow.lead or ""
                                team_ids = [a.id for a in self.workflow.get_team_agents()]
                                system_msg = build_orchestrated_system_message(
                                    self.workflow.topic, lead_id, team_ids
                                )
                            else:
                                system_msg = build_team_system_message(self.workflow.topic, agent_ids)
                            record.add_message(MessageRole.SYSTEM, system_msg)
                            self.store.save_conversation(workflow_id, record)

                            # Continue loop with new topic
                            continue
                        elif user_input:
                            record.add_message(MessageRole.USER, user_input)
                            self.store.save_conversation(workflow_id, record)

                # Finalize
                if record.status == ExecutionStatus.RUNNING:
                    if record.current_round >= self.workflow.max_rounds:
                        record.complete()
                        self.event_handler.on_max_rounds_reached(self.workflow.max_rounds)

                record.ended_at = datetime.now(UTC)
                self.store.save_conversation(workflow_id, record)

                return self._format_result(record)

        finally:
            self.store.release_global_lock()

    def _strip_thinking_blocks(self, text: str) -> str:
        """Strip thinking blocks from agent output.

        Some agents (like Claude) output <thinking>...</thinking> blocks.
        These should be removed from stored messages since:
        1. They're not part of the actual response content
        2. Including them in conversation history causes API errors when
           the history is sent to agents in subsequent prompts

        Args:
            text: Raw agent output that may contain thinking blocks

        Returns:
            Text with thinking blocks removed
        """
        return re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL).strip()

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

    async def _handle_rate_limit(
        self,
        record: ConversationRecord,
        agent: ConversationAgent,
        reset_time: str,
    ) -> str:
        """Handle rate limit.

        In non-interactive mode there is no user to prompt, so auto-skip the
        rate-limited agent for this round (the conversation continues without
        it). Only prompt the user when running interactively.
        """
        if self.non_interactive:
            self.logger.warning(
                "Agent rate-limited; skipping this round (non-interactive)",
                agent_id=agent.id,
            )
            return "skip"
        return await self.event_handler.on_agent_rate_limited(agent.id, reset_time)

    def _get_replacement_agent(self, agent_ref: str) -> ConversationAgent | None:
        """Get a replacement agent by reference name."""
        # Check if it's an existing agent in the workflow
        for agent in self.workflow.agents:
            if agent.id == agent_ref or agent.agent == agent_ref:
                return agent

        # Try to create from settings
        config = get_agent_config(agent_ref)
        if config:
            return ConversationAgent(
                id=f"temp_{agent_ref}",
                agent=agent_ref,
                persona=f"Temporary replacement for rate-limited agent.",
            )

        return None

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
                        from kaigi.models.workflow import ConversationAgent

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

                        # Prepend tool definitions to subagent prompt
                        from kaigi.lib.prompts import TOOL_DEFINITIONS
                        enhanced_prompt = f"{TOOL_DEFINITIONS}\n\n{prompt}"

                        # Execute subagent with tool support (read-only by default)
                        subagent_response = await self._execute_agent(
                            subagent,
                            enhanced_prompt,
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
        use_native_tools: bool = False,
    ) -> None:
        """Execute a single agent's turn.

        Args:
            record: The conversation record
            agent: The agent to execute
            workflow_id: The workflow ID
            with_write_permission: Whether to grant write permissions
            execution_prompt: If provided, use this prompt directly instead of
                            building from conversation history (for execution phase)
            use_native_tools: If True, skip the tool loop and let the agent use
                            its native tools directly (for execution phase)
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

        # Execute agent command
        try:
            if use_native_tools:
                # Skip tool loop - let agent use its native tools directly
                output = await self._execute_agent(agent, prompt, with_write_permission)
            else:
                # Use kaigi's tool loop (for discussion phase)
                output = await self._execute_tool_loop(agent, prompt, with_write_permission=with_write_permission)

            # Check for rate limit in output
            is_rate_limited, reset_time = detect_rate_limit(output)
            if is_rate_limited:
                turn.fail(f"Rate limit hit")
                self.logger.warning(
                    "Agent rate limited",
                    agent_id=agent.id,
                    reset_time=reset_time,
                )
                self.event_handler.on_agent_turn_error(agent.id, f"RATE_LIMITED (resets {reset_time})")
                raise agent_rate_limited(agent.id, reset_time)

            # Add response as message (strip thinking blocks for cleaner history)
            clean_output = self._strip_thinking_blocks(output)
            msg = record.add_message(MessageRole.AGENT, clean_output, agent_id=agent.id)
            turn.complete(msg.id)

            duration = turn.duration_seconds or 0
            self.logger.info(
                "Agent responded",
                agent_id=agent.id,
                duration=duration,
            )

            self.event_handler.on_agent_turn_complete(agent.id, duration, output)

        except TimeoutError:
            turn.fail(f"Timeout after {agent.timeout}s")

            self.logger.error("Agent timeout", agent_id=agent.id, timeout=agent.timeout)

            self.event_handler.on_agent_turn_error(agent.id, "TIMEOUT")

            raise agent_timeout(agent.id, agent.timeout)

        except KaigiError:
            raise

        except Exception as e:
            # Check if the exception message indicates rate limit
            error_str = str(e)
            is_rate_limited, reset_time = detect_rate_limit(error_str)
            if is_rate_limited:
                turn.fail(f"Rate limit hit")
                self.logger.warning(
                    "Agent rate limited (from error)",
                    agent_id=agent.id,
                    reset_time=reset_time,
                )
                self.event_handler.on_agent_turn_error(agent.id, f"RATE_LIMITED (resets {reset_time})")
                raise agent_rate_limited(agent.id, reset_time)

            turn.fail(str(e))

            self.logger.error("Agent error", agent_id=agent.id, error=str(e))

            self.event_handler.on_agent_turn_error(agent.id, str(e))

            raise conversation_error(f"Agent '{agent.id}' failed: {e}")

    def _parse_starting_agent(self, topic: str) -> tuple[str, str | None]:
        """Parse topic for /agentid <prompt> pattern.

        Args:
            topic: The raw topic string

        Returns:
            Tuple of (clean_topic, starting_agent_id)
            - clean_topic: Topic with /agentid command stripped
            - starting_agent_id: Agent ID to start with, or None
        """
        # Check if topic starts with /agentid pattern
        pattern = r'^/([a-zA-Z0-9_-]+)\s+(.+)$'
        match = re.match(pattern, topic, re.DOTALL)

        if not match:
            return topic, None

        agent_id = match.group(1)
        clean_topic = match.group(2)

        # Validate the agent exists in workflow
        agent = self.workflow.get_agent(agent_id)
        if not agent:
            # Invalid agent ID - treat as regular topic (don't parse as command)
            return topic, None

        # Valid agent - set starting agent and return clean topic
        self.starting_agent_id = agent_id
        self.logger.info(
            "Starting conversation with specific agent",
            agent_id=agent_id,
        )
        return clean_topic, agent_id

    def _rotate_to_starting_agent(
        self,
        agents: list[ConversationAgent],
        starting_agent_id: str,
    ) -> list[ConversationAgent]:
        """Rotate agent list so specified agent is first.

        Args:
            agents: List of agents in default order
            starting_agent_id: ID of agent to place first

        Returns:
            Rotated list with starting agent first, followed by remaining agents
            in original order. If starting agent not found, returns original list.
        """
        # Find the starting agent's index
        start_index = None
        for i, agent in enumerate(agents):
            if agent.id == starting_agent_id:
                start_index = i
                break

        if start_index is None:
            # Agent not found - return original list
            return agents

        # Rotate: agents from start_index onward, then agents before start_index
        return agents[start_index:] + agents[:start_index]

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
                    f"Add it to ~/.kaigi/agents.yaml"
                )
            # Merge: settings provide base, workflow can override
            command = agent.command or config.command
            args = agent.args if agent.args else config.args
            env = {**config.env, **agent.env}  # Workflow env overrides settings
            timeout = agent.timeout if agent.timeout != 300 else config.timeout
            model = agent.model or config.model  # Workflow model overrides settings
            return command, args, self._expand_env(env), timeout, model
        else:
            # Inline configuration
            if not agent.command:
                raise workflow_invalid(
                    f"Agent '{agent.id}' has no command. "
                    f"Either specify 'command' or 'agent' (reference to settings)."
                )
            return (
                agent.command, agent.args, self._expand_env(agent.env),
                agent.timeout, agent.model,
            )

    @staticmethod
    def _expand_env(env: dict[str, str]) -> dict[str, str]:
        """Expand ${VAR}/$VAR references in env values.

        Settings-file agents are expanded via AgentConfig.resolve_env_vars(), but
        inline workflow-agent env (and workflow-level overrides) are not - without
        this they would be passed through literally (e.g. the string "${GLM_API_KEY}"
        sent as an auth token, causing 401s).
        """
        from kaigi.lib.settings import _expand_env_vars

        return {k: _expand_env_vars(v) if isinstance(v, str) else v for k, v in env.items()}

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
            from kaigi.lib.agent_client import is_agent_running, send_prompt

            if is_agent_running(agent_id):
                self.logger.info(
                    "Using persistent agent",
                    agent_id=agent_id,
                )

                # Get timeout for persistent agent
                # Note: Model is configured in agent's args (--model flag), not via /model command
                # The /model command only works in interactive mode, not with -p flag
                _, _, _, timeout, _ = self._resolve_agent(agent)

                # Send via IPC (model already set via agent's --model arg)
                return await send_prompt(agent_id, prompt, timeout=timeout)

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
        except TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()
            raise
        finally:
            _current_process = None

        if process.returncode != 0:
            # `claude -p` (and many CLI agents) print API errors to STDOUT, not
            # stderr - so include both, or failures are undiagnosable (we only ever
            # saw a benign startup warning otherwise).
            err = stderr.decode().strip()
            out = stdout.decode().strip()
            detail = "\n".join(
                part for part in (
                    f"[stderr] {err}" if err else "",
                    f"[stdout] {out}" if out else "",
                ) if part
            )
            raise agent_failed(
                agent.id,
                process.returncode,
                detail or "(no output captured)",
            )

        return stdout.decode().strip()

    def _build_prompt(
        self,
        record: ConversationRecord,
        agent: ConversationAgent,
    ) -> str:
        """Build the prompt for an agent's turn.

        Uses persistent state to avoid sending duplicate context to agents
        with persistent memory. Prompt selection:

        1. Agent never seen this workflow → Full prompt (rules + project + topic)
        2. Agent knows rules, new session → Project prompt (project + topic)
        3. Agent addressed this session → Compact prompt (just new messages)
        """
        agent_list = [a.id for a in self.workflow.agents]

        # Determine what context agent needs
        needs_rules = self._agent_state.needs_rules(agent.id)
        in_session = agent.id in self._initialized_agents

        if needs_rules:
            # Agent never seen this workflow - send everything
            self.logger.debug(f"Agent {agent.id}: sending full context (first time)")
            self._agent_state.mark_rules_sent(agent.id)
            self._agent_state.mark_project_sent(agent.id)
            self._initialized_agents.add(agent.id)

            # Build full prompt using existing templates
            return self._build_full_prompt(agent, record.get_conversation_history())

        elif not in_session:
            # Agent knows rules from previous session, but new topic this session
            self.logger.debug(f"Agent {agent.id}: sending project context (new session)")
            self._initialized_agents.add(agent.id)

            # Send project context (agent recalls rules from memory)
            return build_project_prompt(
                agent_id=agent.id,
                persona=agent.persona,
                topic=self.workflow.topic,
                agent_list=agent_list,
                collaboration_mode=self.workflow.collaboration,
                min_rounds=self.workflow.min_rounds,
                history=record.get_conversation_history(),
            )

        else:
            # Compact follow-up. Send only the delta (messages since this agent's
            # last turn) ONLY to agents that retain context across turns - otherwise
            # a stateless (spawn/no-session-persistence) agent would lose all prior
            # context. Default to full history when retention is not guaranteed.
            if self._agent_retains_context(agent):
                history = record.get_conversation_history(for_agent_id=agent.id)
                self.logger.debug(f"Agent {agent.id}: sending compact prompt (delta history)")
            else:
                history = record.get_conversation_history()
                self.logger.debug(f"Agent {agent.id}: sending compact prompt (full history)")
            return build_followup_prompt(
                agent_id=agent.id,
                history=history,
                consensus_keyword=self.workflow.consensus_keyword,
            )

    def _agent_retains_context(self, agent: ConversationAgent) -> bool:
        """Whether an agent retains conversation context across turns.

        Only context-retaining agents can safely receive incremental (delta)
        history. An agent retains context only when ALL of the following hold:
          - a persistent server is currently running for it (PTY session alive),
          - it is NOT configured for spawn-per-prompt mode, and
          - its args do not disable session persistence.

        Defaults to False (safe: send full history) whenever retention cannot be
        confirmed.
        """
        agent_id = agent.agent or agent.id

        try:
            from kaigi.lib.agent_client import is_agent_running

            if not is_agent_running(agent_id):
                return False
        except Exception:
            return False

        spawn_mode = False
        args = agent.args
        if agent.is_reference():
            config = get_agent_config(agent.agent)
            if config is None:
                return False
            spawn_mode = config.spawn_mode
            args = agent.args if agent.args else config.args

        if spawn_mode:
            return False
        if any("--no-session-persistence" in arg for arg in args):
            return False
        return True

    def _build_full_prompt(
        self,
        agent: ConversationAgent,
        history: str,
    ) -> str:
        """Build full prompt with rules + project context.

        Used when agent has never seen this workflow before.
        Falls back to existing templates for compatibility.
        """
        # Use existing templates for full prompt (they include everything)
        if self.workflow.collaboration == "orchestrated":
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
                    compact=False,
                )
            else:
                return build_team_member_prompt(
                    agent_id=agent.id,
                    persona=agent.persona,
                    topic=self.workflow.topic,
                    lead_id=self.workflow.lead or "",
                    history=history,
                    min_rounds=self.workflow.min_rounds,
                    compact=False,
                )
        else:
            return build_team_prompt(
                agent_id=agent.id,
                persona=agent.persona,
                topic=self.workflow.topic,
                consensus_keyword=self.workflow.consensus_keyword,
                history=history,
                min_rounds=self.workflow.min_rounds,
                compact=False,
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
- You have FULL permission to read, write, and edit files
- The team reached consensus and the user approved
- You are now authorized to implement the agreed changes

## Consensus (what you agreed to do):
{consensus}

## Your Task
Execute the agreed changes NOW. Use your native tools to:
1. Read any files you need to understand
2. Edit or write files to implement the changes
3. Verify your changes worked

## Important
- DO NOT ask for permission - you already have it
- DO NOT explain what you will do - just DO it
- Use your Edit tool for surgical changes
- Use your Write tool for new files
- Report what you actually changed when done

Execute the consensus now.
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

                    with open(path) as f:
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

        Checks both project-local (.kaigi/subagents.yaml) and global
        (~/.kaigi/subagents.yaml) config files. Project config takes
        precedence over global config.

        Args:
            agent_type: The type of subagent to load (e.g., "explore", "plan", "review")

        Returns:
            Dictionary with subagent configuration (model, persona, permissions, agent)

        Raises:
            ValueError: If agent_type is not found in any config file
        """

        # Paths to check (project takes precedence)
        project_config = Path.cwd() / ".kaigi" / "subagents.yaml"
        global_config = Path.home() / ".kaigi" / "subagents.yaml"

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

    def _parse_approval_command(self, response: str) -> tuple[bool, str | None, str | None]:
        """Parse 'approve [agent-id] [model]' command.

        Returns: (is_valid_approval, agent_id, model_override)
        Returns (False, None, None) for:
        - Non-approval input (feedback, discussion)
        - Invalid agent ID (treat as feedback input)
        """
        parts = response.strip().split()
        if not parts or parts[0].lower() not in ["approve", "y", "yes"]:
            # Not an approval command - treat as feedback
            return (False, None, None)

        # It's an approval attempt
        agent_id = parts[1] if len(parts) > 1 else None
        model = parts[2] if len(parts) > 2 else None

        # Validate agent_id if provided
        if agent_id:
            agent_ids = [a.id for a in self.workflow.agents]
            if agent_id not in agent_ids:
                # Invalid agent - but user clearly meant to approve
                # Print error and treat as rejection so they can retry
                click.echo(f"Error: Agent '{agent_id}' not in workflow.")
                click.echo(f"Available agents: {', '.join(agent_ids)}")
                return (False, None, None)

        return (True, agent_id, model)

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
            elif cmd == "/clear":
                return "__new_topic__"
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
        click.echo(f"  {click.style('/clear', fg='bright_yellow')}                        Start fresh: clear history & prompt for new topic")
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
            from kaigi.lib.settings import load_agent_settings
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
        click.echo(f"Current agents: {', '.join(click.style(a.id, fg='green') for a in self.workflow.agents)}")

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

        project_dir = Path.cwd() / ".kaigi"
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
        yaml_content = "# Orchestrator Project Agents\n"
        yaml_content += "# Saved from conversation session\n\n"
        yaml_content += yaml.dump(
            {"agents": agents_config}, default_flow_style=False, sort_keys=False
        )

        config_path.write_text(yaml_content)
        click.echo(f"Saved {len(agents_config)} agents to: {config_path}")

    def _cmd_config(self) -> None:
        """Show config file locations."""
        from kaigi.lib.settings import get_global_settings_path, get_project_settings_path

        click.echo()
        click.echo("Config file locations:")

        global_path = get_global_settings_path()
        exists = "exists" if global_path.exists() else "not found"
        click.echo(f"  Global:  {global_path} ({exists})")

        project_path = get_project_settings_path()
        if project_path:
            click.echo(f"  Project: {project_path} (active)")
        else:
            project_default = Path.cwd() / ".kaigi" / "agents.yaml"
            click.echo(f"  Project: {project_default} (not created)")
            click.echo()
            click.echo(f"Use {click.style('/save', fg='bright_yellow')} to create project config from current agents.")
        click.echo()

    def _cmd_config_show(self) -> None:
        """Display project config file contents."""
        from kaigi.lib.settings import get_project_settings_path

        project_path = get_project_settings_path()
        if not project_path or not project_path.exists():
            click.echo(f"{click.style('ERROR', fg='red')}: No project config found.")
            click.echo(f"Use {click.style('/save', fg='bright_yellow')} to create one from current agents.")
            return

        click.echo(f"\n--- {project_path} ---")
        click.echo(project_path.read_text())
        click.echo("---")

    def _cmd_clear(self) -> None:
        """Clear agent context state.

        Clears kaigi's tracking of what context has been sent to agents.
        Next prompt will resend full kaigi rules to all agents.
        """
        self._agent_state.reset()
        self._initialized_agents.clear()
        click.echo()
        click.echo(f"{click.style('Cleared', fg='green')}")
        click.echo("Next prompt will send full kaigi rules to all agents.")
        click.echo()

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
    non_interactive: bool = False,
    no_execution: bool = False,
    enable_color: bool = True,
    persistent_mode: bool = False,
) -> dict[str, Any]:
    """Execute a conversation workflow.

    Convenience function for CLI usage.

    Args:
        workflow: The workflow to execute
        yaml_content: Raw YAML content for storage
        event_handler: Event handler for UI (defaults to CliEventHandler)
        non_interactive: If True, skip prompts and auto-approve consensus
        no_execution: If True, advisory/decision mode - stop at the consensus
            recommendation and never enter the write-enabled execution phase
        enable_color: Whether to enable colorized output when using default CliEventHandler (default: True)
        persistent_mode: If True, conversation will return to shell after completion (default: False)
    """
    # Import here to avoid circular dependency
    if event_handler is None:
        from kaigi.lib.logging import disable_logging
        from kaigi.services.cli_event_handler import CliEventHandler

        # Disable JSON logs for cleaner output
        disable_logging()
        event_handler = CliEventHandler(enable_color=enable_color)

    executor = ConversationExecutor(
        workflow=workflow,
        yaml_content=yaml_content,
        event_handler=event_handler,
        non_interactive=non_interactive,
        decision_only=no_execution,
        persistent_mode=persistent_mode,
    )
    return executor.execute()
