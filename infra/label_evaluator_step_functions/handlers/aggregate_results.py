"""Aggregate evaluation results and generate summary report.

This handler collects all individual receipt evaluation results from S3
and generates a summary report with statistics by issue type and merchant.
"""

import json
import logging
import os
from collections import Counter
from typing import TYPE_CHECKING, Any, TypedDict

import boto3

if TYPE_CHECKING:
    from evaluator_types import AggregateResultsOutput


class ReceiptResultSummary(TypedDict, total=False):
    """Summary of a single receipt evaluation."""

    status: str
    image_id: str
    receipt_id: int
    issues_found: int
    results_s3_key: str
    error: str


class IssueDetail(TypedDict, total=False):
    """Details of a detected labeling issue."""

    image_id: str
    receipt_id: int
    type: str
    word_text: str
    current_label: str | None
    suggested_status: str
    reasoning: str


logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> "AggregateResultsOutput":
    """
    Aggregate all evaluation results and generate summary report.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "process_results": [[{"status": "completed", ...}], ...],
        "merchant_name": "Sprouts Farmers Market",
        "dry_run": true
    }

    Output:
    {
        "execution_id": "abc123",
        "summary": {
            "total_receipts": 150,
            "successful_receipts": 148,
            "failed_receipts": 2,
            "total_issues": 23,
            "by_issue_type": {"position_anomaly": 12, "same_line_conflict": 11},
            "by_status": {"NEEDS_REVIEW": 20, "INVALID": 3}
        },
        "report_s3_key": "reports/{exec}/summary.json"
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    process_results = event.get("process_results", [])
    merchant_name = event.get("merchant_name", "Unknown")
    dry_run = event.get("dry_run", True)

    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    logger.info("Aggregating results for execution %s", execution_id)

    # Flatten nested results from distributed map
    all_results: list[ReceiptResultSummary] = []
    for batch_results in process_results:
        if isinstance(batch_results, list):
            all_results.extend(batch_results)
        elif isinstance(batch_results, dict):
            all_results.append(batch_results)  # type: ignore[arg-type]

    # Count statistics
    total_receipts = len(all_results)
    successful_receipts = sum(
        1 for r in all_results if r.get("status") == "completed"
    )
    failed_receipts = sum(1 for r in all_results if r.get("status") == "error")

    # Aggregate issues
    all_issues: list[IssueDetail] = []
    issue_type_counter: Counter = Counter()
    status_counter: Counter = Counter()

    for result in all_results:
        if result.get("status") != "completed":
            continue

        # Load detailed results from S3 if available
        results_key = result.get("results_s3_key")
        if results_key:
            try:
                response = s3.get_object(Bucket=batch_bucket, Key=results_key)
                detailed = json.loads(response["Body"].read().decode("utf-8"))
                issues = detailed.get("issues", [])

                for issue in issues:
                    all_issues.append(
                        {
                            "image_id": result.get("image_id", ""),
                            "receipt_id": result.get("receipt_id", 0),
                            **issue,
                        }
                    )
                    issue_type_counter[issue.get("type", "unknown")] += 1
                    status_counter[
                        issue.get("suggested_status", "unknown")
                    ] += 1

            except Exception as e:
                logger.warning(
                    "Could not load results from %s: %s",
                    results_key,
                    e,
                )
                # Fall back to summary in result
                issues_found = result.get("issues_found", 0)
                issue_type_counter["unknown"] += issues_found
                status_counter["unknown"] += issues_found

    total_issues = sum(issue_type_counter.values())

    logger.info(
        "Aggregated %s receipts, %s successful, %s failed, %s total issues",
        total_receipts,
        successful_receipts,
        failed_receipts,
        total_issues,
    )

    # Build summary
    summary = {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "dry_run": dry_run,
        "total_receipts": total_receipts,
        "successful_receipts": successful_receipts,
        "failed_receipts": failed_receipts,
        "total_issues": total_issues,
        "by_issue_type": dict(issue_type_counter.most_common()),
        "by_status": dict(status_counter.most_common()),
        "sample_issues": all_issues[:20],  # Include sample for review
    }

    # Build detailed report
    report = {
        "execution_id": execution_id,
        "merchant_name": merchant_name,
        "dry_run": dry_run,
        "summary": summary,
        "all_issues": all_issues,
        "receipt_results": [
            {
                "image_id": r.get("image_id"),
                "receipt_id": r.get("receipt_id"),
                "status": r.get("status"),
                "issues_found": r.get("issues_found", 0),
                "results_s3_key": r.get("results_s3_key"),
            }
            for r in all_results
        ],
    }

    # Upload summary report to S3
    report_key = f"reports/{execution_id}/summary.json"
    try:
        s3.put_object(
            Bucket=batch_bucket,
            Key=report_key,
            Body=json.dumps(report, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(
            "Uploaded summary report to s3://%s/%s",
            batch_bucket,
            report_key,
        )
    except Exception as e:
        logger.error("Failed to upload summary report to %s: %s", report_key, e)
        raise

    # Also upload issues-only report for easy analysis
    issues_key = f"reports/{execution_id}/issues.json"
    try:
        s3.put_object(
            Bucket=batch_bucket,
            Key=issues_key,
            Body=json.dumps(all_issues, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(
            "Uploaded issues report to s3://%s/%s",
            batch_bucket,
            issues_key,
        )
    except Exception as e:
        logger.error("Failed to upload issues report to %s: %s", issues_key, e)
        raise

    return {
        "execution_id": execution_id,
        "summary": {
            "total_receipts": total_receipts,
            "successful_receipts": successful_receipts,
            "failed_receipts": failed_receipts,
            "total_issues": total_issues,
            "by_issue_type": dict(issue_type_counter.most_common(5)),
            "by_status": dict(status_counter.most_common()),
        },
        "report_s3_key": report_key,
        "issues_s3_key": issues_key,
    }
