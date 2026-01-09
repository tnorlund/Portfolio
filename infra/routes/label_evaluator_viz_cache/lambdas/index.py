"""Lambda handler for serving label evaluator visualization cache.

Uses random batch sampling from individual receipt files (like LayoutLM pattern).
Each request returns a random sample of receipts from the cache pool.

Query Parameters:
- batch_size: Number of receipts to return (default: 10, max: 50)
"""

import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
RECEIPTS_PREFIX = "receipts/"
DEFAULT_BATCH_SIZE = 10
MAX_BATCH_SIZE = 50

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def _list_cached_receipts() -> list[str]:
    """List all cached receipt keys from S3 using paginator.

    Returns:
        List of S3 keys for cached receipt files.
    """
    keys: list[str] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=S3_CACHE_BUCKET, Prefix=RECEIPTS_PREFIX
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    keys.append(key)
    except ClientError:
        logger.exception("Error listing cached receipts")
    return keys


def _fetch_receipt(key: str) -> dict[str, Any] | None:
    """Fetch a single receipt from S3.

    Args:
        key: S3 key for the receipt file.

    Returns:
        Receipt data dict, or None if fetch failed.
    """
    try:
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.exception("Error fetching receipt %s", key)
        return None


def _fetch_metadata() -> dict[str, Any]:
    """Fetch pool metadata from S3.

    Returns:
        Metadata dict with version, execution_id, total_receipts, etc.
        Empty dict if metadata file not found.
    """
    try:
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key="metadata.json")
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.warning("Could not fetch metadata.json")
        return {}


def _calculate_aggregate_stats(
    receipts: list[dict[str, Any]], pool_size: int
) -> dict[str, Any]:
    """Calculate aggregate statistics across the fetched batch.

    Args:
        receipts: List of receipt dicts in the current batch.
        pool_size: Total number of receipts in the cache pool.

    Returns:
        Dict with aggregate statistics.
    """
    if not receipts:
        return {"total_receipts_in_pool": pool_size, "batch_size": 0}

    issues = [r.get("issues_found", 0) for r in receipts]
    return {
        "total_receipts_in_pool": pool_size,
        "batch_size": len(receipts),
        "avg_issues": sum(issues) / len(issues) if issues else 0,
        "max_issues": max(issues) if issues else 0,
        "receipts_with_issues": sum(1 for i in issues if i > 0),
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handle API Gateway requests for label evaluator visualization cache.

    Returns a random batch of cached receipts with their evaluation results.

    Query Parameters:
        batch_size: Number of receipts to return (default: 10, max: 50)

    Response structure:
    {
        "receipts": [...],  # Random batch
        "aggregate_stats": {
            "total_receipts_in_pool": int,
            "batch_size": int,
            "avg_issues": float,
            "max_issues": int,
            "receipts_with_issues": int
        },
        "execution_id": str | null,
        "cached_at": str | null,
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
            "body": json.dumps(
                {"error": "Configuration error: S3_CACHE_BUCKET not set"}
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    try:
        # Parse batch_size from query params
        query_params = event.get("queryStringParameters") or {}
        try:
            batch_size = int(query_params.get("batch_size", DEFAULT_BATCH_SIZE))
            batch_size = max(1, min(batch_size, MAX_BATCH_SIZE))  # Clamp to [1, MAX]
        except (ValueError, TypeError):
            batch_size = DEFAULT_BATCH_SIZE

        logger.info("Requested batch_size: %d", batch_size)

        # List all cached receipts
        cached_keys = _list_cached_receipts()
        if not cached_keys:
            logger.warning("No cached receipts found in %s", RECEIPTS_PREFIX)
            return {
                "statusCode": 404,
                "body": json.dumps(
                    {
                        "error": "No cached receipts found",
                        "message": "Run the cache generator Step Function first.",
                    }
                ),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }

        # Random sample from the pool
        pool_size = len(cached_keys)
        sample_size = min(batch_size, pool_size)
        selected_keys = random.sample(cached_keys, sample_size)

        logger.info(
            "Sampling %d receipts from pool of %d", sample_size, pool_size
        )

        # Fetch selected receipts
        receipts: list[dict[str, Any]] = []
        for key in selected_keys:
            receipt = _fetch_receipt(key)
            if receipt:
                receipts.append(receipt)

        # Get metadata and build response
        metadata = _fetch_metadata()
        aggregate_stats = _calculate_aggregate_stats(receipts, pool_size)

        response_data = {
            "receipts": receipts,
            "aggregate_stats": aggregate_stats,
            "execution_id": metadata.get("execution_id"),
            "cached_at": metadata.get("cached_at"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Returning %d receipts (requested %d) from execution %s",
            len(receipts),
            batch_size,
            metadata.get("execution_id", "unknown"),
        )

        return {
            "statusCode": 200,
            "body": json.dumps(response_data, default=str),
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
