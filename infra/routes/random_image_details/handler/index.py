import json
import logging
import os
import random

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
QUERY_LIMIT = 500


def handler(event, context):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    # Determine requested image type via query parameter, defaulting to "SCAN"
    query_params = event.get("queryStringParameters") or {}
    image_type_str = (
        query_params.get("image_type", ImageType.SCAN.value).upper()
        if isinstance(query_params, dict)
        else ImageType.SCAN.value
    )

    # Validate and convert to ImageType enum
    try:
        image_type = ImageType[image_type_str]
    except KeyError:
        return {
            "statusCode": 400,
            "body": f"Invalid image_type '{image_type_str}'. Must be one of {[t.value for t in ImageType]}",
        }

    if http_method == "GET":
        try:
            # Use the client to list the first 50 images
            client = DynamoClient(dynamodb_table_name)
            receipts, lek = client.list_receipts(QUERY_LIMIT)
            while lek:
                next_receipts, lek = client.list_receipts(QUERY_LIMIT, lek)
                receipts.extend(next_receipts)

            # Group all receipts by their image_id
            # Set the value to the dict to the number of receipts with that image_id
            receipts_by_image_id = {}
            for receipt in receipts:
                if receipt.image_id not in receipts_by_image_id:
                    receipts_by_image_id[receipt.image_id] = 0
                receipts_by_image_id[receipt.image_id] += 1

            # If the requested type is SCAN, only keep images that have ≥2 receipts
            if image_type == ImageType.SCAN:
                receipts_by_image_id = {
                    key: value
                    for key, value in receipts_by_image_id.items()
                    if value >= 2
                }

            # List all images of the requested type
            images, last_evaluated_key = client.list_images_by_type(image_type)
            images = [
                image
                for image in images
                if image.image_id in receipts_by_image_id
            ]

            # Randomly chose an image_id of the images with 2 receipts
            if len(images) == 0:
                return {
                    "statusCode": 404,
                    "body": "No images with 2 receipts found",
                }

            image_id = random.choice([image.image_id for image in images])

            image_details = client.getImageClusterDetails(image_id)
            (
                image,
                lines,
                receipts,
            ) = image_details
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "image": dict(image),
                        "lines": [dict(line) for line in lines],
                        "receipts": [dict(receipt) for receipt in receipts],
                    }
                ),
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
