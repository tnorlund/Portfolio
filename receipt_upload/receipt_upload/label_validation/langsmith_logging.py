"""Langsmith integration for logging uncertain label validations.

Uncertain label validations (NEEDS_REVIEW decisions) are logged to Langsmith
for human annotation. This allows building a feedback loop where humans can
correct mislabeled predictions, which improves future validation accuracy.
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default Langsmith project for label validation
DEFAULT_PROJECT = "receipt-label-validation"


def _get_langsmith_project() -> str:
    """Get the Langsmith project name from env or default."""
    return os.environ.get("LANGCHAIN_PROJECT", DEFAULT_PROJECT)


def _is_langsmith_enabled() -> bool:
    """Check if Langsmith is enabled (API key is set)."""
    return bool(os.environ.get("LANGCHAIN_API_KEY"))


def log_validation_for_review(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    word_text: str,
    predicted_label: str,
    consensus_label: Optional[str],
    confidence: float,
    matching_count: int,
    reason: str,
    merchant_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Log an uncertain label validation to Langsmith for human review.

    This function uses the @traceable decorator dynamically to avoid import
    errors when langsmith is not installed or configured.

    Args:
        image_id: Image ID of the receipt
        receipt_id: Receipt ID
        line_id: Line ID
        word_id: Word ID
        word_text: The text of the word being validated
        predicted_label: The predicted label from LayoutLM
        consensus_label: The consensus label from similar validated words
        confidence: Confidence score of the consensus
        matching_count: Number of similar validated words found
        reason: Reason for the NEEDS_REVIEW decision
        merchant_name: Optional merchant name for context

    Returns:
        Dict with logged data if successful, None if Langsmith is not enabled
    """
    if not _is_langsmith_enabled():
        logger.debug(
            "Langsmith not enabled, skipping validation review log for %s",
            f"{image_id}#{receipt_id}#{line_id}#{word_id}",
        )
        return None

    try:
        from langsmith.run_helpers import traceable
    except ImportError:
        logger.warning(
            "langsmith package not installed, skipping validation review log"
        )
        return None

    # Create the traceable function dynamically
    @traceable(project_name=_get_langsmith_project(), name="label_validation")
    def _log_to_langsmith(data: Dict[str, Any]) -> Dict[str, Any]:
        return data

    log_data = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
        "word_text": word_text,
        "predicted_label": predicted_label,
        "consensus_label": consensus_label,
        "confidence": confidence,
        "matching_count": matching_count,
        "reason": reason,
        "merchant_name": merchant_name,
    }

    try:
        result = _log_to_langsmith(log_data)
        logger.info(
            "Logged validation for review: %s/%s/%s/%s - %s",
            image_id,
            receipt_id,
            line_id,
            word_id,
            reason,
        )
        return result
    except Exception as e:
        logger.warning("Failed to log to Langsmith: %s", e)
        return None


def log_validation_feedback(
    run_id: str,
    correct_label: str,
    annotator: str = "human",
) -> bool:
    """Log feedback for a validation decision.

    This is called when a human annotator corrects a label decision,
    providing feedback that can be used to improve the validation system.

    Args:
        run_id: The Langsmith run ID to attach feedback to
        correct_label: The correct label as determined by the annotator
        annotator: Identifier for who provided the feedback

    Returns:
        True if feedback was logged successfully, False otherwise
    """
    if not _is_langsmith_enabled():
        logger.debug("Langsmith not enabled, skipping feedback log")
        return False

    try:
        from langsmith import Client

        client = Client()
        client.create_feedback(
            run_id=run_id,
            key="correct_label",
            value=correct_label,
            comment=f"Annotated by {annotator}",
        )
        logger.info("Logged feedback for run %s: correct_label=%s", run_id, correct_label)
        return True
    except ImportError:
        logger.warning("langsmith package not installed")
        return False
    except Exception as e:
        logger.warning("Failed to log feedback to Langsmith: %s", e)
        return False
