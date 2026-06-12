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
        if (
            e.response.get("Error", {}).get("Code") == "NoSuchKey"
            and allow_missing
        ):
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
    """Download ChromaDB snapshot from S3 using ``receipt_chroma``.

    Delegates to :func:`receipt_chroma.s3.download_snapshot_atomic` which
    handles atomic pointer resolution, compressed (tar.gz) and legacy
    uncompressed formats, parallel downloads, and integrity verification.

    A completion marker (``.download_complete``) is checked first so that
    warm Lambda invocations skip re-downloading.

    Args:
        s3_client: Boto3 S3 client (passed through to receipt_chroma)
        bucket: S3 bucket containing ChromaDB snapshots
        collection: Collection name (``"words"`` or ``"lines"``)
        cache_path: Local path to cache the downloaded files

    Returns:
        Path to the cached ChromaDB directory
    """
    from receipt_chroma.s3 import download_snapshot_atomic

    # Use completion marker to skip re-download on warm Lambda starts.
    completion_marker = os.path.join(cache_path, ".download_complete")
    if os.path.exists(completion_marker):
        logger.info("ChromaDB already cached at %s", cache_path)
        return cache_path

    logger.info("Downloading ChromaDB from s3://%s/%s/", bucket, collection)

    result = download_snapshot_atomic(
        bucket=bucket,
        collection=collection,
        local_path=cache_path,
        verify_integrity=False,
        s3_client=s3_client,
    )

    status = result.get("status", "error")
    version = result.get("version_id", "unknown")
    logger.info(
        "Snapshot download result: status=%s, collection=%s, version=%s",
        status,
        collection,
        version,
    )

    if status != "downloaded" and not result.get("initialized"):
        raise RuntimeError(
            f"Failed to download {collection} snapshot: {result}"
        )

    # Create completion marker after successful download.
    with open(completion_marker, "w") as f:
        f.write(version)

    return cache_path
