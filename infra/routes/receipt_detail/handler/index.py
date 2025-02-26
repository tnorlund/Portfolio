import os
import logging
import json
from typing import Any, Dict
from receipt_dynamo import DynamoClient  # type: ignore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]

def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        # Get query parameters
        query_params = event.get("queryStringParameters") or {}
        image_id = query_params.get("image_id")
        receipt_id = query_params.get("receipt_id")

        if not image_id or not receipt_id:
            return {
                "statusCode": 400,
                "body": json.dumps({
                    "error": "Missing required query parameters: image_id and receipt_id"
                })
            }

        try:
            # Convert receipt_id to int
            receipt_id = int(receipt_id)
            
            # Initialize DynamoDB client and get receipt details
            client = DynamoClient(dynamodb_table_name)
            receipt, lines, words, letters, tags, validations, initial_taggings = (
                client.getReceiptDetails(image_id, receipt_id)
            )

            # Convert objects to dictionaries and structure the response
            response = {
                "receipt": dict(receipt),
                "words": [dict(word) for word in words],
                "tags": [dict(tag) for tag in tags],
            }

            return {
                "statusCode": 200,
                "body": json.dumps(response)
            }

        except ValueError as e:
            return {
                "statusCode": 404,
                "body": json.dumps({
                    "error": str(e)
                })
            }
        except Exception as e:
            logger.error("Error processing request: %s", str(e))
            return {
                "statusCode": 500,
                "body": json.dumps({
                    "error": "Internal server error"
                })
            }
    
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
