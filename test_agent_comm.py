#!/usr/bin/env python3
"""Manual test script for agent communication debugging."""

import asyncio
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.lib.agent_client import AgentClient, AgentNotRunning, is_agent_running, list_running_agents
from orchestrator.lib.agent_server import get_socket_path, get_pid_path


async def test_agent_basic(agent_id: str):
    """Test basic agent connectivity."""
    print(f"\n{'='*60}")
    print(f"Testing Agent: {agent_id}")
    print('='*60)

    # Check socket exists
    socket_path = get_socket_path(agent_id)
    pid_path = get_pid_path(agent_id)

    print(f"Socket: {socket_path} (exists: {socket_path.exists()})")
    print(f"PID file: {pid_path} (exists: {pid_path.exists()})")

    if pid_path.exists():
        pid = pid_path.read_text().strip()
        print(f"PID in file: {pid}")

        # Check if process is running
        import os
        try:
            os.kill(int(pid), 0)
            print(f"Process {pid} is RUNNING")
        except (ValueError, ProcessLookupError):
            print(f"Process {pid} is NOT running (stale PID file)")

    # Test connection
    client = AgentClient(agent_id)
    try:
        await client.connect()
        print("✓ Socket connected")

        # Test ping
        ping_result = await client.ping()
        print(f"✓ Ping: {ping_result}")

        # Test simple prompt
        print("\n--- Sending test prompt ---")
        response = await client.prompt("Say 'HELLO WORLD' and nothing else", timeout=30)

        print(f"\n--- Response ({len(response)} chars) ---")
        print(response)
        print(f"--- End response ---\n")

        await client.close()
        return True

    except AgentNotRunning as e:
        print(f"✗ Agent not running: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_raw_socket(agent_id: str):
    """Test raw socket communication (debugging)."""
    print(f"\n{'='*60}")
    print(f"Raw Socket Test: {agent_id}")
    print('='*60)

    socket_path = get_socket_path(agent_id)

    try:
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        print("✓ Connected to socket")

        # Send ping
        ping_msg = {"type": "ping"}
        writer.write((json.dumps(ping_msg) + "\n").encode())
        await writer.drain()
        print(f"Sent: {ping_msg}")

        # Read response
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if line:
            response = json.loads(line.decode())
            print(f"Received: {response}")

        # Send prompt
        print("\n--- Sending prompt ---")
        prompt_msg = {"type": "prompt", "content": "Say 'TEST'", "timeout": 30}
        writer.write((json.dumps(prompt_msg) + "\n").encode())
        await writer.drain()
        print(f"Sent: {prompt_msg}")

        # Read chunks
        print("\n--- Reading response ---")
        full_response = ""
        chunk_count = 0

        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=35)
                if not line:
                    print("Connection closed (empty line)")
                    break

                msg = json.loads(line.decode())
                msg_type = msg.get("type")

                if msg_type == "chunk":
                    chunk_count += 1
                    chunk = msg.get("content", "")
                    full_response += chunk
                    print(f"[Chunk {chunk_count}] {repr(chunk[:50])}")

                elif msg_type == "done":
                    print(f"\n[DONE] Final content length: {len(msg.get('content', ''))}")
                    final_content = msg.get("content", "")
                    if final_content != full_response:
                        print(f"Warning: Final content differs from chunks!")
                        print(f"  Chunks length: {len(full_response)}")
                        print(f"  Final length: {len(final_content)}")
                    full_response = final_content
                    break

                elif msg_type == "error":
                    print(f"\n[ERROR] {msg.get('message')}")
                    break

            except asyncio.TimeoutError:
                print("Timeout waiting for response")
                break

        print(f"\n--- Full Response ({len(full_response)} chars) ---")
        print(full_response)
        print("--- End response ---")

        writer.close()
        await writer.wait_closed()

    except Exception as e:
        print(f"✗ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


def check_agent_command(agent_id: str):
    """Check what command would be used for this agent."""
    from orchestrator.lib.settings import get_agent_config

    config = get_agent_config(agent_id)
    if config:
        print(f"\n--- Agent Config: {agent_id} ---")
        print(f"  Command: {config.command}")
        print(f"  Args: {config.args}")
        print(f"  Env: {config.env}")
        print(f"  Timeout: {config.timeout}")
        print(f"  Model: {config.model}")
        print(f"  Description: {config.description}")
    else:
        print(f"\n✗ No config found for '{agent_id}'")


async def main():
    """Run all tests."""
    # Check running agents
    print("\n=== Running Agents ===")
    running = list_running_agents()
    if running:
        for agent in running:
            print(f"  {agent['id']}: PID={agent['pid']}, socket={agent['socket']}")
    else:
        print("  (none)")

    # Test each agent
    for agent in ["claude", "copilot"]:
        check_agent_command(agent)

        if is_agent_running(agent):
            await test_agent_basic(agent)
            # await test_raw_socket(agent)  # Uncomment for detailed debugging
        else:
            print(f"\n{agent} is not running")


if __name__ == "__main__":
    asyncio.run(main())
