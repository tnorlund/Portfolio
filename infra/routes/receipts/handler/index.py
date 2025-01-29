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

    if http_method == "GET":
        client = DynamoClient(dynamodb_table_name)
        query_params = event.get("queryStringParameters") or {}
        if "limit" not in query_params:
            receipts = client.listReceipts()
            return {
                "statusCode": 200,
                "body": json.dumps(
                    [dict(receipt) for receipt in receipts]
                ),
            }
        else:
            limit = int(query_params["limit"])
            receipts = client.listReceipts(limit=limit)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    [dict(receipt) for receipt in receipts]
                ),
            }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
