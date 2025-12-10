"""
List Place IDs Handler (Zip Lambda)

Scans DynamoDB for all receipt metadatas and extracts unique place_ids.
Groups place_ids into batches for Step Functions Map processing.
"""

import logging
import os
from typing import Any, Dict, Set

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    List unique place_ids from DynamoDB and return batches.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "batch_size": 10  // Optional: place_ids per batch (default: 10)
    }

    Output:
    {
        "place_id_batches": [
            ["place_id_1", "place_id_2", ...],
            ["place_id_3", "place_id_4", ...],
            ...
        ],
        "total_place_ids": 25,
        "total_batches": 3
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_size = event.get("batch_size", 10)  # Default: 10 place_ids per batch
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    logger.info(
        "Listing place_ids for execution_id=%s, batch_size=%s",
        execution_id,
        batch_size,
    )

    dynamo = DynamoClient(table_name)

    # Collect unique place_ids
    place_ids: Set[str] = set()
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
                    # Only add valid string place_ids
                    if (
                        metadata.place_id
                        and isinstance(metadata.place_id, str)
                        and metadata.place_id.strip()
                    ):
                        place_ids.add(metadata.place_id)

                # Log progress every 1000 records
                if total_scanned % 1000 == 0:
                    logger.info(
                        "Scanned %s metadatas, found %s unique place_ids",
                        total_scanned,
                        len(place_ids),
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
        len(place_ids),
    )

    # Convert to sorted list for consistent batching
    place_ids_list = sorted(list(place_ids))

    # Group into batches
    batches = []
    for i in range(0, len(place_ids_list), batch_size):
        batch = place_ids_list[i : i + batch_size]
        batches.append(batch)

    logger.info("Created %s batches of place_ids", len(batches))

    result = {
        "place_id_batches": batches,
        "total_place_ids": len(place_ids_list),
        "total_batches": len(batches),
    }

    return result
