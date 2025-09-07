import json
import logging
import os
from typing import Any, Dict

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event: Dict[str, Any], _):
    logger.info("Currency validation: listing receipts")
    logger.info(f"Event: {json.dumps(event)}")

    client = DynamoClient(DYNAMODB_TABLE_NAME)

    # Optionally support filtering via input, default all receipts
    try:
        receipts, _ = client.list_receipts()
        items = [
            {"receipt_id": r.receipt_id, "image_id": r.image_id}
            for r in receipts
        ]
        return {"statusCode": 200, "receipts": items}
    except Exception as e:
        logger.exception("Failed to list receipts")
        return {"statusCode": 500, "error": str(e)}


