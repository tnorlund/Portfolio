"""Final aggregation for the traced Step Function.

This is a zip-based Lambda that doesn't have access to langsmith.
Tracing is handled by the container-based Lambdas.
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


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Aggregate results across all receipts (two-phase architecture).

    Supports both old format (all_merchant_results) and new format
    (all_batch_results from two-phase Step Function).

    Input (new two-phase format):
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "all_batch_results": [[receipt_results...], ...],
        "pattern_results": [{"merchant_name": ..., "status": "patterns_computed"}, ...],
        "total_merchants": 18,
        "total_receipts": 702
    }

    Input (legacy format):
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "all_merchant_results": [...]
    }

    Output:
    {
        "status": "completed",
        "total_merchants": 18,
        "total_receipts": 702,
        "total_issues": 3421,
        "by_issue_type": {...},
        "report_s3_key": "reports/{exec}/grand_summary.json"
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")

    # Support both old and new formats
    all_batch_results = event.get("all_batch_results", [])
    merchant_results = event.get("all_merchant_results", [])

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    # Aggregate counters
    total_issues = 0
    issue_type_counter: Counter = Counter()
    status_counter: Counter = Counter()
    merchant_stats: dict[str, dict] = {}
    pattern_results = event.get("pattern_results", [])

    # Use input totals from ListAllReceipts if available (two-phase format)
    input_total_merchants = event.get("total_merchants", 0)
    input_total_receipts = event.get("total_receipts", 0)

    # Handle two-phase format (all_batch_results)
    if all_batch_results:
        logger.info(
            "Final aggregation (two-phase) for execution %s with %s batches",
            execution_id,
            len(all_batch_results),
        )

        # Flatten all batch results - each batch contains ProcessReceipts output
        for batch_result in all_batch_results:
            # batch_result is the output of ProcessReceipts map (array of receipt results)
            if not isinstance(batch_result, list):
                continue
            for receipt_result in batch_result:
                if not isinstance(receipt_result, dict):
                    continue

                merchant_name = receipt_result.get("merchant_name", "Unknown")
                status = receipt_result.get("status", "unknown")
                issues_found = receipt_result.get("issues_found", 0)

                # Aggregate by merchant
                if merchant_name not in merchant_stats:
                    merchant_stats[merchant_name] = {
                        "total_receipts": 0,
                        "total_issues": 0,
                        "completed": 0,
                        "errors": 0,
                    }
                merchant_stats[merchant_name]["total_receipts"] += 1
                merchant_stats[merchant_name]["total_issues"] += issues_found
                if status == "completed":
                    merchant_stats[merchant_name]["completed"] += 1
                elif status in ("error", "failed"):
                    merchant_stats[merchant_name]["errors"] += 1

                total_issues += issues_found
                status_counter[status] += 1

        # Use input totals or compute from results
        total_merchants = input_total_merchants or len(merchant_stats)
        total_receipts = input_total_receipts or sum(
            m["total_receipts"] for m in merchant_stats.values()
        )
        successful_merchants = sum(
            1 for m in merchant_stats.values() if m["errors"] == 0
        )
        failed_merchants = sum(
            1 for m in merchant_stats.values() if m["errors"] > 0
        )

        # Build by_merchant list
        by_merchant = [
            {
                "merchant_name": name,
                "total_receipts": stats["total_receipts"],
                "total_issues": stats["total_issues"],
                "status": "completed" if stats["errors"] == 0 else "partial",
            }
            for name, stats in merchant_stats.items()
        ]

    # Handle legacy format (all_merchant_results)
    else:
        logger.info(
            "Final aggregation (legacy) for execution %s with %s merchants",
            execution_id,
            len(merchant_results),
        )

        total_merchants = 0
        total_receipts = 0
        successful_merchants = 0
        failed_merchants = 0
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
            summary = result.get("summary", {})
            if isinstance(summary, dict):
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
        "Grand total: %s merchants, %s receipts, %s issues",
        total_merchants,
        total_receipts,
        total_issues,
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
            "Uploaded grand summary to s3://%s/%s",
            batch_bucket,
            report_key,
        )
    except Exception:
        logger.exception("Failed to upload grand summary")
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
