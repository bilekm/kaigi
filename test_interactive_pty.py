#!/usr/bin/env python3
"""Test if claude interactive mode works with PTY."""

import os
import pty
import select
import time
import sys

# Fork
pid = os.fork()

if pid == 0:
    # Child - run claude (interactive mode, no flags)
    os.setsid()
    env = os.environ.copy()
    env["TERM"] = "dumb"
    os.execvpe("claude", ["claude"], env)
else:
    # Parent - interact
    master_fd, slave_fd = pty.openpty()
    os.close(slave_fd)

    # Set non-blocking after a delay
    import fcntl

    print("Waiting for claude to start...")
    time.sleep(3)

    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    # Read initial output
    print("\n=== Initial output ===")
    output = ""
    for _ in range(40):
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if ready:
            try:
                data = os.read(master_fd, 8192)
                if data:
                    chunk = data.decode("utf-8", errors="replace")
                    output += chunk
            except OSError:
                break
        time.sleep(0.05)

    print(f"Total: {len(output)} chars")
    print(repr(output[:800]))

    # Send prompt with carriage return
    print("\n=== Sending prompt ===")
    prompt = "Say hello"
    # Try with \r\n
    os.write(master_fd, (prompt + "\r\n").encode("utf-8"))
    print(f"Sent: {repr(prompt)}")

    # Read response
    print("\n=== Reading response (15 seconds) ===")
    response = ""
    last_activity = time.time()

    for i in range(300):  # 15 seconds
        ready, _, _ = select.select([master_fd], [], [], 0.05)
        if ready:
            try:
                data = os.read(master_fd, 8192)
                if data:
                    chunk = data.decode("utf-8", errors="replace")
                    response += chunk
                    last_activity = time.time()
                    print(f"Got {len(chunk)} chars, total: {len(response)}")
            except OSError:
                break

        # Check if idle for 2 seconds
        if time.time() - last_activity > 2 and response:
            print("Idle for 2 seconds, assuming done")
            break

        time.sleep(0.05)

    print(f"\n=== Final response ({len(response)} chars) ===")
    print(response[:2000])

    # Clean up
    print("\n=== Cleaning up ===")
    os.kill(pid, 9)
    try:
        os.waitpid(pid, 0)
    except:
        pass
    os.close(master_fd)
