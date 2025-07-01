"""
Simplified Lambda handler using the modular receipt_label package.

This handler demonstrates how to use the new modular merchant validation
components from the receipt_label package.
"""

import os
from typing import Any, Dict

from receipt_label.merchant_validation import (
    create_validation_handler,
    get_receipt_details,
    write_receipt_metadata_to_dynamo,
)

from .common import setup_logger

# Set up logging
logger = setup_logger(__name__)


def validate_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Lambda handler for validating a single receipt's merchant data.

    This is a simplified version that uses the modular components
    from the receipt_label package.

    Args:
        event: Lambda event containing image_id and receipt_id
        context: Lambda context

    Returns:
        Dict with validation status and results
    """
    logger.info("Starting validate_single_receipt_handler")

    # Extract required fields
    image_id = event["image_id"]
    receipt_id = int(event["receipt_id"])

    # Debug logging
    logger.info("Receipt ID type: %s, value: %s", type(receipt_id).__name__, receipt_id)

    # Get receipt details from DynamoDB
    (
        _receipt,
        receipt_lines,
        receipt_words,
        _receipt_letters,
        _receipt_word_tags,
        _receipt_word_labels,
    ) = get_receipt_details(image_id, receipt_id)
    logger.info("Got Receipt details for %s %s", image_id, receipt_id)

    # Create validation handler
    validator = create_validation_handler()

    # Validate merchant
    metadata, status_info = validator.validate_receipt_merchant(
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
        max_attempts=int(os.environ.get("MAX_AGENT_ATTEMPTS", 2)),
        timeout_seconds=int(os.environ.get("AGENT_TIMEOUT_SECONDS", 300)),
    )

    logger.info("Got metadata for %s %s: %s", image_id, receipt_id, dict(metadata))

    # Write to DynamoDB
    logger.info("Writing metadata to DynamoDB for %s %s", image_id, receipt_id)
    write_receipt_metadata_to_dynamo(metadata)

    # Return status for Step Functions
    return {"image_id": image_id, "receipt_id": receipt_id, **status_info}


# For local testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(f"Usage: python {__file__} <image_id> <receipt_id>")
        sys.exit(1)

    test_event = {"image_id": sys.argv[1], "receipt_id": int(sys.argv[2])}

    result = validate_handler(test_event, None)
    print(f"Result: {result}")
