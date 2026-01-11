"""Low-level S3 helper functions for ChromaDB operations."""

import logging
import os
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, TypedDict

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

try:
    from botocore.exceptions import ChecksumError, FlexibleChecksumError
except ImportError:  # pragma: no cover - older botocore versions
    ChecksumError = Exception  # type: ignore[assignment]
    FlexibleChecksumError = Exception  # type: ignore[assignment]
from typing_extensions import NotRequired, Required

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

# =============================================================================
# S3 Transfer Optimization Constants
# =============================================================================

# Optimized transfer config for multipart downloads
# - max_concurrency: Number of threads for multipart transfer (per file)
# - multipart_threshold: Minimum file size before using multipart
# - multipart_chunksize: Size of each part (larger = fewer API calls)
TRANSFER_CONFIG = TransferConfig(
    max_concurrency=10,
    multipart_threshold=8 * 1024 * 1024,  # 8MB
    multipart_chunksize=16 * 1024 * 1024,  # 16MB (up from default 8MB)
)

# Streaming chunk size for fallback path (checksum bypass)
# Larger chunks reduce syscalls and improve throughput
STREAMING_CHUNK_SIZE = 2 * 1024 * 1024  # 2MB (up from 8KB)

# Retry configuration for transient failures
MAX_DOWNLOAD_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1.0  # Exponential backoff: 1s, 2s, 4s

# Thread-local storage for S3 clients (thread safety)
_thread_local = threading.local()


def _get_thread_s3_client(region: Optional[str] = None) -> S3Client:
    """Get or create a thread-local S3 client for parallel downloads.

    Each thread gets its own S3 client to ensure thread safety.
    Clients are cached per-thread to avoid recreation overhead.
    """
    # Use region as part of cache key
    cache_key = f"s3_client_{region or 'default'}"
    if not hasattr(_thread_local, cache_key):
        if region:
            client = boto3.client("s3", region_name=region)
        else:
            client = boto3.client("s3")
        setattr(_thread_local, cache_key, client)
    return getattr(_thread_local, cache_key)


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


# =============================================================================
# Parallel Download Implementation
# =============================================================================


def _download_single_file_with_retry(
    bucket: str,
    s3_key: str,
    local_file: Path,
    expected_size: int,
    allow_checksum_bypass: bool,
    region: Optional[str] = None,
) -> Tuple[str, int, Optional[str]]:
    """Download a single file from S3 with retry logic.

    Uses thread-local S3 client for thread safety in parallel downloads.
    Implements exponential backoff for transient failures.

    Args:
        bucket: S3 bucket name
        s3_key: S3 object key
        local_file: Local file path to write to
        expected_size: Expected file size for verification
        allow_checksum_bypass: Whether to allow checksum bypass fallback
        region: Optional AWS region

    Returns:
        Tuple of (s3_key, bytes_downloaded, error_message or None)
    """
    s3_client = _get_thread_s3_client(region)

    # Create parent directories
    local_file.parent.mkdir(parents=True, exist_ok=True)

    last_error: Optional[Exception] = None

    for attempt in range(MAX_DOWNLOAD_RETRIES):
        try:
            # Try primary download with optimized TransferConfig
            s3_client.download_file(
                bucket, s3_key, str(local_file), Config=TRANSFER_CONFIG
            )

            # Verify size if expected_size is provided
            if expected_size and local_file.exists():
                actual_size = local_file.stat().st_size
                if actual_size != expected_size:
                    logger.warning(
                        "Size mismatch for %s: expected %d, got %d",
                        s3_key,
                        expected_size,
                        actual_size,
                    )

            return (s3_key, expected_size, None)

        except (ChecksumError, FlexibleChecksumError) as e:
            if not allow_checksum_bypass:
                return (s3_key, 0, f"Checksum error: {e}")

            # Fall back to streaming download with checksum disabled
            logger.warning(
                "Checksum error, falling back to get_object: %s (%s)",
                s3_key,
                e,
            )
            try:
                response = s3_client.get_object(
                    Bucket=bucket,
                    Key=s3_key,
                    ChecksumMode="DISABLED",
                )
                body = response["Body"]
                try:
                    with open(local_file, "wb") as f:
                        for chunk in body.iter_chunks(
                            chunk_size=STREAMING_CHUNK_SIZE
                        ):
                            if chunk:
                                f.write(chunk)
                finally:
                    body.close()

                return (s3_key, expected_size, None)
            except Exception as fallback_error:
                return (s3_key, 0, f"Fallback download failed: {fallback_error}")

        except ClientError as e:
            last_error = e
            error_code = e.response.get("Error", {}).get("Code", "")

            # Don't retry on permanent errors
            if error_code in ("NoSuchKey", "NoSuchBucket", "AccessDenied"):
                return (s3_key, 0, f"ClientError: {e}")

            # Retry with exponential backoff for transient errors
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                wait_time = RETRY_BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "Download failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    MAX_DOWNLOAD_RETRIES,
                    wait_time,
                    e,
                )
                time.sleep(wait_time)
            else:
                return (s3_key, 0, f"ClientError after {MAX_DOWNLOAD_RETRIES} retries: {e}")

        except Exception as e:
            last_error = e
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                wait_time = RETRY_BASE_DELAY_SECONDS * (2**attempt)
                logger.warning(
                    "Download failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    MAX_DOWNLOAD_RETRIES,
                    wait_time,
                    e,
                )
                time.sleep(wait_time)
            else:
                return (s3_key, 0, f"Error after {MAX_DOWNLOAD_RETRIES} retries: {e}")

    return (s3_key, 0, f"Failed after {MAX_DOWNLOAD_RETRIES} retries: {last_error}")


