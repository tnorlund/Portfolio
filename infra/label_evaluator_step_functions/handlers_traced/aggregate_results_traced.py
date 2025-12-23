"""Aggregate results with trace propagation.

This handler resumes the trace and creates a child span for
aggregating evaluation results across all receipts for a merchant.
"""

import json
import logging
import os
from typing import Any

import boto3

# Import tracing utilities
import sys
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lambdas", "utils"
))
from tracing import flush_langsmith_traces, resume_trace

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Aggregate evaluation results for a merchant with trace propagation.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "process_results": [[[{...receipt results...}]]],
        "merchant_name": "Sprouts",
        "langsmith_headers": {...}
    }

    Output:
    {
        "total_receipts": 45,
        "total_issues": 12,
        "successful_evaluations": 44,
        "failed_evaluations": 1,
        "summary_s3_key": "summaries/{exec}/{merchant}.json",
        "langsmith_headers": {...}
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    process_results = event.get("process_results", [])
    merchant_name = event.get("merchant_name", "Unknown")

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    # Resume the trace as a child
    with resume_trace(
        f"aggregate_results:{merchant_name[:20]}",
        event,
        metadata={"merchant_name": merchant_name},
        tags=["aggregate-results"],
    ) as trace_ctx:

        logger.info(
            "Aggregating results for merchant '%s'",
            merchant_name,
        )

        # Flatten nested results from Map states
        all_results = []
        for batch in process_results:
            if isinstance(batch, list):
                for receipt_batch in batch:
                    if isinstance(receipt_batch, list):
                        all_results.extend(receipt_batch)
                    elif isinstance(receipt_batch, dict):
                        all_results.append(receipt_batch)
            elif isinstance(batch, dict):
                all_results.append(batch)

        # Aggregate statistics
        total_receipts = len(all_results)
        total_issues = 0
        successful = 0
        failed = 0
        issues_by_type: dict[str, int] = {}

        for result in all_results:
            if result.get("status") == "completed":
                successful += 1
                issues_found = result.get("issues_found", 0)
                total_issues += issues_found
            else:
                failed += 1

        logger.info(
            "Aggregated %s receipts: %s successful, %s failed, %s total issues",
            total_receipts,
            successful,
            failed,
            total_issues,
        )

        # Save summary to S3
        safe_merchant = "".join(
            c if c.isalnum() else "_" for c in merchant_name
        )[:50]
        summary_key = f"summaries/{execution_id}/{safe_merchant}.json"

        summary = {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "total_receipts": total_receipts,
            "successful_evaluations": successful,
            "failed_evaluations": failed,
            "total_issues": total_issues,
            "issues_by_type": issues_by_type,
            "results": all_results,
        }

        try:
            s3.put_object(
                Bucket=batch_bucket,
                Key=summary_key,
                Body=json.dumps(summary, indent=2).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(
                "Saved summary to s3://%s/%s",
                batch_bucket,
                summary_key,
            )
        except Exception:
            logger.exception("Failed to save summary")

        result = {
            "total_receipts": total_receipts,
            "total_issues": total_issues,
            "successful_evaluations": successful,
            "failed_evaluations": failed,
            "summary_s3_key": summary_key,
            "merchant_name": merchant_name,
        }

        # Add trace headers for propagation
        output = trace_ctx.wrap_output(result)

    # Flush traces before Lambda exits
    flush_langsmith_traces()

    return output
