"""Prompt templates for conversation mode."""

AGENT_TURN_TEMPLATE = """\
You are participating in a team discussion to solve a problem.

## Your Role
{persona}

## The Task
{topic}

## Rules
1. Discuss constructively with your teammates
2. Consider different perspectives and build on others' ideas
3. When you're ready to propose a final solution that everyone should agree on, start your message with: "{consensus_keyword}"
4. All team members must include "{consensus_keyword}" in their response for consensus to be reached
5. You can see project files but cannot modify them directly - propose changes instead
6. Keep your response focused and constructive (aim for 100-300 words unless more detail is needed)

## Project Context Files
{context_files}

## Conversation History
{history}

---

It's your turn to respond as [{agent_id}].
"""

SYSTEM_TOPIC_MESSAGE = """\
Discussion topic: {topic}

Team members: {agent_list}

The team will discuss this topic and work toward a consensus solution.
"""

USER_INJECTION_NOTE = """\
[Team Lead has joined the discussion]

{user_message}

Please take this feedback into account and continue the discussion.
"""

CONSENSUS_PROMPT = """\
=== CONSENSUS REACHED ===

All team members have signaled agreement. The proposed solution is:

{consensus_content}

---

Team Lead: Review this solution and type 'approve' to finalize, or provide feedback to continue the discussion.
"""


def build_agent_prompt(
    agent_id: str,
    persona: str,
    topic: str,
    consensus_keyword: str,
    context_files: str,
    history: str,
) -> str:
    """Build the full prompt for an agent's turn."""
    return AGENT_TURN_TEMPLATE.format(
        persona=persona or f"You are team member {agent_id}.",
        topic=topic,
        consensus_keyword=consensus_keyword,
        context_files=context_files or "(No context files provided)",
        history=history or "(No previous messages - you are starting the discussion)",
        agent_id=agent_id,
    )


def build_system_message(topic: str, agent_ids: list[str]) -> str:
    """Build the initial system message for a conversation."""
    return SYSTEM_TOPIC_MESSAGE.format(
        topic=topic,
        agent_list=", ".join(agent_ids),
    )


def format_context_files(files: dict[str, str], max_chars_per_file: int = 2000) -> str:
    """Format context files for inclusion in prompt."""
    if not files:
        return "(No context files)"

    lines = []
    for path, content in files.items():
        lines.append(f"--- {path} ---")
        if len(content) > max_chars_per_file:
            lines.append(content[:max_chars_per_file] + "\n... (truncated)")
        else:
            lines.append(content)

    return "\n\n".join(lines)
