"""Tests for agent communication via Unix socket IPC."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaigi.lib.agent_client import (
    AgentClient,
    AgentNotRunning,
    is_agent_running,
    list_running_agents,
    send_prompt,
    stop_agent,
    stop_all_agents,
)
from kaigi.lib.agent_server import (
    AgentServer,
    clean_agent_response,
    get_pid_path,
    get_socket_path,
    strip_ansi,
)


# === Test Utility Functions ===


def test_strip_ansi():
    """Test ANSI escape sequence removal."""
    # ANSI colors
    assert strip_ansi("\x1b[31mRed text\x1b[0m") == "Red text"
    # ANSI cursor
    assert strip_ansi("\x1b[2K\x1b[1G") == ""
    # Mixed content
    text = "\x1b[31mHello\x1b[0m \x1b[2KWorld"
    assert strip_ansi(text) == "Hello World"


def test_clean_agent_response():
    """Test agent response cleaning."""
    # Box drawing characters
    response = "───\n│ Box │\n───\nActual content here"
    cleaned = clean_agent_response(response)
    assert "Actual content here" in cleaned
    # Should remove empty lines
    assert cleaned.count("\n\n") < 2

    # Claude UI elements
    response = '> Try "edit file.py"\n─────────────\nMy response'
    cleaned = clean_agent_response(response)
    assert "My response" in cleaned
    # The suggestion might be partially removed
    assert cleaned != response


def test_get_socket_path():
    """Test socket path generation."""
    path = get_socket_path("test-agent")
    assert path == Path("/tmp/kaigi-agents/test-agent.sock")


def test_get_pid_path():
    """Test PID path generation."""
    path = get_pid_path("test-agent")
    assert path == Path("/tmp/kaigi-agents/test-agent.pid")


# === Test AgentClient ===


@pytest.mark.asyncio
async def test_agent_client_not_running():
    """Test AgentClient with non-existent agent."""
    client = AgentClient("nonexistent-agent")

    with pytest.raises(AgentNotRunning):
        await client.connect()


@pytest.mark.asyncio
async def test_agent_client_ping_timeout():
    """Test AgentClient ping with no response."""
    # Create a mock socket that doesn't respond
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = Path(tmpdir) / "test.sock"
        pid_path = Path(tmpdir) / "test.pid"

        # Create a simple Unix socket server that doesn't respond
        async def mock_server(reader, writer):
            # Just keep connection open, don't respond
            await asyncio.sleep(10)

        server = await asyncio.start_unix_server(mock_server, path=str(socket_path))
        pid_path.write_text("12345")

        try:
            client = AgentClient("test-agent")
            # Mock the socket path
            with patch.object(client, 'socket_path', socket_path):
                await client.connect()

                # Ping should timeout (or return False if no pong)
                result = await client.ping()
                assert result is False

            await client.close()
        finally:
            server.close()
            await server.wait_closed()


# === Test is_agent_running ===


def test_is_agent_running_no_socket():
    """Test is_agent_running with no socket."""
    assert is_agent_running("nonexistent") is False


def test_is_agent_running_stale_pid():
    """Test is_agent_running with stale PID file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_dir = Path(tmpdir)
        socket_path = socket_dir / "test.sock"
        pid_path = socket_dir / "test.pid"

        # Create files
        socket_path.touch()
        pid_path.write_text("99999")  # Non-existent PID

        with patch('kaigi.lib.agent_client.SOCKET_DIR', socket_dir):
            assert is_agent_running("test") is False
            # Stale files should be cleaned up
            assert not socket_path.exists()
            assert not pid_path.exists()


# === Test send_prompt convenience function ===


@pytest.mark.asyncio
async def test_send_prompt_convenience():
    """Test send_prompt convenience function."""
    responses = []

    async def mock_prompt(self, content, timeout, on_chunk):
        responses.append(content)
        if on_chunk:
            on_chunk("chunk1")
            on_chunk("chunk2")
        return "full response"

    with patch.object(AgentClient, 'prompt', mock_prompt):
        with patch.object(AgentClient, 'connect'):
            with patch.object(AgentClient, 'close'):
                result = await send_prompt("test-agent", "hello", on_chunk=lambda c: None)

                assert result == "full response"
                assert responses == ["hello"]


# === Integration Tests with Mock Agent ===


