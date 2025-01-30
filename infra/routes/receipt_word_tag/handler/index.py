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
        if "tag" in query_params:
            rwts = client.getReceiptWordTags(query_params["tag"])
            rws = client.getReceiptWordsByKeys([rwt.to_ReceiptWord_key() for rwt in rwts])
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "payload": [dict(rw) for rw in rws],
                    }
                ),
            }
        else:
            return {"statusCode": 400, "body": "Missing required query parameter 'tag'"}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
