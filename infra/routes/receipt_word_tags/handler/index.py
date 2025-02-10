import os
import logging
import json
from dynamo import DynamoClient
import random


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, context):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        try:
            client = DynamoClient(dynamodb_table_name)
            query_params = event.get("queryStringParameters") or {}

            if "tag" in query_params:
                tag = query_params["tag"]
                # Set a default page size (limit) of 5 if not provided.
                limit = int(query_params.get("limit", 5))

                # If a lastEvaluatedKey is provided, decode it from JSON.
                lastEvaluatedKey = None
                if "lastEvaluatedKey" in query_params:
                    try:
                        lastEvaluatedKey = json.loads(query_params["lastEvaluatedKey"])
                    except json.JSONDecodeError:
                        logger.error("Error decoding lastEvaluatedKey; ignoring it.")
                        lastEvaluatedKey = None

                # Query the DynamoDB GSI for words with the specified tag, paginated.
                rwts, lek = client.getReceiptWordTags(tag, limit=limit, lastEvaluatedKey=lastEvaluatedKey)
                rws = client.getReceiptWordsByKeys([rwt.to_ReceiptWord_key() for rwt in rwts])

                # Combine the rwts and rws into a single list of dictionaries
                combined = []
                for rwt, rw in zip(rwts, rws):
                    combined.append({
                        "word": dict(rw),
                        "tag": dict(rwt),
                    })
                response_body = {
                    "payload": combined,
                    "lastEvaluatedKey": lek  # This will be None if there are no more pages.
                }
                return {
                    "statusCode": 200,
                    "body": json.dumps(response_body),
                }
            else:
                return {"statusCode": 400, "body": "Missing required query parameter 'tag'"}
        except Exception as e:
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
