"""Atomic snapshot operations for ChromaDB S3 storage."""

import gc
import logging
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import boto3
import chromadb
from botocore.exceptions import ClientError

from receipt_chroma import ChromaClient
from receipt_chroma.s3.helpers import (
    _cleanup_old_snapshot_versions,
    _cleanup_s3_prefix,
    download_snapshot_from_s3,
    upload_snapshot_with_hash,
)

logger = logging.getLogger(__name__)


def _validate_snapshot_after_upload(
    bucket: str,
    versioned_key: str,
    collection_name: str,
    s3_client: Optional[Any] = None,
) -> Tuple[bool, float]:
    """
    Validate that a snapshot uploaded to S3 can be opened and read by ChromaDB.

    This is a fast validation that verifies the snapshot is valid before
    updating the pointer. If validation fails, the versioned upload should
    be cleaned up.

    Args:
        bucket: S3 bucket name
        versioned_key: S3 key prefix for the versioned snapshot
        collection_name: Expected ChromaDB collection name
        s3_client: Optional boto3 S3 client (creates one if not provided)

    Returns:
        Tuple of (success: bool, duration_seconds: float)
    """
    validation_start_time = time.time()
    temp_dir = None

    if s3_client is None:
        s3_client = boto3.client("s3")

    try:
        temp_dir = tempfile.mkdtemp()
        logger.info(
            "Validating snapshot by downloading from S3: %s (temp_dir: %s)",
            versioned_key,
            temp_dir,
        )

        # Download snapshot from versioned location
        download_result = download_snapshot_from_s3(
            bucket=bucket,
            snapshot_key=versioned_key,
            local_snapshot_path=temp_dir,
            verify_integrity=False,  # Skip hash check for speed
            s3_client=s3_client,
        )

        if download_result.get("status") != "downloaded":
            validation_duration = time.time() - validation_start_time
            logger.error(
                "Failed to download snapshot for validation: %s (duration: %.2fs)",
                versioned_key,
                validation_duration,
            )
            return False, validation_duration

        # Check for SQLite files
        temp_path = Path(temp_dir)
        sqlite_files = list(temp_path.rglob("*.sqlite*"))
        if not sqlite_files:
            validation_duration = time.time() - validation_start_time
            logger.error(
                "No SQLite files found in snapshot (duration: %.2fs)",
                validation_duration,
            )
            return False, validation_duration

        # Try to open with ChromaDB
        try:
            test_client = chromadb.PersistentClient(path=temp_dir)
            collections = test_client.list_collections()

            if not collections:
                validation_duration = time.time() - validation_start_time
                logger.error(
                    "No collections found in snapshot (duration: %.2fs)",
                    validation_duration,
                )
                return False, validation_duration

            # Verify expected collection exists
            collection_names = [c.name for c in collections]
            if collection_name not in collection_names:
                validation_duration = time.time() - validation_start_time
                logger.error(
                    "Expected collection '%s' not found in snapshot (found: %s, duration: %.2fs)",
                    collection_name,
                    collection_names,
                    validation_duration,
                )
                return False, validation_duration

            # Lightweight check: verify collection can be accessed
            test_collection = test_client.get_collection(collection_name)
            count = test_collection.count()  # Lightweight operation

            # Clean up test client
            del test_client
            gc.collect()

            validation_duration = time.time() - validation_start_time
            logger.info(
                "Snapshot validation successful: %s (collections: %d, count: %d, duration: %.2fs)",
                versioned_key,
                len(collections),
                count,
                validation_duration,
            )
            return True, validation_duration

        except Exception as e:  # pylint: disable=broad-exception-caught
            # Catch all exceptions during validation to ensure cleanup
            validation_duration = time.time() - validation_start_time
            logger.error(
                "Failed to open snapshot with ChromaDB during validation: %s (type: %s, duration: %.2fs)",
                versioned_key,
                type(e).__name__,
                validation_duration,
                exc_info=True,
            )
            return False, validation_duration

    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch all exceptions during validation to ensure cleanup
        validation_duration = time.time() - validation_start_time
        logger.error(
            "Error during snapshot validation: %s (type: %s, duration: %.2fs)",
            versioned_key,
            type(e).__name__,
            validation_duration,
            exc_info=True,
        )
        return False, validation_duration
    finally:
        # Clean up temp directory
        if temp_dir:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:  # pylint: disable=broad-exception-caught
                # Ignore cleanup errors
                pass


