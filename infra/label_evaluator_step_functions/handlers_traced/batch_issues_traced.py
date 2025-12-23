"""Batch issues with trace propagation.

This handler resumes the trace and creates a child span for
batching issues for parallel LLM review.
"""

import logging
import os
import sys
from typing import Any

import boto3

# Import tracing utilities
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lambdas", "utils"
))
from tracing import child_trace, flush_langsmith_traces, resume_trace
from s3_helpers import get_merchant_hash, load_json_from_s3, upload_json_to_s3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """
    Batch issues for parallel LLM review with trace propagation.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "issues_s3_key": "issues/{exec}/{merchant_hash}.json",
        "batch_size": 50,
        "dry_run": false,
        "langsmith_headers": {...}
    }

    Output:
    {
        "execution_id": "abc123",
        "merchant_name": "Sprouts Farmers Market",
        "merchant_receipt_count": 50,
        "total_issues": 150,
        "batch_count": 3,
        "batch_manifest_s3_key": "batches/{exec}/{merchant_hash}_manifest.json",
        "batches": [...],
        "dry_run": false,
        "langsmith_headers": {...}
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    merchant_name = event.get("merchant_name", "Unknown")
    merchant_receipt_count = event.get("merchant_receipt_count", 0)
    issues_s3_key = event.get("issues_s3_key")
    batch_size = event.get("batch_size", 25)
    dry_run = event.get("dry_run", False)

    if not batch_bucket:
        raise ValueError("batch_bucket is required")
    if not issues_s3_key:
        raise ValueError("issues_s3_key is required")

    # Resume the trace as a child
    with resume_trace(
        f"batch_issues:{merchant_name[:20]}",
        event,
        metadata={
            "merchant_name": merchant_name,
            "batch_size": batch_size,
        },
        tags=["batch-issues"],
    ) as trace_ctx:

        # Load collected issues
        with child_trace("load_issues", trace_ctx):
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
            result = {
                "execution_id": execution_id,
                "merchant_name": merchant_name,
                "merchant_receipt_count": merchant_receipt_count,
                "total_issues": 0,
                "batch_count": 0,
                "batch_manifest_s3_key": None,
                "batches": [],
                "dry_run": dry_run,
            }
            output = trace_ctx.wrap_output(result)
            flush_langsmith_traces()
            return output

        # Split into batches
        with child_trace("create_batches", trace_ctx, metadata={
            "total_issues": total_issues,
            "batch_size": batch_size,
        }):
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
        with child_trace("upload_manifest", trace_ctx):
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

        result = {
            "execution_id": execution_id,
            "merchant_name": merchant_name,
            "merchant_receipt_count": merchant_receipt_count,
            "total_issues": total_issues,
            "batch_count": batch_count,
            "batch_manifest_s3_key": manifest_s3_key,
            "batches": batches_info,
            "dry_run": dry_run,
        }

        output = trace_ctx.wrap_output(result)

    # Flush traces before Lambda exits
    flush_langsmith_traces()

    return output
