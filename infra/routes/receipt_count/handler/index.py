import os
import logging
import json
from receipt_dynamo import DynamoClient
import random


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, context):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        try:
            # Use the client to list the first 50 images
            client = DynamoClient(dynamodb_table_name)

            # Paginate through all images to get the final count
            receipts, lek = client.listReceipts(50)
            while lek:
                next_receipts, lek = client.listReceipts(50, lek)
                receipts.extend(next_receipts)

            return {
                "statusCode": 200,
                "body": len(receipts),
            }
        except Exception as e:
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
