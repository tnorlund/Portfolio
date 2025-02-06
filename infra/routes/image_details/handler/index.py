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
            # Use the client to list the first 50 images
            client = DynamoClient(dynamodb_table_name)
            receipts, _ = client.listReceipts(50)

            # Group all receipts by their image_id
            # Set the value to the dict to the number of receipts with that image_id
            receipts_by_image_id = {}
            for receipt in receipts:
                if receipt.image_id not in receipts_by_image_id:
                    receipts_by_image_id[receipt.image_id] = 0
                receipts_by_image_id[receipt.image_id] += 1

            # Get the image_id with the most receipts
            image_id = max(
                receipts_by_image_id, key=lambda key: receipts_by_image_id[key]
            )

            image_details = client.getImageDetails(image_id)
            (
                images,
                _,
                words,
                _,
                _,
                receipts,
                _,
                receipt_words,
                _,
                _,
                _,
            ) = image_details
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "images": [dict(image) for image in images],
                        "words": [dict(word) for word in words],
                        "receipts": [dict(receipt) for receipt in receipts],
                        "receipt_words": [
                            dict(receipt_word) for receipt_word in receipt_words
                        ],
                    }
                ),
            }
        except Exception as e:
            return {"statusCode": 500, "body": f"Internal server error: {str(e)}"}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
