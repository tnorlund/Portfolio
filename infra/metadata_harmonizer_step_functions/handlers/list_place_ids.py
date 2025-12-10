"""
List Place IDs Handler (Zip Lambda)

Scans DynamoDB for all receipt metadatas and extracts unique place_ids.
Groups place_ids into batches for Step Functions Map processing.

For large place_id groups (e.g., >20 receipts), splits them into sub-batches
to prevent Lambda timeouts and memory issues.
"""

import logging
import os
from collections import defaultdict
from typing import Any, Dict, Set

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    List unique place_ids from DynamoDB and return batches.

    For large place_id groups (e.g., >max_receipts_per_batch), splits them
    into sub-batches to prevent Lambda timeouts.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "batch_size": 10,  // Optional: place_ids per batch (default: 10)
        "max_receipts_per_batch": 20  // Optional: max receipts per batch (default: 20)
    }

    Output:
    {
        "place_id_batches": [
            ["place_id_1", "place_id_2", ...],
            ["place_id_3", "place_id_4", ...],
            ["place_id_large:0", ...],  // Large group split into sub-batches
            ["place_id_large:1", ...],
            ...
        ],
        "total_place_ids": 25,
        "total_batches": 3,
        "large_groups_split": 2  // Number of large groups that were split
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_size = event.get("batch_size", 10)  # Default: 10 place_ids per batch
    max_receipts_per_batch = event.get(
        "max_receipts_per_batch", 20
    )  # Default: 20 receipts per batch
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    logger.info(
        "Listing place_ids for execution_id=%s, batch_size=%s, max_receipts_per_batch=%s",
        execution_id,
        batch_size,
        max_receipts_per_batch,
    )

    dynamo = DynamoClient(table_name)

    # Collect place_ids and count receipts per place_id
    place_id_counts: Dict[str, int] = defaultdict(int)
    last_evaluated_key = None
    total_scanned = 0

    try:
        # List all receipt metadatas to find unique place_ids
        # Catch validation errors for malformed records and skip those batches
        from receipt_dynamo.data.base_operations.error_handling import (
            OperationError,
        )

        while True:
            try:
                metadatas, last_evaluated_key = dynamo.list_receipt_metadatas(
                    limit=1000, last_evaluated_key=last_evaluated_key
                )

                for metadata in metadatas:
                    total_scanned += 1
                    # Count receipts per place_id
                    if (
                        metadata.place_id
                        and isinstance(metadata.place_id, str)
                        and metadata.place_id.strip()
                    ):
                        place_id_counts[metadata.place_id] += 1

                # Log progress every 1000 records
                if total_scanned % 1000 == 0:
                    logger.info(
                        "Scanned %s metadatas, found %s unique place_ids",
                        total_scanned,
                        len(place_id_counts),
                    )

                # Break if no more pages
                if last_evaluated_key is None:
                    break

            except OperationError as e:
                # If validation fails on a batch (malformed place_id), log and skip
                # This can happen if some records have invalid place_id types
                logger.warning(
                    "Validation error in batch (skipping): %s", str(e)[:200]
                )
                # If we have a last_evaluated_key, we can try to continue
                # Otherwise, we've hit the end or can't continue
                if last_evaluated_key is None:
                    logger.warning(
                        "Cannot continue after validation error - no pagination key. "
                        "Some records may have been skipped."
                    )
                    break
                # Continue to next iteration to try next page
                continue

    except Exception as e:
        logger.error("Error scanning metadatas: %s", e)
        raise

    logger.info(
        "Finished scanning: %s metadatas, %s unique place_ids",
        total_scanned,
        len(place_id_counts),
    )

    # Identify large groups that need splitting
    large_groups = {
        pid: count
        for pid, count in place_id_counts.items()
        if count > max_receipts_per_batch
    }
    small_groups = {
        pid: count
        for pid, count in place_id_counts.items()
        if count <= max_receipts_per_batch
    }

    if large_groups:
        logger.info(
            "Found %s large place_id groups (>%s receipts) that will be split: %s",
            len(large_groups),
            max_receipts_per_batch,
            {pid: count for pid, count in list(large_groups.items())[:5]},
        )

    # Create batches
    batches = []
    large_groups_split = 0

    # First, handle small groups (batch by place_id count)
    small_place_ids = sorted(small_groups.keys())
    for i in range(0, len(small_place_ids), batch_size):
        batch = small_place_ids[i : i + batch_size]
        batches.append(batch)

    # Then, handle large groups (split by receipt count)
    for place_id, receipt_count in sorted(
        large_groups.items(), key=lambda x: -x[1]
    ):  # Process largest first
        # Split into sub-batches
        num_sub_batches = (
            receipt_count + max_receipts_per_batch - 1
        ) // max_receipts_per_batch
        large_groups_split += 1

        logger.info(
            "Splitting place_id %s (%s receipts) into %s sub-batches",
            place_id[:16],
            receipt_count,
            num_sub_batches,
        )

        # Create sub-batches: place_id:0, place_id:1, etc.
        # The Lambda handler will need to handle this special format
        for sub_batch_idx in range(num_sub_batches):
            # Use special format: "place_id:sub_batch_idx" to indicate this is a sub-batch
            # The Lambda handler will parse this and process only that portion
            sub_batch_place_id = f"{place_id}:{sub_batch_idx}"
            batches.append([sub_batch_place_id])

    logger.info(
        "Created %s batches: %s from small groups, %s from large groups (split)",
        len(batches),
        len(small_place_ids) // batch_size
        + (1 if len(small_place_ids) % batch_size else 0),
        large_groups_split,
    )

    result = {
        "place_id_batches": batches,
        "total_place_ids": len(place_id_counts),
        "total_batches": len(batches),
        "large_groups_split": large_groups_split,
        "max_receipts_per_batch": max_receipts_per_batch,
    }

    return result
