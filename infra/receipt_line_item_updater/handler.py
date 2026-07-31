"""Lambda handler for receipt line-item recompute messages.

Triggered by SQS when a RECEIPT_SUMMARY record changes (insert or modify)
-- the point at which words, sections, summary and merchant all exist for
a receipt -- and rewrites its RECEIPT_LINE_ITEM rows via the canonical
band-block decoder. Mirrors receipt_summary_updater's handler contract:
per-receipt dedupe and partial-batch failure reporting.
"""

import logging
import os
from typing import Any

# pylint: disable-next=wrong-import-order  # Local Lambda module
from line_item_processor import deduplicate_messages, update_receipt_line_items
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    EntityError,
    OperationError,
)

log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Process line-item recompute messages from SQS."""
    records = event.get("Records", [])
    if not records:
        logger.info("No records to process")
        return {"batchItemFailures": []}

    unique_receipts, malformed_message_ids = deduplicate_messages(records)
    logger.info(
        "Processing %d unique receipts from %d messages (%d malformed)",
        len(unique_receipts),
        len(records),
        len(malformed_message_ids),
    )

    failed_message_ids: list[str] = list(malformed_message_ids)
    success_count = 0
    for (image_id, receipt_id), message_ids in unique_receipts.items():
        try:
            result = update_receipt_line_items(image_id, receipt_id)
            success_count += 1
            logger.info(
                "line items updated for %s:%d: %s",
                image_id[:8],
                receipt_id,
                result,
            )
        except (EntityError, OperationError, DynamoDBError) as e:
            logger.error(
                "failed line items for %s:%d: %s",
                image_id[:8],
                receipt_id,
                e,
                exc_info=True,
            )
            failed_message_ids.extend(message_ids)

    logger.info(
        "Completed: %d succeeded, %d failed",
        success_count,
        len(unique_receipts) - success_count,
    )
    return {
        "batchItemFailures": [
            {"itemIdentifier": msg_id} for msg_id in failed_message_ids
        ]
    }
