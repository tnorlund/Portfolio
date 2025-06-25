"""Lambda handler for listing receipts that need merchant validation."""

from typing import Any, Dict

from receipt_label.merchant_validation import \
    list_receipts_for_merchant_validation

from .common import setup_logger

logger = setup_logger(__name__)


def list_handler(_event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Lambda handler for listing receipts for merchant validation.
    """
    logger.info("Starting list_receipts_handler")
    receipts = list_receipts_for_merchant_validation()
    logger.info("Found %s receipts for merchant validation", len(receipts))
    return {
        "statusCode": 200,
        "receipts": [
            {
                "image_id": receipt[0],
                "receipt_id": receipt[1],
            }
            for receipt in receipts
        ],
    }
