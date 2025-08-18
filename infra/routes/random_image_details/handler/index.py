"""Lambda handler for retrieving random image details with specific receipt counts."""

import json
import logging
import os
import random

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    OperationError,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ["DYNAMODB_TABLE_NAME"]
QUERY_LIMIT = 500


def handler(event, _context):
    """Handle API Gateway requests for random image details.

    Args:
        event: API Gateway event containing HTTP request details
        context: Lambda context (unused but required by Lambda)

    Returns:
        dict: HTTP response with status code and body
    """
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
            "body": (
                f"Invalid image_type '{image_type_str}'. Must be one of "
                f"{[t.value for t in ImageType]}"
            ),
        }

    if http_method == "GET":
        return handle_get_request(image_type)

    if http_method == "POST":
        return {"statusCode": 405, "body": "Method not allowed"}
    return {"statusCode": 405, "body": f"Method {http_method} not allowed"}


def handle_get_request(image_type):
    """Handle GET request to retrieve random image details.

    Args:
        image_type: ImageType enum specifying the type of image to retrieve

    Returns:
        dict: HTTP response with image details or error
    """
    try:
        client = DynamoClient(dynamodb_table_name)

        # For SCAN type, we want images with exactly 2 receipts
        # For PHOTO type, we want images with exactly 1 receipt
        target_receipt_count = 2 if image_type == ImageType.SCAN else 1

        # Query images with the exact receipt count using GSI3
        all_images = []

        images, lek = client.list_images_by_type(
            image_type,
            receipt_count=target_receipt_count,
            limit=QUERY_LIMIT,
        )
        all_images.extend(images)

        # Continue pagination if needed
        while lek:
            images, lek = client.list_images_by_type(
                image_type,
                receipt_count=target_receipt_count,
                limit=QUERY_LIMIT,
                last_evaluated_key=lek,
            )
            all_images.extend(images)

        # Check if we found any matching images
        if not all_images:
            return {
                "statusCode": 404,
                "body": (
                    f"No {image_type.value} images with exactly "
                    f"{target_receipt_count} receipt(s) found"
                ),
            }

        # Randomly select one image
        selected_image = random.choice(all_images)
        image_id = selected_image.image_id

        # Get all details for the randomly selected image
        image_details = client.get_image_details(image_id)

        # Extract relevant fields from ImageDetails object
        image = dict(image_details.images[0]) if image_details.images else None
        lines = [dict(line) for line in image_details.lines]
        receipts = [dict(receipt) for receipt in image_details.receipts]
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "image": image,
                    "lines": lines,
                    "receipts": receipts,
                }
            ),
        }
    except (EntityNotFoundError, OperationError) as exc:
        logger.error("Database operation failed: %s", exc)
        return {
            "statusCode": 500,
            "body": f"Database error: {str(exc)}",
        }
    except (KeyError, ValueError) as exc:
        logger.error("Data processing error: %s", exc)
        return {
            "statusCode": 500,
            "body": f"Data processing error: {str(exc)}",
        }
