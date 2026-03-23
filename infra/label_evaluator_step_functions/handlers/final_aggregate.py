"""Final aggregation for the traced Step Function.

This is a zip-based Lambda that doesn't have access to langsmith.
Tracing is handled by the container-based Lambdas.
"""

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Aggregate results across all receipts (two-phase architecture).

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "receipt_results": [{...}, {...}, ...],  # Flat list of receipt results
        "pattern_results": [{"merchant_name": ..., "status": "patterns_computed"}, ...],
        "total_merchants": 18,
        "total_receipts": 702
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
    receipt_results = event.get("receipt_results", [])

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    # Aggregate counters
    total_issues = 0
    issue_type_counter: Counter = Counter()
    status_counter: Counter = Counter()
    merchant_stats: dict[str, dict] = {}

    # Use input totals from ListAllReceipts if available
    input_total_merchants = event.get("total_merchants", 0)
    input_total_receipts = event.get("total_receipts", 0)

    logger.info(
        "Final aggregation for execution %s with %s receipts",
        execution_id,
        len(receipt_results),
    )

    # Process flat receipt results
    for receipt_result in receipt_results:
        if not isinstance(receipt_result, dict):
            continue

        merchant_name = receipt_result.get("merchant_name", "Unknown")
        status = receipt_result.get("status", "unknown")
        issues_found = receipt_result.get("issues_found", 0)

        # Aggregate issue types if available
        by_type = receipt_result.get("by_issue_type", {})
        if isinstance(by_type, dict):
            for issue_type, count in by_type.items():
                issue_type_counter[issue_type] += count

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

    logger.info(
        "Grand total: %s merchants, %s receipts, %s issues",
        total_merchants,
        total_receipts,
        total_issues,
    )

    # Count succeeded vs failed receipts
    total_succeeded = status_counter.get("completed", 0)
    total_failed = sum(
        count for status, count in status_counter.items()
        if status in ("error", "failed")
    )

    # Build grand summary
    grand_summary = {
        "execution_id": execution_id,
        "total_merchants": total_merchants,
        "successful_merchants": successful_merchants,
        "failed_merchants": failed_merchants,
        "total_receipts": total_receipts,
        "total_succeeded": total_succeeded,
        "total_failed": total_failed,
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

    # Write receipts/ viz-cache metadata (evaluator owns receipts/)
    viz_cache_bucket = os.environ.get("VIZ_CACHE_BUCKET")
    if viz_cache_bucket:
        try:
            timestamp_now = datetime.now(timezone.utc)
            timestamp_str = timestamp_now.strftime("%Y%m%d-%H%M%S")
            viz_metadata = {
                "version": timestamp_str,
                "execution_id": execution_id,
                "total_receipts": total_receipts,
                "receipts_with_issues": total_issues > 0
                and sum(
                    1
                    for r in receipt_results
                    if isinstance(r, dict) and r.get("issues_found", 0) > 0
                )
                or 0,
                "cached_at": timestamp_now.isoformat(),
            }
            # Recount receipts_with_issues properly
            receipts_with_issues_count = sum(
                1
                for r in receipt_results
                if isinstance(r, dict) and r.get("issues_found", 0) > 0
            )
            viz_metadata["receipts_with_issues"] = receipts_with_issues_count

            s3.put_object(
                Bucket=viz_cache_bucket,
                Key="receipts/metadata.json",
                Body=json.dumps(viz_metadata, indent=2).encode("utf-8"),
                ContentType="application/json",
            )

            # Write latest.json pointer
            latest = {
                "version": timestamp_str,
                "prefix": "receipts/",
                "cached_at": timestamp_now.isoformat(),
            }
            s3.put_object(
                Bucket=viz_cache_bucket,
                Key="latest.json",
                Body=json.dumps(latest, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(
                "Wrote viz-cache metadata to s3://%s/receipts/metadata.json",
                viz_cache_bucket,
            )
        except Exception:
            logger.exception("Failed to write viz-cache metadata")

    return {
        "status": "completed",
        "execution_id": execution_id,
        "total_merchants": total_merchants,
        "total_receipts": total_receipts,
        "total_succeeded": total_succeeded,
        "total_failed": total_failed,
        "total_issues": total_issues,
        "by_issue_type": dict(issue_type_counter.most_common(10)),
        "report_s3_key": report_key,
    }
