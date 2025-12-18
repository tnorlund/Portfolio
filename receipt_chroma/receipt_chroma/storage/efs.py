"""EFS + S3 hybrid snapshot management for ChromaDB compaction.

This module provides utilities for managing ChromaDB snapshots using EFS as
primary storage and S3 as backup/archive. It implements:
- EFS snapshot caching and version tracking
- Hybrid snapshot access (EFS primary, S3 fallback)
- Background S3 sync for backup
- Atomic pointer management
"""

import os
import shutil
import tempfile
import time
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic


class EFSSnapshotManager:
    """Manages ChromaDB snapshots using EFS + S3 hybrid approach."""

    def __init__(
        self,
        collection: str,
        bucket: str,
        logger: Any,
        metrics: Any = None,
        efs_root: Optional[str] = None,
    ):
        """
        Initialize EFS snapshot manager.

        Args:
            collection: Collection name (lines/words)
            bucket: S3 bucket name
            logger: Logger instance
            metrics: Optional metrics collector
            efs_root: Optional EFS root path (defaults to /tmp/chroma)
        """
        self.collection = collection
        self.logger = logger
        self.metrics = metrics

        # EFS mount path
        self.efs_root = efs_root or os.environ.get("CHROMA_ROOT", "/tmp/chroma")
        self.efs_snapshots_dir = os.path.join(self.efs_root, "snapshots", collection)

        # S3 configuration
        self.bucket = bucket
        self.s3_client = boto3.client("s3")

        # Version tracking file
        self.version_file = os.path.join(self.efs_snapshots_dir, ".version")

        # Ensure EFS directories exist
        os.makedirs(self.efs_snapshots_dir, exist_ok=True)

    def get_current_efs_version(self) -> Optional[str]:
        """Get the current snapshot version stored on EFS."""
        try:
            if os.path.exists(self.version_file):
                with open(self.version_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception as e:
            self.logger.warning("Failed to read EFS version file", error=str(e))
        return None

    def set_efs_version(self, version: str) -> None:
        """Set the current snapshot version on EFS."""
        try:
            with open(self.version_file, "w", encoding="utf-8") as f:
                f.write(version)
        except Exception:
            self.logger.exception("Failed to write EFS version file")

    def get_latest_s3_version(self) -> Optional[str]:
        """Get the latest snapshot version from S3."""
        try:
            pointer_key = f"{self.collection}/snapshot/latest-pointer.txt"

            try:
                response = self.s3_client.get_object(
                    Bucket=self.bucket, Key=pointer_key
                )
                version_id = response["Body"].read().decode("utf-8").strip()
                return version_id
            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    # Fallback to direct /latest/ path for backward
                    # compatibility
                    return "latest-direct"
                raise
        except Exception:
            self.logger.exception("Failed to get S3 version")
            return None

    def download_snapshot_to_efs(self, version: str) -> Dict[str, Any]:
        """
        Download snapshot from S3 to EFS.

        Args:
            version: Snapshot version to download

        Returns:
            Download result with status and metadata
        """
        start_time = time.time()

        # Create temporary directory for download
        temp_dir = tempfile.mkdtemp()
        try:
            # Download to temp directory first
            download_result = download_snapshot_atomic(
                bucket=self.bucket,
                collection=self.collection,
                local_path=temp_dir,
                verify_integrity=True,
            )

            if download_result.get("status") != "downloaded":
                shutil.rmtree(temp_dir, ignore_errors=True)
                return {
                    "status": "failed",
                    "error": f"Download failed: {download_result}",
                    "download_time_ms": (time.time() - start_time) * 1000,
                }

            # Move to EFS
            snapshot_path = os.path.join(self.efs_snapshots_dir, version)
            if os.path.exists(snapshot_path):
                shutil.rmtree(snapshot_path)

            shutil.move(temp_dir, snapshot_path)

            # Update version file
            self.set_efs_version(version)

            download_time = time.time() - start_time

            self.logger.info(
                "Snapshot downloaded to EFS",
                collection=self.collection,
                version=version,
                download_time_ms=download_time * 1000,
                efs_path=snapshot_path,
            )

            if self.metrics:
                self.metrics.timer(
                    "EFSSnapshotDownloadTime",
                    download_time,
                    {"collection": self.collection},
                )

            return {
                "status": "downloaded",
                "version": version,
                "efs_path": snapshot_path,
                "download_time_ms": download_time * 1000,
            }

        except Exception as e:
            # Clean up temp directory on error
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.logger.exception(
                "Failed to download snapshot to EFS",
                collection=self.collection,
                version=version,
            )

            if self.metrics:
                self.metrics.count(
                    "EFSSnapshotDownloadError",
                    1,
                    {
                        "collection": self.collection,
                        "error_type": type(e).__name__,
                    },
                )

            return {
                "status": "failed",
                "error": str(e),
                "download_time_ms": (time.time() - start_time) * 1000,
            }

    def get_snapshot_path(self, version: str) -> str:
        """Get the EFS path for a specific snapshot version."""
        return os.path.join(self.efs_snapshots_dir, version)

    def ensure_snapshot_available(self, version: str) -> Dict[str, Any]:
        """
        Ensure snapshot is available on EFS, downloading from S3 if needed.

        Args:
            version: Required snapshot version

        Returns:
            Result with status and snapshot path
        """
        # Check if we already have this version on EFS
        snapshot_path = self.get_snapshot_path(version)
        if os.path.exists(snapshot_path):
            self.logger.debug(
                "Snapshot already available on EFS",
                collection=self.collection,
                version=version,
                efs_path=snapshot_path,
            )
            return {
                "status": "available",
                "version": version,
                "efs_path": snapshot_path,
                "source": "efs_cache",
            }

        # Download from S3
        self.logger.info(
            "Downloading snapshot from S3 to EFS",
            collection=self.collection,
            version=version,
        )

        download_result = self.download_snapshot_to_efs(version)

        if download_result["status"] == "downloaded":
            return {
                "status": "available",
                "version": version,
                "efs_path": download_result["efs_path"],
                "source": "s3_download",
            }
        return download_result

    def sync_to_s3_async(self, version: str, snapshot_path: str) -> None:
        """
        Start background sync of EFS snapshot to S3.

        Args:
            version: Snapshot version
            snapshot_path: Path to snapshot on EFS
        """
        try:
            # This would typically be implemented as a background task
            # For now, we'll do a synchronous upload but could be made async
            self.logger.info(
                "Starting S3 sync",
                collection=self.collection,
                version=version,
                efs_path=snapshot_path,
            )

            # Upload snapshot to S3
            upload_result = upload_snapshot_atomic(
                bucket=self.bucket,
                collection=self.collection,
                local_path=snapshot_path,
            )

            if upload_result.get("status") == "uploaded":
                self.logger.info(
                    "S3 sync completed",
                    collection=self.collection,
                    version=version,
                )

                if self.metrics:
                    self.metrics.count(
                        "EFSS3SyncSuccess", 1, {"collection": self.collection}
                    )
            else:
                self.logger.error(
                    "S3 sync failed",
                    collection=self.collection,
                    version=version,
                    result=upload_result,
                )

                if self.metrics:
                    self.metrics.count(
                        "EFSS3SyncError", 1, {"collection": self.collection}
                    )

        except Exception as e:
            self.logger.exception(
                "S3 sync error",
                collection=self.collection,
                version=version,
            )

            if self.metrics:
                self.metrics.count(
                    "EFSS3SyncError",
                    1,
                    {
                        "collection": self.collection,
                        "error_type": type(e).__name__,
                    },
                )

    def cleanup_old_snapshots(self, keep_versions: int = 3) -> None:
        """
        Clean up old snapshots from EFS to save space.

        Args:
            keep_versions: Number of recent versions to keep
        """
        try:
            if not os.path.exists(self.efs_snapshots_dir):
                return

            # Get all snapshot directories
            snapshots = []
            for item in os.listdir(self.efs_snapshots_dir):
                item_path = os.path.join(self.efs_snapshots_dir, item)
                if os.path.isdir(item_path) and item != ".version":
                    snapshots.append((item, os.path.getmtime(item_path)))

            # Sort by modification time (newest first)
            snapshots.sort(key=lambda x: x[1], reverse=True)

            # Remove old snapshots
            for version, _ in snapshots[keep_versions:]:
                snapshot_path = os.path.join(self.efs_snapshots_dir, version)
                shutil.rmtree(snapshot_path)

                self.logger.info(
                    "Cleaned up old snapshot",
                    collection=self.collection,
                    version=version,
                    efs_path=snapshot_path,
                )

                if self.metrics:
                    self.metrics.count(
                        "EFSSnapshotCleanup",
                        1,
                        {"collection": self.collection},
                    )

        except Exception:
            self.logger.exception(
                "Failed to cleanup old snapshots",
                collection=self.collection,
            )


def get_efs_snapshot_manager(
    collection: str, bucket: str, logger: Any, metrics: Any = None
) -> EFSSnapshotManager:
    """
    Factory function to create EFS snapshot manager.

    Args:
        collection: Collection name (lines/words)
        bucket: S3 bucket name
        logger: Logger instance
        metrics: Optional metrics collector

    Returns:
        EFSSnapshotManager instance
    """
    return EFSSnapshotManager(collection, bucket, logger, metrics)
