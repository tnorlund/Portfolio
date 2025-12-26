"""Shared S3 helpers for label evaluator Lambdas."""

import hashlib
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def get_merchant_hash(merchant_name: str) -> str:
    """Create a short, stable hash for merchant name S3 keys."""
    digest = hashlib.sha256(merchant_name.encode("utf-8")).hexdigest()
    return digest[:12]


def load_json_from_s3(
    s3_client: boto3.client,  # type: ignore[type-arg]
    bucket: str,
    key: str,
    logger: Any | None = None,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    """
    Load JSON data from S3.

    Args:
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        key: S3 object key
        logger: Optional logger for error reporting
        allow_missing: If True, return None when object doesn't exist
                       instead of raising an exception

    Returns:
        Parsed JSON data as dict, or None if allow_missing=True and object
        doesn't exist
    """
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey" and allow_missing:
            return None
        if logger is not None:
            logger.exception(
                "Failed to load JSON from s3://%s/%s", bucket, key
            )
        raise
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


def download_chromadb_snapshot(
    s3_client: boto3.client,  # type: ignore[type-arg]
    bucket: str,
    collection: str,
    cache_path: str,
) -> str:
    """
    Download ChromaDB snapshot from S3 using atomic pointer pattern.

    Args:
        s3_client: Boto3 S3 client
        bucket: S3 bucket containing ChromaDB snapshots
        collection: Collection name (e.g., "words")
        cache_path: Local path to cache the downloaded files

    Returns:
        Path to the cached ChromaDB directory
    """
    chroma_db_file = os.path.join(cache_path, "chroma.sqlite3")
    if os.path.exists(chroma_db_file):
        logger.info("ChromaDB already cached at %s", cache_path)
        return cache_path

    logger.info("Downloading ChromaDB from s3://%s/%s/", bucket, collection)

    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=pointer_key)
        timestamp = response["Body"].read().decode().strip()
        logger.info("Latest snapshot: %s", timestamp)
    except Exception:
        logger.exception("Failed to get pointer")
        raise

    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    paginator = s3_client.get_paginator("list_objects_v2")

    os.makedirs(cache_path, exist_ok=True)
    downloaded = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path or key.endswith(".snapshot_hash"):
                continue

            local_path = os.path.join(cache_path, relative_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3_client.download_file(bucket, key, local_path)
            downloaded += 1

    logger.info("Downloaded %d files to %s", downloaded, cache_path)
    return cache_path
