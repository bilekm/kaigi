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

# ANSI escape sequence pattern
ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]|\x1b\[[\?]?[0-9;]*[hlm]')


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    # Remove ANSI escape sequences
    text = ANSI_ESCAPE.sub('', text)
    # Remove other control characters except newline/tab
    text = ''.join(c for c in text if c == '\n' or c == '\t' or (ord(c) >= 32 and ord(c) < 127) or ord(c) > 127)
    return text


# Pattern for Claude's terminal UI elements
CLAUDE_UI_PATTERNS = [
    re.compile(r'^[─│┌┐└┘├┤┬┴┼╭╮╰╯]+\s*$', re.MULTILINE),  # Box drawing lines
    re.compile(r'^\s*>\s*\[Pasted text.*?\].*$', re.MULTILINE),  # Pasted text indicators
    re.compile(r'^\s*>\s*Try ".*"$', re.MULTILINE),  # Try suggestions
    re.compile(r'^\s*\?\s*for shortcuts\s*$', re.MULTILINE),  # Shortcut hints
    re.compile(r'^\s*>\s*$', re.MULTILINE),  # Empty prompts
]


def clean_agent_response(text: str) -> str:
    """Clean agent response by removing terminal UI elements."""
    # First strip ANSI
    text = strip_ansi(text)

    # Remove Claude UI patterns
    for pattern in CLAUDE_UI_PATTERNS:
        text = pattern.sub('', text)

    # Remove lines that are just box drawing characters
    lines = []
    for line in text.split('\n'):
        # Skip lines that are only whitespace and box drawing
        stripped = line.strip()
        if stripped and not all(c in '─│┌┐└┘├┤┬┴┼╭╮╰╯ ' for c in stripped):
            lines.append(line)

    # Clean up multiple blank lines
    text = '\n'.join(lines)
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')

    return text.strip()


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
        spawn_mode: bool = False,
    ):
        self.agent_id = agent_id
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.ready_pattern = re.compile(ready_pattern) if ready_pattern else None
        self.spawn_mode = spawn_mode  # Use spawn-per-prompt mode instead of PTY

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

        # Track if we've had at least one conversation (for spawn mode)
        self._has_conversation = False

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
            # Use a proper terminal type - dumb causes issues with some CLIs
            env.setdefault("TERM", "xterm-256color")

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
            self._log(f"Command: {self.command} {' '.join(self.args)}")

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
        """Write input to the agent.

        Uses \r (carriage return) for PTY input submission.
        """
        try:
            # PTY typically uses \r to submit input (Enter key)
            os.write(self.master_fd, (text + "\r").encode("utf-8"))
        except Exception as e:
            self._log(f"Write error: {e}")
            raise

    async def _wait_for_ready(self, timeout: float = 10) -> str:
        """Wait for agent to be ready, return accumulated output.

        Some agents don't produce any initial output (e.g., claude --output-format text).
        In such cases, we wait for a brief idle period and assume readiness.
        """
        output = ""
        start_time = asyncio.get_event_loop().time()
        last_output_time = start_time
        idle_threshold = 1.0  # 1 second of no output = ready
        no_output_threshold = 2.0  # 2 seconds with no output at all = assume ready

        while True:
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                self._log(f"Timeout ({timeout}s) waiting for ready prompt. Assuming ready.")
                break

            # Check if agent has been idle (no output for idle_threshold seconds)
            idle_time = asyncio.get_event_loop().time() - last_output_time
            if idle_time > idle_threshold and output:
                # Agent produced output then went quiet - probably ready
                self._log(f"Agent ready after {elapsed:.1f}s ({len(output)} chars output, {idle_time:.1f}s idle)")
                break

            # Special case: no output at all for no_output_threshold seconds
            if not output and idle_time > no_output_threshold:
                self._log(f"Agent ready after {elapsed:.1f}s (no initial output, assuming ready)")
                break

            # Read available output
            chunk = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._read_output(0.1)
            )

            if chunk:
                output += chunk
                last_output_time = asyncio.get_event_loop().time()
                # Check if ready (has a prompt pattern)
                if self._is_ready(output):
                    self._log(f"Agent ready after {elapsed:.1f}s (prompt detected)")
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

        # Use spawn_mode from config instead of hardcoded command list
        if self.spawn_mode:
            # Spawn mode: run agent with -p for each request
            # Maintains conversation memory through -c/--continue flag
            await self._handle_prompt_spawn(prompt, timeout, writer)
        else:
            # PTY mode: write to persistent PTY connection
            await self._handle_prompt_pty(prompt, timeout, writer)

    async def _handle_prompt_spawn(
        self,
        prompt: str,
        timeout: int,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle prompt using spawn-per-prompt mode (for claude/copilot)."""
        import subprocess

        self._log(f"Using spawn mode for prompt")

        # Build command based on agent type
        # claude: first prompt uses -p, subsequent use -c -p
        # copilot: first prompt uses -p, subsequent use --continue -p
        is_claude = self.command == "claude"
        is_copilot = self.command == "copilot"

        if self._has_conversation:
            if is_claude:
                cmd = [self.command, "-c", "-p", prompt]
                self._log(f"Running with -c (continue)")
            elif is_copilot:
                cmd = [self.command, "--continue", "-p", prompt]
                self._log(f"Running with --continue")
            else:
                # Generic agent - just use -p
                cmd = [self.command, "-p", prompt]
                self._log(f"Running with -p (generic)")
        else:
            cmd = [self.command, "-p", prompt]
            self._log(f"Running without continue (first prompt)")

        # Add any additional args (filtered)
        skip_next = False
        for arg in self.args:
            if skip_next:
                skip_next = False
                continue
            if arg in ("-p", "--print", "--prompt", "--output-format"):
                skip_next = True
                continue
            if "{{prompt}}" in arg:
                continue
            cmd.append(arg)

        self._log(f"Running: {' '.join(cmd[:4])}...")

        try:
            # Run the command
            # Note: copilot -p requires stdin to be provided, even if empty
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env={**os.environ, **self.env},
                    input="",  # Provide empty stdin to prevent blocking
                )
            )

            response = result.stdout
            self._log(f"Spawn complete, output: {len(response)} chars")

            # Mark that we now have a conversation
            self._has_conversation = True

            # Send response
            clean_response = clean_agent_response(response)
            done_msg = {"type": "done", "content": clean_response}
            writer.write((json.dumps(done_msg) + "\n").encode())
            await writer.drain()

        except subprocess.TimeoutExpired:
            error_msg = {"type": "error", "message": f"Timeout after {timeout}s"}
            writer.write((json.dumps(error_msg) + "\n").encode())
            await writer.drain()
        except Exception as e:
            error_msg = {"type": "error", "message": str(e)}
            writer.write((json.dumps(error_msg) + "\n").encode())
            await writer.drain()

    async def _handle_prompt_pty(
        self,
        prompt: str,
        timeout: int,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle prompt using persistent PTY connection."""
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

                # Stream chunk to client (strip ANSI)
                clean_chunk = strip_ansi(chunk)
                if clean_chunk:  # Only send non-empty chunks
                    chunk_msg = {"type": "chunk", "content": clean_chunk}
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

        # Log raw response for debugging
        self._log(f"Raw response: {repr(response[:500])}")

        # Clean response (strip ANSI and terminal UI elements)
        clean_response = clean_agent_response(response)
        done_msg = {"type": "done", "content": clean_response}
        writer.write((json.dumps(done_msg) + "\n").encode())
        await writer.drain()

        self._log(f"Response complete ({len(response)} raw -> {len(clean_response)} clean chars)")

    async def start(self) -> None:
        """Start the agent server."""
        # Clean up any existing socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        # Spawn the agent first
        self._spawn_agent()

        # Wait for agent to be ready
        self._log("Waiting for agent to be ready...")
        startup_output = await self._wait_for_ready(timeout=30)
        self._log(f"Agent ready. Startup output: {len(startup_output)} chars")

        # Now start socket server - only after agent is ready
        self.server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        self._log(f"Server listening on {self.socket_path}")

        self.running = True

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
    spawn_mode: bool = False,
) -> None:
    """Run an agent server (blocking)."""
    server = AgentServer(
        agent_id=agent_id,
        command=command,
        args=args,
        env=env,
        ready_pattern=ready_pattern,
        spawn_mode=spawn_mode,
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
