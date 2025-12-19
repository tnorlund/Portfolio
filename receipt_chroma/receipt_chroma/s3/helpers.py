"""Low-level S3 helper functions for ChromaDB operations."""

import logging
import os
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional, TypedDict

import boto3
from typing_extensions import NotRequired, Required

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class DownloadResult(TypedDict, total=False):
    """Result from downloading a snapshot from S3."""

    status: Required[Literal["downloaded", "failed"]]  # Always present
    snapshot_key: NotRequired[
        str
    ]  # Usually present, but not in exception cases
    local_path: NotRequired[str]  # Only present on successful download
    file_count: NotRequired[int]  # Only present on successful download
    total_size_bytes: NotRequired[int]  # Only present on successful download
    error: NotRequired[str]  # Only present if status == "failed"


class UploadResult(TypedDict, total=False):
    """Result from uploading a snapshot to S3."""

    status: Literal["uploaded", "failed"]
    snapshot_key: str
    file_count: int
    total_size_bytes: int
    hash: str
    hash_algorithm: str
    error: str  # Only present if status == "failed"


class DeltaTarballResult(TypedDict, total=False):
    """Result from uploading a delta tarball to S3."""

    status: Literal["uploaded", "failed"]
    delta_key: str
    object_key: str
    tar_size_bytes: int
    error: str  # Only present if status == "failed"


def download_snapshot_from_s3(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    verify_integrity: bool = False,
    region: Optional[str] = None,
    s3_client: Optional[S3Client] = None,
) -> DownloadResult:
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
        if region:
            s3_client = boto3.client("s3", region_name=region)
        else:
            s3_client = boto3.client("s3")

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
        logger.exception("Error downloading snapshot from S3")
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
    s3_client: Optional[S3Client] = None,
) -> UploadResult:
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
        "Starting snapshot upload with hash: local_path=%s, bucket=%s, "
        "key=%s, clear=%s",
        local_snapshot_path,
        bucket,
        snapshot_key,
        clear_destination,
    )

    if s3_client is None:
        if region:
            s3_client = boto3.client("s3", region_name=region)
        else:
            s3_client = boto3.client("s3")

    try:
        snapshot_path = Path(local_snapshot_path)
        if not snapshot_path.exists():
            logger.error(
                "Local snapshot path does not exist: %s", local_snapshot_path
            )
            return {
                "status": "failed",
                "error": (
                    f"Snapshot path does not exist: {local_snapshot_path}"
                ),
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
                # Simple hash calculation - sum of file sizes and
                # modification times. This is a lightweight alternative to
                # full content hashing
                import hashlib  # pylint: disable=import-outside-toplevel

                hash_obj = hashlib.new(hash_algorithm)
                for file_path in snapshot_path.rglob("*"):
                    if file_path.is_file():
                        stat = file_path.stat()
                        rel_path = file_path.relative_to(snapshot_path)
                        hash_str = f"{rel_path}:{stat.st_size}:{stat.st_mtime}"
                        hash_obj.update(hash_str.encode())
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
                extra_args["Metadata"] = {
                    k: str(v) for k, v in metadata.items()
                }

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
                Metadata={k: str(v) for k, v in (metadata or {}).items()},
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
        logger.exception("Error uploading snapshot to S3")
        return {"status": "failed", "error": str(e)}


def _cleanup_s3_prefix(s3_client: S3Client, bucket: str, prefix: str) -> None:
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
    s3_client: S3Client, bucket: str, collection: str, keep_versions: int
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
                # Extract version ID from path like
                # "lines/snapshot/timestamped/20250826_143052/"
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


def upload_delta_tarball(
    local_delta_dir: str,
    bucket: str,
    delta_prefix: str,
    metadata: Optional[Dict[str, Any]] = None,
    region: Optional[str] = None,
    s3_client: Optional[S3Client] = None,
) -> DeltaTarballResult:
    """
    Create a gzip-compressed tarball from a delta directory and upload to S3.

    This function is used by producer lambdas to create delta tarballs that
    will be processed by the compaction Lambda. The compaction Lambda expects
    tarballs at {delta_prefix}/delta.tar.gz.

    Args:
        local_delta_dir: Path to local delta directory
            (ChromaDB persist directory)
        bucket: S3 bucket name
        delta_prefix: S3 prefix for the delta (e.g., "lines/delta/{run_id}")
        metadata: Optional metadata to include in S3 object
        region: Optional AWS region
        s3_client: Optional boto3 S3 client (creates one if not provided)

    Returns:
        Dict with status, delta_key (prefix), object_key (full S3 key),
        and tar_size_bytes

    Example:
        >>> result = upload_delta_tarball(
        ...     local_delta_dir="/tmp/delta_123",
        ...     bucket="my-bucket",
        ...     delta_prefix="lines/delta/abc-123"
        ... )
        >>> print(result["object_key"])
        "lines/delta/abc-123/delta.tar.gz"
    """
    logger.info(
        "Bundling and uploading delta tarball: dir=%s bucket=%s prefix=%s",
        local_delta_dir,
        bucket,
        delta_prefix,
    )

    if s3_client is None:
        if region:
            s3_client = boto3.client("s3", region_name=region)
        else:
            s3_client = boto3.client("s3")

    try:
        delta_path = Path(local_delta_dir)
        if not delta_path.exists() or not delta_path.is_dir():
            logger.error(
                "Delta directory does not exist or is not a directory: %s",
                local_delta_dir,
            )
            return {
                "status": "failed",
                "error": f"Delta directory does not exist: {local_delta_dir}",
            }

        # Create tarball in temp directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            tar_path = os.path.join(tmp_dir, "delta.tar.gz")

            # Create gzip-compressed tarball
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(local_delta_dir, arcname=".")

            tar_size = os.path.getsize(tar_path)
            logger.info(
                "Created delta tarball: path=%s, size_bytes=%d",
                tar_path,
                tar_size,
            )

            # Upload tarball to S3
            s3_key = f"{delta_prefix.rstrip('/')}/delta.tar.gz"

            # Prepare metadata
            extra_args = {
                "ContentType": "application/gzip",
            }
            if metadata:
                extra_args["Metadata"] = {
                    k: str(v) for k, v in metadata.items()
                }

            s3_client.upload_file(
                tar_path, bucket, s3_key, ExtraArgs=extra_args
            )

            logger.info(
                "Uploaded delta tarball: bucket=%s, key=%s, size_bytes=%d",
                bucket,
                s3_key,
                tar_size,
            )

            return {
                "status": "uploaded",
                "delta_key": delta_prefix,
                "object_key": s3_key,
                "tar_size_bytes": tar_size,
            }

    except Exception as e:
        logger.exception("Error uploading delta tarball to S3")
        return {"status": "failed", "error": str(e)}
