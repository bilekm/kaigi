#!/usr/bin/env python3
"""Test PTY interaction with claude CLI."""

import os
import pty
import select
import time
import sys

# Fork
pid = os.fork()

if pid == 0:
    # Child process - run claude
    os.setsid()
    env = os.environ.copy()
    env["TERM"] = "dumb"

    # Run claude --output-format text
    cmd = ["claude", "--output-format", "text"]
    os.execvpe("claude", cmd, env)
else:
    # Parent - read output
    master_fd, slave_fd = pty.openpty()
    os.close(slave_fd)

    # Set non-blocking
    import fcntl
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    print("Reading initial output...")
    output = ""

    start_time = time.time()
    while time.time() - start_time < 3:
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if ready:
            try:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                chunk = data.decode("utf-8", errors="replace")
                output += chunk
                print(f"Got {len(chunk)} chars: {repr(chunk[:100])}")
            except OSError:
                break
        time.sleep(0.05)

    print(f"\nTotal output: {len(output)} chars")
    print("=== Raw output ===")
    print(repr(output))
    print("\n=== Cleaned output ===")
    print(output)

    # Clean up
    os.kill(pid, 9)  # SIGKILL
    os.waitpid(pid, 0)
    os.close(master_fd)
