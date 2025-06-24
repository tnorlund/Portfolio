"""Lambda handler for listing receipts that need merchant validation."""

from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict

from receipt_label.merchant_validation import (
    list_receipts_for_merchant_validation,
)

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


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