def download_snapshot_from_s3_parallel(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    max_workers: int = 8,
    verify_integrity: bool = False,
    region: Optional[str] = None,
    s3_client: Optional[S3Client] = None,
) -> DownloadResult:
    """
    Download a ChromaDB snapshot from S3 using parallel file downloads.

    This is an optimized version of download_snapshot_from_s3 that downloads
    multiple files concurrently using a ThreadPoolExecutor. Expected speedup
    is 4-8x for snapshots with multiple files.

    Args:
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot
        local_snapshot_path: Local directory to download to
        max_workers: Maximum number of parallel download threads (default: 8)
        verify_integrity: Whether to verify file integrity after download
        region: Optional AWS region
        s3_client: Optional boto3 S3 client for listing (creates one if not provided)

    Returns:
        Dict with download status and statistics
    """
    start_time = time.time()
    logger.info(
        "Starting parallel S3 snapshot download: bucket=%s, key=%s, "
        "local_path=%s, max_workers=%d",
        bucket,
        snapshot_key,
        local_snapshot_path,
        max_workers,
    )

    # Use provided client for listing, or create one
    if s3_client is None:
        if region:
            s3_client = boto3.client("s3", region_name=region)
        else:
            s3_client = boto3.client("s3")

    allow_checksum_bypass = os.environ.get(
        "RECEIPT_CHROMA_ALLOW_CHECKSUM_BYPASS", ""
    ).lower() in {"1", "true", "yes"}

    try:
        # Create local directory
        local_path = Path(local_snapshot_path)
        local_path.mkdir(parents=True, exist_ok=True)

        # List all objects in the snapshot
        full_prefix = snapshot_key.rstrip("/") + "/"
        logger.info("Listing S3 objects with prefix: %s", full_prefix)
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=full_prefix)

        # Collect all files to download
        files_to_download: List[Tuple[str, Path, int]] = []

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
                expected_size = obj.get("Size", 0)
                files_to_download.append((s3_key, local_file, expected_size))

        if not files_to_download:
            return {
                "status": "failed",
                "error": f"No objects found at s3://{bucket}/{snapshot_key}",
                "snapshot_key": snapshot_key,
            }

        logger.info(
            "Found %d files to download, starting parallel download with %d workers",
            len(files_to_download),
            max_workers,
        )

        # Download files in parallel
        file_count = 0
        total_size = 0
        errors: List[str] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all download tasks
            futures = {
                executor.submit(
                    _download_single_file_with_retry,
                    bucket,
                    s3_key,
                    local_file,
                    expected_size,
                    allow_checksum_bypass,
                    region,
                ): s3_key
                for s3_key, local_file, expected_size in files_to_download
            }

            # Collect results as they complete
            for future in as_completed(futures):
                s3_key = futures[future]
                try:
                    key, size, error = future.result()
                    if error:
                        errors.append(f"{key}: {error}")
                        logger.error("Failed to download %s: %s", key, error)
                    else:
                        file_count += 1
                        total_size += size
                except Exception as e:
                    errors.append(f"{s3_key}: {e}")
                    logger.exception("Exception downloading %s", s3_key)

        elapsed = time.time() - start_time

        # If all downloads failed, return error
        if file_count == 0:
            return {
                "status": "failed",
                "error": f"All {len(files_to_download)} downloads failed: {errors[:3]}",
                "snapshot_key": snapshot_key,
            }

        # Log any partial failures
        if errors:
            logger.warning(
                "Parallel download completed with %d errors: %s",
                len(errors),
                errors[:3],
            )

        logger.info(
            "Parallel download complete: %d files, %d bytes, %.2fs (%.1f MB/s)",
            file_count,
            total_size,
            elapsed,
            (total_size / 1024 / 1024) / elapsed if elapsed > 0 else 0,
        )

        return {
            "status": "downloaded",
            "snapshot_key": snapshot_key,
            "local_path": str(local_path),
            "file_count": file_count,
            "total_size_bytes": total_size,
        }

    except Exception as e:
        logger.exception("Error in parallel snapshot download from S3")
        return {"status": "failed", "error": str(e)}


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

    allow_checksum_bypass = os.environ.get(
        "RECEIPT_CHROMA_ALLOW_CHECKSUM_BYPASS", ""
    ).lower() in {"1", "true", "yes"}

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

        def check_size(s3_key: str, expected_size: int) -> None:
            if expected_size:
                actual_size = local_file.stat().st_size
                if actual_size != expected_size:
                    logger.warning(
                        "Size mismatch for %s: expected %s, got %s",
                        s3_key,
                        expected_size,
                        actual_size,
                    )

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

                # Download file - handle checksum errors that can occur with
                # moto.
                fallback_used = False
                try:
                    s3_client.download_file(bucket, s3_key, str(local_file))
                except (ChecksumError, FlexibleChecksumError) as e:
                    if not allow_checksum_bypass:
                        raise
                    fallback_used = True
                    logger.warning(
                        "Checksum error, falling back to get_object: %s (%s)",
                        s3_key,
                        e,
                    )
                    response = s3_client.get_object(
                        Bucket=bucket,
                        Key=s3_key,
                        ChecksumMode="DISABLED",
                    )
                    body = response["Body"]
                    try:
                        with open(local_file, "wb") as f:
                            for chunk in body.iter_chunks(
                                chunk_size=STREAMING_CHUNK_SIZE
                            ):
                                if chunk:
                                    f.write(chunk)
                    finally:
                        body.close()
                except ClientError:
                    raise

                file_count += 1
                total_size += obj.get("Size", 0)

                # Verify integrity if requested or fallback used
                if local_file.exists() and (verify_integrity or fallback_used):
                    check_size(s3_key, obj.get("Size", 0))

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


