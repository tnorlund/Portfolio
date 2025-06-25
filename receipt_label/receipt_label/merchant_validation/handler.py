"""
High-level merchant validation handler.

This module provides a simple interface for validating merchant data
that can be used in Lambda functions or other contexts.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from receipt_dynamo.constants import ValidationMethod
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata

from .agent import MerchantValidationAgent
from .merchant_validation import (
    build_receipt_metadata_from_result,
    build_receipt_metadata_from_result_no_match,
    extract_candidate_merchant_fields,
)
from .result_processor import (
    build_receipt_metadata_from_partial_result,
    extract_best_partial_match,
    sanitize_metadata_strings,
    sanitize_string,
)

logger = logging.getLogger(__name__)


class MerchantValidationHandler:
    """High-level handler for merchant validation operations."""

    def __init__(
        self, google_places_api_key: str, model: str = "gpt-3.5-turbo"
    ):
        """
        Initialize the validation handler.

        Args:
            google_places_api_key: API key for Google Places
            model: OpenAI model to use for validation
        """
        self.agent = MerchantValidationAgent(google_places_api_key, model)
        self.google_places_api_key = google_places_api_key

    def validate_receipt_merchant(
        self,
        image_id: str,
        receipt_id: int,
        receipt_lines: list,
        receipt_words: list,
        max_attempts: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Tuple[ReceiptMetadata, Dict[str, Any]]:
        """
        Validate merchant information for a receipt.

        Args:
            image_id: Image UUID
            receipt_id: Receipt ID
            receipt_lines: List of receipt line objects
            receipt_words: List of receipt word objects
            max_attempts: Maximum retry attempts
            timeout_seconds: Timeout per attempt

        Returns:
            Tuple of (ReceiptMetadata, status_info)
        """
        # Prepare user input
        user_input = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "raw_text": [line.text for line in receipt_lines],
            "extracted_data": {
                key: [w.text for w in words]
                for key, words in extract_candidate_merchant_fields(
                    receipt_words
                ).items()
            },
        }

        # Run the agent
        metadata, partial_results = self.agent.validate_receipt(
            user_input, max_attempts, timeout_seconds
        )

        # Process results
        if metadata is not None:
            # Successful validation
            metadata = sanitize_metadata_strings(metadata)
            return self._build_success_metadata(
                image_id, receipt_id, metadata
            ), {
                "status": "processed",
                "place_id": metadata.get("place_id", ""),
                "merchant_name": metadata.get("merchant_name", ""),
            }

        # Agent failed - try to use partial results
        best_partial_match = extract_best_partial_match(
            partial_results, user_input
        )

        if best_partial_match:
            logger.info(
                f"Using best partial match from {best_partial_match.get('source', 'unknown')}"
            )
            metadata_obj = build_receipt_metadata_from_partial_result(
                image_id,
                receipt_id,
                best_partial_match,
                user_input["raw_text"],
            )
        else:
            # Complete failure - no match
            metadata_obj = build_receipt_metadata_from_result_no_match(
                receipt_id, image_id, None
            )

        # Add failure context
        failure_context = (
            f"Agent validation failed after {max_attempts or 2} attempts."
        )
        if partial_results:
            failure_context += f" Partial results were collected from: {', '.join([r['function'] for r in partial_results])}"
        metadata_obj.reasoning = f"{failure_context} {metadata_obj.reasoning}"

        return metadata_obj, {
            "status": "no_match",
            "failure_reason": "agent_attempts_exhausted",
            "partial_data_used": bool(best_partial_match),
        }

    def _build_success_metadata(
        self, image_id: str, receipt_id: int, metadata: Dict[str, Any]
    ) -> ReceiptMetadata:
        """Build ReceiptMetadata from successful agent result."""
        vb_enum = ValidationMethod(
            metadata.get("validated_by", ValidationMethod.INFERENCE.value)
        )
        matched_fields = list(
            {
                f.strip()
                for f in metadata.get("matched_fields", [])
                if f.strip()
            }
        )

        return ReceiptMetadata(
            image_id=image_id,
            receipt_id=receipt_id,
            place_id=sanitize_string(metadata.get("place_id", "")),
            merchant_name=sanitize_string(metadata.get("merchant_name", "")),
            address=sanitize_string(metadata.get("address", "")),
            phone_number=sanitize_string(metadata.get("phone_number", "")),
            merchant_category=sanitize_string(
                metadata.get("merchant_category", "")
            ),
            matched_fields=matched_fields,
            timestamp=metadata.get("timestamp") or datetime.now(timezone.utc),
            validated_by=vb_enum,
            reasoning=sanitize_string(metadata.get("reasoning", "")),
        )


# Factory function for easy Lambda usage
def create_validation_handler(
    api_key: Optional[str] = None,
) -> MerchantValidationHandler:
    """
    Create a validation handler with optional API key override.

    Args:
        api_key: Google Places API key (defaults to GOOGLE_PLACES_API_KEY env var)

    Returns:
        MerchantValidationHandler instance
    """
    api_key = api_key or os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise ValueError(
            "Google Places API key must be provided or set in GOOGLE_PLACES_API_KEY env var"
        )

    return MerchantValidationHandler(api_key)
