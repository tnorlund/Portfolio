"""Collect issues from all receipts for a merchant.

This handler gathers all flagged issues from the evaluation results
for batch LLM review. It reads individual receipt results from S3
and consolidates them into a single file for efficient processing.
"""

import logging
import os
from typing import TYPE_CHECKING, Any

import boto3

from .utils.s3_helpers import (
    get_merchant_hash,
    load_json_from_s3,
    upload_json_to_s3,
)

if TYPE_CHECKING:
    from evaluator_types import CollectIssuesOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> "CollectIssuesOutput":
    """
    Collect all issues from evaluated receipts for batch LLM review.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "process_results": [
            [  # Batch 0
                [  # Receipt results
                    {"status": "completed", "results_s3_key": "...", ...},
                    ...
                ]
            ],
            ...
        ]
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Sprouts Farmers Market",
        "total_issues": 42,
        "issues_s3_key": "issues/{exec}/{merchant_hash}.json"
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    process_results = event.get("process_results", [])

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(
        f"Collecting issues for {merchant_name} from {len(process_results)} batches"
    )

    # Flatten the nested batch results to get individual receipt results
    receipt_results: list[dict[str, Any]] = []
    for batch in process_results:
        if isinstance(batch, list):
            for receipt_batch in batch:
                if isinstance(receipt_batch, list):
                    receipt_results.extend(receipt_batch)
                elif isinstance(receipt_batch, dict):
                    receipt_results.append(receipt_batch)
        elif isinstance(batch, dict):
            receipt_results.append(batch)

    logger.info(f"Found {len(receipt_results)} receipt results to process")

    # Collect issues from each receipt
    collected_issues: list[dict[str, Any]] = []
    receipts_with_issues = 0
    receipts_processed = 0

    for result in receipt_results:
        if not isinstance(result, dict):
            continue

        status = result.get("status")
        results_s3_key = result.get("results_s3_key")

        if status != "completed" or not results_s3_key:
            continue

        receipts_processed += 1

        try:
            # Load the full evaluation results from S3
            eval_results = load_json_from_s3(
                s3, batch_bucket, results_s3_key, logger=logger
            )
            issues = eval_results.get("issues", [])

            if not issues:
                continue

            receipts_with_issues += 1
            image_id = eval_results.get("image_id")
            receipt_id = eval_results.get("receipt_id")

            # Collect each issue with receipt context
            for issue in issues:
                collected_issues.append(
                    {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "results_s3_key": results_s3_key,
                        "issue": issue,
                    }
                )

        except Exception as e:
            logger.warning(
                f"Failed to load results from {results_s3_key}: {e}"
            )
            continue

    logger.info(
        f"Collected {len(collected_issues)} issues from "
        f"{receipts_with_issues}/{receipts_processed} receipts"
    )

    # Upload collected issues to S3
    merchant_hash = get_merchant_hash(merchant_name)
    issues_s3_key = f"issues/{execution_id}/{merchant_hash}.json"

    issues_data = {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "total_issues": len(collected_issues),
        "receipts_with_issues": receipts_with_issues,
        "receipts_processed": receipts_processed,
        "issues": collected_issues,
    }

    upload_json_to_s3(s3, batch_bucket, issues_s3_key, issues_data)

    logger.info(f"Uploaded issues to s3://{batch_bucket}/{issues_s3_key}")

    return {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "total_issues": len(collected_issues),
        "issues_s3_key": issues_s3_key,
    }
