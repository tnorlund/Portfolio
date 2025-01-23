import os
import logging
import json
from dynamo import DynamoClient  # type: ignore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def serialize_listImageDetails_payload(
    payload: dict[int, dict[str, object]]
) -> dict[int, dict[str, object]]:
    """
    Convert each Image/Line/Receipt object into a pure dict.
    """
    serialized = {}
    for image_id, data in payload.items():
        serialized[image_id] = {}

        # If there's an 'image' object, convert it to a dict
        if "image" in data:
            serialized[image_id]["image"] = dict(data["image"])

        # Convert the list of lines
        if "lines" in data:
            serialized[image_id]["lines"] = [dict(line) for line in data["lines"]]

        # Convert the list of receipts
        if "receipts" in data:
            serialized[image_id]["receipts"] = [
                dict(receipt) for receipt in data["receipts"]
            ]

    return serialized


def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        client = DynamoClient(dynamodb_table_name)
        query_params = event.get("queryStringParameters") or {}
        if "limit" not in query_params:
            payload, last_evaluated_key = client.listImageDetails()
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "payload": serialize_listImageDetails_payload(payload),
                        "last_evaluated_key": last_evaluated_key,
                    }
                ),
            }
        else:
            limit = int(query_params["limit"])
            if "last_evaluated_key" in query_params:
                last_evaluated_key = json.loads(query_params["last_evaluated_key"])
                payload, last_evaluated_key = client.listImageDetails(
                    limit, last_evaluated_key
                )
                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "payload": serialize_listImageDetails_payload(payload),
                            "last_evaluated_key": last_evaluated_key,
                        }
                    ),
                }
            payload, last_evaluated_key = client.listImageDetails(limit)
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "payload": serialize_listImageDetails_payload(payload),
                        "last_evaluated_key": last_evaluated_key,
                    }
                ),
            }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
