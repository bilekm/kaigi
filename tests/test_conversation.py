"""Tests for the conversation executor service."""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone

from kaigi.models.execution import (
    ConversationRecord,
    ConsensusStatus,
    ExecutionStatus,
    MessageRole,
    TurnResult,
)
from kaigi.models.workflow import (
    ConversationAgent,
    ConversationWorkflow,
)
from kaigi.services.conversation import ConversationExecutor
from kaigi.services.event_handler import NullEventHandler
from kaigi.services.store import WorkflowStore


@pytest.fixture
def mock_store():
    """Create a mock workflow store."""
    store = Mock(spec=WorkflowStore)
    store.acquire_global_lock = Mock(return_value=True)
    store.release_global_lock = Mock()
    store.save_conversation_workflow = Mock()
    store.create_conversation = Mock()
    store.save_conversation = Mock()
    return store


@pytest.fixture
def sample_workflow():
    """Create a sample conversation workflow."""
    return ConversationWorkflow(
        name="test-workflow",
        version="1.0",
        mode="conversation",
        description="Test workflow",
        agents=[
            ConversationAgent(
                id="agent1",
                agent="claude",
                persona="You are Agent 1.",
            ),
            ConversationAgent(
                id="agent2",
                agent="copilot",
                persona="You are Agent 2.",
            ),
        ],
        topic="Test topic",
        max_rounds=3,
        consensus_keyword="AGREED:",
    )


@pytest.fixture
def null_handler():
    """Create a null event handler for testing."""
    return NullEventHandler()


