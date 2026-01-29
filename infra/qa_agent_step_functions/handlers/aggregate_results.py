"""Zip Lambda handler: aggregate Map output and write results to S3.

Receives the list of per-question results from the Map state.
Extracts unique (image_id, receipt_id) pairs from evidence.
Writes question-results.ndjson to S3 for the Spark job.
"""

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Aggregate Map results and write NDJSON to S3.

    Args:
        event: Contains 'results' (list of per-question dicts) and 'execution_id'.

    Returns:
        dict with receipt_keys, counts, and S3 key for NDJSON file.
    """
    results = event["results"]
    execution_id = event["execution_id"]
    batch_bucket = event["batch_bucket"]

    receipt_keys: set[tuple[str, str]] = set()
    for r in results:
        for e in r.get("evidence", []):
            image_id = e.get("imageId") or e.get("image_id")
            receipt_id = e.get("receiptId") or e.get("receipt_id")
            if image_id and receipt_id:
                receipt_keys.add((image_id, str(receipt_id)))

    # Write NDJSON (one JSON line per question result)
    ndjson_key = f"qa-runs/{execution_id}/question-results.ndjson"
    ndjson_lines = "\n".join(json.dumps(r, default=str) for r in results)

    s3_client.put_object(
        Bucket=batch_bucket,
        Key=ndjson_key,
        Body=ndjson_lines.encode("utf-8"),
        ContentType="application/x-ndjson",
    )
    logger.info(
        "Wrote %d question results to s3://%s/%s",
        len(results),
        batch_bucket,
        ndjson_key,
    )

    return {
        "execution_id": execution_id,
        "batch_bucket": batch_bucket,
        "receipt_keys": [
            {"image_id": k[0], "receipt_id": k[1]} for k in receipt_keys
        ],
        "total_questions": len(results),
        "success_count": sum(1 for r in results if r.get("success")),
        "results_ndjson_key": ndjson_key,
    }
