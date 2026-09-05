import json
import logging
import os
import random

from _api_dynamo import get_api_dynamo_client
from _lambda_profiler import profile_handler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


@profile_handler
def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method == "GET":
        query_params = event.get("queryStringParameters") or {}

        # Check for optional 'image_type' parameter
        image_type = query_params.get("image_type")

        # Check for an optional 'limit'
        limit_param = query_params.get("limit")
        limit = int(limit_param) if limit_param is not None else None
        if limit is not None and limit <= 0:
            return {
                "statusCode": 400,
                "body": "limit must be a positive integer",
            }

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

        client = get_api_dynamo_client(DYNAMODB_TABLE_NAME)
        if image_type:
            # If image_type is specified, use listImagesByType
            raw_images, lek = client.list_images_by_type(
                image_type=image_type,
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            )
            # Remove duplicates
            seen_ids = set()
            images = []
            for img in raw_images:
                if img["image_id"] not in seen_ids:
                    seen_ids.add(img["image_id"])
                    images.append(img)
        else:
            # If no image_type, fetch equal distribution of Photo and Scan
            # types
            images = []

            # If limit is specified, split it between Photo and Scan
            if limit:
                photo_limit = (limit + 1) // 2
                scan_limit = limit // 2
            else:
                photo_limit = None
                scan_limit = None

            # Fetch Photo images
            if photo_limit != 0:
                photo_images, photo_lek = client.list_images_by_type(
                    image_type="PHOTO",
                    limit=photo_limit,
                    last_evaluated_key=(
                        last_evaluated_key.get("photo")
                        if last_evaluated_key
                        and isinstance(last_evaluated_key, dict)
                        else None
                    ),
                )
            else:
                photo_images, photo_lek = [], None

            # Fetch Scan images
            if scan_limit != 0:
                scan_images, scan_lek = client.list_images_by_type(
                    image_type="SCAN",
                    limit=scan_limit,
                    last_evaluated_key=(
                        last_evaluated_key.get("scan")
                        if last_evaluated_key
                        and isinstance(last_evaluated_key, dict)
                        else None
                    ),
                )
            else:
                scan_images, scan_lek = [], None

            # Combine images and remove duplicates
            # Use a set to track unique image_ids
            seen_ids = set()
            unique_images = []

            for img in photo_images + scan_images:
                if img["image_id"] not in seen_ids:
                    seen_ids.add(img["image_id"])
                    unique_images.append(img)

            # Shuffle for mixed distribution
            images = unique_images
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
