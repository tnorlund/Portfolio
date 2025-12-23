"""Batch issues for parallel LLM review.

This handler takes the collected issues from a merchant and splits them into
smaller batches for parallel processing. Each batch will be processed by a
separate Lambda invocation in a Distributed Map.
"""

import logging
import os
from typing import Any

import boto3

from .utils.s3_helpers import (
    get_merchant_hash,
    load_json_from_s3,
    upload_json_to_s3,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Batch issues for parallel LLM review.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "issues_s3_key": "issues/{exec}/{merchant_hash}.json",
        "batch_size": 50,  # Optional, defaults to 50
        "dry_run": false
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "total_issues": 150,
        "batch_count": 3,
        "batch_manifest_s3_key": "batches/{exec}/{merchant_hash}_manifest.json",
        "batches": [
            {"batch_index": 0, "batch_s3_key": "batches/{exec}/{merchant_hash}_0.json", "issue_count": 50},
            {"batch_index": 1, "batch_s3_key": "batches/{exec}/{merchant_hash}_1.json", "issue_count": 50},
            {"batch_index": 2, "batch_s3_key": "batches/{exec}/{merchant_hash}_2.json", "issue_count": 50}
        ],
        "dry_run": false
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    merchant_receipt_count = event.get("merchant_receipt_count", 0)
    issues_s3_key = event.get("issues_s3_key")
    batch_size = event.get("batch_size", 25)  # Reduced from 50 to avoid Lambda timeout
    dry_run = event.get("dry_run", False)

    if not batch_bucket:
        raise ValueError("batch_bucket is required")
    if not issues_s3_key:
        raise ValueError("issues_s3_key is required")

    # Load collected issues
    logger.info(f"Loading issues from s3://{batch_bucket}/{issues_s3_key}")
    try:
        issues_data = load_json_from_s3(
            s3, batch_bucket, issues_s3_key, logger=logger
        )
    except Exception:
        logger.exception(
            "Failed to load issues from s3://%s/%s",
            batch_bucket,
            issues_s3_key,
        )
        raise
    all_issues = issues_data.get("issues", [])
    total_issues = len(all_issues)

    if total_issues == 0:
        logger.info("No issues to batch")
        return {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "merchant_receipt_count": merchant_receipt_count,
            "total_issues": 0,
            "batch_count": 0,
            "batch_manifest_s3_key": None,
            "batches": [],
            "dry_run": dry_run,
        }

    # Split into batches
    batches_info = []
    merchant_hash = get_merchant_hash(merchant_name)

    for batch_idx in range(0, total_issues, batch_size):
        batch_issues = all_issues[batch_idx : batch_idx + batch_size]
        batch_num = batch_idx // batch_size

        # Upload batch to S3
        batch_s3_key = (
            f"batches/{execution_id}/{merchant_hash}_{batch_num}.json"
        )
        batch_data = {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "merchant_receipt_count": merchant_receipt_count,
            "batch_index": batch_num,
            "issue_count": len(batch_issues),
            "issues": batch_issues,
            "dry_run": dry_run,
        }
        upload_json_to_s3(s3, batch_bucket, batch_s3_key, batch_data)

        batches_info.append(
            {
                "batch_index": batch_num,
                "batch_s3_key": batch_s3_key,
                "issue_count": len(batch_issues),
            }
        )

    batch_count = len(batches_info)
    logger.info(
        f"Created {batch_count} batches of ~{batch_size} issues each "
        f"for {merchant_name}"
    )

    # Upload manifest
    manifest_s3_key = f"batches/{execution_id}/{merchant_hash}_manifest.json"
    manifest_data = {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "merchant_receipt_count": merchant_receipt_count,
        "total_issues": total_issues,
        "batch_count": batch_count,
        "batch_size": batch_size,
        "batches": batches_info,
        "dry_run": dry_run,
    }
    upload_json_to_s3(s3, batch_bucket, manifest_s3_key, manifest_data)

    logger.info(f"Uploaded manifest to s3://{batch_bucket}/{manifest_s3_key}")

    return {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "merchant_receipt_count": merchant_receipt_count,
        "total_issues": total_issues,
        "batch_count": batch_count,
        "batch_manifest_s3_key": manifest_s3_key,
        "batches": batches_info,
        "dry_run": dry_run,
    }