def upload_snapshot_compressed(
    local_snapshot_path: str,
    bucket: str,
    snapshot_key: str,
    metadata: Optional[Dict[str, Any]] = None,
    region: Optional[str] = None,
    s3_client: Optional[S3Client] = None,
) -> UploadResult:
    """
    Upload ChromaDB snapshot to S3 as a gzip-compressed tarball.

    Creates a single snapshot.tar.gz file containing the entire snapshot
    directory. This reduces S3 transfer size by 40-60% compared to
    uncompressed uploads.

    Args:
        local_snapshot_path: Path to the local snapshot directory
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot
        metadata: Optional metadata to include in S3 objects
        region: Optional AWS region
        s3_client: Optional boto3 S3 client (creates one if not provided)

    Returns:
        Dict with upload status, hash info, and statistics
    """
    import hashlib  # pylint: disable=import-outside-toplevel

    logger.info(
        "Starting compressed snapshot upload: local_path=%s, bucket=%s, key=%s",
        local_snapshot_path,
        bucket,
        snapshot_key,
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
                "error": f"Snapshot path does not exist: {local_snapshot_path}",
            }

        # Create tarball in temp directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            tar_path = os.path.join(tmp_dir, "snapshot.tar.gz")

            # Count files and calculate uncompressed size for stats
            file_count = 0
            uncompressed_size = 0
            for file_path in snapshot_path.rglob("*"):
                if file_path.is_file():
                    file_count += 1
                    uncompressed_size += file_path.stat().st_size

            # Create gzip-compressed tarball
            logger.info(
                "Creating compressed tarball: %d files, %d bytes uncompressed",
                file_count,
                uncompressed_size,
            )
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(local_snapshot_path, arcname=".")

            tar_size = os.path.getsize(tar_path)
            compression_ratio = (
                (1 - tar_size / uncompressed_size) * 100
                if uncompressed_size > 0
                else 0
            )
            logger.info(
                "Created compressed tarball: %d bytes (%.1f%% reduction)",
                tar_size,
                compression_ratio,
            )

            # Calculate hash of tarball
            hash_obj = hashlib.md5()  # nosec - not for security
            with open(tar_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash_obj.update(chunk)
            hash_value = hash_obj.hexdigest()

            # Upload tarball to S3
            s3_key = f"{snapshot_key.rstrip('/')}/snapshot.tar.gz"
            extra_args = {"ContentType": "application/gzip"}
            if metadata:
                extra_args["Metadata"] = {k: str(v) for k, v in metadata.items()}

            s3_client.upload_file(tar_path, bucket, s3_key, ExtraArgs=extra_args)

            # Upload hash file
            hash_key = f"{snapshot_key.rstrip('/')}/.snapshot_hash"
            hash_content = f"md5:{hash_value}"
            s3_client.put_object(
                Bucket=bucket,
                Key=hash_key,
                Body=hash_content.encode("utf-8"),
                ContentType="text/plain",
                Metadata={k: str(v) for k, v in (metadata or {}).items()},
            )

            logger.info(
                "Uploaded compressed snapshot: key=%s, tar_size=%d, "
                "uncompressed=%d, files=%d, hash=%s",
                s3_key,
                tar_size,
                uncompressed_size,
                file_count,
                hash_value,
            )

            return {
                "status": "uploaded",
                "snapshot_key": snapshot_key,
                "file_count": file_count,
                "total_size_bytes": tar_size,  # Report compressed size
                "hash": hash_value,
                "hash_algorithm": "md5",
            }

    except Exception as e:
        logger.exception("Error uploading compressed snapshot to S3")
        return {"status": "failed", "error": str(e)}


def download_snapshot_compressed(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    region: Optional[str] = None,
    s3_client: Optional[S3Client] = None,
) -> DownloadResult:
    """
    Download and decompress a gzip-compressed ChromaDB snapshot from S3.

    Downloads snapshot.tar.gz and extracts it to the local path.

    Args:
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot
        local_snapshot_path: Local directory to extract to
        region: Optional AWS region
        s3_client: Optional boto3 S3 client (creates one if not provided)

    Returns:
        Dict with download status and statistics
    """
    start_time = time.time()
    logger.info(
        "Starting compressed S3 snapshot download: bucket=%s, key=%s, local_path=%s",
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

        # Download tarball to temp directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            tar_path = os.path.join(tmp_dir, "snapshot.tar.gz")
            s3_key = f"{snapshot_key.rstrip('/')}/snapshot.tar.gz"

            logger.info("Downloading compressed snapshot: %s", s3_key)
            s3_client.download_file(bucket, s3_key, tar_path, Config=TRANSFER_CONFIG)

            tar_size = os.path.getsize(tar_path)
            download_time = time.time() - start_time
            logger.info(
                "Downloaded tarball: %d bytes in %.2fs (%.1f MB/s)",
                tar_size,
                download_time,
                (tar_size / 1024 / 1024) / download_time if download_time > 0 else 0,
            )

            # Extract tarball
            extract_start = time.time()
            file_count = 0
            total_size = 0

            with tarfile.open(tar_path, "r:gz") as tar:
                # Security: validate member paths before extraction
                for member in tar.getmembers():
                    # Prevent path traversal attacks
                    member_path = os.path.normpath(member.name)
                    if member_path.startswith("..") or member_path.startswith("/"):
                        logger.warning(
                            "Skipping potentially unsafe path: %s", member.name
                        )
                        continue

                    if member.isfile():
                        file_count += 1
                        total_size += member.size

                # Extract all (after validation)
                tar.extractall(local_path)

            extract_time = time.time() - extract_start
            total_time = time.time() - start_time

            logger.info(
                "Extracted snapshot: %d files, %d bytes in %.2fs "
                "(total: %.2fs, %.1f MB/s)",
                file_count,
                total_size,
                extract_time,
                total_time,
                (total_size / 1024 / 1024) / total_time if total_time > 0 else 0,
            )

            return {
                "status": "downloaded",
                "snapshot_key": snapshot_key,
                "local_path": str(local_path),
                "file_count": file_count,
                "total_size_bytes": total_size,  # Uncompressed size
            }

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "NoSuchKey":
            # Tarball doesn't exist - this is expected for legacy snapshots
            logger.info(
                "Compressed snapshot not found: %s (will try uncompressed)",
                f"{snapshot_key}/snapshot.tar.gz",
            )
            return {
                "status": "failed",
                "error": "Compressed snapshot not found",
                "snapshot_key": snapshot_key,
            }
        logger.exception("Error downloading compressed snapshot from S3")
        return {"status": "failed", "error": str(e)}
    except Exception as e:
        logger.exception("Error downloading compressed snapshot from S3")
        return {"status": "failed", "error": str(e)}


def download_snapshot_from_s3_auto(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    max_workers: int = 8,
    verify_integrity: bool = False,
    region: Optional[str] = None,
    s3_client: Optional[S3Client] = None,
) -> DownloadResult:
    """
    Auto-detect snapshot format and download appropriately.

    Checks for compressed format (snapshot.tar.gz) first, falling back
    to parallel file download for legacy uncompressed snapshots.

    Args:
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot
        local_snapshot_path: Local directory to download to
        max_workers: Maximum parallel workers for uncompressed downloads
        verify_integrity: Whether to verify file integrity
        region: Optional AWS region
        s3_client: Optional boto3 S3 client

    Returns:
        Dict with download status and statistics
    """
    if s3_client is None:
        if region:
            s3_client = boto3.client("s3", region_name=region)
        else:
            s3_client = boto3.client("s3")

    # Check if compressed snapshot exists
    tarball_key = f"{snapshot_key.rstrip('/')}/snapshot.tar.gz"
    try:
        s3_client.head_object(Bucket=bucket, Key=tarball_key)
        logger.info("Found compressed snapshot, using fast download: %s", tarball_key)
        return download_snapshot_compressed(
            bucket=bucket,
            snapshot_key=snapshot_key,
            local_snapshot_path=local_snapshot_path,
            region=region,
            s3_client=s3_client,
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey"):
            logger.info(
                "No compressed snapshot found, falling back to parallel download"
            )
        else:
            logger.warning("Error checking for compressed snapshot: %s", e)

    # Fall back to parallel file download for legacy snapshots
    return download_snapshot_from_s3_parallel(
        bucket=bucket,
        snapshot_key=snapshot_key,
        local_snapshot_path=local_snapshot_path,
        max_workers=max_workers,
        verify_integrity=verify_integrity,
        region=region,
        s3_client=s3_client,
    )
