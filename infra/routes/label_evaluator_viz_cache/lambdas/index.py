"""Lambda handler for serving label evaluator visualization cache.

This Lambda serves cached label evaluator examples for the
LabelEvaluatorVisualization component. It returns a collection of
receipts with their evaluation results (currency, metadata, financial).
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
LATEST_POINTER_KEY = "latest.json"

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def _fetch_cache() -> dict[str, Any]:
    """Fetch the visualization cache from S3.

    Reads the latest.json pointer to find the current versioned cache file,
    then fetches and returns that cache.
    """
    # First, get the latest.json pointer to find the versioned cache file
    try:
        pointer_response = s3_client.get_object(
            Bucket=S3_CACHE_BUCKET, Key=LATEST_POINTER_KEY
        )
        pointer = json.loads(pointer_response["Body"].read().decode("utf-8"))
        cache_key = pointer.get("cache_key")
        if not cache_key:
            raise ValueError("latest.json missing 'cache_key' field")
        logger.info("Fetching cache from %s", cache_key)
    except s3_client.exceptions.NoSuchKey:
        logger.error("No latest.json pointer found in bucket %s", S3_CACHE_BUCKET)
        raise

    # Fetch the versioned cache file
    response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=cache_key)
    return json.loads(response["Body"].read().decode("utf-8"))


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handle API Gateway requests for label evaluator visualization cache.

    Returns cached receipts with their evaluation results.

    Response structure:
    {
        "execution_id": str,
        "receipts": [{
            "image_id": str,
            "receipt_id": int,
            "issues_found": int,
            "image_url": str,
            "words": [{
                "text": str,
                "label": str | null,
                "line_id": int,
                "word_id": int,
                "bbox": {"x": float, "y": float, "width": float, "height": float}
            }],
            "geometric": {
                "image_id": str,
                "receipt_id": int,
                "issues_found": int,
                "issues": [...],
                "error": str | null,
                "merchant_receipts_analyzed": int,
                "label_types_found": int
            },
            "currency": {
                "image_id": str,
                "receipt_id": int,
                "merchant_name": str,
                "duration_seconds": float,
                "decisions": {"VALID": int, "INVALID": int, "NEEDS_REVIEW": int},
                "all_decisions": [...]
            },
            "metadata": {...},
            "financial": {...}
        }],
        "summary": {
            "total_receipts": int,
            "receipts_with_issues": int
        },
        "cached_at": str,
        "fetched_at": str
    }
    """
    logger.info("Received event: %s", event)

    # Handle API Gateway v2 event format
    try:
        http_method = event["requestContext"]["http"]["method"].upper()
    except (KeyError, TypeError) as e:
        logger.error("Invalid event structure: %s", e)
        return {
            "statusCode": 400,
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
        # Fetch cached data
        logger.info("Fetching cache from s3://%s/%s", S3_CACHE_BUCKET, LATEST_POINTER_KEY)
        cache_data = _fetch_cache()

        # Add fetch timestamp
        cache_data["fetched_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Returning %d receipts from execution %s",
            len(cache_data.get("receipts", [])),
            cache_data.get("execution_id", "unknown"),
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
            logger.warning("Cache file not found: %s", LATEST_POINTER_KEY)
            return {
                "statusCode": 404,
                "body": json.dumps({
                    "error": "Cache not found",
                    "message": "No cached visualization data found. Run the cache generator first.",
                }),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }
        logger.error("S3 error: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"S3 error: {str(e)}"}),
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
