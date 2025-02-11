import os
import logging
import json
from dynamo import DynamoClient  # type: ignore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]

def convert_payload_to_dict(payload):
    """Convert all objects in the receipt details payload to dictionaries."""
    result = {}
    for key, detail in payload.items():
        result[key] = {
            "receipt": dict(detail["receipt"]),
            "words": [dict(word) for word in detail["words"]],
            "word_tags": [dict(tag) for tag in detail["word_tags"]]
        }
    return result

def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        client = DynamoClient(dynamodb_table_name)
        query_params = event.get("queryStringParameters") or {}
        
        limit = int(query_params.get("limit", 0))  # Default to 0 if not provided
        last_evaluated_key = None
        
        if "last_evaluated_key" in query_params:
            last_evaluated_key = json.loads(query_params["last_evaluated_key"])
        
        payload, last_evaluated_key = client.listReceiptDetails(
            limit if limit > 0 else None,
            last_evaluated_key
        )
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "payload": convert_payload_to_dict(payload),
                "last_evaluated_key": last_evaluated_key,
            }),
        }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
