"""Final aggregation with trace propagation.

This handler resumes the trace and creates a child span for
aggregating results across all merchants.
"""

import json
import logging
import os
import sys
from collections import Counter
from typing import Any

import boto3

# Import tracing utilities
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lambdas", "utils"
))
from tracing import child_trace, flush_langsmith_traces, resume_trace

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Aggregate results across all merchants with trace propagation.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "all_merchant_results": [...],
        "langsmith_headers": {...}
    }

    Output:
    {
        "status": "completed",
        "total_merchants": 18,
        "total_receipts": 702,
        "total_issues": 3421,
        "by_issue_type": {...},
        "report_s3_key": "reports/{exec}/grand_summary.json",
        "langsmith_headers": {...}
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_results = event.get("all_merchant_results", [])

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    # Resume the trace as a child
    with resume_trace(
        "final_aggregate",
        event,
        metadata={"merchant_count": len(merchant_results)},
        tags=["final-aggregate"],
    ) as trace_ctx:

        logger.info(
            "Final aggregation for execution %s with %s merchants",
            execution_id,
            len(merchant_results),
        )

        # Aggregate across all merchants
        with child_trace("aggregate_metrics", trace_ctx):
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
        with child_trace("upload_summary", trace_ctx):
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

        result = {
            "status": "completed",
            "execution_id": execution_id,
            "total_merchants": total_merchants,
            "total_receipts": total_receipts,
            "total_issues": total_issues,
            "by_issue_type": dict(issue_type_counter.most_common(10)),
            "report_s3_key": report_key,
        }

        output = trace_ctx.wrap_output(result)

    # Flush traces before Lambda exits
    flush_langsmith_traces()

    return output
