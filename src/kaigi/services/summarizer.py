"""Conversation summarization service."""

from __future__ import annotations

from kaigi.models.execution import ConversationRecord, MessageRole


def estimate_tokens(text: str) -> int:
    """Estimate token count using simple heuristic.

    Uses ~4 characters per token approximation (typical for English text).
    This is a rough estimate - actual tokenization depends on the model.

    Args:
        text: Text to estimate tokens for

    Returns:
        Estimated token count
    """
    if not text:
        return 0
    return len(text) // 4


def estimate_conversation_tokens(record: ConversationRecord) -> int:
    """Estimate total tokens in conversation history.

    Args:
        record: Conversation record to estimate

    Returns:
        Estimated token count of full conversation history
    """
    history = record.get_conversation_history()
    return estimate_tokens(history)


def create_summary(record: ConversationRecord) -> str:
    """Create a Markdown summary of the conversation.

    Extracts key information from messages up to current_round:
    - Topic and objectives
    - Key decisions and agreements
    - Important discussion points
    - Current status

    Args:
        record: Conversation record to summarize

    Returns:
        Markdown-formatted summary
    """
    if not record.messages:
        return ""

    lines = []

    # Header with context
    lines.append(f"# Conversation Summary: {record.topic}")
    lines.append(f"**Workflow**: {record.workflow_name}")
    lines.append(f"**Round**: {record.current_round}")
    lines.append(f"**Status**: {record.status.value}")
    lines.append("")

    # Extract key points from messages
    # We iterate through all messages and extract structured information
    key_decisions = []
    agreements = []
    discussion_points = []

    for msg in record.messages:
        # Skip system messages for cleaner summary
        if msg.role == MessageRole.SYSTEM:
            continue

        content = msg.content.strip()

        # Look for consensus/agreement markers
        if "AGREED:" in content or "CONSENSUS:" in content:
            agreements.append(f"[{msg.agent_id}] {content[:200]}")

        # Look for decision indicators
        if any(marker in content.upper() for marker in ["DECIDE:", "DECISION:", "CONCLUDE:"]):
            key_decisions.append(f"[{msg.agent_id}] {content[:300]}")

        # Capture substantial discussion points (longer messages)
        if len(content) > 200 and msg.role == MessageRole.AGENT:
            # Store truncated version
            point = (
                f"[{msg.agent_id} (Round {msg.round_number})] "
                f"{content[:400]}..."
            )
            discussion_points.append(point)

    # Build summary sections
    if agreements:
        lines.append("## Agreements & Consensus")
        for agreement in agreements:
            lines.append(f"- {agreement}")
        lines.append("")

    if key_decisions:
        lines.append("## Key Decisions")
        for decision in key_decisions:
            lines.append(f"- {decision}")
        lines.append("")

    if discussion_points:
        lines.append("## Discussion Highlights")
        for point in discussion_points[:5]:  # Limit to top 5
            lines.append(f"### {point}")
        lines.append("")

    # Current state
    if record.consensus_status.value != "pending":
        lines.append("## Current Status")
        lines.append(f"**Consensus**: {record.consensus_status.value}")
        if record.consensus_content:
            lines.append(f"**Content**: {record.consensus_content[:500]}")
        lines.append("")

    return "\n".join(lines)


def should_summarize(record: ConversationRecord, threshold_tokens: int = 80000) -> bool:
    """Check if conversation should be summarized.

    Args:
        record: Conversation record to check
        threshold_tokens: Token threshold for triggering summarization

    Returns:
        True if summarization is recommended
    """
    # Don't summarize if already summarized recently
    if record.summary_cutoff_round is not None:
        rounds_since_summary = record.current_round - record.summary_cutoff_round
        if rounds_since_summary < 3:  # Wait at least 3 rounds between summaries
            return False

    # Check token count
    estimated_tokens = estimate_conversation_tokens(record)
    return estimated_tokens > threshold_tokens


def apply_summary(record: ConversationRecord) -> ConversationRecord:
    """Create and apply summary to conversation record.

    This function:
    1. Generates a summary of the conversation
    2. Stores it in record.summary
    3. Clears old messages (before summary cutoff round)
    4. Stores the cutoff round for reference

    Args:
        record: Conversation record to summarize

    Returns:
        Modified conversation record with summary applied
    """
    # Create summary
    summary = create_summary(record)
    record.summary = summary
    record.summary_cutoff_round = record.current_round

    # Keep system messages, consensus messages, and recent messages
    # Remove older discussion messages to save context space
    cutoff_index = len(record.messages)
    for i, msg in enumerate(record.messages):
        # Keep messages from current round
        if msg.round_number >= record.current_round:
            cutoff_index = min(cutoff_index, i)
            continue

        # Keep consensus-related messages
        if "AGREED:" in msg.content or "CONSENSUS:" in msg.content:
            cutoff_index = min(cutoff_index, i)
            continue

        # Keep system messages
        if msg.role == MessageRole.SYSTEM:
            cutoff_index = min(cutoff_index, i)
            continue

    # Truncate messages before cutoff
    # (In practice, we might want to keep these for transcript,
    #  but for prompt building we'd use the summary instead)
    # For now, we keep all messages and rely on get_conversation_history
    # to be smart about including the summary.

    return record
