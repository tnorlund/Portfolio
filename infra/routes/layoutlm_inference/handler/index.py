"""Lambda handler for serving LayoutLM inference cache.

This Lambda function serves batches of cached LayoutLM inference results from S3.
It randomly selects receipts from the cache pool to provide variety for the visualization.
"""

import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
CACHE_PREFIX = "layoutlm-inference-cache/receipts/"
LEGACY_CACHE_KEY = "layoutlm-inference-cache/latest.json"
BATCH_SIZE = 5  # Number of receipts to return per request

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def _list_cached_receipts() -> List[str]:
    """List all cached receipt keys from S3.

    Returns:
        List of S3 keys for cached receipts
    """
    keys = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=S3_CACHE_BUCKET, Prefix=CACHE_PREFIX
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    keys.append(key)
    except ClientError:
        logger.exception("Error listing cached receipts")
    return keys


def _fetch_receipt(key: str) -> Dict[str, Any]:
    """Fetch a single receipt from S3.

    Args:
        key: S3 key for the receipt

    Returns:
        Receipt data dict

    Raises:
        ClientError: If S3 fetch fails
    """
    response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def _calculate_aggregate_stats(receipts: List[Dict[str, Any]], pool_size: int) -> Dict[str, Any]:
    """Calculate aggregate statistics across batch.

    Args:
        receipts: List of receipt data dicts
        pool_size: Total number of receipts in the pool

    Returns:
        Dict with aggregate statistics
    """
    if not receipts:
        return {
            "avg_accuracy": 0.0,
            "min_accuracy": 0.0,
            "max_accuracy": 0.0,
            "avg_inference_time_ms": 0.0,
            "total_receipts_in_pool": pool_size,
            "batch_size": 0,
            "total_words_processed": 0,
            "estimated_throughput_per_hour": 0,
        }

    accuracies = [r.get("metrics", {}).get("overall_accuracy", 0) for r in receipts]
    inference_times = [r.get("inference_time_ms", 100) for r in receipts]
    total_words = sum(r.get("metrics", {}).get("total_words", 0) for r in receipts)

    avg_inference_time = sum(inference_times) / len(inference_times) if inference_times else 100
    # Estimate throughput: receipts per hour based on avg inference time
    if avg_inference_time > 0:
        throughput = (3600 * 1000 / avg_inference_time)
    else:
        throughput = 0

    return {
        "avg_accuracy": sum(accuracies) / len(accuracies) if accuracies else 0.0,
        "min_accuracy": min(accuracies) if accuracies else 0.0,
        "max_accuracy": max(accuracies) if accuracies else 0.0,
        "avg_inference_time_ms": round(avg_inference_time, 2),
        "total_receipts_in_pool": pool_size,
        "batch_size": len(receipts),
        "total_words_processed": total_words,
        "estimated_throughput_per_hour": round(throughput),
    }


def _try_legacy_cache() -> Dict[str, Any]:
    """Try to fetch from legacy single-file cache.

    Returns:
        Legacy cache data wrapped in batch format, or None if not found
    """
    try:
        response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=LEGACY_CACHE_KEY)
        cache_data = json.loads(response["Body"].read().decode("utf-8"))

        # Wrap legacy response in new batch format
        # Add entities_summary if not present
        if "entities_summary" not in cache_data:
            cache_data["entities_summary"] = {
                "merchant_name": None,
                "date": None,
                "address": None,
                "amount": None,
            }
        if "inference_time_ms" not in cache_data:
            cache_data["inference_time_ms"] = 100  # Default estimate

        return {
            "receipts": [cache_data],
            "aggregate_stats": {
                "avg_accuracy": cache_data.get("metrics", {}).get("overall_accuracy", 0),
                "min_accuracy": cache_data.get("metrics", {}).get("overall_accuracy", 0),
                "max_accuracy": cache_data.get("metrics", {}).get("overall_accuracy", 0),
                "avg_inference_time_ms": cache_data.get("inference_time_ms", 100),
                "total_receipts_in_pool": 1,
                "batch_size": 1,
                "total_words_processed": cache_data.get("metrics", {}).get("total_words", 0),
                "estimated_throughput_per_hour": 36000,
            },
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "legacy_mode": True,
        }
    except ClientError:
        return None


def handler(event, _context):
    """Handle API Gateway requests for LayoutLM inference cache.

    Returns a batch of randomly selected receipts from the cache pool,
    along with aggregate statistics.

    Args:
        event: API Gateway event containing HTTP request details
        _context: Lambda context (unused but required by Lambda)

    Returns:
        dict: HTTP response with batch of cached inference data or error
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
            "body": json.dumps(
                {"error": "Configuration error: S3_CACHE_BUCKET not set"}
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    try:
        # List all cached receipts
        logger.info("Listing cached receipts from %s", CACHE_PREFIX)
        cached_keys = _list_cached_receipts()

        if not cached_keys:
            logger.warning("No receipts in cache pool, trying legacy cache")
            # Try legacy single-file cache as fallback
            legacy_response = _try_legacy_cache()
            if legacy_response:
                logger.info("Using legacy cache")
                return {
                    "statusCode": 200,
                    "body": json.dumps(legacy_response, default=str),
                    "headers": {
                        "Content-Type": "application/json",
                        "Access-Control-Allow-Origin": "*",
                    },
                }

            return {
                "statusCode": 404,
                "body": json.dumps({
                    "error": "Cache pool empty",
                    "message": "No cached receipts found. The batch cache generator should populate the pool.",
                    "bucket": S3_CACHE_BUCKET,
                    "prefix": CACHE_PREFIX,
                }),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }

        # Randomly select batch_size receipts
        pool_size = len(cached_keys)
        sample_size = min(BATCH_SIZE, pool_size)
        selected_keys = random.sample(cached_keys, sample_size)

        logger.info(
            "Selected %d receipts from pool of %d",
            sample_size,
            pool_size,
        )

        # Fetch each selected receipt
        receipts = []
        for key in selected_keys:
            try:
                receipt = _fetch_receipt(key)
                receipts.append(receipt)
            except ClientError:
                logger.exception("Error fetching receipt %s", key)
                # Continue with remaining receipts

        if not receipts:
            return {
                "statusCode": 500,
                "body": json.dumps({
                    "error": "Failed to fetch receipts",
                    "message": "All receipt fetches failed",
                }),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }

        # Calculate aggregate statistics
        aggregate_stats = _calculate_aggregate_stats(receipts, pool_size)

        # Build response
        response_data = {
            "receipts": receipts,
            "aggregate_stats": aggregate_stats,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Returning batch of %d receipts, avg accuracy: %.2f%%",
            len(receipts),
            aggregate_stats["avg_accuracy"] * 100,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(response_data, default=str),
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
