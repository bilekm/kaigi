"""Prompt templates for conversation mode."""

# === TEAM MODE (Equal Collaboration) ===

TEAM_AGENT_TEMPLATE = """\
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
5. Use your tools to read files as needed - don't ask for file contents, read them yourself
6. To modify files, propose the changes clearly and wait for approval
7. Keep your response focused and constructive (aim for 100-300 words unless more detail is needed)

## Conversation History
{history}

---

It's your turn to respond as [{agent_id}].
"""

TEAM_SYSTEM_MESSAGE = """\
Discussion topic: {topic}

Team members: {agent_list}

The team will discuss this topic and work toward a consensus solution.
"""


# === ORCHESTRATED MODE (Lead + Team) ===

LEAD_AGENT_TEMPLATE = """\
You are the LEAD of this team. You analyze tasks, delegate work, and make final decisions.

## Your Role
{persona}

## The Task
{topic}

## Your Team
{team_list}

## Rules for Leading
1. Analyze the task and break it into actionable subtasks
2. Assign tasks to team members using: "ASSIGN @agent_id: <task description>"
3. You can also ask questions: "QUESTION @agent_id: <question>"
4. Review team responses and provide feedback
5. When ready to conclude, state: "{consensus_keyword}" followed by your final decision
6. You make the final decision - team provides input but you decide
7. Be clear and specific in your assignments
8. Use your tools to read files as needed

## Conversation History
{history}

---

It's your turn as Lead [{agent_id}]. Analyze the situation and direct your team.
"""

TEAM_MEMBER_TEMPLATE = """\
You are a team member working under the lead's direction.

## Your Role
{persona}

## The Task
{topic}

## Team Lead: {lead_id}

## Rules for Team Members
1. Wait for assignments from the lead (marked with "ASSIGN @{agent_id}:")
2. Execute assigned tasks thoroughly and report results
3. You can suggest improvements to the lead: "SUGGEST: <your suggestion>"
4. You can ask the lead questions: "QUESTION @{lead_id}: <question>"
5. Respond to questions from the lead or other team members
6. The lead makes final decisions - provide your best input
7. Stay focused on your assigned tasks
8. Use your tools to read files as needed

## Conversation History
{history}

---

It's your turn as [{agent_id}]. Respond to any assignments or continue the discussion.
"""

ORCHESTRATED_SYSTEM_MESSAGE = """\
Task: {topic}

Mode: Orchestrated collaboration
Lead: {lead_id}
Team: {team_list}

The lead will analyze the task, assign work to team members, and make final decisions.
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


def build_team_prompt(
    agent_id: str,
    persona: str,
    topic: str,
    consensus_keyword: str,
    history: str,
) -> str:
    """Build prompt for team mode (equal collaboration)."""
    return TEAM_AGENT_TEMPLATE.format(
        persona=persona or f"You are team member {agent_id}.",
        topic=topic,
        consensus_keyword=consensus_keyword,
        history=history or "(No previous messages - you are starting the discussion)",
        agent_id=agent_id,
    )


def build_lead_prompt(
    agent_id: str,
    persona: str,
    topic: str,
    consensus_keyword: str,
    history: str,
    team_list: str,
) -> str:
    """Build prompt for lead agent in orchestrated mode."""
    return LEAD_AGENT_TEMPLATE.format(
        persona=persona or f"You are the team lead {agent_id}.",
        topic=topic,
        consensus_keyword=consensus_keyword,
        history=history or "(No previous messages - you are starting the discussion)",
        agent_id=agent_id,
        team_list=team_list,
    )


def build_team_member_prompt(
    agent_id: str,
    persona: str,
    topic: str,
    lead_id: str,
    history: str,
) -> str:
    """Build prompt for team member in orchestrated mode."""
    return TEAM_MEMBER_TEMPLATE.format(
        persona=persona or f"You are team member {agent_id}.",
        topic=topic,
        lead_id=lead_id,
        history=history or "(No previous messages - waiting for lead's direction)",
        agent_id=agent_id,
    )


def build_team_system_message(topic: str, agent_ids: list[str]) -> str:
    """Build the initial system message for team mode."""
    return TEAM_SYSTEM_MESSAGE.format(
        topic=topic,
        agent_list=", ".join(agent_ids),
    )


def build_orchestrated_system_message(
    topic: str,
    lead_id: str,
    team_ids: list[str],
) -> str:
    """Build the initial system message for orchestrated mode."""
    return ORCHESTRATED_SYSTEM_MESSAGE.format(
        topic=topic,
        lead_id=lead_id,
        team_list=", ".join(team_ids),
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
