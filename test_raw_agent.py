#!/usr/bin/env python3
"""Debug raw agent communication without client library."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.lib.agent_server import get_socket_path


async def test_raw_agent_communication(agent_id: str):
    """Test raw communication with agent."""
    socket_path = get_socket_path(agent_id)

    print(f"\n{'='*60}")
    print(f"Testing RAW communication with: {agent_id}")
    print(f"Socket: {socket_path}")
    print('='*60)

    try:
        reader, writer = await asyncio.open_unix_connection(path=str(socket_path))
        print("✓ Connected to socket\n")

        # 1. Send PING
        print("1. Sending PING...")
        ping_msg = {"type": "ping"}
        writer.write((json.dumps(ping_msg) + "\n").encode())
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=5)
        if line:
            response = json.loads(line.decode())
            print(f"   Response: {response}\n")

        # 2. Send PROMPT
        print("2. Sending PROMPT: 'Say HELLO in one word'...")
        prompt_msg = {
            "type": "prompt",
            "content": "Say HELLO in one word",
            "timeout": 30
        }
        writer.write((json.dumps(prompt_msg) + "\n").encode())
        await writer.drain()
        print()

        # 3. Read ALL responses with detailed logging
        print("3. Reading response...")

        all_raw_lines = []
        chunks_received = 0
        final_response = ""

        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=35)

                if not line:
                    print("   [Connection closed - empty line]")
                    break

                all_raw_lines.append(line)
                raw_hex = line.hex()[:100]  # First 100 chars of hex

                try:
                    msg = json.loads(line.decode())
                    msg_type = msg.get("type")
                    content = msg.get("content", "")

                    if msg_type == "chunk":
                        chunks_received += 1
                        content_preview = repr(content[:50])
                        print(f"   [CHUNK #{chunks_received}] len={len(content)} preview={content_preview}")
                        final_response += content

                    elif msg_type == "done":
                        print(f"   [DONE] Final content len={len(content)}")
                        final_response = content
                        break

                    elif msg_type == "error":
                        print(f"   [ERROR] {msg.get('message')}")
                        break

                    else:
                        print(f"   [UNKNOWN] type={msg_type}")

                except json.JSONDecodeError as e:
                    print(f"   [INVALID JSON] {e}")
                    print(f"   Raw (first 200 chars): {line[:200]}")
                    break

            except asyncio.TimeoutError:
                print("   [TIMEOUT] Waiting for response")
                break

        print()
        print(f"{'='*60}")
        print("SUMMARY")
        print('='*60)
        print(f"Chunks received: {chunks_received}")
        print(f"Total raw lines: {len(all_raw_lines)}")
        print(f"Final response length: {len(final_response)}")
        print()
        print("--- FINAL RESPONSE ---")
        print(final_response)
        print("--- END RESPONSE ---")
        print()
        print("--- RAW BYTES (first 500) ---")
        if final_response:
            print(repr(final_response[:500]))
        else:
            print("(empty)")
        print()

        writer.close()
        await writer.wait_closed()

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """Run tests."""
    for agent_id in ["claude", "copilot"]:
        await test_raw_agent_communication(agent_id)


if __name__ == "__main__":
    asyncio.run(main())