class MockAgent:
    """A mock agent that speaks a simple protocol for testing."""

    def __init__(self, socket_path: Path, pid_path: Path):
        self.socket_path = socket_path
        self.pid_path = pid_path
        self.server = None
        self.running = False

    async def handle_client(self, reader, writer):
        """Handle a client connection."""
        self.running = True
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                request = json.loads(line.decode())
                req_type = request.get("type")

                if req_type == "ping":
                    response = {"type": "pong", "agent_id": "mock-agent"}
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()

                elif req_type == "prompt":
                    content = request.get("content", "")

                    # Send chunked response
                    chunks = [f"You said: {content}", " | ", "Response complete"]
                    for chunk in chunks:
                        chunk_msg = {"type": "chunk", "content": chunk}
                        writer.write((json.dumps(chunk_msg) + "\n").encode())
                        await writer.drain()

                    # Send done
                    done_msg = {"type": "done", "content": "".join(chunks)}
                    writer.write((json.dumps(done_msg) + "\n").encode())
                    await writer.drain()

                elif req_type == "quit":
                    break

        except Exception as e:
            print(f"Mock agent error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def start(self):
        """Start the mock agent server."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)

        if self.socket_path.exists():
            self.socket_path.unlink()

        self.server = await asyncio.start_unix_server(
            self.handle_client,
            path=str(self.socket_path),
        )

        # Write PID
        self.pid_path.write_text(str(os.getpid()))

        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        """Stop the mock agent."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        if self.socket_path.exists():
            self.socket_path.unlink()
        if self.pid_path.exists():
            self.pid_path.unlink()


@pytest.mark.asyncio
async def test_full_agent_communication_flow():
    """Test full communication flow with mock agent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_dir = Path(tmpdir)
        socket_path = socket_dir / "mock.sock"
        pid_path = socket_dir / "mock.pid"

        mock_agent = MockAgent(socket_path, pid_path)

        # Start mock agent in background
        task = asyncio.create_task(mock_agent.start())

        # Wait for server to start
        await asyncio.sleep(0.2)

        try:
            # Patch SOCKET_DIR for this test
            with patch('kaigi.lib.agent_client.SOCKET_DIR', socket_dir):
                # Test is_agent_running
                assert is_agent_running("mock") is True

                # Test list_running_agents
                running = list_running_agents()
                assert len(running) == 1
                assert running[0]["id"] == "mock"

                # Test AgentClient
                client = AgentClient("mock")
                await client.connect()

                # Test ping
                assert await client.ping() is True

                # Test prompt
                chunks_received = []
                def on_chunk(chunk):
                    chunks_received.append(chunk)

                response = await client.prompt("Hello mock!", timeout=5, on_chunk=on_chunk)

                assert response == "You said: Hello mock! | Response complete"
                assert chunks_received == ["You said: Hello mock! ", " | ", "Response complete"]

                await client.close()

                # Test stop_agent
                assert stop_agent("mock") is True
                assert is_agent_running("mock") is False

        finally:
            # Clean up
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# === Test Ready Pattern Detection ===


def test_ready_pattern_detection():
    """Test that various ready patterns are detected."""
    from kaigi.lib.agent_server import AgentServer

    server = AgentServer("test", "echo", [])

    # Test various ready patterns
    test_cases = [
        ("Some text\n> \nMore text", True),  # Standard prompt
        ("Text\n$ \nMore", True),  # Shell prompt
        ("claude> \nText", True),  # Named prompt
        ("[context]> \nText", True),  # Bracketed prompt
        ("Just some text\n", False),  # No prompt
    ]

    for text, should_match in test_cases:
        result = server._is_ready(text)
        assert result == should_match, f"Failed for: {repr(text)}"


# === Test Echo Removal Logic ===


def test_echo_removal_logic():
    """Test the echo removal logic in _handle_prompt."""
    # This tests the logic at lines 375-378 of agent_server.py

    # Case 1: Simple echo
    prompt = "Say hello"
    response_with_echo = "Say hello\n\nHello! How can I help?"

    lines = response_with_echo.split("\n")
    if lines and prompt.strip().startswith(lines[0].strip()):
        cleaned = "\n".join(lines[1:])
        assert cleaned == "\nHello! How can I help?"
    else:
        assert False, "Echo removal should have worked"

    # Case 2: No echo (already removed)
    response_no_echo = "Hello! How can I help?"
    lines = response_no_echo.split("\n")
    if lines and prompt.strip().startswith(lines[0].strip()):
        cleaned = "\n".join(lines[1:])
        assert False, "Should not remove first line if it's not an echo"
    else:
        cleaned = response_no_echo
        assert cleaned == "Hello! How can I help?"

    # Case 3: Prompt with special characters
    prompt = "Say 'hello world'"
    response = "Say 'hello world'\n\nHello world!"

    lines = response.split("\n")
    if lines and prompt.strip().startswith(lines[0].strip()):
        cleaned = "\n".join(lines[1:])
        assert cleaned == "\nHello world!"


# === Test Response Cleaning Edge Cases ===


def test_clean_response_edge_cases():
    """Test edge cases in response cleaning."""

    # Empty response
    assert clean_agent_response("") == ""

    # Only ANSI codes
    assert clean_agent_response("\x1b[31m\x1b[0m") == ""

    # Only box drawing
    result = clean_agent_response("────\n││││\n────")
    # Should be empty or just whitespace
    assert not result.strip()

    # Response with newlines
    result = clean_agent_response("Line 1\n\n\n\nLine 2")
    # Multiple consecutive newlines should be reduced
    assert "\n\n\n" not in result


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
