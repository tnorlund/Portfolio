"""
List Receipts Handler (Zip Lambda)

Scans DynamoDB for all receipts and returns batches for Step Functions Map processing.
Groups receipts into batches for parallel processing.

For large batches, can split them to prevent Lambda timeouts.
"""

import logging
import os
from typing import Any, Dict, List, Tuple

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    List receipts from DynamoDB and return batches.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "batch_size": 50,  // Optional: receipts per batch (default: 50)
        "limit": null  // Optional: limit total receipts (for testing)
    }

    Output:
    {
        "receipt_batches": [
            [{"image_id": "img1", "receipt_id": 1}, {"image_id": "img2", "receipt_id": 1}, ...],
            [{"image_id": "img3", "receipt_id": 1}, ...],
            ...
        ],
        "total_receipts": 150,
        "total_batches": 3
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_size = event.get("batch_size", 50)  # Default: 50 receipts per batch
    limit = event.get("limit")  # Optional limit for testing
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    logger.info(
        "Listing receipts for execution_id=%s, batch_size=%s, limit=%s",
        execution_id,
        batch_size,
        limit,
    )

    dynamo = DynamoClient(table_name)

    # Collect receipts
    receipts: List[Tuple[str, int]] = []
    last_evaluated_key = None
    total_scanned = 0

    try:
        # List all receipt metadatas to get receipt keys
        while True:
            metadatas, last_evaluated_key = dynamo.list_receipt_metadatas(
                limit=1000, last_evaluated_key=last_evaluated_key
            )

            for metadata in metadatas:
                total_scanned += 1
                receipts.append((metadata.image_id, metadata.receipt_id))

                # Apply limit if specified
                if limit and len(receipts) >= limit:
                    break

            # Break if limit reached or no more pages
            if (
                limit and len(receipts) >= limit
            ) or last_evaluated_key is None:
                break

    except Exception as e:
        logger.error("Error scanning receipts: %s", e)
        raise

    logger.info(
        "Finished scanning: %s metadatas, %s receipts",
        total_scanned,
        len(receipts),
    )

    # Create batches
    batches = []
    for i in range(0, len(receipts), batch_size):
        batch = [
            {"image_id": image_id, "receipt_id": receipt_id}
            for image_id, receipt_id in receipts[i : i + batch_size]
        ]
        batches.append(batch)

    logger.info(
        "Created %s batches: %s receipts total",
        len(batches),
        len(receipts),
    )

    result = {
        "receipt_batches": batches,
        "total_receipts": len(receipts),
        "total_batches": len(batches),
    }

    return result
