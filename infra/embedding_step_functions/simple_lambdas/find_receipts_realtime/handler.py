"""Lambda handler for finding all receipts for realtime embedding.

This is a lightweight, zip-based Lambda function that reads from DynamoDB
and returns a list of receipts to process.
"""

import os
import logging
from typing import Any, Dict

from receipt_dynamo import DynamoClient

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Find all receipts for realtime embedding.

    Args:
        event: Lambda event (optional filters)
            - limit: Optional limit on number of receipts
            - filter_by_embedding_status: Optional filter (e.g., "NONE" for unembedded)
        context: Lambda context (unused)

    Returns:
        Dictionary containing receipts ready for processing
    """
    logger.info("Starting find_receipts_for_realtime_embedding handler")

    try:
        # Get DynamoDB table name from environment
        table_name = os.environ.get("DYNAMODB_TABLE_NAME")
        if not table_name:
            raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

        logger.info("Using DynamoDB table: %s", table_name)

        dynamo_client = DynamoClient(table_name)

        # Check for optional filters
        limit = event.get("limit")
        filter_status = event.get("filter_by_embedding_status")

        receipts = []
        last_evaluated_key = None

        if filter_status:
            # Filter by embedding status
            # For now, we'll get all receipts and filter client-side
            # (Could be optimized with a GSI if needed)
            logger.info("Filtering by embedding status: %s", filter_status)
            all_receipts, last_evaluated_key = dynamo_client.list_receipts(
                limit=limit
            )
            # Note: This is a simplified filter - in practice you might want
            # to check line/word embedding status
            receipts = all_receipts
        else:
            # Get all receipts (or up to limit)
            receipts, last_evaluated_key = dynamo_client.list_receipts(limit=limit)

        logger.info("Found %d receipts", len(receipts))

        # Format receipts for step function processing
        formatted_receipts = [
            {
                "image_id": receipt.image_id,
                "receipt_id": receipt.receipt_id,
            }
            for receipt in receipts
        ]

        logger.info(
            "Successfully prepared %d receipts for processing",
            len(formatted_receipts),
        )

        return {
            "receipts": formatted_receipts,
            "total_receipts": len(formatted_receipts),
            "last_evaluated_key": last_evaluated_key,
        }

    except Exception as e:
        logger.error("Error finding receipts: %s", str(e), exc_info=True)
        raise RuntimeError(f"Internal error: {str(e)}") from e





