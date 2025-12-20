"""Final aggregation across all merchants.

This handler takes the results from all merchants and creates a grand
summary with totals across all receipts.
"""

import json
import logging
import os
from collections import Counter
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Aggregate results across all merchants into a grand summary.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "all_merchant_results": [
            {"merchant_name": "...", "status": "completed", "summary": {...}},
            ...
        ]
    }

    Output:
    {
        "status": "completed",
        "total_merchants": 18,
        "total_receipts": 702,
        "total_issues": 3421,
        "by_issue_type": {...},
        "by_merchant": {...},
        "report_s3_key": "reports/{exec}/grand_summary.json"
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_results = event.get("all_merchant_results", [])

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info(
        f"Final aggregation for execution {execution_id} "
        f"with {len(merchant_results)} merchants"
    )

    # Aggregate across all merchants
    total_merchants = 0
    total_receipts = 0
    total_issues = 0
    successful_merchants = 0
    failed_merchants = 0

    issue_type_counter: Counter = Counter()
    status_counter: Counter = Counter()
    by_merchant: list[dict[str, Any]] = []

    for result in merchant_results:
        if not isinstance(result, dict):
            continue

        merchant_name = result.get("merchant_name", "Unknown")
        status = result.get("status", "unknown")
        receipt_count = result.get("total_receipts", 0)

        total_merchants += 1
        total_receipts += receipt_count

        if status == "completed":
            successful_merchants += 1
        elif status in ("error", "failed"):
            failed_merchants += 1

        # Get summary from merchant result
        # Handle both nested (summary.summary) and flat (summary) structures
        summary = result.get("summary", {})
        if isinstance(summary, dict):
            # Check for nested structure (summary.summary.total_issues)
            inner_summary = summary.get("summary", summary)
            if isinstance(inner_summary, dict):
                merchant_issues = inner_summary.get("total_issues", 0)
                total_issues += merchant_issues

                # Aggregate issue types
                by_type = inner_summary.get("by_issue_type", {})
                if isinstance(by_type, dict):
                    for issue_type, count in by_type.items():
                        issue_type_counter[issue_type] += count

                # Aggregate statuses
                by_status = inner_summary.get("by_status", {})
                if isinstance(by_status, dict):
                    for s, count in by_status.items():
                        status_counter[s] += count

                by_merchant.append(
                    {
                        "merchant_name": merchant_name,
                        "total_receipts": receipt_count,
                        "total_issues": merchant_issues,
                        "status": status,
                    }
                )

    logger.info(
        f"Grand total: {total_merchants} merchants, "
        f"{total_receipts} receipts, {total_issues} issues"
    )

    # Build grand summary
    grand_summary = {
        "execution_id": execution_id,
        "total_merchants": total_merchants,
        "successful_merchants": successful_merchants,
        "failed_merchants": failed_merchants,
        "total_receipts": total_receipts,
        "total_issues": total_issues,
        "by_issue_type": dict(issue_type_counter.most_common()),
        "by_status": dict(status_counter.most_common()),
        "by_merchant": sorted(
            by_merchant, key=lambda x: x.get("total_issues", 0), reverse=True
        ),
    }

    # Upload grand summary to S3
    report_key = f"reports/{execution_id}/grand_summary.json"
    try:
        s3.put_object(
            Bucket=batch_bucket,
            Key=report_key,
            Body=json.dumps(grand_summary, indent=2, default=str).encode(
                "utf-8"
            ),
            ContentType="application/json",
        )
        logger.info(
            f"Uploaded grand summary to s3://{batch_bucket}/{report_key}"
        )
    except Exception as e:
        logger.error(f"Failed to upload grand summary: {e}")
        raise

    return {
        "status": "completed",
        "execution_id": execution_id,
        "total_merchants": total_merchants,
        "total_receipts": total_receipts,
        "total_issues": total_issues,
        "by_issue_type": dict(issue_type_counter.most_common(10)),
        "report_s3_key": report_key,
    }
