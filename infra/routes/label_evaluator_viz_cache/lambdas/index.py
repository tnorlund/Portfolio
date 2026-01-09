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


class CacheNotFoundError(Exception):
    """Raised when the cache file or pointer is not found in S3."""


def _fetch_cache(bucket: str) -> dict[str, Any]:
    """Fetch the visualization cache from S3.

    Reads the latest.json pointer to find the current versioned cache file,
    then fetches and returns that cache.

    Raises:
        CacheNotFoundError: If the pointer or cache file is missing.
        ClientError: For other S3 errors.
    """
    # First, get the latest.json pointer to find the versioned cache file
    try:
        pointer_response = s3_client.get_object(
            Bucket=bucket, Key=LATEST_POINTER_KEY
        )
        pointer = json.loads(pointer_response["Body"].read().decode("utf-8"))
        cache_key = pointer.get("cache_key")
        if not cache_key:
            raise CacheNotFoundError("latest.json missing 'cache_key' field")
        logger.info("Fetching cache from %s", cache_key)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "NoSuchKey":
            raise CacheNotFoundError(f"Pointer not found: {LATEST_POINTER_KEY}") from e
        raise

    # Fetch the versioned cache file
    try:
        response = s3_client.get_object(Bucket=bucket, Key=cache_key)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "NoSuchKey":
            raise CacheNotFoundError(f"Cache file not found: {cache_key}") from e
        raise

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
    except (KeyError, TypeError):
        logger.exception("Invalid event structure")
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
        cache_data = _fetch_cache(S3_CACHE_BUCKET)

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

    except CacheNotFoundError as e:
        logger.warning("Cache not found: %s", e)
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
    except ClientError:
        logger.exception("S3 error")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "S3 error occurred"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
    except Exception:
        logger.exception("Unexpected error")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
