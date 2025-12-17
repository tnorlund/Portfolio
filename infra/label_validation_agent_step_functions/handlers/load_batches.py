"""
Load Batches Handler (Zip Lambda)

Reads the batch manifest from S3 and returns only indices
to avoid Step Functions payload size limits. Each ValidateLabels Lambda
will read its specific batch file from the manifest using the index.
"""

import json
import logging
import os
from typing import Any, Dict

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Load batch manifest and return indices only (best practice for large payloads).

    Input:
    {
        "manifest_s3_key": "batches/abc123/manifest.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name"
    }

    Output:
    {
        "batch_indices": [0, 1, 2, ...],  # Only indices, not full objects
        "manifest_s3_key": "batches/abc123/manifest.json",
        "total_batches": 25
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET", "")
    manifest_key = event.get("manifest_s3_key")
    langchain_project = event.get("langchain_project")  # Pass through for downstream Lambdas

    if not manifest_key:
        raise ValueError("manifest_s3_key not provided")

    if not batch_bucket:
        raise ValueError("BATCH_BUCKET not set")

    logger.info(
        "Loading batch manifest from s3://%s/%s for execution %s",
        batch_bucket,
        manifest_key,
        execution_id,
    )

    try:
        response = s3.get_object(Bucket=batch_bucket, Key=manifest_key)
        manifest_content = response["Body"].read().decode("utf-8")
        batches = json.loads(manifest_content)
        total_batches = len(batches)
        logger.info("Loaded manifest with %d batches", total_batches)

        # Return only indices to minimize Step Functions payload
        # Each ValidateLabels Lambda will read its specific batch from the manifest using the index
        result = {
            "batch_indices": list(range(total_batches)),
            "manifest_s3_key": manifest_key,
            "total_batches": total_batches,
        }
        # Pass through langchain_project if provided
        if langchain_project:
            result["langchain_project"] = langchain_project
        return result
    except Exception as e:
        logger.error("Failed to load batches from %s: %s", manifest_key, e)
        raise







