"""Shared S3 helpers for label evaluator handlers."""

import hashlib
import json
from typing import Any

import boto3


def get_merchant_hash(merchant_name: str) -> str:
    """Create a short, stable hash for merchant name S3 keys."""
    digest = hashlib.sha256(merchant_name.encode("utf-8")).hexdigest()
    return digest[:12]


def load_json_from_s3(
    s3_client: boto3.client,  # type: ignore[type-arg]
    bucket: str,
    key: str,
    logger: Any | None = None,
) -> dict[str, Any]:
    """Load JSON data from S3."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception:
        if logger is not None:
            logger.exception(
                "Failed to load JSON from s3://%s/%s", bucket, key
            )
        raise


def upload_json_to_s3(
    s3_client: boto3.client,  # type: ignore[type-arg]
    bucket: str,
    key: str,
    data: Any,
) -> None:
    """Upload JSON data to S3."""
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )
