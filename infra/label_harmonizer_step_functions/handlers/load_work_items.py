"""
Load Work Items Handler (Zip Lambda)

Reads the combined work items manifest from S3 and returns only indices
to avoid Step Functions payload size limits. Each HarmonizeLabels Lambda
will read its specific work item from S3 using the index.
"""

import json
import logging
import os
from typing import Any, Dict, List

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Load work items manifest and return indices only (best practice for large payloads).

    Input:
    {
        "work_items_manifest_s3_key": "batches/.../combined_manifest.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name"
    }

    Output:
    {
        "work_item_indices": [0, 1, 2, ...],  # Only indices, not full objects
        "work_items_manifest_s3_key": "batches/.../combined_manifest.json",
        "total_work_items": 1234
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET", "")
    manifest_key = event.get("work_items_manifest_s3_key")
    langchain_project = event.get(
        "langchain_project"
    )  # Pass through for downstream Lambdas

    if not manifest_key:
        raise ValueError("work_items_manifest_s3_key not provided")

    if not batch_bucket:
        raise ValueError("BATCH_BUCKET not set")

    logger.info(
        "Loading work items manifest from s3://%s/%s for execution %s",
        batch_bucket,
        manifest_key,
        execution_id,
    )

    try:
        response = s3.get_object(Bucket=batch_bucket, Key=manifest_key)
        manifest_content = response["Body"].read().decode("utf-8")
        work_items = json.loads(manifest_content)
        total_items = len(work_items)
        logger.info("Loaded manifest with %d work items", total_items)

        # Return only indices to minimize Step Functions payload
        # Each HarmonizeLabels Lambda will read its specific item from S3 using the index
        result = {
            "work_item_indices": list(range(total_items)),
            "work_items_manifest_s3_key": manifest_key,
            "total_work_items": total_items,
        }
        # Pass through langchain_project if provided
        if langchain_project:
            result["langchain_project"] = langchain_project
        return result
    except Exception as e:
        logger.error("Failed to load work items from %s: %s", manifest_key, e)
        raise
