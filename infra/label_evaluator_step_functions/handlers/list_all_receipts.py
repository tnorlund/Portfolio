"""List all receipts across all merchants for the flattened Step Function.

This handler combines the functionality of list_merchants and list_receipts:
1. Queries all merchants (or filtered list)
2. Gets all receipts for those merchants
3. Returns a flat batched list of receipts + unique merchant list

The two-phase architecture uses this to:
- Phase 1: Compute patterns for all merchants in parallel
- Phase 2: Process all receipts in parallel (load patterns from S3)
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
from collections import Counter
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from handlers.evaluator_types import MerchantInfo

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

# Default batch size for receipt batches (stay under Step Functions 256KB limit)
DEFAULT_BATCH_SIZE = 100

# Default minimum receipts for pattern learning
DEFAULT_MIN_RECEIPTS = 5


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    List all receipts across all merchants (or filtered by input).

    This handler is optimized for the two-phase Step Function architecture:
    - Returns unique merchant list for Phase 1 (pattern computation)
    - Returns batched receipt list for Phase 2 (receipt processing)

    Input:
    {
        "execution_id": str,
        "batch_bucket": str,
        "merchants": [{"merchant_name": str}, ...] | None,  # Optional filter
        "limit": int | None,  # Per-merchant limit
        "batch_size": int,  # Receipts per batch (default 100)
        "min_receipts": int,  # Minimum receipts for pattern learning (default 5)
        "max_training_receipts": int  # Max training receipts per merchant (default 50)
    }

    Output:
    {
        "merchants": [{"merchant_name": str, "receipt_count": int}, ...],
        "receipt_batches": [
            [{"image_id": str, "receipt_id": int, "merchant_name": str}, ...],
            ...
        ],
        "total_merchants": int,
        "total_receipts": int,
        "total_batches": int,
        "max_training_receipts": int,
        "manifest_s3_key": str
    }
    """
    from handlers.evaluator_types import MerchantInfo

    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    batch_size = event.get("batch_size", DEFAULT_BATCH_SIZE)
    min_receipts = event.get("min_receipts", DEFAULT_MIN_RECEIPTS)
    max_training_receipts = event.get("max_training_receipts", 50)
    limit = event.get("limit")  # Per-merchant limit

    # Optional merchant filter from input
    merchant_filter = event.get("merchants")
    if merchant_filter:
        # Extract merchant names from filter
        filter_names = {m.get("merchant_name") for m in merchant_filter if m.get("merchant_name")}
    else:
        filter_names = None

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(
        "Listing all receipts (batch_size=%s, min_receipts=%s, limit=%s, filter=%s)",
        batch_size,
        min_receipts,
        limit,
        len(filter_names) if filter_names else "all",
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

    logger.info("Found %s total receipt places", len(all_places))

    # Count receipts per merchant and collect receipts
    merchant_counts: Counter = Counter()
    receipts_by_merchant: dict[str, list[dict]] = {}

    for place in all_places:
        merchant_name = place.merchant_name

        # Apply filter if provided
        if filter_names and merchant_name not in filter_names:
            continue

        merchant_counts[merchant_name] += 1

        if merchant_name not in receipts_by_merchant:
            receipts_by_merchant[merchant_name] = []

        # Apply per-merchant limit
        if limit and len(receipts_by_merchant[merchant_name]) >= limit:
            continue

        receipts_by_merchant[merchant_name].append({
            "image_id": place.image_id,
            "receipt_id": place.receipt_id,
            "merchant_name": merchant_name,
        })

    # Filter merchants by minimum receipt threshold
    qualifying_merchants: list[MerchantInfo] = []
    skipped_count = 0

    for merchant_name, count in sorted(
        merchant_counts.items(), key=lambda x: -x[1]
    ):
        if count >= min_receipts:
            qualifying_merchants.append(
                MerchantInfo(merchant_name=merchant_name, receipt_count=count)
            )
        else:
            skipped_count += 1
            # Remove receipts for merchants that don't qualify
            receipts_by_merchant.pop(merchant_name, None)

    # Flatten receipts into a single list, sorted by merchant for S3 cache locality
    all_receipts = []
    for merchant_name in sorted(receipts_by_merchant.keys()):
        all_receipts.extend(receipts_by_merchant[merchant_name])

    logger.info(
        "Found %s merchants with >= %s receipts (%s total receipts). Skipped %s merchants.",
        len(qualifying_merchants),
        min_receipts,
        len(all_receipts),
        skipped_count,
    )

    # Split into batches for Step Functions state size safety
    receipt_batches = []
    for i in range(0, len(all_receipts), batch_size):
        receipt_batches.append(all_receipts[i : i + batch_size])

    logger.info(
        "Created %s receipt batches of size %s",
        len(receipt_batches),
        batch_size,
    )

    # Upload manifest to S3 for reference
    manifest = {
        "execution_id": execution_id,
        "min_receipts": min_receipts,
        "max_training_receipts": max_training_receipts,
        "batch_size": batch_size,
        "merchants": qualifying_merchants,
        "total_merchants": len(qualifying_merchants),
        "total_receipts": len(all_receipts),
        "total_batches": len(receipt_batches),
        "skipped_merchants": skipped_count,
        "filter_applied": filter_names is not None,
    }

    manifest_key = f"manifests/{execution_id}/all_receipts.json"
    try:
        s3.put_object(
            Bucket=batch_bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Created manifest at s3://%s/%s", batch_bucket, manifest_key)
    except Exception:
        logger.exception("Failed to upload manifest to %s", manifest_key)
        manifest_key = None

    return {
        "merchants": qualifying_merchants,
        "receipt_batches": receipt_batches,
        "total_merchants": len(qualifying_merchants),
        "total_receipts": len(all_receipts),
        "total_batches": len(receipt_batches),
        "max_training_receipts": max_training_receipts,
        "min_receipts": min_receipts,
        "manifest_s3_key": manifest_key,
    }
