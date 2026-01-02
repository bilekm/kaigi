"""Conversation event handler interface for testability.

This module defines the contract for handling conversation lifecycle events,
enabling clean separation of concerns and easier testing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ConversationEventHandler(ABC):
    """Interface for handling conversation lifecycle events.

    Implementations can handle UI output, user input, logging, and other
    side effects without coupling the core conversation logic to specific
    frameworks (e.g., Click, logging, file system).

    Events are called in this order:
    1. on_conversation_start
    2. on_round_start (for each round)
    3. on_agent_turn_start (for each agent in round)
    4. on_agent_turn_complete (after each agent responds)
    5. on_consensus_reached (if consensus achieved)
    6. on_conversation_complete (always called at end)
    """

    @abstractmethod
    def on_conversation_start(
        self,
        workflow_name: str,
        topic: str,
        agents: list[str],
        collaboration: str,
        lead: str | None = None,
    ) -> None:
        """Called when conversation starts.

        Args:
            workflow_name: Name of the workflow being executed
            topic: Discussion topic
            agents: List of agent IDs participating
            collaboration: Collaboration mode ('team' or 'orchestrated')
            lead: Lead agent ID (for orchestrated mode)
        """

    @abstractmethod
    def on_round_start(self, round_num: int) -> None:
        """Called at the start of each discussion round.

        Args:
            round_num: Current round number (1-indexed)
        """

    @abstractmethod
    def on_agent_turn_start(self, agent_id: str) -> None:
        """Called before an agent takes their turn.

        Args:
            agent_id: ID of the agent about to respond
        """

    @abstractmethod
    def on_agent_turn_complete(
        self,
        agent_id: str,
        duration: float,
        output: str,
    ) -> None:
        """Called after an agent completes their turn.

        Args:
            agent_id: ID of the agent that responded
            duration: Response time in seconds
            output: The agent's response text
        """

    @abstractmethod
    def on_agent_turn_error(
        self,
        agent_id: str,
        error: str,
    ) -> None:
        """Called when an agent turn fails.

        Args:
            agent_id: ID of the agent that failed
            error: Error message
        """

    @abstractmethod
    def on_consensus_reached(self, content: str) -> None:
        """Called when consensus is achieved.

        Args:
            content: The agreed-upon content (after consensus keyword)
        """

    @abstractmethod
    def on_consensus_too_early(self, current_round: int, min_rounds: int) -> None:
        """Called when consensus keyword detected but min_rounds not yet reached.

        Args:
            current_round: The current round number
            min_rounds: Minimum rounds required before consensus
        """

    @abstractmethod
    async def prompt_user_approval(self, content: str) -> bool:
        """Prompt user to approve or reject the consensus.

        Args:
            content: The consensus content to display

        Returns:
            True if user approved, False otherwise
        """

    @abstractmethod
    async def prompt_topic(self, workflow_name: str, agents: list[str], agent_types: dict[str, str] | None = None) -> str | None:
        """Prompt user for the discussion topic.

        Args:
            workflow_name: Name of the workflow
            agents: List of agent IDs (role IDs like 'architect', 'reviewer')
            agent_types: Optional dict mapping agent type to role ID
                        (e.g., {'claude': 'reviewer', 'copilot': 'architect'})

        Returns:
            The topic string, or None if user wants to quit
        """

    @abstractmethod
    async def prompt_user_input(self) -> str | None:
        """Prompt user for input between rounds.

        Returns:
            User input string, or None to continue, or special values
            like "__quit__" to end the conversation
        """

    @abstractmethod
    async def prompt_user_question(
        self,
        question: str,
        options: list[dict[str, str]] | None = None,
    ) -> str:
        """Prompt user a question during agent execution.

        Used by the ask_user tool to get clarification from the user
        mid-task. The agent provides a question and optional choices.

        Args:
            question: The question to ask the user
            options: Optional list of choices, each with 'label' and 'description'

        Returns:
            The user's response (either an option label or free-form text)
        """

    @abstractmethod
    def on_command_result(self, command: str, result: str) -> None:
        """Called when a slash command is executed.

        Args:
            command: The command that was executed
            result: Result message to display
        """

    @abstractmethod
    def on_conversation_complete(
        self,
        status: str,
        rounds: int,
        consensus_status: str | None = None,
    ) -> None:
        """Called when conversation completes (successfully or otherwise).

        Args:
            status: Final execution status
            rounds: Number of rounds completed
            consensus_status: Final consensus status (if any)
        """

    @abstractmethod
    def on_max_rounds_reached(self, max_rounds: int) -> None:
        """Called when conversation reaches max rounds without consensus.

        Args:
            max_rounds: The maximum number of rounds configured
        """


class NullEventHandler(ConversationEventHandler):
    """No-op event handler for testing or non-interactive mode.

    Useful for testing where you don't want any side effects,
    or for automated batch processing.
    """

    def on_conversation_start(
        self,
        workflow_name: str,
        topic: str,
        agents: list[str],
        collaboration: str,
        lead: str | None = None,
    ) -> None:
        pass

    def on_round_start(self, round_num: int) -> None:
        pass

    def on_agent_turn_start(self, agent_id: str) -> None:
        pass

    def on_agent_turn_complete(
        self,
        agent_id: str,
        duration: float,
        output: str,
    ) -> None:
        pass

    def on_agent_turn_error(
        self,
        agent_id: str,
        error: str,
    ) -> None:
        pass

    def on_consensus_reached(self, content: str) -> None:
        pass

    def on_consensus_too_early(self, current_round: int, min_rounds: int) -> None:
        pass

    async def prompt_user_approval(self, content: str) -> bool:
        # In non-interactive mode, auto-approve
        return True

    async def prompt_topic(self, workflow_name: str, agents: list[str], agent_types: dict[str, str] | None = None) -> str | None:
        return None

    async def prompt_user_input(self) -> str | None:
        return None

    async def prompt_user_question(
        self,
        question: str,
        options: list[dict[str, str]] | None = None,
    ) -> str:
        # In non-interactive mode, return first option or empty string
        if options:
            return options[0].get("label", "")
        return ""

    def on_command_result(self, command: str, result: str) -> None:
        pass

    def on_conversation_complete(
        self,
        status: str,
        rounds: int,
        consensus_status: str | None = None,
    ) -> None:
        pass

    def on_max_rounds_reached(self, max_rounds: int) -> None:
        pass
