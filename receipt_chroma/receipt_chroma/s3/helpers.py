"""Low-level S3 helper functions for ChromaDB operations."""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def download_snapshot_from_s3(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    verify_integrity: bool = False,
    region: Optional[str] = None,
    s3_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Download a ChromaDB snapshot from S3 to local filesystem.

    Args:
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot
        local_snapshot_path: Local directory to download to
        verify_integrity: Whether to verify file integrity after download
        region: Optional AWS region
        s3_client: Optional boto3 S3 client (creates one if not provided)

    Returns:
        Dict with download status and statistics
    """
    logger.info(
        "Starting S3 snapshot download: bucket=%s, key=%s, local_path=%s",
        bucket,
        snapshot_key,
        local_snapshot_path,
    )

    if s3_client is None:
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3_client = boto3.client(**client_kwargs)

    try:
        # Create local directory
        local_path = Path(local_snapshot_path)
        local_path.mkdir(parents=True, exist_ok=True)
        logger.info("Created local directory: %s", local_path)

        # List all objects in the snapshot
        full_prefix = snapshot_key.rstrip("/") + "/"
        logger.info("Listing S3 objects with prefix: %s", full_prefix)
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=full_prefix)

        file_count = 0
        total_size = 0

        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                s3_key = obj["Key"]

                # Calculate local file path
                relative_path = s3_key[len(snapshot_key.rstrip("/") + "/") :]
                if not relative_path:  # Skip the directory itself
                    continue

                local_file = local_path / relative_path

                # Create parent directories
                local_file.parent.mkdir(parents=True, exist_ok=True)

                # Download file
                s3_client.download_file(bucket, s3_key, str(local_file))

                file_count += 1
                total_size += obj.get("Size", 0)

                # Verify integrity if requested
                if verify_integrity and local_file.exists():
                    actual_size = local_file.stat().st_size
                    expected_size = obj.get("Size", 0)
                    if actual_size != expected_size:
                        logger.warning(
                            "Size mismatch for %s: expected %s, got %s",
                            s3_key,
                            expected_size,
                            actual_size,
                        )

        # If no files were downloaded, return error status
        if file_count == 0:
            return {
                "status": "failed",
                "error": f"No objects found at s3://{bucket}/{snapshot_key}",
                "snapshot_key": snapshot_key,
            }

        return {
            "status": "downloaded",
            "snapshot_key": snapshot_key,
            "local_path": str(local_path),
            "file_count": file_count,
            "total_size_bytes": total_size,
        }

    except Exception as e:
        logger.error("Error downloading snapshot from S3: %s", e)
        return {"status": "failed", "error": str(e)}


def upload_snapshot_with_hash(
    local_snapshot_path: str,
    bucket: str,
    snapshot_key: str,
    calculate_hash: bool = True,
    hash_algorithm: str = "md5",
    metadata: Optional[Dict[str, Any]] = None,
    region: Optional[str] = None,
    clear_destination: bool = True,
    s3_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Upload ChromaDB snapshot to S3 with optional hash calculation.

    Args:
        local_snapshot_path: Path to the local snapshot directory
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot
        calculate_hash: Whether to calculate and store hash (default: True)
        hash_algorithm: Hash algorithm to use ("md5", "sha256", etc.)
        metadata: Optional metadata to include in S3 objects
        region: Optional AWS region
        clear_destination: Whether to clear S3 destination before upload
        s3_client: Optional boto3 S3 client (creates one if not provided)

    Returns:
        Dict with upload status, hash info, and statistics
    """
    logger.info(
        "Starting snapshot upload with hash: local_path=%s, bucket=%s, key=%s, clear=%s",
        local_snapshot_path,
        bucket,
        snapshot_key,
        clear_destination,
    )

    if s3_client is None:
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3_client = boto3.client(**client_kwargs)

    try:
        snapshot_path = Path(local_snapshot_path)
        if not snapshot_path.exists():
            logger.error(
                "Local snapshot path does not exist: %s", local_snapshot_path
            )
            return {
                "status": "failed",
                "error": f"Snapshot path does not exist: {local_snapshot_path}",
            }

        # Clear destination directory if requested
        if clear_destination:
            logger.info("Clearing S3 destination before upload...")
            _cleanup_s3_prefix(s3_client, bucket, snapshot_key)
            logger.info("Cleared S3 destination: %s", snapshot_key)

        # Calculate hash before upload if requested
        hash_value = None
        if calculate_hash:
            try:
                # Simple hash calculation - sum of file sizes and modification times
                # This is a lightweight alternative to full content hashing
                import hashlib

                hash_obj = hashlib.new(hash_algorithm)
                for file_path in snapshot_path.rglob("*"):
                    if file_path.is_file():
                        stat = file_path.stat()
                        hash_obj.update(
                            f"{file_path.relative_to(snapshot_path)}:{stat.st_size}:{stat.st_mtime}".encode()
                        )
                hash_value = hash_obj.hexdigest()
                logger.info(
                    "Calculated %s hash: %s", hash_algorithm, hash_value
                )
            except Exception as e:
                logger.warning("Failed to calculate hash: %s", e)
                hash_value = None

        # Upload all files
        file_count = 0
        total_size = 0

        for file_path in snapshot_path.rglob("*"):
            if not file_path.is_file():
                continue

            relative = file_path.relative_to(snapshot_path)
            s3_key = f"{snapshot_key.rstrip('/')}/{relative}"

            # Upload file
            extra_args = {}
            if metadata:
                extra_args["Metadata"] = metadata

            s3_client.upload_file(
                str(file_path), bucket, s3_key, ExtraArgs=extra_args
            )

            file_count += 1
            total_size += file_path.stat().st_size

        # Upload hash file if calculated
        if hash_value:
            hash_key = f"{snapshot_key.rstrip('/')}/.snapshot_hash"
            hash_content = f"{hash_algorithm}:{hash_value}"
            s3_client.put_object(
                Bucket=bucket,
                Key=hash_key,
                Body=hash_content.encode("utf-8"),
                ContentType="text/plain",
                Metadata=metadata or {},
            )

        logger.info(
            "Uploaded snapshot: %d files, %d bytes, hash=%s",
            file_count,
            total_size,
            hash_value or "not_calculated",
        )

        return {
            "status": "uploaded",
            "snapshot_key": snapshot_key,
            "file_count": file_count,
            "total_size_bytes": total_size,
            "hash": hash_value,
            "hash_algorithm": hash_algorithm if hash_value else None,
        }

    except Exception as e:
        logger.error("Error uploading snapshot to S3: %s", e)
        return {"status": "failed", "error": str(e)}


def _cleanup_s3_prefix(s3_client: Any, bucket: str, prefix: str) -> None:
    """Helper function to delete all objects under an S3 prefix."""
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        if "Contents" in page:
            delete_keys = [{"Key": obj["Key"]} for obj in page["Contents"]]
            if delete_keys:
                s3_client.delete_objects(
                    Bucket=bucket, Delete={"Objects": delete_keys}
                )


def _cleanup_old_snapshot_versions(
    s3_client: Any, bucket: str, collection: str, keep_versions: int
) -> None:
    """Helper function to clean up old timestamped snapshot versions."""
    timestamped_prefix = f"{collection}/snapshot/timestamped/"

    # List all timestamped versions
    paginator = s3_client.get_paginator("list_objects_v2")
    versions = []

    for page in paginator.paginate(
        Bucket=bucket, Prefix=timestamped_prefix, Delimiter="/"
    ):
        if "CommonPrefixes" in page:
            for prefix_info in page["CommonPrefixes"]:
                version_path = prefix_info["Prefix"]
                # Extract version ID from path like "lines/snapshot/timestamped/20250826_143052/"
                version_id = version_path.split("/")[-2]
                versions.append(version_id)

    # Sort versions by timestamp (newest first)
    versions.sort(reverse=True)

    # Delete old versions beyond keep_versions
    versions_to_delete = versions[keep_versions:]
    for version_id in versions_to_delete:
        version_prefix = f"{collection}/snapshot/timestamped/{version_id}/"
        try:
            _cleanup_s3_prefix(s3_client, bucket, version_prefix)
            logger.info("Cleaned up old snapshot version: %s", version_prefix)
        except Exception as e:
            logger.warning(
                "Failed to cleanup version %s: %s", version_prefix, e
            )
