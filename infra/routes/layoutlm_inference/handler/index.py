"""Lambda handler for serving LayoutLM inference cache.

This Lambda function serves the cached LayoutLM inference results from S3.
"""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
CACHE_KEY = "layoutlm-inference-cache/latest.json"

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def handler(event, _context):
    """Handle API Gateway requests for LayoutLM inference cache.

    Args:
        event: API Gateway event containing HTTP request details
        _context: Lambda context (unused but required by Lambda)

    Returns:
        dict: HTTP response with cached inference data or error
    """
    logger.info("Received event: %s", event)

    # Handle API Gateway v2 event format
    try:
        http_method = event["requestContext"]["http"]["method"].upper()
    except (KeyError, TypeError) as e:
        logger.error("Invalid event structure: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Invalid event structure"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if http_method != "GET":
        return {
            "statusCode": 405,
            "body": json.dumps({"error": f"Method {http_method} not allowed"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if not S3_CACHE_BUCKET:
        logger.error("S3_CACHE_BUCKET environment variable not set")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Configuration error: S3_CACHE_BUCKET not set"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    try:
        # Download cached JSON from S3
        logger.info("Fetching cache from S3: %s/%s", S3_CACHE_BUCKET, CACHE_KEY)
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=CACHE_KEY)
        cache_data = json.loads(response["Body"].read().decode("utf-8"))

        logger.info("Cache retrieved successfully")
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
            logger.warning("Cache not found in S3: %s/%s", S3_CACHE_BUCKET, CACHE_KEY)
            return {
                "statusCode": 404,
                "body": json.dumps({
                    "error": "Cache not found",
                    "message": "The inference cache has not been generated yet. The cache generator Lambda should create it automatically.",
                    "bucket": S3_CACHE_BUCKET,
                    "key": CACHE_KEY,
                }),
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

