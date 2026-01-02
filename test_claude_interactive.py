#!/usr/bin/env python3
"""Test direct PTY communication with claude CLI in interactive mode."""

import os
import pty
import select
import time

# Fork
pid = os.fork()

if pid == 0:
    # Child - run claude WITHOUT --output-format
    os.setsid()
    env = os.environ.copy()
    env["TERM"] = "dumb"
    # Run just "claude" in interactive mode
    os.execvpe("claude", ["claude"], env)
else:
    # Parent - interact
    master_fd, slave_fd = pty.openpty()
    os.close(slave_fd)

    # Set non-blocking
    import fcntl
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    # Wait for startup
    time.sleep(3)
    print("=== Initial output ===")
    output = ""
    for _ in range(30):
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
    print(f"\nTotal: {len(output)} chars")

    # Send prompt
    print("\n=== Sending prompt ===")
    prompt = "Say hello in one word"
    os.write(master_fd, (prompt + "\n").encode("utf-8"))
    print(f"Sent: {repr(prompt)}")

    # Read response
    print("\n=== Reading response (10 seconds) ===")
    response = ""
    for i in range(200):  # 10 seconds
        ready, _, _ = select.select([master_fd], [], [], 0.05)
        if ready:
            try:
                data = os.read(master_fd, 4096)
                if data:
                    chunk = data.decode("utf-8", errors="replace")
                    response += chunk
                    print(f"Got {len(chunk)} chars, total: {len(response)}")
            except OSError:
                break
        time.sleep(0.05)

    print(f"\n=== Final response ({len(response)} chars) ===")
    # Clean up ANSI for display
    from orchestrator.lib.agent_server import strip_ansi, clean_agent_response
    clean = clean_agent_response(response)
    print(clean[:500])

    # Clean up
    os.kill(pid, 9)
    try:
        os.waitpid(pid, 0)
    except:
        pass
    os.close(master_fd)
