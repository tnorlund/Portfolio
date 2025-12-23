"""Shared S3 helpers for label evaluator Lambdas."""

import json
from typing import Any

import boto3


def load_json_from_s3(
    s3_client: boto3.client,  # type: ignore[type-arg]
    bucket: str,
    key: str,
) -> dict[str, Any]:
    """Load JSON data from S3."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


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
