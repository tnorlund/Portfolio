import os
import logging
import json
from dynamo import DynamoClient  # type: ignore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]

def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    client = DynamoClient(dynamodb_table_name)
    query_params = event.get("queryStringParameters") or {}

    if http_method == "GET":
        if "limit" not in query_params:
            receipts, _ = client.listReceipts()
            return {
                "statusCode": 200,
                "body": json.dumps([dict(receipt) for receipt in receipts]),
            }
        else:
            limit = int(query_params["limit"])
            receipts, last_evaluated_key = client.listReceipts(limit=limit)
            response_body = {
                "receipts": [dict(receipt) for receipt in receipts],
                "lastEvaluatedKey": last_evaluated_key  # may be None if no more pages exist
            }
            return {
                "statusCode": 200,
                "body": json.dumps(response_body),
            }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}