import json
import logging
import os
import random

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
QUERY_LIMIT = 500


def handler(event, context):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        try:
            # Use the client to list the first 50 images
            client = DynamoClient(dynamodb_table_name)

            # Paginate through all images to get the final count
            images, lek = client.list_images(QUERY_LIMIT)
            while lek:
                next_images, lek = client.list_images(QUERY_LIMIT, lek)
                images.extend(next_images)

            return {
                "statusCode": 200,
                "body": len(images),
            }
        except Exception as e:
            return {
                "statusCode": 500,
                "body": f"Internal server error: {str(e)}",
            }
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
