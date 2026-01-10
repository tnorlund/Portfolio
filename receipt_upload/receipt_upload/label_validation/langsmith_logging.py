"""Langsmith integration for logging label validations and merchant resolution.

ALL validation decisions are logged to Langsmith to build a comprehensive dataset
for improving the system. This includes:
- ChromaDB consensus validations (Tier 1)
- LLM validations (Tier 2)
- Merchant resolution results (both ChromaDB and Place ID Finder)

Human annotators can review and correct any decision, providing feedback
that improves future accuracy.
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default Langsmith projects
DEFAULT_LABEL_PROJECT = "receipt-label-validation"
DEFAULT_MERCHANT_PROJECT = "receipt-merchant-resolution"


def _get_label_validation_project() -> str:
    """Get the Langsmith project name for label validation."""
    return os.environ.get("LANGCHAIN_LABEL_PROJECT", DEFAULT_LABEL_PROJECT)


def _get_merchant_resolution_project() -> str:
    """Get the Langsmith project name for merchant resolution."""
    return os.environ.get("LANGCHAIN_MERCHANT_PROJECT", DEFAULT_MERCHANT_PROJECT)


def _is_langsmith_enabled() -> bool:
    """Check if Langsmith is enabled (API key is set)."""
    return bool(os.environ.get("LANGCHAIN_API_KEY"))


def log_label_validation(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    word_text: str,
    predicted_label: str,
    final_label: str,
    validation_source: str,  # "chroma" or "llm"
    decision: str,  # "valid", "corrected", "needs_review"
    confidence: float,
    reasoning: str,
    similar_words: Optional[List[Dict[str, Any]]] = None,
    merchant_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Log a label validation decision to Langsmith.

    ALL validation decisions are logged to build a comprehensive dataset
    for improving the system.

    Args:
        image_id: Image ID of the receipt
        receipt_id: Receipt ID
        line_id: Line ID
        word_id: Word ID
        word_text: The text of the word being validated
        predicted_label: The original predicted label from LayoutLM
        final_label: The final label after validation
        validation_source: "chroma" for ChromaDB consensus, "llm" for LLM
        decision: "valid", "corrected", or "needs_review"
        confidence: Confidence score (0.0 to 1.0)
        reasoning: Explanation for the decision
        similar_words: Optional list of similar validated words used as evidence
        merchant_name: Optional merchant name for context

    Returns:
        Dict with logged data if successful, None if Langsmith is not enabled
    """
    if not _is_langsmith_enabled():
        logger.debug(
            "Langsmith not enabled, skipping label validation log for %s",
            f"{image_id}#{receipt_id}#{line_id}#{word_id}",
        )
        return None

    try:
        from langsmith.run_helpers import traceable
    except ImportError:
        logger.warning(
            "langsmith package not installed, skipping label validation log"
        )
        return None

    # Create the traceable function dynamically
    @traceable(
        project_name=_get_label_validation_project(),
        name=f"label_validation_{validation_source}",
    )
    def _log_to_langsmith(data: Dict[str, Any]) -> Dict[str, Any]:
        return data

    log_data = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_id": line_id,
        "word_id": word_id,
        "word_text": word_text,
        "predicted_label": predicted_label,
        "final_label": final_label,
        "validation_source": validation_source,
        "decision": decision,
        "confidence": confidence,
        "reasoning": reasoning,
        "similar_words": similar_words or [],
        "merchant_name": merchant_name,
    }

    try:
        result = _log_to_langsmith(log_data)
        logger.info(
            "Logged label validation: %s/%s/%s/%s - %s (%s, conf=%.2f)",
            image_id,
            receipt_id,
            line_id,
            word_id,
            decision,
            validation_source,
            confidence,
        )
        return result
    except Exception as e:
        logger.warning("Failed to log label validation to Langsmith: %s", e)
        return None


def log_merchant_resolution(
    image_id: str,
    receipt_id: int,
    resolution_tier: str,  # "chroma_phone", "chroma_address", "chroma_text", "place_id_finder"
    merchant_name: Optional[str],
    place_id: Optional[str],
    confidence: float,
    phone_extracted: Optional[str] = None,
    address_extracted: Optional[str] = None,
    similarity_matches: Optional[List[Dict[str, Any]]] = None,
    source_receipt: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Log a merchant resolution decision to Langsmith.

    ALL merchant resolution attempts are logged to build a comprehensive dataset.

    Args:
        image_id: Image ID of the receipt
        receipt_id: Receipt ID
        resolution_tier: How the merchant was resolved
        merchant_name: Resolved merchant name (or None if not found)
        place_id: Resolved Google Place ID (or None if not found)
        confidence: Confidence score (0.0 to 1.0)
        phone_extracted: Phone number extracted from receipt
        address_extracted: Address extracted from receipt
        similarity_matches: Top ChromaDB similarity matches with scores
        source_receipt: Source receipt ID for Tier 1 matches

    Returns:
        Dict with logged data if successful, None if Langsmith is not enabled
    """
    if not _is_langsmith_enabled():
        logger.debug(
            "Langsmith not enabled, skipping merchant resolution log for %s#%s",
            image_id,
            receipt_id,
        )
        return None

    try:
        from langsmith.run_helpers import traceable
    except ImportError:
        logger.warning(
            "langsmith package not installed, skipping merchant resolution log"
        )
        return None

    # Create the traceable function dynamically
    @traceable(
        project_name=_get_merchant_resolution_project(),
        name=f"merchant_resolution_{resolution_tier}",
    )
    def _log_to_langsmith(data: Dict[str, Any]) -> Dict[str, Any]:
        return data

    log_data = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "resolution_tier": resolution_tier,
        "merchant_name": merchant_name,
        "place_id": place_id,
        "confidence": confidence,
        "phone_extracted": phone_extracted,
        "address_extracted": address_extracted,
        "similarity_matches": similarity_matches or [],
        "source_receipt": source_receipt,
        "found": place_id is not None,
    }

    try:
        result = _log_to_langsmith(log_data)
        logger.info(
            "Logged merchant resolution: %s#%s - %s via %s (conf=%.2f)",
            image_id,
            receipt_id,
            merchant_name or "NOT_FOUND",
            resolution_tier,
            confidence,
        )
        return result
    except Exception as e:
        logger.warning("Failed to log merchant resolution to Langsmith: %s", e)
        return None


# Keep old function for backwards compatibility, but mark as deprecated
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
    """DEPRECATED: Use log_label_validation instead.

    Kept for backwards compatibility.
    """
    return log_label_validation(
        image_id=image_id,
        receipt_id=receipt_id,
        line_id=line_id,
        word_id=word_id,
        word_text=word_text,
        predicted_label=predicted_label,
        final_label=consensus_label or predicted_label,
        validation_source="llm",
        decision="needs_review",
        confidence=confidence,
        reasoning=reason,
        similar_words=None,
        merchant_name=merchant_name,
    )


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
