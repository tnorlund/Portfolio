import json
import logging
import os
from typing import Any, Dict

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    ReceiptDynamoError,
    EntityValidationError,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event: Dict[str, Any], _):
    """
    Lambda handler to list all receipts for label creation/update.
    """
    logger.info("Create labels: listing receipts")
    logger.info("Event: %s", json.dumps(event))

    client = DynamoClient(DYNAMODB_TABLE_NAME)

    try:
        receipts, _ = client.list_receipts()
        items = [
            {"receipt_id": r.receipt_id, "image_id": r.image_id}
            for r in receipts
        ]
        return {"statusCode": 200, "receipts": items}
    except EntityValidationError as e:
        logger.warning("Invalid list_receipts parameters: %s", e)
        raise
    except ReceiptDynamoError as e:
        logger.error("Dynamo error during list_receipts: %s", e)
        raise

