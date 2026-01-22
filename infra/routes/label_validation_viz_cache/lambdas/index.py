"""Lambda handler for serving label validation visualization cache.

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
RECEIPTS_PREFIX = "receipts/"
DEFAULT_BATCH_SIZE = 20
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
        Metadata dict with version, total_receipts, aggregate_stats, etc.
        Empty dict if metadata file not found.
    """
    try:
        response = s3_client.get_object(
            Bucket=S3_CACHE_BUCKET, Key="metadata.json"
        )
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
        Dict with aggregate statistics for label validation.
    """
    if not receipts:
        return {
            "total_receipts": pool_size,
            "avg_chroma_rate": 0.0,
            "avg_confidence": 0.0,
        }

    # Calculate ChromaDB validation rate (% of words validated by ChromaDB vs LLM)
    total_words = 0
    chroma_words = 0
    total_valid = 0
    total_invalid = 0
    total_needs_review = 0

    for r in receipts:
        chroma = r.get("chroma", {})
        llm = r.get("llm")

        chroma_count = chroma.get("words_count", 0)
        llm_count = llm.get("words_count", 0) if llm else 0

        total_words += chroma_count + llm_count
        chroma_words += chroma_count

        # Aggregate decisions
        chroma_decisions = chroma.get("decisions", {})
        total_valid += chroma_decisions.get("VALID", 0)
        total_invalid += chroma_decisions.get("INVALID", 0)
        total_needs_review += chroma_decisions.get("NEEDS_REVIEW", 0)

        if llm:
            llm_decisions = llm.get("decisions", {})
            total_valid += llm_decisions.get("VALID", 0)
            total_invalid += llm_decisions.get("INVALID", 0)
            total_needs_review += llm_decisions.get("NEEDS_REVIEW", 0)

    avg_chroma_rate = (
        (chroma_words / total_words * 100) if total_words > 0 else 0.0
    )

    return {
        "total_receipts": pool_size,
        "avg_chroma_rate": round(avg_chroma_rate, 1),
        "total_valid": total_valid,
        "total_invalid": total_invalid,
        "total_needs_review": total_needs_review,
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handle API Gateway requests for label validation visualization cache.

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
        # Parse query params
        query_params = event.get("queryStringParameters") or {}

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
        cached_keys = _list_cached_receipts()
        if not cached_keys:
            logger.warning("No cached receipts found in %s", RECEIPTS_PREFIX)
            return {
                "statusCode": 404,
                "body": json.dumps(
                    {
                        "error": "No cached receipts found",
                        "message": "Run the cache generator first.",
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

        # Rebuild list in original selected_keys order
        receipts: list[dict[str, Any]] = [
            key_to_receipt[key]
            for key in selected_keys
            if key_to_receipt.get(key)
        ]

        # Get metadata and build response
        metadata = _fetch_metadata()
        aggregate_stats = _calculate_aggregate_stats(receipts, total_count)

        response_data = {
            "receipts": receipts,
            "total_count": total_count,
            "offset": offset,
            "has_more": has_more,
            "seed": seed,
            "aggregate_stats": aggregate_stats,
            "cached_at": metadata.get("cached_at"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info("Returning %d receipts", len(receipts))

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
