"""Storage manager for ChromaDB snapshots with EFS/S3 abstraction."""

import os
from enum import Enum
from typing import Any, Dict, Optional

from receipt_chroma.lock_manager import LockManager
from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic
from receipt_chroma.storage.efs import EFSSnapshotManager


class StorageMode(Enum):
    """Storage backend modes for ChromaDB snapshots."""

    S3_ONLY = "s3"
    EFS = "efs"
    AUTO = "auto"


class StorageManager:
    """Abstracts storage operations across EFS and S3 backends."""

    def __init__(
        self,
        collection: str,
        bucket: str,
        mode: StorageMode = StorageMode.AUTO,
        efs_root: Optional[str] = None,
        logger: Optional[Any] = None,
        metrics: Optional[Any] = None,
    ):
        """
        Initialize storage manager.

        Args:
            collection: Collection name (lines/words)
            bucket: S3 bucket name
            mode: Storage mode (AUTO, EFS, S3_ONLY)
            efs_root: EFS mount path (optional)
            logger: Logger instance
            metrics: Optional metrics collector
        """
        self.collection = collection
        self.bucket = bucket
        self.mode = mode
        self.efs_root = efs_root or os.environ.get("CHROMA_ROOT", "/tmp/chroma")
        self.logger = logger
        self.metrics = metrics

        # Detect effective mode
        self._effective_mode = self._detect_mode()

        # Initialize EFS manager if using EFS
        self._efs_manager: Optional[EFSSnapshotManager] = None
        if self._effective_mode == StorageMode.EFS:
            self._efs_manager = EFSSnapshotManager(
                collection=collection, logger=logger, metrics=metrics
            )

    def _detect_mode(self) -> StorageMode:
        """Detect the effective storage mode based on configuration and availability."""
        if self.mode == StorageMode.S3_ONLY:
            if self.logger:
                self.logger.info("Storage mode: S3-only (forced)")
            return StorageMode.S3_ONLY

        if self.mode == StorageMode.EFS:
            # Verify EFS is available
            if self._is_efs_available():
                if self.logger:
                    self.logger.info("Storage mode: EFS (forced)")
                return StorageMode.EFS
            if self.logger:
                self.logger.warning(
                    "EFS mode requested but EFS not available, falling back to S3"
                )
            return StorageMode.S3_ONLY

        # AUTO mode: check EFS availability
        if self._is_efs_available():
            if self.logger:
                self.logger.info("Storage mode: EFS (auto-detected)")
            return StorageMode.EFS

        if self.logger:
            self.logger.info("Storage mode: S3-only (auto-detected)")
        return StorageMode.S3_ONLY

    def _is_efs_available(self) -> bool:
        """Check if EFS is mounted and available."""
        # Check if EFS root path exists and is writable
        try:
            if not os.path.exists(self.efs_root):
                return False

            # Try to create a test directory to verify write access
            test_dir = os.path.join(self.efs_root, ".storage_test")
            os.makedirs(test_dir, exist_ok=True)
            os.rmdir(test_dir)
            return True
        except (OSError, PermissionError):
            return False

    def download_snapshot(
        self,
        local_path: str,
        verify_integrity: bool = True,
        lock_manager: Optional[LockManager] = None,
    ) -> Dict[str, Any]:
        """
        Download snapshot from storage backend.

        Args:
            local_path: Local path to download snapshot to
            verify_integrity: Whether to verify snapshot integrity
            lock_manager: Optional lock manager for CAS validation

        Returns:
            Download result dict with status and metadata
        """
        if self._effective_mode == StorageMode.EFS and self._efs_manager:
            # EFS mode: get latest version and ensure it's available locally
            latest_version = self._efs_manager.get_latest_s3_version()
            if latest_version:
                result = self._efs_manager.ensure_snapshot_available(
                    latest_version
                )
                if result["status"] == "available":
                    # Copy from EFS to local_path
                    import shutil

                    shutil.copytree(
                        result["efs_path"], local_path, dirs_exist_ok=True
                    )
                    return {
                        "status": "downloaded",
                        "version": latest_version,
                        "source": "efs",
                        "local_path": local_path,
                    }

        # S3-only mode or EFS fallback
        return download_snapshot_atomic(
            bucket=self.bucket,
            collection=self.collection,
            local_path=local_path,
            verify_integrity=verify_integrity,
        )

    def upload_snapshot(
        self,
        local_path: str,
        lock_manager: Optional[LockManager] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Upload snapshot to storage backend.

        Args:
            local_path: Local path of snapshot to upload
            lock_manager: Optional lock manager for atomic operations
            metadata: Optional metadata to attach to snapshot

        Returns:
            Upload result dict with status and metadata
        """
        # Always upload to S3 for durability
        result = upload_snapshot_atomic(
            local_path=local_path,
            bucket=self.bucket,
            collection=self.collection,
            lock_manager=lock_manager,
            metadata=metadata,
        )

        # If using EFS, also sync to EFS cache (background operation)
        if (
            self._effective_mode == StorageMode.EFS
            and self._efs_manager
            and result.get("status") == "uploaded"
        ):
            version_id = result.get("version_id", "latest")
            try:
                self._efs_manager.sync_to_s3_async(version_id, local_path)
            except Exception as e:
                if self.logger:
                    self.logger.warning(
                        "Failed to sync to EFS cache", error=str(e)
                    )

        return result

    def get_latest_version(self) -> Optional[str]:
        """
        Get the latest snapshot version from storage backend.

        Returns:
            Version ID or None if not found
        """
        if self._effective_mode == StorageMode.EFS and self._efs_manager:
            return self._efs_manager.get_latest_s3_version()

        # S3 mode: read pointer file
        import boto3
        from botocore.exceptions import ClientError

        s3_client = boto3.client("s3")
        pointer_key = f"{self.collection}/snapshot/latest-pointer.txt"

        try:
            response = s3_client.get_object(
                Bucket=self.bucket, Key=pointer_key
            )
            return response["Body"].read().decode("utf-8").strip()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return "latest-direct"
            raise

    @property
    def effective_mode(self) -> StorageMode:
        """Get the effective storage mode being used."""
        return self._effective_mode

    @property
    def is_using_efs(self) -> bool:
        """Check if currently using EFS storage."""
        return self._effective_mode == StorageMode.EFS
