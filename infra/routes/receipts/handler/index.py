import json
import logging
import os

from _api_dynamo import get_api_dynamo_client
from _lambda_profiler import profile_handler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


@profile_handler
def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        query_params = event.get("queryStringParameters") or {}

        # Check for an optional 'limit'
        limit = query_params.get("limit")
        if limit is not None:
            limit = int(limit)
            if limit <= 0:
                return {
                    "statusCode": 400,
                    "body": "limit must be a positive integer",
                }

        # Check for an optional 'lastEvaluatedKey'
        lastEvaluatedKey = None
        if "lastEvaluatedKey" in query_params:
            try:
                lastEvaluatedKey = json.loads(query_params["lastEvaluatedKey"])
            except json.JSONDecodeError:
                logger.error("Error decoding lastEvaluatedKey; ignoring it.")
                lastEvaluatedKey = None

        client = get_api_dynamo_client(dynamodb_table_name)
        # Call listReceipts with the provided parameters.
        receipts, lek = client.list_receipts(
            limit=limit, last_evaluated_key=lastEvaluatedKey
        )

        response_body = {
            "receipts": [dict(r) for r in receipts],
            "lastEvaluatedKey": lek,  # Will be None if there are no more pages.
        }
        return {"statusCode": 200, "body": json.dumps(response_body)}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
