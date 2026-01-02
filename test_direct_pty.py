#!/usr/bin/env python3
"""Test direct PTY communication with claude CLI."""

import os
import pty
import select
import time

# Fork
pid = os.fork()

if pid == 0:
    # Child - run claude
    os.setsid()
    env = os.environ.copy()
    env["TERM"] = "dumb"
    os.execvpe("claude", ["claude", "--output-format", "text"], env)
else:
    # Parent - interact
    master_fd, slave_fd = pty.openpty()
    os.close(slave_fd)

    # Set non-blocking
    import fcntl
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    # Wait for startup
    time.sleep(2)
    print("=== Initial output ===")
    output = ""
    for _ in range(20):
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if ready:
            try:
                data = os.read(master_fd, 4096)
                if data:
                    chunk = data.decode("utf-8", errors="replace")
                    output += chunk
            except OSError:
                break
        time.sleep(0.05)

    print(repr(output[:500]))

    # Send prompt
    print("\n=== Sending prompt ===")
    prompt = "Say hello"
    os.write(master_fd, (prompt + "\n").encode("utf-8"))
    print(f"Sent: {repr(prompt)}")

    # Read response
    print("\n=== Reading response ===")
    response = ""
    for i in range(100):  # 5 seconds
        ready, _, _ = select.select([master_fd], [], [], 0.05)
        if ready:
            try:
                data = os.read(master_fd, 4096)
                if data:
                    chunk = data.decode("utf-8", errors="replace")
                    response += chunk
                    print(f"Got {len(chunk)} chars")
            except OSError:
                break
        time.sleep(0.05)

    print(f"\n=== Final response ({len(response)} chars) ===")
    print(response[:1000])

    # Clean up
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    os.close(master_fd)
