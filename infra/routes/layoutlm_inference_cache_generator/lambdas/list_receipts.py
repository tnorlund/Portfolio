"""Lambda handler for listing random receipts with VALID labels.

This Lambda is used by the Step Function to get a list of receipts
that should be processed for the LayoutLM inference cache pool.
"""

import json
import logging
import os
import random
from typing import Any, Dict, List, Set, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]

# Configuration
DEFAULT_TARGET_COUNT = 50  # Target number of receipts to cache
BATCH_SIZE = 10  # Number of receipts per inference batch


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """List random receipts with VALID labels for batch inference.

    Args:
        event: Step Function input, may contain:
            - target_count: Number of receipts to select (default: 50)
            - batch_size: Number of receipts per batch (default: 10)
        _context: Lambda context (unused)

    Returns:
        dict: Contains:
            - receipts: List of {image_id, receipt_id} pairs
            - batches: Receipts grouped into batches for parallel processing
            - total_count: Total number of receipts selected
            - batch_count: Number of batches
    """
    logger.info("Starting list receipts for batch inference")
    logger.info("Event: %s", json.dumps(event))

    # Parse configuration from event
    target_count = event.get("target_count", DEFAULT_TARGET_COUNT)
    batch_size = event.get("batch_size", BATCH_SIZE)

    try:
        # Initialize DynamoDB client
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Get unique receipts that have VALID labels (2x target for random selection)
        min_needed = target_count * 2
        logger.info("Finding at least %d receipts with VALID labels", min_needed)
        receipt_ids = _get_receipts_with_valid_labels(dynamo_client, min_needed)

        if not receipt_ids:
            logger.warning("No receipts with VALID labels found")
            return {
                "receipts": [],
                "batches": [],
                "total_count": 0,
                "batch_count": 0,
                "error": "No receipts with VALID labels found",
            }

        logger.info("Found %d unique receipts with VALID labels", len(receipt_ids))

        # Randomly select target_count receipts
        selected_count = min(target_count, len(receipt_ids))
        selected_receipts = random.sample(receipt_ids, selected_count)

        logger.info(
            "Selected %d receipts from %d available",
            selected_count,
            len(receipt_ids),
        )

        # Convert to list of dicts for JSON serialization
        receipts_list = [
            {"image_id": image_id, "receipt_id": receipt_id}
            for image_id, receipt_id in selected_receipts
        ]

        # Group into batches for parallel processing
        batches = _create_batches(receipts_list, batch_size)

        logger.info(
            "Created %d batches of size %d",
            len(batches),
            batch_size,
        )

        return {
            "receipts": receipts_list,
            "batches": batches,
            "total_count": len(receipts_list),
            "batch_count": len(batches),
        }

    except Exception as e:
        logger.error("Error listing receipts: %s", e, exc_info=True)
        return {
            "receipts": [],
            "batches": [],
            "total_count": 0,
            "batch_count": 0,
            "error": str(e),
        }


def _get_receipts_with_valid_labels(
    dynamo_client: DynamoClient,
    min_receipts: int,
) -> List[Tuple[str, int]]:
    """Get unique receipt IDs that have at least one VALID label.

    Paginates through VALID labels using GSI3, stopping early once we have
    enough unique receipts.

    Args:
        dynamo_client: DynamoDB client instance
        min_receipts: Minimum number of unique receipts needed

    Returns:
        List of (image_id, receipt_id) tuples
    """
    unique_receipts: Set[Tuple[str, int]] = set()
    last_key = None

    while len(unique_receipts) < min_receipts:
        # Query VALID labels using GSI3 (indexed by validation status)
        labels, last_key = dynamo_client.list_receipt_word_labels_with_status(
            ValidationStatus.VALID,
            last_evaluated_key=last_key,
        )

        # Extract unique receipt IDs from this page
        for label in labels:
            unique_receipts.add((label.image_id, label.receipt_id))

        logger.info(
            "Processed page of labels, unique receipts so far: %d",
            len(unique_receipts),
        )

        # Stop if no more pages
        if not last_key:
            break

    return list(unique_receipts)


def _create_batches(
    items: List[Dict[str, Any]],
    batch_size: int,
) -> List[List[Dict[str, Any]]]:
    """Split items into batches of specified size.

    Args:
        items: List of items to batch
        batch_size: Maximum items per batch

    Returns:
        List of batches, each containing up to batch_size items
    """
    batches = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batches.append(batch)
    return batches
