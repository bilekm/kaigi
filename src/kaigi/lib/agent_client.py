"""Agent client - connects to persistent agent servers via Unix socket."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator, Callable

from kaigi.lib.agent_server import SOCKET_DIR, get_pid_path, get_socket_path


class AgentNotRunning(Exception):
    """Agent server is not running."""

    pass


class AgentClient:
    """Client for communicating with a persistent agent server."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.socket_path = get_socket_path(agent_id)
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        """Connect to the agent server."""
        if not self.socket_path.exists():
            raise AgentNotRunning(f"Agent '{self.agent_id}' is not running (no socket)")

        try:
            self.reader, self.writer = await asyncio.open_unix_connection(
                path=str(self.socket_path)
            )
        except (ConnectionRefusedError, FileNotFoundError) as e:
            raise AgentNotRunning(f"Agent '{self.agent_id}' is not running: {e}")

    async def close(self) -> None:
        """Close the connection."""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None

    async def ping(self) -> bool:
        """Check if the agent is responsive."""
        if not self.writer or not self.reader:
            return False

        try:
            request = {"type": "ping"}
            self.writer.write((json.dumps(request) + "\n").encode())
            await self.writer.drain()

            line = await asyncio.wait_for(self.reader.readline(), timeout=5)
            response = json.loads(line.decode())
            return response.get("type") == "pong"
        except Exception:
            return False

    async def clear(self) -> bool:
        """Clear agent's conversation memory.

        After clearing, the next prompt will start a fresh conversation
        (without -c/--continue flag).

        Returns:
            True if memory was cleared, False on error.
        """
        if not self.writer or not self.reader:
            return False

        try:
            request = {"type": "clear"}
            self.writer.write((json.dumps(request) + "\n").encode())
            await self.writer.drain()

            line = await asyncio.wait_for(self.reader.readline(), timeout=5)
            response = json.loads(line.decode())
            return response.get("type") == "cleared"
        except Exception:
            return False

    async def prompt(
        self,
        content: str,
        timeout: int = 120,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Send a prompt and get the response.

        Args:
            content: The prompt text
            timeout: Maximum time to wait for response
            on_chunk: Optional callback for streaming chunks

        Returns:
            The complete response text
        """
        if not self.writer or not self.reader:
            raise AgentNotRunning(f"Not connected to agent '{self.agent_id}'")

        # Send prompt
        request = {"type": "prompt", "content": content, "timeout": timeout}
        self.writer.write((json.dumps(request) + "\n").encode())
        await self.writer.drain()

        # Collect response
        full_response = ""

        try:
            while True:
                line = await asyncio.wait_for(
                    self.reader.readline(),
                    timeout=timeout + 5,  # Extra buffer for server timeout
                )

                if not line:
                    break

                response = json.loads(line.decode())
                msg_type = response.get("type")

                if msg_type == "chunk":
                    chunk = response.get("content", "")
                    full_response += chunk
                    if on_chunk:
                        on_chunk(chunk)

                elif msg_type == "done":
                    # Use the final content from done message
                    full_response = response.get("content", full_response)
                    break

                elif msg_type == "error":
                    raise Exception(response.get("message", "Unknown error"))

        except asyncio.TimeoutError:
            raise TimeoutError(f"Agent '{self.agent_id}' response timeout")

        return full_response

    async def prompt_stream(
        self,
        content: str,
        timeout: int = 120,
    ) -> AsyncIterator[str]:
        """Send a prompt and stream the response.

        Yields chunks as they arrive.
        """
        if not self.writer or not self.reader:
            raise AgentNotRunning(f"Not connected to agent '{self.agent_id}'")

        # Send prompt
        request = {"type": "prompt", "content": content, "timeout": timeout}
        self.writer.write((json.dumps(request) + "\n").encode())
        await self.writer.drain()

        try:
            while True:
                line = await asyncio.wait_for(
                    self.reader.readline(),
                    timeout=timeout + 5,
                )

                if not line:
                    break

                response = json.loads(line.decode())
                msg_type = response.get("type")

                if msg_type == "chunk":
                    yield response.get("content", "")

                elif msg_type == "done":
                    break

                elif msg_type == "error":
                    raise Exception(response.get("message", "Unknown error"))

        except asyncio.TimeoutError:
            raise TimeoutError(f"Agent '{self.agent_id}' response timeout")


async def send_prompt(
    agent_id: str,
    content: str,
    timeout: int = 120,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """Send a prompt to a running agent and get the response.

    Convenience function that handles connection management.
    """
    client = AgentClient(agent_id)
    try:
        await client.connect()
        return await client.prompt(content, timeout, on_chunk)
    finally:
        await client.close()


async def clear_agent_memory(agent_id: str) -> bool:
    """Clear an agent's conversation memory.

    After clearing, the next prompt starts a fresh conversation.

    Args:
        agent_id: The agent to clear

    Returns:
        True if cleared successfully, False otherwise.
    """
    client = AgentClient(agent_id)
    try:
        await client.connect()
        return await client.clear()
    finally:
        await client.close()


def is_agent_running(agent_id: str) -> bool:
    """Check if an agent server is running."""
    socket_path = get_socket_path(agent_id)
    pid_path = get_pid_path(agent_id)

    if not socket_path.exists():
        return False

    if not pid_path.exists():
        return False

    # Check if process is actually running
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)  # Signal 0 just checks if process exists
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # Clean up stale files
        if socket_path.exists():
            socket_path.unlink()
        if pid_path.exists():
            pid_path.unlink()
        return False


def list_running_agents() -> list[dict]:
    """List all running agent servers."""
    agents = []

    if not SOCKET_DIR.exists():
        return agents

    for socket_file in SOCKET_DIR.glob("*.sock"):
        agent_id = socket_file.stem
        pid_path = get_pid_path(agent_id)

        if is_agent_running(agent_id):
            try:
                pid = int(pid_path.read_text().strip())
            except (ValueError, FileNotFoundError):
                pid = None

            agents.append({
                "id": agent_id,
                "socket": str(socket_file),
                "pid": pid,
            })

    return agents


def stop_agent(agent_id: str) -> bool:
    """Stop a running agent server."""
    pid_path = get_pid_path(agent_id)
    socket_path = get_socket_path(agent_id)

    if not pid_path.exists():
        return False

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 15)  # SIGTERM

        # Wait a bit for graceful shutdown
        import time
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break

        # Force kill if still running
        try:
            os.kill(pid, 9)  # SIGKILL
        except ProcessLookupError:
            pass

    except (ValueError, ProcessLookupError, PermissionError):
        pass

    # Clean up files
    if socket_path.exists():
        socket_path.unlink()
    if pid_path.exists():
        pid_path.unlink()

    return True


def stop_all_agents() -> int:
    """Stop all running agent servers. Returns count of stopped agents."""
    count = 0
    for agent in list_running_agents():
        if stop_agent(agent["id"]):
            count += 1
    return count
