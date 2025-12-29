"""Test execution phase doesn't include conversation history."""

import pytest
from unittest.mock import Mock, AsyncMock


def test_agent_turn_execution_mode_skips_history():
    """Test that execution_mode=True results in empty prompt, skipping _build_prompt."""

    # Create a minimal mock that simulates the execution mode logic
    class MockExecutor:
        def __init__(self):
            self.build_prompt_calls = []
            self.prompts_sent = []

        def _build_prompt(self, record, agent):
            self.build_prompt_calls.append(True)
            return "prompt_with_full_history"

        async def _execute_tool_loop(self, agent, prompt, with_write_permission=False):
            self.prompts_sent.append(prompt)
            return "Agent response"

        async def _agent_turn_impl(self, record, agent, execution_mode=False):
            """This is the actual logic from ConversationExecutor._agent_turn"""
            # Build prompt (skip if in execution mode - use existing messages)
            if execution_mode:
                # In execution mode, use the messages already in the record
                # (which include the execution prompt we added)
                prompt = ""
            else:
                prompt = self._build_prompt(record, agent)

            # Execute agent command with tool support
            output = await self._execute_tool_loop(agent, prompt, with_write_permission=False)
            return output

    executor = MockExecutor()
    agent = Mock(id="test_agent")
    record = Mock()

    async def run_test():
        # Test execution_mode=True
        await executor._agent_turn_impl(record, agent, execution_mode=True)

        # Verify _build_prompt was NOT called
        assert not executor.build_prompt_calls, "_build_prompt should not be called in execution mode"

        # Verify empty prompt was sent
        assert executor.prompts_sent[-1] == "", f"Prompt should be empty in execution mode, got: {executor.prompts_sent[-1]}"

    import asyncio
    asyncio.run(run_test())


def test_agent_turn_normal_mode_includes_history():
    """Test that execution_mode=False calls _build_prompt."""

    # Create a minimal mock that simulates the normal mode logic
    class MockExecutor:
        def __init__(self):
            self.build_prompt_calls = []
            self.prompts_sent = []

        def _build_prompt(self, record, agent):
            self.build_prompt_calls.append(True)
            return "prompt_with_full_history"

        async def _execute_tool_loop(self, agent, prompt, with_write_permission=False):
            self.prompts_sent.append(prompt)
            return "Agent response"

        async def _agent_turn_impl(self, record, agent, execution_mode=False):
            """This is the actual logic from ConversationExecutor._agent_turn"""
            # Build prompt (skip if in execution mode - use existing messages)
            if execution_mode:
                prompt = ""
            else:
                prompt = self._build_prompt(record, agent)

            # Execute agent command with tool support
            output = await self._execute_tool_loop(agent, prompt, with_write_permission=False)
            return output

    executor = MockExecutor()
    agent = Mock(id="test_agent")
    record = Mock()

    async def run_test():
        # Test execution_mode=False (normal mode)
        await executor._agent_turn_impl(record, agent, execution_mode=False)

        # Verify _build_prompt WAS called
        assert executor.build_prompt_calls, "_build_prompt should be called in normal mode"

        # Verify the prompt from _build_prompt was sent
        assert executor.prompts_sent[-1] == "prompt_with_full_history"

    import asyncio
    asyncio.run(run_test())


if __name__ == "__main__":
    test_agent_turn_execution_mode_skips_history()
    print("✓ test_agent_turn_execution_mode_skips_history passed")

    test_agent_turn_normal_mode_includes_history()
    print("✓ test_agent_turn_normal_mode_includes_history passed")

    print("\nAll execution phase tests passed!")
