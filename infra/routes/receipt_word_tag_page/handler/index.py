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
            # Assume getReceiptWordTagsByTag returns a tuple: (list_of_tags, lastEvaluatedKey)
            rwts, lek = client.getReceiptWordTags(tag, limit=limit, lastEvaluatedKey=lastEvaluatedKey)
            
            # Convert the receipt word tags into receipt word keys, then fetch the full receipt words.
            # (You may already have a helper for this.)
            rws = client.getReceiptWordsByKeys([rwt.to_ReceiptWord_key() for rwt in rwts])
            
            response_body = {
                "words": [dict(rw) for rw in rws],
                "lastEvaluatedKey": lek  # This will be None if there are no more pages.
            }
            return {
                "statusCode": 200,
                "body": json.dumps(response_body)
            }
        else:
            return {"statusCode": 400, "body": "Missing required query parameter 'tag'"}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}