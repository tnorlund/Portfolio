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
            receipts, lek = client.listReceipts(500)
            while lek:
                next_receipts, lek = client.listReceipts(500, lek)
                receipts.extend(next_receipts)

            # Group all receipts by their image_id
            # Set the value to the dict to the number of receipts with that image_id
            receipts_by_image_id = {}
            for receipt in receipts:
                if receipt.image_id not in receipts_by_image_id:
                    receipts_by_image_id[receipt.image_id] = 0
                receipts_by_image_id[receipt.image_id] += 1

            # Only use images with 2 receipts
            receipts_by_image_id = {
                key: value for key, value in receipts_by_image_id.items() if value == 2
            }

            # Randomly chose an image_id of the images with 2 receipts
            if len(receipts_by_image_id) == 0:
                return {"statusCode": 404, "body": "No images with 2 receipts found"}

            image_id = random.choice(list(receipts_by_image_id.keys()))

            image_details = client.getImageDetails(image_id)
            (
                images,
                lines,
                words,
                letters,
                word_tags,
                receipts,
                receipt_lines,
                receipt_words,
                receipt_letters,
                gpt_validations,
                gpt_initial_taggings,
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
