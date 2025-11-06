"""Lambda handler for retrieving a random receipt with words and labels."""

import json
import logging
import os
import random

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    OperationError,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
QUERY_LIMIT = 1000  # Sample size for random selection


def handler(event, _context):
    """Handle API Gateway requests for random receipt details.

    Args:
        event: API Gateway event containing HTTP request details
        context: Lambda context (unused but required by Lambda)

    Returns:
        dict: HTTP response with receipt details or error
    """
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        return handle_get_request()

    if http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    return {"statusCode": 405, "body": f"Method {http_method} not allowed"}


def handle_get_request():
    """Handle GET request to retrieve random receipt with words and labels.

    Returns:
        dict: HTTP response with receipt details or error
    """
    try:
        client = DynamoClient(dynamodb_table_name)

        # Get a sample of receipts for random selection
        receipts, lek = client.list_receipts(limit=QUERY_LIMIT)

        # Continue pagination if needed (up to QUERY_LIMIT total)
        while lek and len(receipts) < QUERY_LIMIT:
            remaining = QUERY_LIMIT - len(receipts)
            next_receipts, lek = client.list_receipts(
                limit=min(remaining, QUERY_LIMIT),
                last_evaluated_key=lek,
            )
            receipts.extend(next_receipts)

        # Check if we found any receipts
        if not receipts:
            return {
                "statusCode": 404,
                "body": "No receipts found",
            }

        # Randomly select one receipt
        selected_receipt = random.choice(receipts)

        # Get full details including words and labels
        receipt_details = client.get_receipt_details(
            selected_receipt.image_id,
            selected_receipt.receipt_id,
        )

        # Return structured response
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "receipt": dict(receipt_details.receipt),
                    "words": [dict(w) for w in receipt_details.words],
                    "labels": [dict(l) for l in receipt_details.labels],
                },
                default=str,  # Handle datetime serialization
            ),
        }
    except EntityNotFoundError as exc:
        logger.error("Receipt not found: %s", exc)
        return {
            "statusCode": 404,
            "body": f"Receipt not found: {str(exc)}",
        }
    except OperationError as exc:
        logger.error("Database operation failed: %s", exc)
        return {
            "statusCode": 500,
            "body": f"Database error: {str(exc)}",
        }
    except (KeyError, ValueError) as exc:
        logger.error("Data processing error: %s", exc)
        return {
            "statusCode": 500,
            "body": f"Data processing error: {str(exc)}",
        }
    except Exception as exc:
        logger.error("Unexpected error: %s", exc, exc_info=True)
        return {
            "statusCode": 500,
            "body": f"Internal server error: {str(exc)}",
        }

