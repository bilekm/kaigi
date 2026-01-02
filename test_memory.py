#!/usr/bin/env python3
"""Test conversation memory - second prompt should remember first."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.lib.agent_client import AgentClient


async def test_memory():
    """Test that second prompt remembers the first."""
    print("=" * 60)
    print("Testing Conversation Memory")
    print("=" * 60)

    client = AgentClient("claude")
    await client.connect()

    # First prompt
    print("\n[1] First prompt: 'My name is Alice'")
    response1 = await client.prompt("My name is Alice", timeout=30)
    print(f"Response: {response1[:100]}...")

    # Second prompt - should remember the name
    print("\n[2] Second prompt: 'What is my name?'")
    response2 = await client.prompt("What is my name?", timeout=30)
    print(f"Response: {response2[:100]}...")

    # Check if memory worked
    if "Alice" in response2:
        print("\n✓ SUCCESS! Agent remembered the name 'Alice'")
    else:
        print("\n✗ FAIL! Agent did NOT remember the name")
        print(f"Full response: {response2}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_memory())
