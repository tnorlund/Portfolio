"""
Aggregate Results Handler (Zip Lambda)

Combines all validation results and generates a summary report.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def _load_results_from_s3(batch_bucket: str, execution_id: str) -> List[Dict]:
    """Load all results from S3 for the given execution_id."""
    results: List[Dict] = []
    prefix = f"results/{execution_id}/"

    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=batch_bucket, Prefix=prefix)

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".json"):
                    try:
                        response = s3.get_object(Bucket=batch_bucket, Key=key)
                        result = json.loads(response["Body"].read().decode("utf-8"))
                        results.append(result)
                    except Exception as e:
                        logger.warning(f"Failed to load result from {key}: {e}")

        logger.info(f"Loaded {len(results)} results from S3")
    except Exception as e:
        logger.error(f"Failed to load results from S3: {e}")
        raise

    return results


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Aggregate results from all validation runs.

    Input:
    {
        "execution_id": "abc123",
        "process_results": [
            {"status": "completed", "valid_count": 30, ...},
            ...
        ],
        "dry_run": true
    }

    Output:
    {
        "execution_id": "abc123",
        "summary": {
            "total_labels_processed": 12345,
            "total_valid": 8000,
            "total_invalid": 3000,
            "total_needs_review": 1345,
            "total_updated": 11000,
            ...
        },
        "report_path": "s3://bucket/reports/..."
    }
    """
    execution_id = event.get("execution_id", "unknown")
    process_results: List[Dict] = event.get("process_results", [])
    dry_run = event.get("dry_run", True)
    batch_bucket = os.environ.get("BATCH_BUCKET", "")

    logger.info(f"Aggregating results for execution {execution_id}")

    # Always read results from S3 to avoid 256KB payload limit
    logger.info("Reading results from S3 (payload only contains batch_count)...")
    process_results = _load_results_from_s3(batch_bucket, execution_id)

    logger.info(f"Processing {len(process_results)} results from S3")

    # Aggregate metrics
    total_labels = 0
    total_valid = 0
    total_invalid = 0
    total_needs_review = 0
    total_updated = 0
    total_skipped = 0
    total_failed = 0
    label_type_stats: Dict[str, Dict] = {}
    errors: List[Dict] = []

    for result in process_results:
        if not isinstance(result, dict):
            logger.warning(f"Skipping non-dict result: {result}")
            continue

        status = result.get("status", "unknown")
        if status == "error":
            errors.append(result)
            continue

        labels_processed = result.get("labels_processed", 0)
        valid_count = result.get("valid_count", 0)
        invalid_count = result.get("invalid_count", 0)
        needs_review_count = result.get("needs_review_count", 0)
        updated_count = result.get("updated_count", 0)
        skipped_count = result.get("skipped_count", 0)
        failed_count = result.get("failed_count", 0)

        total_labels += labels_processed
        total_valid += valid_count
        total_invalid += invalid_count
        total_needs_review += needs_review_count
        total_updated += updated_count
        total_skipped += skipped_count
        total_failed += failed_count

        # Aggregate by label type (if available in results)
        # Note: Results may not have label_type if processing mixed batches
        # We can extract from individual label results if needed

    # Build summary
    summary = {
        "execution_id": execution_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "dry_run": dry_run,
        "totals": {
            "labels_processed": total_labels,
            "valid_count": total_valid,
            "invalid_count": total_invalid,
            "needs_review_count": total_needs_review,
            "updated_count": total_updated,
            "skipped_count": total_skipped,
            "failed_count": total_failed,
            "errors": len(errors),
        },
        "by_label_type": label_type_stats,
        "errors": errors[:10],  # First 10 errors
    }

    logger.info(
        f"Summary: {total_labels} labels, {total_valid} VALID, "
        f"{total_invalid} INVALID, {total_needs_review} NEEDS_REVIEW, "
        f"{total_updated} updated, {len(errors)} errors"
    )

    # Upload full report to S3
    report_key = f"reports/{execution_id}/summary.json"
    if batch_bucket:
        try:
            s3.put_object(
                Bucket=batch_bucket,
                Key=report_key,
                Body=json.dumps(summary, indent=2, default=str).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"Report uploaded to s3://{batch_bucket}/{report_key}")
        except Exception as e:
            logger.error(f"Failed to upload report: {e}")

    return {
        "execution_id": execution_id,
        "summary": summary,
        "report_path": f"s3://{batch_bucket}/{report_key}" if batch_bucket else None,
    }




