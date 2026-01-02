#!/usr/bin/env python3
"""Test copilot agent communication."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.lib.agent_client import AgentClient


async def test_copilot():
    """Test copilot agent."""
    print("=" * 60)
    print("Testing Copilot Agent")
    print("=" * 60)

    client = AgentClient("copilot")
    await client.connect()

    # Simple test prompt
    print("\n[1] Sending: 'Say hello in one word'")
    try:
        response = await client.prompt("Say hello in one word", timeout=60)
        print(f"Response: {response[:200]}...")
    except Exception as e:
        print(f"Error: {e}")
        return

    # Test memory
    print("\n[2] Sending: 'My name is Bob'")
    try:
        response2 = await client.prompt("My name is Bob", timeout=60)
        print(f"Response: {response2[:200]}...")
    except Exception as e:
        print(f"Error: {e}")
        return

    print("\n[3] Sending: 'What is my name?' (testing memory)")
    try:
        response3 = await client.prompt("What is my name?", timeout=60)
        print(f"Response: {response3[:200]}...")

        if "Bob" in response3 or "bob" in response3.lower():
            print("\n✓ SUCCESS! Copilot remembered the name 'Bob'")
        else:
            print("\n? Copilot response doesn't contain 'Bob' - checking full response...")
            print(f"Full: {response3}")
    except Exception as e:
        print(f"Error: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_copilot())