def upload_snapshot_atomic(
    local_path: str,
    bucket: str,
    collection: str,  # "lines" or "words"
    lock_manager: Optional[Any] = None,
    metadata: Optional[Dict[str, str]] = None,
    keep_versions: int = 4,
    s3_client: Optional[Any] = None,
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
        lock_manager: Optional lock manager to validate ownership during operation
        metadata: Optional metadata to attach to S3 objects
        keep_versions: Number of versions to retain (default: 4)
        s3_client: Optional boto3 S3 client (creates one if not provided)

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
            "Starting atomic snapshot upload: collection=%s, version=%s, local_path=%s",
            collection,
            version_id,
            local_path,
        )

        # Step 1: Upload to versioned location (no race condition possible)
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
                "error": f"Failed to upload to versioned location: {upload_result}",
                "collection": collection,
                "version_id": version_id,
            }

        # Step 2: Validate uploaded snapshot before updating pointer
        validation_result, validation_duration = (
            _validate_snapshot_after_upload(
                bucket=bucket,
                versioned_key=versioned_key,
                collection_name=collection,
                s3_client=s3_client,
            )
        )

        if not validation_result:
            # Clean up versioned upload on validation failure
            logger.error(
                "Step 2 failed - snapshot validation failed, cleaning up versioned upload: %s (duration: %.2fs)",
                versioned_key,
                validation_duration,
            )
            try:
                _cleanup_s3_prefix(s3_client, bucket, versioned_key)
            except (
                Exception
            ) as cleanup_error:  # pylint: disable=broad-exception-caught
                # Cleanup failures are non-critical
                logger.warning(
                    "Failed to cleanup versioned upload: %s", cleanup_error
                )

            return {
                "status": "error",
                "error": "Snapshot validation failed after upload",
                "collection": collection,
                "version_id": version_id,
                "validation_duration": validation_duration,
            }

        logger.info(
            "Step 2 completed - snapshot validation successful "
            "(duration: %.2fs)",
            validation_duration,
        )

        # Step 3: Final lock validation before atomic promotion
        if lock_manager and not lock_manager.validate_ownership():
            logger.error(
                "Step 3 failed - lock validation failed, "
                "cleaning up versioned upload"
            )
            # Clean up versioned upload
            try:
                _cleanup_s3_prefix(s3_client, bucket, versioned_key)
            except (
                Exception
            ) as cleanup_error:  # pylint: disable=broad-exception-caught
                # Cleanup failures are non-critical
                logger.warning(
                    "Failed to cleanup versioned upload: %s", cleanup_error
                )

            return {
                "status": "error",
                "error": "Lock validation failed before atomic promotion",
                "collection": collection,
                "version_id": version_id,
            }

        # Step 4: Atomic promotion - single S3 write operation
        logger.info(
            "Step 4 - atomic promotion, writing pointer file: %s -> %s",
            pointer_key,
            version_id,
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=pointer_key,
            Body=version_id.encode("utf-8"),
            ContentType="text/plain",
            Metadata=metadata or {},
        )

        # Step 5: Background cleanup of old versions
        try:
            _cleanup_old_snapshot_versions(
                s3_client, bucket, collection, keep_versions
            )
        except (
            Exception
        ) as cleanup_error:  # pylint: disable=broad-exception-caught
            # Cleanup failures are non-critical
            logger.warning("Failed to cleanup old versions: %s", cleanup_error)

        logger.info(
            "Atomic snapshot upload completed successfully: collection=%s, version=%s",
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
            "validation_duration": validation_duration,
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
                    "No pointer found, will attempt to initialize empty snapshot: %s",
                    collection,
                )
                # Set versioned_key to trigger initialization in download failure path
                versioned_key = f"{collection}/snapshot/timestamped/not_found/"
                version_id = None
            else:
                raise

        # Step 2: Download from resolved version (immutable)
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
        local_path: Optional local directory path (creates temp dir if not provided)
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
            metadata_only=True,  # Use metadata-only to avoid OpenAI API requirement
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
