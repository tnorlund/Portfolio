import json
import logging
import os
import random

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        client = DynamoClient(DYNAMODB_TABLE_NAME)
        query_params = event.get("queryStringParameters") or {}

        # Check for optional 'image_type' parameter
        image_type = query_params.get("image_type")

        # Check for an optional 'limit'
        limit_param = query_params.get("limit")
        limit = int(limit_param) if limit_param is not None else None

        # Check for an optional 'lastEvaluatedKey'
        last_evaluated_key = None
        if "lastEvaluatedKey" in query_params:
            try:
                last_evaluated_key = json.loads(
                    query_params["lastEvaluatedKey"]
                )
            except json.JSONDecodeError:
                logger.error("Error decoding lastEvaluatedKey; ignoring it.")
                last_evaluated_key = None

        if image_type:
            # If image_type is specified, use listImagesByType
            images, lek = client.listImagesByType(
                image_type=image_type,
                limit=limit,
                lastEvaluatedKey=last_evaluated_key,
            )
        else:
            # If no image_type, fetch equal distribution of Photo and Scan
            # types
            images = []

            # If limit is specified, split it between Photo and Scan
            if limit:
                photo_limit = limit // 2
                scan_limit = limit - photo_limit
            else:
                photo_limit = None
                scan_limit = None

            # Fetch Photo images
            photo_images, photo_lek = client.listImagesByType(
                image_type=ImageType.PHOTO.value,
                limit=photo_limit,
                lastEvaluatedKey=(
                    last_evaluated_key.get("photo")
                    if last_evaluated_key
                    and isinstance(last_evaluated_key, dict)
                    else None
                ),
            )

            # Fetch Scan images
            scan_images, scan_lek = client.listImagesByType(
                image_type=ImageType.SCAN.value,
                limit=scan_limit,
                lastEvaluatedKey=(
                    last_evaluated_key.get("scan")
                    if last_evaluated_key
                    and isinstance(last_evaluated_key, dict)
                    else None
                ),
            )

            # Combine and shuffle the images for a mixed distribution
            images = photo_images + scan_images
            random.shuffle(images)

            # Create composite lastEvaluatedKey
            lek = None
            if photo_lek or scan_lek:
                lek = {}
                if photo_lek:
                    lek["photo"] = photo_lek
                if scan_lek:
                    lek["scan"] = scan_lek

        response_body = {
            "images": [dict(i) for i in images],
            "lastEvaluatedKey": lek,
        }
        return {"statusCode": 200, "body": json.dumps(response_body)}
    elif http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    else:
        return {"statusCode": 405, "body": f"Method {http_method} not allowed"}
