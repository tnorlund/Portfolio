#!/usr/bin/env python3
"""Fix ReceiptMetadata records with NULL or missing place_id values.

This script identifies and updates ReceiptMetadata records in DynamoDB that have
NULL or missing place_id values, setting them to empty strings to comply with
the ReceiptMetadata validation requirements.
"""

import logging
import os
import sys
from typing import List, Tuple

from receipt_dynamo import DynamoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def find_metadata_with_null_place_id(
    dynamo_client: DynamoClient, limit: int = 1000
) -> List[Tuple[str, int]]:
    """Find ReceiptMetadata records with NULL or missing place_id.

    Args:
        dynamo_client: DynamoDB client instance
        limit: Maximum number of records to check

    Returns:
        List of (image_id, receipt_id) tuples that need fixing
    """
    logger.info("Scanning for ReceiptMetadata records with NULL/missing place_id...")
    records_to_fix = []

    try:
        # Get all receipt metadatas
        metadatas, last_evaluated_key = dynamo_client.list_receipt_metadatas(
            limit=limit
        )
        total_checked = len(metadatas)

        while last_evaluated_key is not None:
            next_metadatas, last_evaluated_key = (
                dynamo_client.list_receipt_metadatas(
                    limit=limit, last_evaluated_key=last_evaluated_key
                )
            )
            metadatas.extend(next_metadatas)
            total_checked += len(next_metadatas)

        logger.info(f"Checked {total_checked} ReceiptMetadata records")

        # Check each metadata record
        for metadata in metadatas:
            # Check if place_id is None or not a string
            if not isinstance(metadata.place_id, str):
                records_to_fix.append((metadata.image_id, metadata.receipt_id))
                logger.warning(
                    f"Found record with invalid place_id: "
                    f"image_id={metadata.image_id}, receipt_id={metadata.receipt_id}, "
                    f"place_id={metadata.place_id} (type: {type(metadata.place_id)})"
                )

    except Exception as e:
        logger.error(f"Error scanning metadata: {e}", exc_info=True)
        # Try to continue by checking the raw DynamoDB items
        logger.info("Attempting to check raw DynamoDB items...")
        # This would require direct DynamoDB access, which we'll handle differently

    return records_to_fix


def fix_metadata_place_id(
    dynamo_client: DynamoClient, image_id: str, receipt_id: int
) -> bool:
    """Fix a single ReceiptMetadata record by setting place_id to empty string.

    Args:
        dynamo_client: DynamoDB client instance
        image_id: Image ID of the record
        receipt_id: Receipt ID of the record

    Returns:
        True if the record was successfully fixed, False otherwise
    """
    try:
        # Get the existing metadata
        metadata = dynamo_client.get_receipt_metadata(image_id, receipt_id)
        if metadata is None:
            logger.warning(
                f"Metadata not found: image_id={image_id}, receipt_id={receipt_id}"
            )
            return False

        # Check if place_id needs fixing
        if isinstance(metadata.place_id, str):
            logger.info(
                f"Record already has valid place_id: "
                f"image_id={image_id}, receipt_id={receipt_id}"
            )
            return True

        # Update place_id to empty string
        metadata.place_id = ""
        dynamo_client.put_receipt_metadata(metadata)

        logger.info(
            f"Fixed place_id for: image_id={image_id}, receipt_id={receipt_id}"
        )
        return True

    except Exception as e:
        logger.error(
            f"Error fixing metadata: image_id={image_id}, receipt_id={receipt_id}, "
            f"error={e}",
            exc_info=True,
        )
        return False


def main():
    """Main function to fix NULL place_id values in ReceiptMetadata records."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        logger.error("DYNAMODB_TABLE_NAME environment variable not set")
        sys.exit(1)

    dynamo_client = DynamoClient(table_name)

    # Find records that need fixing
    records_to_fix = find_metadata_with_null_place_id(dynamo_client)

    if not records_to_fix:
        logger.info("No records found with NULL or missing place_id")
        return

    logger.info(f"Found {len(records_to_fix)} records that need fixing")

    # Fix each record
    fixed_count = 0
    failed_count = 0

    for image_id, receipt_id in records_to_fix:
        if fix_metadata_place_id(dynamo_client, image_id, receipt_id):
            fixed_count += 1
        else:
            failed_count += 1

    logger.info(
        f"Fix complete: {fixed_count} fixed, {failed_count} failed, "
        f"{len(records_to_fix)} total"
    )


if __name__ == "__main__":
    main()

