"""
Aggregate Results Handler (Zip Lambda)

Aggregates results from all combine receipt operations and generates a summary report.
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


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Aggregate results from combine receipt operations.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "dry_run": true,
        "combine_results": [
            {
                "image_id": "image-uuid",
                "new_receipt_id": 4,
                "original_receipt_ids": [1, 2],
                "status": "success",
                "compaction_run_id": "run-uuid"
            },
            ...
        ]
    }

    Output:
    {
        "summary": {
            "total_images": 5,
            "successful": 4,
            "failed": 1,
            "dry_run": true,
            "execution_id": "abc123"
        },
        "results_s3_key": "results/abc123/summary.json"
    }
    """
    execution_id = event["execution_id"]
    batch_bucket = event.get("batch_bucket") or os.environ["BATCH_BUCKET"]
    dry_run = event.get("dry_run", True)
    combine_results = event.get("combine_results", [])

    logger.info(f"Aggregating results for execution_id={execution_id}")

    # Aggregate statistics
    total_images = len(combine_results)
    successful = sum(
        1 for r in combine_results if r.get("status") == "success"
    )
    no_combination = sum(
        1 for r in combine_results if r.get("status") == "no_combination"
    )
    failed = total_images - successful - no_combination

    # Group by status
    successful_images = [
        r for r in combine_results if r.get("status") == "success"
    ]
    no_combination_images = [
        r for r in combine_results if r.get("status") == "no_combination"
    ]
    failed_images = [
        r
        for r in combine_results
        if r.get("status") not in ("success", "no_combination")
    ]

    summary = {
        "execution_id": execution_id,
        "execution_time": datetime.utcnow().isoformat(),
        "dry_run": dry_run,
        "total_images": total_images,
        "successful": successful,
        "no_combination": no_combination,
        "failed": failed,
        "successful_images": [
            {
                "image_id": r.get("image_id"),
                "new_receipt_id": r.get("new_receipt_id"),
                "original_receipt_ids": r.get("original_receipt_ids"),
                "compaction_run_id": r.get("compaction_run_id"),
            }
            for r in successful_images
        ],
        "no_combination_images": [
            {
                "image_id": r.get("image_id"),
                "original_receipt_ids": r.get("original_receipt_ids")
                or r.get("receipt_ids"),
                "raw_answer": r.get("raw_answer"),
                "candidates": r.get("candidates"),
            }
            for r in no_combination_images
        ],
        "failed_images": [
            {
                "image_id": r.get("image_id"),
                "error": r.get("error"),
                "raw_answer": r.get("raw_answer"),
            }
            for r in failed_images
        ],
    }

    # Save summary to S3
    results_key = f"results/{execution_id}/summary.json"
    s3.put_object(
        Bucket=batch_bucket,
        Key=results_key,
        Body=json.dumps(summary, indent=2),
        ContentType="application/json",
    )

    logger.info(
        f"Summary: {successful}/{total_images} successful, "
        f"{failed} failed (dry_run={dry_run})"
    )

    return {
        "summary": summary,
        "results_s3_key": results_key,
    }
