"""Lambda handler for listing receipts with PENDING/NEEDS_REVIEW labels."""

import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event: Dict[str, Any], _) -> Dict[str, Any]:
    """
    List receipts that have PENDING or NEEDS_REVIEW labels.

    Args:
        event: Lambda event (optional "limit" key, optional "label_type" filter)

    Returns:
        {
            "statusCode": 200,
            "receipts": [
                {"image_id": "...", "receipt_id": 1, "pending_label_count": 5},
                ...
            ]
        }
    """
    logger.info("Listing receipts with PENDING/NEEDS_REVIEW labels")
    logger.info("Event: %s", json.dumps(event))

    client = DynamoClient(DYNAMODB_TABLE_NAME)
    limit = event.get("limit")
    label_type = event.get("label_type")  # Optional filter by label type

    # Query PENDING labels
    pending_labels = []
    try:
        labels, _ = client.list_receipt_word_labels_with_status(
            status=ValidationStatus.PENDING.value,
            limit=limit,
        )
        pending_labels.extend(labels)
        logger.info("Found %d PENDING labels", len(labels))
    except Exception as e:
        logger.warning("Error querying PENDING labels: %s", e)

    # Query NEEDS_REVIEW labels
    needs_review_labels = []
    try:
        labels, _ = client.list_receipt_word_labels_with_status(
            status=ValidationStatus.NEEDS_REVIEW.value,
            limit=limit,
        )
        needs_review_labels.extend(labels)
        logger.info("Found %d NEEDS_REVIEW labels", len(labels))
    except Exception as e:
        logger.warning("Error querying NEEDS_REVIEW labels: %s", e)

    all_labels = pending_labels + needs_review_labels

    # Filter by label type if specified
    if label_type:
        all_labels = [
            l for l in all_labels
            if l.label.upper() == label_type.upper()
        ]
        logger.info("Filtered to %d %s labels", len(all_labels), label_type)

    # Group by receipt
    receipts_dict = defaultdict(list)
    for label in all_labels:
        key = (label.image_id, label.receipt_id)
        receipts_dict[key].append(label)

    # Create receipt list
    receipts = []
    for (image_id, receipt_id), labels in receipts_dict.items():
        receipts.append({
            "image_id": image_id,
            "receipt_id": receipt_id,
            "pending_label_count": len(labels),
        })

    logger.info("Found %d receipts with PENDING/NEEDS_REVIEW labels", len(receipts))

    return {
        "statusCode": 200,
        "receipts": receipts,
        "total_receipts": len(receipts),
        "total_labels": len(all_labels),
    }

