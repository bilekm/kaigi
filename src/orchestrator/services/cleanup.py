"""7-day retention cleanup service."""

from __future__ import annotations

from typing import Any

from orchestrator.lib.logging import get_logger
from orchestrator.services.store import WorkflowStore


def run_cleanup(
    max_age_days: int = 7,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Remove expired workflow data.

    Args:
        max_age_days: Maximum age in days for executions to keep.
        dry_run: If True, only report what would be deleted.
        force: If True, delete without confirmation.

    Returns:
        Dictionary with cleanup results.
    """
    store = WorkflowStore()
    logger = get_logger()

    stale = list(store.find_stale_executions(max_age_days=max_age_days))

    if not stale:
        logger.info("No stale executions found")
        return {
            "deleted_count": 0,
            "executions": [],
        }

    deleted = []

    for workflow_id, execution_id, ended_at in stale:
        if dry_run:
            logger.info(
                "Would delete execution",
                workflow_id=workflow_id,
                execution_id=execution_id,
                ended_at=ended_at.isoformat(),
            )
        else:
            store.delete_execution(workflow_id, execution_id)
            logger.info(
                "Deleted execution",
                workflow_id=workflow_id,
                execution_id=execution_id,
            )

        deleted.append({
            "workflow_id": workflow_id,
            "execution_id": execution_id,
            "ended_at": ended_at.isoformat(),
        })

    return {
        "deleted_count": len(deleted),
        "dry_run": dry_run,
        "executions": deleted,
    }
