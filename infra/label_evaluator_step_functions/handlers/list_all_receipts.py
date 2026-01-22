"""List all receipts across all merchants for the flattened Step Function.

This handler combines the functionality of list_merchants and list_receipts:
1. Queries all merchants (or filtered list)
2. Gets all receipts for those merchants
3. Returns a flat list of receipts + unique merchant list

The two-phase architecture uses this to:
- Phase 1: Compute patterns for all merchants in parallel
- Phase 2: Process all receipts in parallel (load patterns from S3)
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
import random
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from handlers.evaluator_types import MerchantInfo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

# Default minimum receipts for pattern learning
DEFAULT_MIN_RECEIPTS = 5


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    List all receipts and derive merchants from the selected receipts.

    This handler is optimized for the two-phase Step Function architecture:
    - Returns unique merchant list for Phase 1 (pattern computation)
    - Returns flat receipt list for Phase 2 (receipt processing)

    Input:
    {
        "execution_id": str,
        "batch_bucket": str,
        "limit": int | None,  # TOTAL receipts to fetch (None = all)
        "since_date": str | None,  # ISO date string (e.g., "2025-01-15") to filter by timestamp
        "min_receipts": int,  # Minimum receipts for pattern learning (default 5)
        "max_training_receipts": int  # Max training receipts per merchant (default 50)
    }

    Note: since_date filtering is applied BEFORE limit sampling. Receipts with
    timestamp=None are excluded when since_date is specified.

    Output:
    {
        "merchants": [{"merchant_name": str, "receipt_count": int}, ...],
        "receipts": [{"image_id": str, "receipt_id": int, "merchant_name": str}, ...],
        "total_merchants": int,
        "total_receipts": int,
        "max_training_receipts": int,
        "manifest_s3_key": str
    }
    """
    from handlers.evaluator_types import MerchantInfo

    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    min_receipts = event.get("min_receipts", DEFAULT_MIN_RECEIPTS)
    max_training_receipts = event.get("max_training_receipts", 50)
    limit = event.get("limit")  # Total receipts limit (None = all)

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(
        "Listing all receipts (min_receipts=%s, limit=%s)",
        min_receipts,
        limit,
    )

    # Import DynamoDB client
    from receipt_dynamo import DynamoClient

    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo = DynamoClient(table_name=table_name)

    # Get all ReceiptPlaces with pagination
    all_places = []
    result = dynamo.list_receipt_places()
    all_places.extend(result[0])

    while result[1]:  # Continue if there's more data
        result = dynamo.list_receipt_places(last_evaluated_key=result[1])
        all_places.extend(result[0])

    total_places = len(all_places)
    logger.info("Found %s total receipt places", total_places)

    # Filter by since_date if provided (before applying limit)
    since_date_str = event.get("since_date")
    if since_date_str:
        from datetime import datetime, timezone

        # Parse ISO date string to datetime (start of day UTC)
        since_date = datetime.fromisoformat(
            since_date_str.replace("Z", "+00:00")
        )
        if since_date.tzinfo is None:
            since_date = since_date.replace(tzinfo=timezone.utc)

        def _to_utc(dt: datetime) -> datetime:
            """Normalize datetime to UTC, handling naive timestamps as UTC."""
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        # Filter receipts by timestamp
        before_count = len(all_places)
        all_places = [
            p
            for p in all_places
            if p.timestamp and _to_utc(p.timestamp) >= since_date
        ]
        after_count = len(all_places)
        logger.info(
            "Filtered by since_date=%s: %d -> %d receipts",
            since_date_str,
            before_count,
            after_count,
        )

    # Apply total limit if specified (randomly sample from all places)
    filtered_total = len(all_places)
    if limit and limit < filtered_total:
        all_places = random.sample(all_places, limit)
        logger.info(
            "Randomly sampled %s places from %s total", limit, filtered_total
        )

    # Collect receipts from the selected places, grouped by merchant
    receipts_by_merchant: dict[str, list[dict]] = {}

    for place in all_places:
        merchant_name = place.merchant_name

        if merchant_name not in receipts_by_merchant:
            receipts_by_merchant[merchant_name] = []

        receipts_by_merchant[merchant_name].append(
            {
                "image_id": place.image_id,
                "receipt_id": place.receipt_id,
                "merchant_name": merchant_name,
            }
        )

    # Get unique merchants from sampled receipts
    unique_merchants = set(receipts_by_merchant.keys())

    # Query DynamoDB for TOTAL receipt count per merchant (not just sample count)
    # This ensures merchants qualify based on their actual database size, not sample size
    total_merchant_counts: dict[str, int] = {}
    for merchant_name in unique_merchants:
        total_count = 0
        result = dynamo.get_receipt_places_by_merchant(merchant_name)
        total_count += len(result[0])
        while result[1]:  # Paginate to get full count
            result = dynamo.get_receipt_places_by_merchant(
                merchant_name, last_evaluated_key=result[1]
            )
            total_count += len(result[0])
        total_merchant_counts[merchant_name] = total_count

    logger.info(
        "Queried total counts for %s merchants: %s",
        len(unique_merchants),
        {
            k: v
            for k, v in sorted(
                total_merchant_counts.items(), key=lambda x: -x[1]
            )[:5]
        },
    )

    # Filter merchants by minimum receipt threshold using TOTAL counts (for pattern computation)
    qualifying_merchants: list[MerchantInfo] = []
    merchants_with_patterns: set[str] = set()
    skipped_count = 0

    for merchant_name in unique_merchants:
        total_count = total_merchant_counts[merchant_name]
        if total_count >= min_receipts:
            qualifying_merchants.append(
                MerchantInfo(
                    merchant_name=merchant_name, receipt_count=total_count
                )
            )
            merchants_with_patterns.add(merchant_name)
        else:
            skipped_count += 1
            # NOTE: Don't remove receipts - they still get processed, just without patterns

    # Sort qualifying merchants by receipt count (descending)
    qualifying_merchants.sort(key=lambda x: x["receipt_count"], reverse=True)

    # Flatten ALL receipts into a single list, sorted by merchant for S3 cache locality
    # Add merchant_receipt_count (using TOTAL count) and has_patterns to each receipt
    all_receipts = []
    for merchant_name in sorted(receipts_by_merchant.keys()):
        total_count = total_merchant_counts[merchant_name]
        has_patterns = merchant_name in merchants_with_patterns
        for receipt in receipts_by_merchant[merchant_name]:
            receipt["merchant_receipt_count"] = total_count  # Use TOTAL count
            receipt["has_patterns"] = has_patterns
        all_receipts.extend(receipts_by_merchant[merchant_name])

    logger.info(
        "Found %s merchants with >= %s receipts (%s total receipts). Skipped %s merchants.",
        len(qualifying_merchants),
        min_receipts,
        len(all_receipts),
        skipped_count,
    )

    # Upload manifest to S3 for reference
    manifest = {
        "execution_id": execution_id,
        "since_date": since_date_str,
        "min_receipts": min_receipts,
        "max_training_receipts": max_training_receipts,
        "limit": limit,
        "merchants": qualifying_merchants,
        "total_merchants": len(qualifying_merchants),
        "total_receipts": len(all_receipts),
        "skipped_merchants": skipped_count,
    }

    manifest_key = f"manifests/{execution_id}/all_receipts.json"
    try:
        s3.put_object(
            Bucket=batch_bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(
            "Created manifest at s3://%s/%s", batch_bucket, manifest_key
        )
    except Exception:
        logger.exception("Failed to upload manifest to %s", manifest_key)
        manifest_key = None

    return {
        "merchants": qualifying_merchants,
        "receipts": all_receipts,
        "total_merchants": len(qualifying_merchants),
        "total_receipts": len(all_receipts),
        "max_training_receipts": max_training_receipts,
        "min_receipts": min_receipts,
        "manifest_s3_key": manifest_key,
    }
