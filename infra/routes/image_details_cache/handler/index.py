"""Lambda handler for serving image details cache."""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ["S3_CACHE_BUCKET"]

# Initialize S3 client
s3_client = boto3.client("s3")


def handler(event, _context):
    """Handle API Gateway requests for image details cache.

    Args:
        event: API Gateway event containing HTTP request details
        _context: Lambda context (unused but required by Lambda)

    Returns:
        dict: HTTP response with cached image details or error
    """
    logger.info("Received event: %s", event)
    http_method = event["requestContext"]["http"]["method"].upper()

    if http_method != "GET":
        return {
            "statusCode": 405,
            "body": json.dumps({"error": f"Method {http_method} not allowed"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    # Get image_type from query params (default: SCAN)
    query_params = event.get("queryStringParameters") or {}
    image_type = (
        query_params.get("image_type", "SCAN").upper()
        if isinstance(query_params, dict)
        else "SCAN"
    )

    # Validate image type
    if image_type not in ["SCAN", "PHOTO"]:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {
                    "error": f"Invalid image_type '{image_type}'. Must be SCAN or PHOTO"
                }
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    cache_key = f"image-details-cache/{image_type}.json"

    try:
        # Download cached JSON from S3
        logger.info(
            "Fetching cache from S3: %s/%s", S3_CACHE_BUCKET, cache_key
        )
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=cache_key)
        cache_data = json.loads(response["Body"].read().decode("utf-8"))

        logger.info(
            "Cache retrieved successfully: %d images",
            len(cache_data.get("images", [])),
        )
        return {
            "statusCode": 200,
            "body": json.dumps(cache_data, default=str),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "NoSuchKey":
            logger.warning("Cache not found in S3: %s", cache_key)
            return {
                "statusCode": 404,
                "body": json.dumps(
                    {
                        "error": f"Image details cache not found for type {image_type}"
                    }
                ),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }
        logger.error("S3 error: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to retrieve cache"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    except Exception as e:
        logger.error("Unexpected error: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