class TestConversationExecutor:
    """Tests for ConversationExecutor class."""

    def test_init(self, sample_workflow, mock_store, null_handler):
        """Test executor initialization."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
            event_handler=null_handler,
        )

        assert executor.workflow == sample_workflow
        assert executor.yaml_content == "name: test"
        assert executor.store == mock_store
        assert executor.event_handler == null_handler

    def test_init_default_handler(self, sample_workflow, mock_store):
        """Test executor uses NullEventHandler by default."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        assert isinstance(executor.event_handler, NullEventHandler)

    def test_init_default_store(self, sample_workflow, null_handler):
        """Test executor creates WorkflowStore by default."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            event_handler=null_handler,
        )

        assert isinstance(executor.store, WorkflowStore)


class TestConsensusLogic:
    """Tests for consensus checking logic."""

    def test_check_consensus_team_mode_all_agree(self, sample_workflow, mock_store):
        """Test consensus detection in team mode when all agents agree."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        record.add_message(MessageRole.AGENT, "I agree. AGREED: Yes", agent_id="agent1")
        record.add_message(MessageRole.AGENT, "Me too. AGREED: Yes", agent_id="agent2")

        agent1 = sample_workflow.agents[0]
        result = executor._check_consensus(record, agent1)

        assert result is True

    def test_check_consensus_team_mode_partial_agree(self, sample_workflow, mock_store):
        """Test consensus not reached when only some agents agree."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        record.add_message(MessageRole.AGENT, "I agree. AGREED: Yes", agent_id="agent1")
        record.add_message(MessageRole.AGENT, "I'm not sure yet.", agent_id="agent2")

        agent1 = sample_workflow.agents[0]
        result = executor._check_consensus(record, agent1)

        assert result is False

    def test_check_consensus_orchestrated_mode_lead_only(self, mock_store):
        """Test in orchestrated mode only lead's decision matters."""
        workflow = ConversationWorkflow(
            name="orchestrated-test",
            version="1.0",
            mode="conversation",
            collaboration="orchestrated",
            lead="lead-agent",
            agents=[
                ConversationAgent(id="lead-agent", agent="claude", persona="Lead"),
                ConversationAgent(id="team-agent", agent="copilot", persona="Team"),
            ],
            topic="Test topic",
            max_rounds=3,
            consensus_keyword="AGREED:",
        )

        executor = ConversationExecutor(
            workflow=workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        # Team agent agreed but lead hasn't
        record.add_message(MessageRole.AGENT, "I agree. AGREED: Yes", agent_id="team-agent")

        team_agent = workflow.agents[1]
        result = executor._check_consensus(record, team_agent)

        assert result is False

    def test_check_consensus_orchestrated_mode_lead_agrees(self, mock_store):
        """Test in orchestrated mode lead's agreement triggers consensus."""
        workflow = ConversationWorkflow(
            name="orchestrated-test",
            version="1.0",
            mode="conversation",
            collaboration="orchestrated",
            lead="lead-agent",
            agents=[
                ConversationAgent(id="lead-agent", agent="claude", persona="Lead"),
                ConversationAgent(id="team-agent", agent="copilot", persona="Team"),
            ],
            topic="Test topic",
            max_rounds=3,
            consensus_keyword="AGREED:",
        )

        executor = ConversationExecutor(
            workflow=workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        # Lead agreed
        record.add_message(MessageRole.AGENT, "Let's do it. AGREED: Plan approved", agent_id="lead-agent")

        lead_agent = workflow.agents[0]
        result = executor._check_consensus(record, lead_agent)

        assert result is True

    def test_extract_consensus_content(self, sample_workflow, mock_store):
        """Test extracting consensus content from messages."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        record.add_message(MessageRole.AGENT, "Some earlier message", agent_id="agent1")
        record.add_message(
            MessageRole.AGENT,
            "I agree. AGREED: This is the consensus content we want to extract.",
            agent_id="agent2",
        )

        executor._extract_consensus_content(record)

        assert record.consensus_content == "AGREED: This is the consensus content we want to extract."


class TestAgentResolution:
    """Tests for agent configuration resolution."""

    def test_resolve_agent_reference(self, sample_workflow, mock_store):
        """Test resolving an agent reference from settings."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        agent = sample_workflow.agents[0]  # agent: claude reference

        with patch("kaigi.services.conversation.get_agent_config") as mock_config:
            mock_config.return_value = Mock(
                command="claude",
                args=["-p", "{{prompt}}"],
                env={},
                timeout=300,
                model="opus-4",
            )

            command, args, env, timeout, model = executor._resolve_agent(agent)

            assert command == "claude"
            assert args == ["-p", "{{prompt}}"]
            assert timeout == 300
            assert model == "opus-4"

    def test_resolve_agent_inline_config(self, mock_store):
        """Test resolving an agent with inline configuration."""
        workflow = ConversationWorkflow(
            name="test",
            version="1.0",
            mode="conversation",
            agents=[
                ConversationAgent(
                    id="custom",
                    command="custom-cli",
                    args=["--prompt", "{{prompt}}"],
                    timeout=600,
                ),
                ConversationAgent(id="other", agent="copilot"),
            ],
            topic="Test topic",
            max_rounds=1,
            min_rounds=1,  # Must be <= max_rounds
        )

        executor = ConversationExecutor(
            workflow=workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        agent = workflow.agents[0]
        command, args, env, timeout, model = executor._resolve_agent(agent)

        assert command == "custom-cli"
        assert args == ["--prompt", "{{prompt}}"]
        assert timeout == 600
        assert model is None

    def test_resolve_agent_inline_override(self, sample_workflow, mock_store):
        """Test inline config overrides settings reference."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        agent = ConversationAgent(
            id="agent1",
            agent="claude",
            timeout=500,  # Override default timeout
            model="sonnet-4",
        )

        with patch("kaigi.services.conversation.get_agent_config") as mock_config:
            mock_config.return_value = Mock(
                command="claude",
                args=["-p", "{{prompt}}"],
                env={},
                timeout=300,
                model="opus-4",
            )

            command, args, env, timeout, model = executor._resolve_agent(agent)

            assert timeout == 500  # Overridden
            assert model == "sonnet-4"  # Overridden


class TestPromptBuilding:
    """Tests for prompt building logic."""

    def test_build_prompt_team_mode(self, sample_workflow, mock_store):
        """Test building prompt for team collaboration mode."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        record.add_message(MessageRole.SYSTEM, "System message")
        record.add_message(MessageRole.AGENT, "Previous response", agent_id="agent1")

        agent = sample_workflow.agents[0]
        prompt = executor._build_prompt(record, agent)

        assert "Test topic" in prompt
        assert agent.persona in prompt
        assert "AGREED:" in prompt
        assert "Previous response" in prompt

    def test_build_prompt_orchestrated_lead(self, mock_store):
        """Test building prompt for lead agent in orchestrated mode."""
        workflow = ConversationWorkflow(
            name="orchestrated-test",
            version="1.0",
            mode="conversation",
            collaboration="orchestrated",
            lead="lead",
            agents=[
                ConversationAgent(id="lead", agent="claude", persona="Lead agent"),
                ConversationAgent(id="member", agent="copilot", persona="Team member"),
            ],
            topic="Test topic",
            max_rounds=3,
            consensus_keyword="DECIDED:",
        )

        executor = ConversationExecutor(
            workflow=workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )

        lead = workflow.agents[0]
        prompt = executor._build_prompt(record, lead)

        assert "LEAD" in prompt  # Lead agent template
        assert "member" in prompt  # Team list included
        assert "DECIDED:" in prompt
        assert "Lead agent" in prompt  # Persona included

    def test_build_prompt_orchestrated_member(self, mock_store):
        """Test building prompt for team member in orchestrated mode."""
        workflow = ConversationWorkflow(
            name="orchestrated-test",
            version="1.0",
            mode="conversation",
            collaboration="orchestrated",
            lead="lead",
            agents=[
                ConversationAgent(id="lead", agent="claude", persona="Lead agent"),
                ConversationAgent(id="member", agent="copilot", persona="Team member"),
            ],
            topic="Test topic",
            max_rounds=3,
        )

        executor = ConversationExecutor(
            workflow=workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )

        member = workflow.agents[1]
        prompt = executor._build_prompt(record, member)

        assert "lead" in prompt.lower()
        # Member shouldn't have the same decision-making language as lead


class TestCommandHandling:
    """Tests for slash command handling."""

    def test_handle_help_command(self, sample_workflow, mock_store):
        """Test /help command returns help text."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        result = executor._handle_command("/help")

        assert result is not None
        assert "help" in result.lower()

    def test_handle_quit_command(self, sample_workflow, mock_store):
        """Test /quit command returns quit signal."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        result = executor._handle_command("/quit")

        assert result == "__quit__"

    def test_handle_unknown_command(self, sample_workflow, mock_store):
        """Test unknown command returns error message."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        result = executor._handle_command("/unknown")

        assert result is not None
        assert "unknown" in result.lower()

    def test_handle_agents_command(self, sample_workflow, mock_store):
        """Test /agents command lists agents."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
        )

        result = executor._handle_command("/agents")

        assert result is not None
        # Should contain agent IDs
        assert "agent1" in result or "agent2" in result


class TestEventHandlerIntegration:
    """Tests for event handler integration."""

    def test_event_handler_called_on_conversation_start(self, sample_workflow, mock_store):
        """Test that event handler is called when conversation starts."""
        mock_handler = Mock()
        mock_handler.on_conversation_start = Mock()
        mock_handler.on_round_start = Mock()
        mock_handler.on_agent_turn_start = Mock()
        mock_handler.on_agent_turn_complete = Mock()
        mock_handler.on_consensus_reached = Mock()
        mock_handler.prompt_user_approval = AsyncMock(return_value="approve")
        mock_handler.prompt_user_input = AsyncMock(return_value=None)
        mock_handler.on_max_rounds_reached = Mock()

        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
            event_handler=mock_handler,
        )

        # Mock agent execution to return quickly
        with patch.object(executor, "_execute_agent", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Simple response. AGREED: Done"

            with patch("kaigi.services.conversation.signal_handler_context"):
                result = executor.execute()

        # Verify event handler was called
        mock_handler.on_conversation_start.assert_called_once()
        mock_handler.on_round_start.assert_called()

    def test_event_handler_not_called_in_null_mode(self, sample_workflow, mock_store, null_handler):
        """Test that null handler doesn't cause errors."""
        executor = ConversationExecutor(
            workflow=sample_workflow,
            yaml_content="name: test",
            store=mock_store,
            event_handler=null_handler,
        )

        # Should not raise any errors
        with patch.object(executor, "_execute_agent", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = "Response with AGREED: Done"

            with patch("kaigi.services.conversation.signal_handler_context"):
                result = executor.execute()

        assert result is not None


class TestTurnResult:
    """Tests for turn result tracking."""

    def test_turn_result_success(self):
        """Test successful turn result."""
        turn = TurnResult(agent_id="test-agent")
        turn.start()

        assert turn.status == "running"
        assert turn.agent_id == "test-agent"

        turn.complete("msg-123")

        assert turn.status == "completed"
        assert turn.message_id == "msg-123"
        assert turn.duration_seconds is not None

    def test_turn_result_failure(self):
        """Test failed turn result."""
        turn = TurnResult(agent_id="test-agent")
        turn.start()

        turn.fail("Something went wrong")

        assert turn.status == "failed"
        # TurnResult stores failure info, check status changed
        assert turn.duration_seconds is not None  # Time was tracked


class TestConversationRecord:
    """Tests for conversation record model."""

    def test_add_message(self):
        """Test adding messages to conversation record."""
        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )

        msg = record.add_message(MessageRole.AGENT, "Test message", agent_id="agent1")

        assert len(record.messages) == 1
        assert msg.content == "Test message"
        assert msg.agent_id == "agent1"
        assert msg.role == MessageRole.AGENT

    def test_get_conversation_history(self):
        """Test getting conversation history."""
        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        record.add_message(MessageRole.SYSTEM, "System message")
        record.add_message(MessageRole.AGENT, "Agent message", agent_id="agent1")
        record.add_message(MessageRole.USER, "User message")

        history = record.get_conversation_history()

        # get_conversation_history returns a formatted string
        assert isinstance(history, str)
        assert "System message" in history
        assert "Agent message" in history
        assert "User message" in history
        assert "[SYSTEM]" in history
        assert "[agent1]" in history

    def test_check_consensus_all_agreed(self):
        """Test consensus check when all agents agreed."""
        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        record.add_message(MessageRole.AGENT, "I agree. AGREED: Yes", agent_id="agent1")
        record.add_message(MessageRole.AGENT, "Me too. AGREED: Yes", agent_id="agent2")

        result = record.check_consensus(["agent1", "agent2"], "AGREED:")

        assert result is True

    def test_check_consensus_partial_agree(self):
        """Test consensus check when not all agents agreed."""
        record = ConversationRecord(
            id="test-id",
            workflow_id="wf-id",
            workflow_name="test",
            topic="Test topic",
            max_rounds=3,
        )
        record.add_message(MessageRole.AGENT, "I agree. AGREED: Yes", agent_id="agent1")
        record.add_message(MessageRole.AGENT, "Not yet.", agent_id="agent2")

        result = record.check_consensus(["agent1", "agent2"], "AGREED:")

        assert result is False
