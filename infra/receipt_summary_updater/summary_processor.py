"""Business logic for computing and upserting ReceiptSummary records.

This module fetches the required data from DynamoDB and computes
a new ReceiptSummary from ReceiptWordLabel and ReceiptWord records.
"""

import json
import logging
import os
from typing import Any

# These imports are available via Lambda layer
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_summary import ReceiptSummary
from receipt_dynamo.entities.receipt_summary_record import ReceiptSummaryRecord

logger = logging.getLogger(__name__)

# Initialize DynamoDB client from environment variable (set by Pulumi)
TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
dynamo_client = DynamoClient(TABLE_NAME) if TABLE_NAME else None


def update_receipt_summary(image_id: str, receipt_id: int) -> dict[str, Any]:
    """Recompute and upsert ReceiptSummary for a receipt.

    Fetches ReceiptWordLabel and ReceiptWord records, optionally
    ReceiptPlace for merchant name, then computes and upserts
    the summary.

    Args:
        image_id: UUID of the image containing the receipt.
        receipt_id: ID of the receipt within the image.

    Returns:
        Dictionary with summary details for logging.

    Raises:
        ValueError: If DYNAMODB_TABLE_NAME environment variable is not set.
    """
    if dynamo_client is None:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    # Fetch all word labels with pagination
    word_labels = []
    last_key = None
    while True:
        page_labels, last_key = dynamo_client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id, last_evaluated_key=last_key
        )
        word_labels.extend(page_labels)
        if last_key is None:
            break

    words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)

    # Try to get merchant name from ReceiptPlace
    merchant_name: str | None = None
    merchant_category: str | None = None
    try:
        place = dynamo_client.get_receipt_place(image_id, receipt_id)
        merchant_name = place.merchant_name
        merchant_category = getattr(place, "merchant_category", None)
    except EntityNotFoundError:
        logger.debug(
            "No ReceiptPlace found for %s:%d, merchant_name will be None",
            image_id,
            receipt_id,
        )

    # Compute summary from labels and words
    summary = ReceiptSummary.from_word_labels_and_words(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
        word_labels=word_labels,
        words=words,
    )

    # Convert to record and upsert
    record = ReceiptSummaryRecord.from_summary(summary)
    dynamo_client.upsert_receipt_summary(record)

    result = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "merchant_category": merchant_category,
        "grand_total": summary.grand_total,
        "tax": summary.tax,
        "item_count": summary.item_count,
        "date": summary.date.isoformat() if summary.date else None,
    }

    logger.info(
        "Updated ReceiptSummary: %s:%d total=$%s",
        image_id[:8],
        receipt_id,
        summary.grand_total,
    )

    return result


def deduplicate_messages(
    records: list[dict[str, Any]],
) -> tuple[dict[tuple[str, int], list[str]], list[str]]:
    """Deduplicate SQS messages by (image_id, receipt_id).

    Groups message IDs by receipt key so we can process each receipt
    once but track all message IDs for batch item failure reporting.

    Args:
        records: List of SQS record dictionaries from the event.

    Returns:
        Tuple of:
        - Dictionary mapping (image_id, receipt_id) to list of message IDs.
        - List of message IDs that failed to parse or were malformed.
    """
    grouped: dict[tuple[str, int], list[str]] = {}
    malformed_message_ids: list[str] = []

    for record in records:
        message_id = record.get("messageId", "")
        try:
            body = json.loads(record.get("body", "{}"))
            entity_data = body.get("entity_data", {})
            image_id = entity_data.get("image_id")
            receipt_id = entity_data.get("receipt_id")

            if image_id and receipt_id is not None:
                key = (image_id, int(receipt_id))
                if key not in grouped:
                    grouped[key] = []
                grouped[key].append(message_id)
            else:
                logger.warning(
                    "Message %s missing image_id or receipt_id: %s",
                    message_id,
                    entity_data,
                )
                malformed_message_ids.append(message_id)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.exception("Failed to parse message %s", message_id)
            malformed_message_ids.append(message_id)

    return grouped, malformed_message_ids
