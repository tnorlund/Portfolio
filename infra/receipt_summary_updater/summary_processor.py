"""Business logic for computing and upserting ReceiptSummary records.

This module fetches the required data from DynamoDB and computes
a new ReceiptSummary from ReceiptWordLabel and ReceiptWord records.
"""

import json
import logging
import os
from typing import Any

# receipt_dynamo ships in the Lambda layer; receipt_upload.tender is
# bundled into this Lambda's archive as a FileAsset referencing the
# canonical source (stdlib-only module, same pattern as the line-item
# updater's band-block decoder).
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_summary import ReceiptSummary
from receipt_dynamo.entities.receipt_summary_record import ReceiptSummaryRecord
from receipt_upload.tender import classify_tender_for_receipt

logger = logging.getLogger(__name__)

# Initialize DynamoDB client from environment variable (set by Pulumi)
TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
dynamo_client = DynamoClient(TABLE_NAME) if TABLE_NAME else None


def _total_line_ids(sections: list[Any] | None) -> list[int]:
    """TOTAL_LINE section line ids from dict or entity section records."""
    ids: list[int] = []
    for section in sections or []:
        if isinstance(section, dict):
            section_type = section.get("section_type")
            line_ids = section.get("line_ids") or []
        else:
            section_type = getattr(section, "section_type", None)
            line_ids = getattr(section, "line_ids", None) or []
        if section_type != "TOTAL_LINE":
            continue
        for line_id in line_ids:
            try:
                ids.append(int(line_id))
            except (TypeError, ValueError):
                continue
    return ids


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

    # Tombstone guard: the parent receipt must still exist. The Dynamo
    # stream fires on child-row deletions too, so when a re-segmentation
    # apply deletes a source receipt, the deletion events for its labels
    # would otherwise resurrect an orphan RECEIPT#N#SUMMARY row minutes
    # after the receipt is gone. Skip the recompute and clear any freshly
    # re-created orphan summary instead (self-healing: a later event for
    # the same deleted receipt sweeps whatever an earlier race left).
    #
    # A parent row that exists but does not parse as a Receipt (e.g. a
    # RESEGMENT_RESERVATION placeholder) raises OperationError, which
    # propagates so the SQS message is retried after the apply commits.
    try:
        dynamo_client.get_receipt(image_id, receipt_id)
    except EntityNotFoundError:
        orphan_summary_deleted = False
        try:
            orphan = dynamo_client.get_receipt_summary(image_id, receipt_id)
            dynamo_client.delete_receipt_summary(orphan)
            orphan_summary_deleted = True
        except EntityNotFoundError:
            pass
        logger.info(
            "Skipping summary regen for %s:%d: parent receipt no longer "
            "exists (orphan summary deleted: %s)",
            image_id[:8],
            receipt_id,
            orphan_summary_deleted,
        )
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "skipped": "parent receipt deleted",
            "orphan_summary_deleted": orphan_summary_deleted,
        }

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

    # Lines + sections feed the tender classifier's payment zone
    lines = dynamo_client.list_receipt_lines_from_receipt(image_id, receipt_id)
    sections = dynamo_client.get_receipt_sections_from_receipt(image_id, receipt_id)
    tender = classify_tender_for_receipt(lines, sections, word_labels, words)
    total_line_ids = _total_line_ids(sections)

    # Bank-match fields are computed OFFLINE (scripts/
    # backfill_tender_bank.py); carry them over from the stored summary
    # so a label-change recompute does not clobber them.
    ledger = bank_amount = bank_match_confidence = None
    try:
        existing = dynamo_client.get_receipt_summary(image_id, receipt_id)
        ledger = existing.ledger
        bank_amount = existing.bank_amount
        bank_match_confidence = existing.bank_match_confidence
    except EntityNotFoundError:
        pass

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

    # item_count: prefer the receipt's extracted ReceiptLineItem rows over
    # the legacy "count VALID LINE_TOTAL labels" rule. Receipts ingested
    # through the current pipeline never get LINE_TOTAL labels, so the
    # label rule reported 0 for receipts that hold real line items.
    #
    # Ordering caveat: a summary write is what triggers the line-item
    # updater (RECEIPT_SUMMARY -> LINE_ITEMS queue), so on a receipt's
    # FIRST summary write there are no rows yet and the count still falls
    # back to labels. It becomes correct on the next recompute (any label
    # or place change). A stream back-edge from RECEIPT_LINE_ITEM to this
    # queue is deliberately NOT added: the line-item updater
    # delete-then-inserts every row on each run, so that edge would be an
    # unbounded recompute loop.
    try:
        line_item_count = len(
            dynamo_client.get_receipt_line_items_from_receipt(image_id, receipt_id)
        )
    except EntityNotFoundError:
        line_item_count = 0

    # Compute summary from labels and words
    summary = ReceiptSummary.from_word_labels_and_words(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=merchant_name,
        word_labels=word_labels,
        words=words,
        tender_class=tender.tender_class,
        card_network=tender.card_network,
        card_last4=tender.card_last4,
        ledger=ledger,
        bank_amount=bank_amount,
        bank_match_confidence=bank_match_confidence,
        line_item_count=line_item_count,
        total_line_ids=total_line_ids,
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
        "tender_class": summary.tender_class,
        "card_network": summary.card_network,
        "card_last4": summary.card_last4,
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
