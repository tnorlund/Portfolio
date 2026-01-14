"""Langsmith integration for logging label validations and merchant resolution.

ALL validation decisions are logged to Langsmith to build a comprehensive dataset
for improving the system. This includes:
- ChromaDB consensus validations (Tier 1)
- LLM validations (Tier 2)
- Merchant resolution results (both ChromaDB and Place ID Finder)

Human annotators can review and correct any decision, providing feedback
that improves future accuracy.

Uses @traceable decorator from langsmith for automatic trace creation.
Requires wait_for_all_tracers() to be called at the end of Lambda handlers
to ensure traces are flushed before the execution context terminates.
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _log(msg: str) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[LANGSMITH_LOGGING] {msg}", flush=True)
    logger.info(msg)


# Enable Langsmith tracing if API key is set
# Both LANGCHAIN_TRACING_V2 (for LangChain) and LANGSMITH_TRACING (for @traceable)
# are needed for complete tracing coverage
_api_key = os.environ.get("LANGCHAIN_API_KEY", "")
_tracing_v2 = os.environ.get("LANGCHAIN_TRACING_V2", "")
_langsmith_tracing = os.environ.get("LANGSMITH_TRACING", "")
_log(
    f"Langsmith config: API_KEY={'set' if _api_key else 'NOT SET'} "
    f"({len(_api_key)} chars), TRACING_V2={_tracing_v2!r}, "
    f"LANGSMITH_TRACING={_langsmith_tracing!r}"
)

if _api_key:
    if not _tracing_v2:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        _log("Auto-enabled LANGCHAIN_TRACING_V2")
    if not _langsmith_tracing:
        os.environ["LANGSMITH_TRACING"] = "true"
        _log("Auto-enabled LANGSMITH_TRACING")

# Default Langsmith projects
DEFAULT_LABEL_PROJECT = "receipt-label-validation"
DEFAULT_MERCHANT_PROJECT = "receipt-merchant-resolution"


def _get_label_validation_project() -> str:
    """Get the Langsmith project name for label validation."""
    return os.environ.get("LANGCHAIN_PROJECT", DEFAULT_LABEL_PROJECT)


def _get_merchant_resolution_project() -> str:
    """Get the Langsmith project name for merchant resolution."""
    return os.environ.get("LANGCHAIN_MERCHANT_PROJECT", DEFAULT_MERCHANT_PROJECT)


def _is_langsmith_enabled() -> bool:
    """Check if Langsmith is enabled (API key is set)."""
    return bool(os.environ.get("LANGCHAIN_API_KEY"))


def _get_traceable():
    """Get the traceable decorator if langsmith is available."""
    try:
        from langsmith.run_helpers import traceable

        return traceable
    except ImportError:
        _log("langsmith package not installed, tracing disabled")

        # Return a no-op decorator if langsmith not installed
        def noop_decorator(*args, **kwargs):
            def wrapper(fn):
                return fn

            return wrapper

        return noop_decorator


def log_label_validation(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    word_text: str,
    predicted_label: str,
    final_label: str,
    validation_source: str,  # "chroma" or "llm"
    decision: str,  # "valid", "invalid", "needs_review"
    confidence: float,
    reasoning: str,
    similar_words: Optional[List[Dict[str, Any]]] = None,
    merchant_name: Optional[str] = None,
    suggested_label: Optional[str] = None,
    label_scores: Optional[List[Dict[str, Any]]] = None,
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
        decision: "valid", "invalid", or "needs_review"
        confidence: Confidence score (0.0 to 1.0)
        reasoning: Explanation for the decision
        similar_words: Optional list of similar validated words used as evidence
        merchant_name: Optional merchant name for context
        suggested_label: Optional best alternative label from brute-force search
        label_scores: Optional list of top label candidates with scores

    Returns:
        Dict with logged data if successful, None if Langsmith is not enabled
    """
    if not _is_langsmith_enabled():
        _log(
            f"Langsmith not enabled (no API key), skipping log for "
            f"{image_id}#{receipt_id}#{line_id}#{word_id}"
        )
        return None

    traceable = _get_traceable()

    @traceable(
        name=f"label_validation_{validation_source}",
        project_name=_get_label_validation_project(),
        tags=[validation_source, decision],
    )
    def _traced_validation(
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        word_text: str,
        predicted_label: str,
        final_label: str,
        validation_source: str,
        decision: str,
        confidence: float,
        reasoning: str,
        similar_words: Optional[List[Dict[str, Any]]],
        merchant_name: Optional[str],
        suggested_label: Optional[str],
        label_scores: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Traced validation - captures all inputs and outputs."""
        return {
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
            "suggested_label": suggested_label,
            "label_scores": label_scores or [],
        }

    try:
        result = _traced_validation(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            word_text=word_text,
            predicted_label=predicted_label,
            final_label=final_label,
            validation_source=validation_source,
            decision=decision,
            confidence=confidence,
            reasoning=reasoning,
            similar_words=similar_words,
            merchant_name=merchant_name,
            suggested_label=suggested_label,
            label_scores=label_scores,
        )

        _log(
            f"Logged label validation: {image_id}/{receipt_id}/{line_id}/{word_id} "
            f"- {decision} ({validation_source}, conf={confidence:.2f})"
        )
        return result

    except Exception as e:
        _log(f"Failed to log label validation to Langsmith: {e}")
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
        _log(
            f"Langsmith not enabled (no API key), skipping merchant log for "
            f"{image_id}#{receipt_id}"
        )
        return None

    traceable = _get_traceable()

    found = place_id is not None

    @traceable(
        name=f"merchant_resolution_{resolution_tier}",
        project_name=_get_merchant_resolution_project(),
        tags=[resolution_tier, "found" if found else "not_found"],
    )
    def _traced_resolution(
        image_id: str,
        receipt_id: int,
        resolution_tier: str,
        merchant_name: Optional[str],
        place_id: Optional[str],
        confidence: float,
        phone_extracted: Optional[str],
        address_extracted: Optional[str],
        similarity_matches: Optional[List[Dict[str, Any]]],
        source_receipt: Optional[str],
    ) -> Dict[str, Any]:
        """Traced resolution - captures all inputs and outputs."""
        return {
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
        result = _traced_resolution(
            image_id=image_id,
            receipt_id=receipt_id,
            resolution_tier=resolution_tier,
            merchant_name=merchant_name,
            place_id=place_id,
            confidence=confidence,
            phone_extracted=phone_extracted,
            address_extracted=address_extracted,
            similarity_matches=similarity_matches,
            source_receipt=source_receipt,
        )

        _log(
            f"Logged merchant resolution: {image_id}#{receipt_id} "
            f"- {merchant_name or 'NOT_FOUND'} via {resolution_tier} "
            f"(conf={confidence:.2f})"
        )
        return result

    except Exception as e:
        _log(f"Failed to log merchant resolution to Langsmith: {e}")
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
    _matching_count: int,
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
        _log("Langsmith not enabled, skipping feedback log")
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
        _log(f"Logged feedback for run {run_id}: correct_label={correct_label}")
        return True
    except ImportError:
        _log("langsmith package not installed")
        return False
    except Exception as e:
        _log(f"Failed to log feedback to Langsmith: {e}")
        return False


def flush_traces() -> None:
    """Flush all pending Langsmith traces.

    Call this at the end of Lambda handlers to ensure all traces
    are sent before the execution context terminates.
    """
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        _log("Flushing Langsmith traces...")
        wait_for_all_tracers()
        _log("Langsmith traces flushed successfully")
    except ImportError:
        _log("langchain_core not installed, skipping trace flush")
    except Exception as e:
        _log(f"Failed to flush Langsmith traces: {e}")
