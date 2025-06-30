import json
import logging
import os
from typing import Any, Dict

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event: Dict[str, Any], _) -> Dict[str, Any]:
    """Lambda handler to list all receipts that need processing.

    Args:
        event: Lambda event data
        _: Lambda context object (unused)

    Returns:
        Dict containing the list of receipts to process
    """
    logger.info("Starting receipt listing process")
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Initialize DynamoDB client
        client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Query DynamoDB for all receipts
        receipts, _ = client.list_receipts()
        logger.info(f"Found {len(receipts)} receipts to process")

        # Format receipts for Step Functions
        receipt_ids = [
            {"receipt_id": receipt.receipt_id, "image_id": receipt.image_id}
            for receipt in receipts
        ]

        return {"statusCode": 200, "receipts": receipt_ids}
    except Exception as e:
        logger.error(f"Error listing receipts: {str(e)}")
        return {"statusCode": 500, "error": str(e)}
