"""
Aggregate Results Handler (Zip Lambda)

Combines all suggestion results and generates a summary report.
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
    Aggregate results from all suggestion runs.

    Input:
    {
        "execution_id": "abc123",
        "process_results": [
            {"status": "completed", "suggestions_made": 50, ...},
            ...
        ],
        "dry_run": true
    }

    Output:
    {
        "execution_id": "abc123",
        "summary": {
            "total_receipts_processed": 100,
            "total_suggestions_made": 500,
            "total_llm_calls": 20,
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
    total_receipts = 0
    total_suggestions = 0
    total_llm_calls = 0
    total_skipped_no_candidates = 0
    total_skipped_low_confidence = 0
    total_unlabeled_words = 0
    errors: List[Dict] = []

    for result in process_results:
        if not isinstance(result, dict):
            logger.warning(f"Skipping non-dict result: {result}")
            continue

        status = result.get("status", "unknown")
        if status == "error":
            errors.append(result)
            continue

        receipts_processed = result.get("receipts_processed", 0)
        suggestions_made = result.get("suggestions_made", 0)
        llm_calls = result.get("llm_calls", 0)
        skipped_no_candidates = result.get("skipped_no_candidates", 0)
        skipped_low_confidence = result.get("skipped_low_confidence", 0)
        unlabeled_words = result.get("unlabeled_words", 0)

        total_receipts += receipts_processed
        total_suggestions += suggestions_made
        total_llm_calls += llm_calls
        total_skipped_no_candidates += skipped_no_candidates
        total_skipped_low_confidence += skipped_low_confidence
        total_unlabeled_words += unlabeled_words

    # Build summary
    summary = {
        "execution_id": execution_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "dry_run": dry_run,
        "totals": {
            "receipts_processed": total_receipts,
            "unlabeled_words_found": total_unlabeled_words,
            "suggestions_made": total_suggestions,
            "llm_calls": total_llm_calls,
            "skipped_no_candidates": total_skipped_no_candidates,
            "skipped_low_confidence": total_skipped_low_confidence,
            "errors": len(errors),
        },
        "averages": {
            "suggestions_per_receipt": (
                total_suggestions / total_receipts if total_receipts > 0 else 0
            ),
            "llm_calls_per_receipt": (
                total_llm_calls / total_receipts if total_receipts > 0 else 0
            ),
        },
        "errors": errors[:10],  # First 10 errors
    }

    logger.info(
        f"Summary: {total_receipts} receipts, {total_unlabeled_words} unlabeled words, "
        f"{total_suggestions} suggestions made, {total_llm_calls} LLM calls, "
        f"{len(errors)} errors"
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
        "report_path": (f"s3://{batch_bucket}/{report_key}" if batch_bucket else None),
    }
