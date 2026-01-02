#!/usr/bin/env python3
"""Test GLM agent communication."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from orchestrator.lib.agent_client import AgentClient


async def test_glm():
    """Test GLM agent (GLM-4.7 via Z.AI)."""
    print("=" * 60)
    print("Testing GLM Agent (GLM-4.7 via Z.AI)")
    print("=" * 60)

    client = AgentClient("glm")
    await client.connect()

    # Simple test prompt
    print("\n[1] Sending: 'Say hello and identify yourself'")
    try:
        response = await client.prompt("Say hello and identify yourself", timeout=90)
        print(f"Response: {response[:300]}...")
    except Exception as e:
        print(f"Error: {e}")
        await client.close()
        return

    # Test memory
    print("\n[2] Sending: 'My name is Charlie, I am testing the orchestrator project'")
    try:
        response2 = await client.prompt("My name is Charlie, I am testing the orchestrator project", timeout=90)
        print(f"Response: {response2[:300]}...")
    except Exception as e:
        print(f"Error: {e}")
        await client.close()
        return

    print("\n[3] Sending: 'What is my name and what project am I testing?' (testing memory)")
    try:
        response3 = await client.prompt("What is my name and what project am I testing?", timeout=90)
        print(f"Response: {response3[:300]}...")

        if "Charlie" in response3 and "orchestrator" in response3.lower():
            print("\n✓ SUCCESS! GLM remembered both the name and project")
        elif "Charlie" in response3:
            print("\n✓ Partial success! GLM remembered the name")
        elif "orchestrator" in response3.lower():
            print("\n✓ Partial success! GLM remembered the project")
        else:
            print("\n? GLM response doesn't contain expected info - checking full response...")
            print(f"Full: {response3}")
    except Exception as e:
        print(f"Error: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(test_glm())
