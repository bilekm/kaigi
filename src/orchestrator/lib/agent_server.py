"""Agent server - wraps CLI agents with Unix socket IPC.

Runs an interactive CLI agent (claude, copilot, etc.) in a PTY and
exposes it via a Unix domain socket for fast, persistent communication.
"""

from __future__ import annotations

import asyncio
import json
import os
import pty
import re
import select
import signal
import sys
import termios
import tty
from pathlib import Path
from typing import Callable

# Socket directory
SOCKET_DIR = Path("/tmp/orchestrator-agents")

# Default timeout for agent responses (seconds)
DEFAULT_RESPONSE_TIMEOUT = 120

# Buffer size for reading from PTY
READ_BUFFER_SIZE = 4096

# Patterns that indicate the agent is ready for input
READY_PATTERNS = [
    re.compile(r"^> $", re.MULTILINE),           # Claude prompt
    re.compile(r"^\$ $", re.MULTILINE),          # Generic shell
    re.compile(r"^claude> $", re.MULTILINE),     # Claude named prompt
    re.compile(r"^copilot> $", re.MULTILINE),    # Copilot prompt
    re.compile(r"^\[.*\]> $", re.MULTILINE),     # Bracketed prompt
]


def get_socket_path(agent_id: str) -> Path:
    """Get the socket path for an agent."""
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    return SOCKET_DIR / f"{agent_id}.sock"


def get_pid_path(agent_id: str) -> Path:
    """Get the PID file path for an agent."""
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    return SOCKET_DIR / f"{agent_id}.pid"


