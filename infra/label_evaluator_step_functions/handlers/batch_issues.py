"""Batch issues for the traced Step Function.

This is a zip-based Lambda that doesn't have access to langsmith.
Tracing is handled by the container-based Lambdas.
"""

import logging
import os
import sys
from collections import defaultdict
from typing import Any

import boto3

# Import S3 helpers from lambdas
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lambdas", "utils"
))
from s3_helpers import get_merchant_hash, load_json_from_s3, upload_json_to_s3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Batch issues by receipt for parallel LLM review.

    Each receipt with issues gets its own batch, enabling one LLM call per receipt.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "issues_s3_key": "issues/{exec}/{merchant_hash}.json",
        "dry_run": false
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "total_issues": 150,
        "batch_count": 8,
        "batch_manifest_s3_key": "batches/{exec}/{merchant_hash}_manifest.json",
        "batches": [
            {
                "batch_index": 0,
                "batch_s3_key": "batches/{exec}/{merchant_hash}_r0.json",
                "image_id": "img_abc",
                "receipt_id": 1,
                "issue_count": 5
            },
            ...
        ],
        "dry_run": false
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    merchant_receipt_count = event.get("merchant_receipt_count", 0)
    issues_s3_key = event.get("issues_s3_key")
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

    if issues_data is None:
        logger.warning(
            "No issues data found at s3://%s/%s",
            batch_bucket,
            issues_s3_key,
        )
        all_issues = []
    else:
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

    # Group issues by receipt (one batch per receipt)
    issues_by_receipt: dict[str, list[dict]] = defaultdict(list)
    for issue in all_issues:
        image_id = issue.get("image_id", "unknown")
        receipt_id = issue.get("receipt_id", 0)
        receipt_key = f"{image_id}:{receipt_id}"
        issues_by_receipt[receipt_key].append(issue)

    batches_info = []
    merchant_hash = get_merchant_hash(merchant_name)

    for receipt_idx, (receipt_key, receipt_issues) in enumerate(
        issues_by_receipt.items()
    ):
        image_id, receipt_id_str = receipt_key.split(":", 1)
        receipt_id = int(receipt_id_str)

        # Upload batch for this receipt to S3
        batch_s3_key = (
            f"batches/{execution_id}/{merchant_hash}_r{receipt_idx}.json"
        )
        batch_data = {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "merchant_receipt_count": merchant_receipt_count,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "batch_index": receipt_idx,
            "issue_count": len(receipt_issues),
            "issues": receipt_issues,
            "dry_run": dry_run,
        }
        upload_json_to_s3(s3, batch_bucket, batch_s3_key, batch_data)

        batches_info.append(
            {
                "batch_index": receipt_idx,
                "batch_s3_key": batch_s3_key,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "issue_count": len(receipt_issues),
            }
        )

    batch_count = len(batches_info)
    logger.info(
        f"Created {batch_count} receipt batches ({total_issues} issues) "
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
        "receipts_with_issues": batch_count,  # One batch per receipt
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
