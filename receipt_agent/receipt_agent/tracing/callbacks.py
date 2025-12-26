"""
LangSmith tracing callbacks and utilities.

This module provides helpers for integrating LangSmith tracing
with the receipt validation workflow.
"""

import logging
from typing import Any, Optional
from uuid import UUID

from langsmith import Client as LangSmithClient
from langsmith.run_helpers import get_current_run_tree

from receipt_agent.config.settings import get_settings

logger = logging.getLogger(__name__)


def create_tracing_callback(
    project_name: Optional[str] = None,
    run_name: Optional[str] = None,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Create LangSmith tracing configuration for a workflow run.

    Args:
        project_name: LangSmith project name (defaults to settings)
        run_name: Name for this specific run
        tags: Tags to add to the run
        metadata: Additional metadata to log

    Returns:
        Configuration dict for LangGraph invocation
    """
    settings = get_settings()

    return {
        "callbacks": [],  # LangSmith auto-instruments when env vars are set
        "tags": tags or [],
        "metadata": {
            "project": project_name or settings.langsmith_project,
            "run_name": run_name,
            **(metadata or {}),
        },
    }


def get_run_url() -> Optional[str]:
    """
    Get the LangSmith URL for the current run.

    Returns:
        URL to the run in LangSmith UI, or None if not in a traced context
    """
    try:
        run_tree = get_current_run_tree()
        if run_tree and run_tree.id:
            settings = get_settings()
            # LangSmith run URL format
            return (
                f"https://smith.langchain.com/o/default/projects/p/"
                f"{settings.langsmith_project}/r/{run_tree.id}"
            )
    except Exception as e:
        logger.debug("Could not get run URL: %s", e)

    return None


def log_feedback(
    run_id: UUID,
    score: float,
    comment: Optional[str] = None,
    key: str = "user_feedback",
) -> bool:
    """
    Log feedback to LangSmith for a specific run.

    Args:
        run_id: UUID of the LangSmith run
        score: Feedback score (0.0 to 1.0)
        comment: Optional feedback comment
        key: Feedback key (default: "user_feedback")

    Returns:
        True if feedback was logged successfully
    """
    settings = get_settings()
    api_key = settings.langsmith_api_key.get_secret_value()

    if not api_key:
        logger.warning("LangSmith API key not set - cannot log feedback")
        return False

    try:
        client = LangSmithClient(api_key=api_key)
        client.create_feedback(
            run_id=run_id,
            key=key,
            score=score,
            comment=comment,
        )
        logger.info("Logged feedback for run %s: %s", run_id, score)
        return True
    except Exception as e:
        logger.error("Failed to log feedback: %s", e)
        return False


class ValidationRunContext:
    """
    Context manager for tracking validation runs in LangSmith.

    Usage:
        ```python
        async with ValidationRunContext(
            image_id=image_id,
            receipt_id=receipt_id,
        ) as ctx:
            result = await agent.validate(image_id, receipt_id)
            ctx.set_result(result)
        ```
    """

    def __init__(
        self,
        image_id: str,
        receipt_id: int,
        tags: Optional[list[str]] = None,
    ):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.tags = tags or []
        self.run_id: Optional[UUID] = None
        self._result: Optional[Any] = None

    async def __aenter__(self) -> "ValidationRunContext":
        """Enter the context and start tracking."""
        try:
            run_tree = get_current_run_tree()
            if run_tree:
                self.run_id = run_tree.id
        except Exception:
            pass
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        """Exit the context and finalize tracking."""
        if exc_type is not None:
            logger.error(
                "Validation run failed: %s",
                exc_val,
                extra={
                    "image_id": self.image_id,
                    "receipt_id": self.receipt_id,
                    "run_id": str(self.run_id) if self.run_id else None,
                },
            )

    def set_result(self, result: Any) -> None:
        """Set the validation result for logging."""
        self._result = result

    def get_run_url(self) -> Optional[str]:
        """Get the LangSmith URL for this run."""
        if self.run_id:
            settings = get_settings()
            return (
                f"https://smith.langchain.com/o/default/projects/p/"
                f"{settings.langsmith_project}/r/{self.run_id}"
            )
        return None

    def log_feedback(
        self,
        score: float,
        comment: Optional[str] = None,
    ) -> bool:
        """Log feedback for this run."""
        if self.run_id:
            return log_feedback(self.run_id, score, comment)
        return False
