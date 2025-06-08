import json
import logging
import os

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        client = DynamoClient(DYNAMODB_TABLE_NAME)
        query_params = event.get("queryStringParameters") or {}

        image_type = query_params.get("image_type")
        if not image_type:
            return {
                "statusCode": 400,
                "body": "image_type query parameter is required",
            }

        limit_param = query_params.get("limit")
        limit = int(limit_param) if limit_param is not None else None

        last_evaluated_key = None
        if "lastEvaluatedKey" in query_params:
            try:
                last_evaluated_key = json.loads(
                    query_params["lastEvaluatedKey"]
                )
            except json.JSONDecodeError:
                logger.error("Error decoding lastEvaluatedKey; ignoring it.")
                last_evaluated_key = None

        images, lek = client.listImagesByType(
            image_type=image_type,
            limit=limit,
            lastEvaluatedKey=last_evaluated_key,
        )
        response_body = {
            "images": [dict(i) for i in images],
            "lastEvaluatedKey": lek,
        }
        return {"statusCode": 200, "body": json.dumps(response_body)}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
