"""Lambda handler for serving label evaluator visualization cache.

Uses seed-based deterministic pagination for consistent random ordering.
Client generates a seed on mount, then paginates through shuffled results.

Query Parameters:
- batch_size: Number of receipts to return (default: 20, max: 50)
- seed: Random seed for deterministic shuffle (default: random)
- offset: Starting position in shuffled list (default: 0)
"""

import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
DEFAULT_BATCH_SIZE = 20
MAX_BATCH_SIZE = 50

# Map viz_type (last path segment) to S3 prefix
VIZ_TYPE_PREFIXES = {
    "visualization": "receipts/",
    "financial_math": "financial-math/",
    "diff": "diff/",
    "journey": "journey/",
    "patterns": "patterns/",
    "evidence": "evidence/",
    "dedup": "dedup/",
}

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def _list_cached_receipts(prefix: str) -> list[str]:
    """List all cached receipt keys from S3 using paginator.

    Args:
        prefix: S3 key prefix to list under.

    Returns:
        List of S3 keys for cached receipt files.
    """
    keys: list[str] = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=S3_CACHE_BUCKET, Prefix=prefix
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json") and not key.endswith(
                    "metadata.json"
                ):
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


def _fetch_metadata(prefix: str) -> dict[str, Any]:
    """Fetch pool metadata from S3.

    Args:
        prefix: S3 key prefix (e.g. "financial-math/") to read metadata from.

    Returns:
        Metadata dict with version, execution_id, total_receipts, etc.
        Empty dict if metadata file not found.
    """
    key = f"{prefix}metadata.json"
    try:
        response = s3_client.get_object(
            Bucket=S3_CACHE_BUCKET, Key=key
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.warning("Could not fetch %s", key)
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


def _handle_patterns_single_merchant(prefix: str) -> dict[str, Any]:
    """Return a single random merchant for the patterns endpoint.

    Picks one merchant randomly (time-based seed for variety across refreshes)
    and returns it as a single object instead of a paginated list.
    """
    import time

    cached_keys = _list_cached_receipts(prefix)
    if not cached_keys:
        return {
            "statusCode": 404,
            "body": json.dumps(
                {
                    "error": "No cached patterns found",
                    "message": "Run the label evaluator to generate patterns cache.",
                }
            ),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    total_count = len(cached_keys)

    # Time-based seed: changes every 30 seconds for variety across refreshes
    seed = int(time.time() // 30)
    rng = random.Random(seed)
    selected_key = rng.choice(sorted(cached_keys))

    merchant = _fetch_receipt(selected_key)
    if merchant is None:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to fetch merchant data"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    metadata = _fetch_metadata(prefix)

    response_data = {
        "merchant": merchant,
        "total_count": total_count,
        "execution_id": metadata.get("execution_id"),
        "cached_at": metadata.get("cached_at"),
    }

    logger.info(
        "Returning single merchant '%s' from %d total (seed=%d)",
        merchant.get("merchant_name", "unknown"),
        total_count,
        seed,
    )

    return {
        "statusCode": 200,
        "body": json.dumps(response_data, default=str),
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handle API Gateway requests for label evaluator visualization cache.

    Uses seed-based deterministic pagination. Client generates a seed on mount,
    then paginates through the shuffled results with consistent ordering.

    Query Parameters:
        batch_size: Number of receipts to return (default: 20, max: 50)
        seed: Random seed for deterministic shuffle (default: server generates one)
        offset: Starting position in shuffled list (default: 0)

    Response structure:
    {
        "receipts": [...],
        "total_count": int,      # Total receipts in cache
        "offset": int,           # Current offset
        "has_more": bool,        # Whether more pages exist
        "seed": int,             # Seed used (return to client for pagination)
        "aggregate_stats": {...},
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

    # Determine S3 prefix from the API path (last segment)
    path = event["requestContext"]["http"]["path"]
    viz_type = path.rstrip("/").split("/")[-1]
    prefix = VIZ_TYPE_PREFIXES.get(viz_type, "receipts/")

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
        # Parse query params
        query_params = event.get("queryStringParameters") or {}

        # --- Single-merchant mode for patterns ---
        if viz_type == "patterns":
            return _handle_patterns_single_merchant(prefix)

        # batch_size: number of receipts per page
        try:
            batch_size = int(
                query_params.get("batch_size", DEFAULT_BATCH_SIZE)
            )
            batch_size = max(1, min(batch_size, MAX_BATCH_SIZE))
        except (ValueError, TypeError):
            batch_size = DEFAULT_BATCH_SIZE

        # seed: for deterministic shuffle (client sends same seed for pagination)
        try:
            seed = int(query_params.get("seed", random.randint(0, 2**31 - 1)))
        except (ValueError, TypeError):
            seed = random.randint(0, 2**31 - 1)

        # offset: starting position in shuffled list
        try:
            offset = int(query_params.get("offset", 0))
            offset = max(0, offset)
        except (ValueError, TypeError):
            offset = 0

        logger.info(
            "batch_size=%d, seed=%d, offset=%d", batch_size, seed, offset
        )

        # List all cached receipts
        cached_keys = _list_cached_receipts(prefix)
        if not cached_keys:
            logger.warning("No cached receipts found in %s", prefix)
            return {
                "statusCode": 404,
                "body": json.dumps(
                    {
                        "error": "No cached receipts found",
                        "message": "Run the label evaluator with analytics enabled to generate the cache.",
                    }
                ),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }

        # Sort keys first for consistency, then shuffle with seed
        total_count = len(cached_keys)
        sorted_keys = sorted(cached_keys)

        # Deterministic shuffle using the provided seed
        rng = random.Random(seed)
        rng.shuffle(sorted_keys)

        # Slice based on offset
        end_offset = min(offset + batch_size, total_count)
        selected_keys = sorted_keys[offset:end_offset]
        has_more = end_offset < total_count

        logger.info(
            "Returning receipts [%d:%d] of %d (has_more=%s)",
            offset,
            end_offset,
            total_count,
            has_more,
        )

        # Fetch selected receipts in parallel for better latency
        # Use dict to map results back to original key order (as_completed returns in completion order)
        max_workers = min(len(selected_keys), 10)
        key_to_receipt: dict[str, dict[str, Any] | None] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_receipt, key): key
                for key in selected_keys
            }
            for future in as_completed(futures):
                key = futures[future]
                key_to_receipt[key] = future.result()

        # Rebuild list in original selected_keys order to preserve seed-based deterministic ordering
        receipts: list[dict[str, Any]] = [
            key_to_receipt[key]
            for key in selected_keys
            if key_to_receipt.get(key)
        ]

        # Get metadata and build response
        metadata = _fetch_metadata(prefix)
        aggregate_stats = _calculate_aggregate_stats(receipts, total_count)

        response_data = {
            "receipts": receipts,
            "total_count": total_count,
            "offset": offset,
            "has_more": has_more,
            "seed": seed,
            "aggregate_stats": aggregate_stats,
            "execution_id": metadata.get("execution_id"),
            "cached_at": metadata.get("cached_at"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            "Returning %d receipts from execution %s",
            len(receipts),
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
