"""
Flatten Merchant Groups Handler (Zip Lambda)

Reads all manifest files from S3 (one per label type), combines them,
and writes a single combined manifest to S3 to avoid Step Functions payload limits.
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
    Flatten merchant groups from all label type manifests.

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "prepare_results": [
            {"manifest_s3_key": "batches/.../MERCHANT_NAME/manifest.json", ...},
            ...
        ]
    }

    Output:
    {
        "work_items_manifest_s3_key": "batches/.../combined_manifest.json",
        "total_work_items": 1234
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get(
        "BATCH_BUCKET", ""
    )
    prepare_results = event.get("prepare_results", [])

    logger.info(
        "Flattening merchant groups for execution %s, %d label types",
        execution_id,
        len(prepare_results),
    )

    if not batch_bucket:
        raise ValueError("BATCH_BUCKET not set")

    # Read all manifests from S3
    all_work_items: List[Dict[str, Any]] = []
    for result in prepare_results:
        if not isinstance(result, dict):
            logger.warning("Skipping non-dict result: %s", result)
            continue

        manifest_key = result.get("manifest_s3_key")
        if not manifest_key:
            logger.warning("Missing manifest_s3_key in result: %s", result)
            continue

        try:
            response = s3.get_object(Bucket=batch_bucket, Key=manifest_key)
            manifest_content = response["Body"].read().decode("utf-8")
            merchant_groups = json.loads(manifest_content)
            all_work_items.extend(merchant_groups)
            logger.info(
                "Loaded %d merchant groups from %s",
                len(merchant_groups),
                manifest_key,
            )
        except Exception as e:
            logger.error("Failed to read manifest %s: %s", manifest_key, e)
            raise

    logger.info("Combined %d total work items", len(all_work_items))

    # Write combined manifest to S3
    combined_manifest_key = f"batches/{execution_id}/combined_manifest.json"
    manifest_content = json.dumps(all_work_items, default=str)
    s3.put_object(
        Bucket=batch_bucket,
        Key=combined_manifest_key,
        Body=manifest_content.encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        "Wrote combined manifest with %d items to s3://%s/%s",
        len(all_work_items),
        batch_bucket,
        combined_manifest_key,
    )

    # Return only the S3 key (minimal payload)
    return {
        "work_items_manifest_s3_key": combined_manifest_key,
        "total_work_items": len(all_work_items),
    }
