"""Lambda handler for processing receipt summary update messages.

This Lambda is triggered by SQS messages when ReceiptWordLabel or
ReceiptPlace records are modified. It recomputes and upserts the
corresponding ReceiptSummary records.
"""

import logging
import os
from typing import Any

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
logger = logging.getLogger(__name__)

# Import processor functions (available via Lambda layer)
from summary_processor import deduplicate_messages, update_receipt_summary


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process receipt summary update messages from SQS.

    Deduplicates messages by (image_id, receipt_id) to avoid redundant
    processing when multiple labels change for the same receipt.

    Args:
        event: SQS event containing Records list.
        context: Lambda context object.

    Returns:
        SQSBatchResponse with batchItemFailures for partial batch failures.
    """
    records = event.get("Records", [])
    if not records:
        logger.info("No records to process")
        return {"batchItemFailures": []}

    logger.info("Processing %d SQS messages", len(records))

    # Deduplicate by (image_id, receipt_id)
    unique_receipts = deduplicate_messages(records)
    logger.info(
        "Deduplicated to %d unique receipts from %d messages",
        len(unique_receipts),
        len(records),
    )

    # Track failed message IDs for batch item failure reporting
    failed_message_ids: list[str] = []
    success_count = 0

    # Process each unique receipt
    for (image_id, receipt_id), message_ids in unique_receipts.items():
        try:
            result = update_receipt_summary(image_id, receipt_id)
            success_count += 1
            logger.debug(
                "Successfully updated summary for %s:%d: %s",
                image_id[:8],
                receipt_id,
                result,
            )
        except Exception as e:
            logger.error(
                "Failed to update summary for %s:%d: %s",
                image_id[:8],
                receipt_id,
                e,
                exc_info=True,
            )
            # Mark all message IDs for this receipt as failed
            failed_message_ids.extend(message_ids)

    logger.info(
        "Completed processing: %d succeeded, %d failed",
        success_count,
        len(unique_receipts) - success_count,
    )

    # Return batch item failures for SQS to retry
    return {
        "batchItemFailures": [
            {"itemIdentifier": msg_id} for msg_id in failed_message_ids
        ]
    }