class AgentServer:
    """Wraps an interactive CLI agent with Unix socket IPC."""

    def __init__(
        self,
        agent_id: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        ready_pattern: str | None = None,
    ):
        self.agent_id = agent_id
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.ready_pattern = re.compile(ready_pattern) if ready_pattern else None

        self.socket_path = get_socket_path(agent_id)
        self.pid_path = get_pid_path(agent_id)

        self.master_fd: int | None = None
        self.slave_fd: int | None = None
        self.child_pid: int | None = None
        self.server: asyncio.Server | None = None
        self.running = False

        # Buffer for accumulating output
        self.output_buffer = ""

        # Callback for logging
        self.log_callback: Callable[[str], None] | None = None

    def _log(self, message: str) -> None:
        """Log a message."""
        if self.log_callback:
            self.log_callback(message)
        else:
            print(f"[{self.agent_id}] {message}", file=sys.stderr)

    def _is_ready(self, output: str) -> bool:
        """Check if the agent is ready for input."""
        # Check custom pattern first
        if self.ready_pattern and self.ready_pattern.search(output):
            return True

        # Check default patterns
        for pattern in READY_PATTERNS:
            if pattern.search(output):
                return True

        return False

    def _spawn_agent(self) -> None:
        """Spawn the agent CLI in a PTY."""
        # Create PTY
        self.master_fd, self.slave_fd = pty.openpty()

        # Fork
        pid = os.fork()

        if pid == 0:
            # Child process
            os.close(self.master_fd)
            os.setsid()

            # Set up slave as controlling terminal
            os.dup2(self.slave_fd, 0)  # stdin
            os.dup2(self.slave_fd, 1)  # stdout
            os.dup2(self.slave_fd, 2)  # stderr

            if self.slave_fd > 2:
                os.close(self.slave_fd)

            # Set up environment
            env = os.environ.copy()
            env.update(self.env)
            env["TERM"] = "dumb"  # Disable fancy terminal features

            # Execute agent
            cmd = [self.command] + self.args
            os.execvpe(self.command, cmd, env)

        else:
            # Parent process
            os.close(self.slave_fd)
            self.child_pid = pid

            # Set master to non-blocking
            import fcntl
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Write PID file
            self.pid_path.write_text(str(pid))

            self._log(f"Spawned agent process (PID: {pid})")

    def _read_output(self, timeout: float = 0.1) -> str:
        """Read available output from the agent."""
        output = ""

        try:
            while True:
                ready, _, _ = select.select([self.master_fd], [], [], timeout)
                if not ready:
                    break

                try:
                    data = os.read(self.master_fd, READ_BUFFER_SIZE)
                    if not data:
                        break
                    output += data.decode("utf-8", errors="replace")
                except OSError:
                    break

                # Short timeout for subsequent reads
                timeout = 0.01
        except Exception as e:
            self._log(f"Read error: {e}")

        return output

    def _write_input(self, text: str) -> None:
        """Write input to the agent."""
        try:
            os.write(self.master_fd, (text + "\n").encode("utf-8"))
        except Exception as e:
            self._log(f"Write error: {e}")
            raise

    async def _wait_for_ready(self, timeout: float = 30) -> str:
        """Wait for agent to be ready, return accumulated output."""
        output = ""
        start_time = asyncio.get_event_loop().time()

        while True:
            # Check timeout
            if asyncio.get_event_loop().time() - start_time > timeout:
                self._log(f"Timeout waiting for ready prompt. Output so far: {output[-200:]}")
                break

            # Read available output
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._read_output(0.1)
            )

            if chunk:
                output += chunk
                # Check if ready
                if self._is_ready(output):
                    break

            await asyncio.sleep(0.05)

        return output

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a client connection."""
        client_addr = writer.get_extra_info("peername")
        self._log(f"Client connected: {client_addr}")

        try:
            while True:
                # Read request
                line = await reader.readline()
                if not line:
                    break

                try:
                    request = json.loads(line.decode())
                except json.JSONDecodeError:
                    error_response = {"type": "error", "message": "Invalid JSON"}
                    writer.write((json.dumps(error_response) + "\n").encode())
                    await writer.drain()
                    continue

                req_type = request.get("type")

                if req_type == "prompt":
                    await self._handle_prompt(request, writer)
                elif req_type == "ping":
                    response = {"type": "pong", "agent_id": self.agent_id}
                    writer.write((json.dumps(response) + "\n").encode())
                    await writer.drain()
                elif req_type == "quit":
                    break
                else:
                    error_response = {"type": "error", "message": f"Unknown type: {req_type}"}
                    writer.write((json.dumps(error_response) + "\n").encode())
                    await writer.drain()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log(f"Client error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            self._log(f"Client disconnected: {client_addr}")

    async def _handle_prompt(
        self,
        request: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a prompt request - send to agent and stream response."""
        prompt = request.get("content", "")
        timeout = request.get("timeout", DEFAULT_RESPONSE_TIMEOUT)

        self._log(f"Received prompt ({len(prompt)} chars)")

        # Clear any pending output
        self._read_output(0.1)

        # Send prompt to agent
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._write_input(prompt)
        )

        # Collect response until ready prompt appears
        response = ""
        start_time = asyncio.get_event_loop().time()
        last_output_time = start_time

        while True:
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                self._log(f"Response timeout after {elapsed:.1f}s")
                break

            # Read output
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._read_output(0.2)
            )

            if chunk:
                last_output_time = asyncio.get_event_loop().time()
                response += chunk

                # Stream chunk to client
                chunk_msg = {"type": "chunk", "content": chunk}
                writer.write((json.dumps(chunk_msg) + "\n").encode())
                await writer.drain()

                # Check if agent is ready for next input
                if self._is_ready(response):
                    # Remove the prompt from the response
                    for pattern in READY_PATTERNS:
                        response = pattern.sub("", response)
                    if self.ready_pattern:
                        response = self.ready_pattern.sub("", response)
                    break

            else:
                # No output - check if we've been idle too long
                idle_time = asyncio.get_event_loop().time() - last_output_time
                if idle_time > 5 and response:
                    # Agent has been quiet for 5 seconds after producing output
                    # Assume it's done
                    self._log("Assuming response complete (5s idle)")
                    break

            await asyncio.sleep(0.05)

        # Remove echo of our input from the beginning
        lines = response.split("\n")
        if lines and prompt.strip().startswith(lines[0].strip()):
            response = "\n".join(lines[1:])

        # Send done message
        done_msg = {"type": "done", "content": response.strip()}
        writer.write((json.dumps(done_msg) + "\n").encode())
        await writer.drain()

        self._log(f"Response complete ({len(response)} chars)")

    async def start(self) -> None:
        """Start the agent server."""
        # Clean up any existing socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Spawn the agent
        self._spawn_agent()

        # Wait for agent to be ready
        self._log("Waiting for agent to be ready...")
        startup_output = await self._wait_for_ready(timeout=30)
        self._log(f"Agent ready. Startup output: {len(startup_output)} chars")

        # Start socket server
        self.server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )

        self.running = True
        self._log(f"Server listening on {self.socket_path}")

        # Serve forever
        async with self.server:
            await self.server.serve_forever()

    def stop(self) -> None:
        """Stop the agent server."""
        self.running = False

        # Stop server
        if self.server:
            self.server.close()

        # Kill child process
        if self.child_pid:
            try:
                os.kill(self.child_pid, signal.SIGTERM)
                os.waitpid(self.child_pid, 0)
            except OSError:
                pass

        # Close PTY
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

        # Clean up files
        if self.socket_path.exists():
            self.socket_path.unlink()
        if self.pid_path.exists():
            self.pid_path.unlink()

        self._log("Server stopped")


def run_agent_server(
    agent_id: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    ready_pattern: str | None = None,
) -> None:
    """Run an agent server (blocking)."""
    server = AgentServer(
        agent_id=agent_id,
        command=command,
        args=args,
        env=env,
        ready_pattern=ready_pattern,
    )

    def handle_signal(signum, frame):
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    # Test: run claude as a server
    import sys

    if len(sys.argv) < 3:
        print("Usage: python agent_server.py <agent-id> <command> [args...]")
        sys.exit(1)

    agent_id = sys.argv[1]
    command = sys.argv[2]
    args = sys.argv[3:] if len(sys.argv) > 3 else []

    run_agent_server(agent_id, command, args)
