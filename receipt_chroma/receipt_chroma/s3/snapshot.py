"""Atomic snapshot operations for ChromaDB S3 storage."""

import gc
import logging
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

from receipt_chroma import ChromaClient
from receipt_chroma.s3.helpers import (
    _cleanup_old_snapshot_versions,
    _cleanup_s3_prefix,
    download_snapshot_from_s3,
    download_snapshot_from_s3_auto,
    upload_snapshot_compressed,
    upload_snapshot_with_hash,
)

logger = logging.getLogger(__name__)


def upload_snapshot_atomic(
    local_path: str,
    bucket: str,
    collection: str,  # "lines" or "words"
    lock_manager: Optional[Any] = None,
    metadata: Optional[Dict[str, str]] = None,
    keep_versions: int = 4,
    s3_client: Optional[Any] = None,
    compressed: bool = True,
) -> Dict[str, Any]:
    """
    Upload ChromaDB snapshot using blue-green atomic deployment pattern.

    This function uploads to a timestamped version, then atomically updates
    a pointer file to reference the new version. Readers will get complete
    snapshots with no race conditions.

    Args:
        local_path: Path to local ChromaDB directory to upload
        bucket: S3 bucket name
        collection: ChromaDB collection name ("lines" or "words")
        lock_manager: Optional lock manager to validate ownership during
            operation
        metadata: Optional metadata to attach to S3 objects
        keep_versions: Number of versions to retain (default: 4)
        s3_client: Optional boto3 S3 client (creates one if not provided)
        compressed: Whether to upload as gzip-compressed tarball (default: True)
            Compressed uploads reduce S3 transfer size by 40-60%.

    Returns:
        Dict with status, version_id, versioned_key, and pointer_key
    """
    if s3_client is None:
        s3_client = boto3.client("s3")

    try:
        # Validate lock ownership before starting
        if lock_manager and not lock_manager.validate_ownership():
            return {
                "status": "error",
                "error": "Lock validation failed before snapshot upload",
                "collection": collection,
            }

        # Generate version identifier using timestamp
        version_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        versioned_key = f"{collection}/snapshot/timestamped/{version_id}/"
        pointer_key = f"{collection}/snapshot/latest-pointer.txt"

        logger.info(
            "Starting atomic snapshot upload: collection=%s, version=%s, "
            "local_path=%s",
            collection,
            version_id,
            local_path,
        )

        # Step 1: Upload to versioned location (no race condition possible)
        if compressed:
            upload_result = upload_snapshot_compressed(
                local_snapshot_path=local_path,
                bucket=bucket,
                snapshot_key=versioned_key,
                metadata=metadata,
                s3_client=s3_client,
            )
        else:
            upload_result = upload_snapshot_with_hash(
                local_snapshot_path=local_path,
                bucket=bucket,
                snapshot_key=versioned_key,
                calculate_hash=True,
                clear_destination=False,  # Don't clear - versioned path is unique
                metadata=metadata,
                s3_client=s3_client,
            )

        if upload_result.get("status") != "uploaded":
            logger.error("Step 1 failed - upload_result=%s", upload_result)
            return {
                "status": "error",
                "error": (f"Failed to upload to versioned location: {upload_result}"),
                "collection": collection,
                "version_id": version_id,
            }

        # Step 2: Final lock validation before atomic promotion
        if lock_manager and not lock_manager.validate_ownership():
            logger.error(
                "Step 2 failed - lock validation failed, "
                "cleaning up versioned upload"
            )
            # Clean up versioned upload
            try:
                _cleanup_s3_prefix(s3_client, bucket, versioned_key)
            except Exception as cleanup_error:  # pylint: disable=broad-exception-caught
                # Cleanup failures are non-critical
                logger.warning("Failed to cleanup versioned upload: %s", cleanup_error)

            return {
                "status": "error",
                "error": "Lock validation failed before atomic promotion",
                "collection": collection,
                "version_id": version_id,
            }

        # Step 3: Atomic promotion - single S3 write operation
        logger.info(
            "Step 3 - atomic promotion, writing pointer file: %s -> %s",
            pointer_key,
            version_id,
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=pointer_key,
            Body=version_id.encode("utf-8"),
            ContentType="text/plain",
            Metadata={k: str(v) for k, v in (metadata or {}).items()},
        )

        # Step 4: Background cleanup of old versions
        try:
            _cleanup_old_snapshot_versions(s3_client, bucket, collection, keep_versions)
        except Exception as cleanup_error:  # pylint: disable=broad-exception-caught
            # Cleanup failures are non-critical
            logger.warning("Failed to cleanup old versions: %s", cleanup_error)

        logger.info(
            "Atomic snapshot upload completed successfully: collection=%s, "
            "version=%s",
            collection,
            version_id,
        )
        return {
            "status": "uploaded",
            "collection": collection,
            "version_id": version_id,
            "versioned_key": versioned_key,
            "pointer_key": pointer_key,
            "hash": upload_result.get("hash", "not_calculated"),
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch all exceptions to return error status
        logger.error("Error during atomic snapshot upload: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "collection": collection,
        }


def download_snapshot_atomic(
    bucket: str,
    collection: str,  # "lines" or "words"
    local_path: str,
    verify_integrity: bool = True,
    s3_client: Optional[Any] = None,
    parallel: bool = True,
    max_workers: int = 8,
) -> Dict[str, Any]:
    """
    Download ChromaDB snapshot using atomic pointer resolution.

    This function reads the version pointer and downloads the corresponding
    timestamped version, ensuring consistent snapshots with no race conditions.

    Args:
        bucket: S3 bucket name
        collection: ChromaDB collection name ("lines" or "words")
        local_path: Local directory to download to
        verify_integrity: Whether to verify snapshot integrity
        s3_client: Optional boto3 S3 client (creates one if not provided)
        parallel: Whether to use parallel downloads (default: True for 4-8x speedup)
        max_workers: Number of parallel download threads (default: 8)

    Returns:
        Dict with status, version_id, and download information
    """
    if s3_client is None:
        s3_client = boto3.client("s3")

    pointer_key = f"{collection}/snapshot/latest-pointer.txt"

    try:
        # Step 1: Resolve current version pointer
        try:
            response = s3_client.get_object(Bucket=bucket, Key=pointer_key)
            version_id = response["Body"].read().decode("utf-8").strip()
            versioned_key = f"{collection}/snapshot/timestamped/{version_id}/"

            logger.info(
                "Resolved snapshot pointer: collection=%s, version=%s",
                collection,
                version_id,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                # No pointer found - try to initialize empty snapshot
                logger.info(
                    "No pointer found, will attempt to initialize empty "
                    "snapshot: %s",
                    collection,
                )
                # Set versioned_key to trigger initialization in download
                # failure path
                versioned_key = f"{collection}/snapshot/timestamped/not_found/"
                version_id = None
            else:
                raise

        # Step 2: Download from resolved version (immutable)
        # Auto-detects compressed format (snapshot.tar.gz) or falls back to
        # parallel file downloads for legacy uncompressed snapshots
        if parallel:
            download_result = download_snapshot_from_s3_auto(
                bucket=bucket,
                snapshot_key=versioned_key,
                local_snapshot_path=local_path,
                max_workers=max_workers,
                verify_integrity=verify_integrity,
                s3_client=s3_client,
            )
        else:
            download_result = download_snapshot_from_s3(
                bucket=bucket,
                snapshot_key=versioned_key,
                local_snapshot_path=local_path,
                verify_integrity=verify_integrity,
                s3_client=s3_client,
            )

        if download_result.get("status") != "downloaded":
            # Check if snapshot doesn't exist - initialize empty snapshot
            error_msg = download_result.get("error", "")
            if (
                "NoSuchKey" in error_msg
                or "does not exist" in error_msg.lower()
                or version_id is None
            ):
                logger.info(
                    "Snapshot not found, initializing empty snapshot",
                    extra={
                        "collection": collection,
                        "snapshot_key": versioned_key,
                    },
                )
                init_result = initialize_empty_snapshot(
                    bucket=bucket,
                    collection=collection,
                    local_path=local_path,
                )

                if init_result.get("status") == "initialized":
                    logger.info(
                        "Successfully initialized empty snapshot",
                        extra={
                            "collection": collection,
                            "version_id": init_result.get("version_id"),
                        },
                    )
                    return {
                        "status": "downloaded",
                        "collection": collection,
                        "version_id": init_result.get("version_id"),
                        "versioned_key": init_result.get("versioned_key"),
                        "local_path": local_path,
                        "initialized": True,
                    }
                logger.error(
                    "Failed to initialize empty snapshot",
                    extra={
                        "collection": collection,
                        "error": init_result.get("error"),
                    },
                )

            return {
                "status": "error",
                "error": f"Failed to download snapshot: {download_result}",
                "collection": collection,
                "version_id": version_id or "unknown",
            }

        logger.info(
            "Atomic snapshot download completed: collection=%s, version=%s",
            collection,
            version_id,
        )
        return {
            "status": "downloaded",
            "collection": collection,
            "version_id": version_id,
            "versioned_key": versioned_key,
            "local_path": local_path,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch all exceptions to return error status
        logger.error("Error during atomic snapshot download: %s", e)
        return {"status": "error", "error": str(e), "collection": collection}


def initialize_empty_snapshot(
    bucket: str,
    collection: str,  # "lines" or "words"
    local_path: Optional[str] = None,
    lock_manager: Optional[Any] = None,
    metadata: Optional[Dict[str, str]] = None,
    s3_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Initialize an empty ChromaDB snapshot with the specified collection.

    Creates a new empty ChromaDB database with the collection initialized,
    then uploads it to S3 using atomic snapshot upload pattern.

    Args:
        bucket: S3 bucket name
        collection: ChromaDB collection name ("lines" or "words")
        local_path: Optional local directory path (creates temp dir if not
            provided)
        lock_manager: Optional lock manager for atomic upload
        metadata: Optional metadata for the snapshot
        s3_client: Optional boto3 S3 client (creates one if not provided)

    Returns:
        Dict with status, version_id, and upload information
    """
    temp_dir = local_path or tempfile.mkdtemp()
    cleanup_temp = local_path is None

    try:
        logger.info(
            "Initializing empty snapshot",
            extra={"collection": collection, "local_path": temp_dir},
        )

        # Create ChromaDB client in write mode
        with ChromaClient(
            persist_directory=temp_dir,
            mode="write",
            metadata_only=True,  # Use metadata-only to avoid OpenAI API
            # requirement
        ) as chroma_client:
            # Create the collection (will be empty)
            collection_obj = chroma_client.get_collection(
                collection,
                create_if_missing=True,
                metadata={
                    "created_by": "empty_snapshot_initializer",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "collection_type": collection,
                    **(metadata or {}),
                },
            )

            # Verify collection was created
            collection_count = collection_obj.count()
            logger.info(
                "Created empty collection",
                extra={
                    "collection": collection,
                    "count": collection_count,
                    "local_path": temp_dir,
                },
            )

        # Client is closed via context manager, but ensure files are flushed
        gc.collect()
        time.sleep(0.1)  # Small delay to ensure file handles are released

        # Upload to S3 using atomic upload pattern
        upload_result = upload_snapshot_atomic(
            local_path=temp_dir,
            bucket=bucket,
            collection=collection,
            lock_manager=lock_manager,
            metadata={
                "initialized": "true",
                "collection": collection,
                "created_at": datetime.now(timezone.utc).isoformat(),
                **(metadata or {}),
            },
            s3_client=s3_client,
        )

        if upload_result.get("status") != "uploaded":
            return {
                "status": "error",
                "error": f"Failed to upload empty snapshot: {upload_result}",
                "collection": collection,
            }

        logger.info(
            "Successfully initialized and uploaded empty snapshot",
            extra={
                "collection": collection,
                "version_id": upload_result.get("version_id"),
            },
        )

        return {
            "status": "initialized",
            "collection": collection,
            "version_id": upload_result.get("version_id"),
            "versioned_key": upload_result.get("versioned_key"),
            "pointer_key": upload_result.get("pointer_key"),
            "local_path": temp_dir,
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch all exceptions to return error status
        logger.error(
            "Error initializing empty snapshot: %s",
            e,
            extra={"collection": collection},
            exc_info=True,
        )
        return {
            "status": "error",
            "error": str(e),
            "collection": collection,
        }
    finally:
        # Clean up temp directory if we created it
        if cleanup_temp:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:  # pylint: disable=broad-exception-caught
                # Ignore cleanup errors
                pass
