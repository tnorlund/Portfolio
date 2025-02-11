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
        try:
            client = DynamoClient(dynamodb_table_name)
            
            # Parse the request body
            body = json.loads(event.get("body", "{}"))
            if not isinstance(body, list):
                return {"statusCode": 400, "body": "Request body must be an array of word tag updates"}

            # Expected format of each item:
            # {
            #   "receiptId": string,
            #   "wordId": string,
            #   "tag": string
            # }

            # Validate the request body
            for item in body:
                if not all(k in item for k in ("receiptId", "wordId", "tag")):
                    return {
                        "statusCode": 400,
                        "body": "Each item must contain receiptId, wordId, and tag"
                    }

            # Process the updates
            updated_items = []
            for item in body:
                rwt = client.putReceiptWordTag(
                    receipt_id=item["receiptId"],
                    word_id=item["wordId"],
                    tag=item["tag"]
                )
                updated_items.append(dict(rwt))

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "message": f"Successfully updated {len(updated_items)} tags",
                    "updated": updated_items
                })
            }

        except json.JSONDecodeError:
            return {"statusCode": 400, "body": "Invalid JSON in request body"}
        except Exception as e:
            logger.error("Error processing request: %s", str(e))
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
