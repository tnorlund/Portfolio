"""EFS + S3 hybrid snapshot management for ChromaDB compaction.

This module provides utilities for managing ChromaDB snapshots using EFS as
primary storage and S3 as backup/archive. It implements:
- EFS snapshot caching and version tracking
- Hybrid snapshot access (EFS primary, S3 fallback)
- Background S3 sync for backup
- Atomic pointer management
"""

import os
import json
import time
import shutil
import tempfile
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

import boto3

from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_atomic,
    upload_snapshot_atomic,
    get_latest_snapshot_pointer,
)


class EFSSnapshotManager:
    """Manages ChromaDB snapshots using EFS + S3 hybrid approach."""
    
    def __init__(self, collection: str, logger: Any, metrics: Any = None):
        """
        Initialize EFS snapshot manager.
        
        Args:
            collection: Collection name (lines/words)
            logger: Logger instance
            metrics: Optional metrics collector
        """
        self.collection = collection
        self.logger = logger
        self.metrics = metrics
        
        # EFS mount path (from Lambda environment)
        self.efs_root = os.environ.get("CHROMA_ROOT", "/tmp/chroma")
        self.efs_snapshots_dir = os.path.join(self.efs_root, "snapshots", collection)
        
        # S3 configuration
        self.bucket = os.environ["CHROMADB_BUCKET"]
        self.s3_client = boto3.client("s3")
        
        # Version tracking file
        self.version_file = os.path.join(self.efs_snapshots_dir, ".version")
        
        # Ensure EFS directories exist
        os.makedirs(self.efs_snapshots_dir, exist_ok=True)
    
    def get_current_efs_version(self) -> Optional[str]:
        """Get the current snapshot version stored on EFS."""
        try:
            if os.path.exists(self.version_file):
                with open(self.version_file, 'r') as f:
                    return f.read().strip()
        except Exception as e:
            self.logger.warning("Failed to read EFS version file", error=str(e))
        return None
    
    def set_efs_version(self, version: str) -> None:
        """Set the current snapshot version on EFS."""
        try:
            with open(self.version_file, 'w') as f:
                f.write(version)
        except Exception as e:
            self.logger.error("Failed to write EFS version file", error=str(e))
    
    def get_latest_s3_version(self) -> Optional[str]:
        """Get the latest snapshot version from S3."""
        try:
            pointer = get_latest_snapshot_pointer(
                bucket=self.bucket,
                collection=self.collection
            )
            return pointer.get("version_id") if pointer else None
        except Exception as e:
            self.logger.error("Failed to get S3 version", error=str(e))
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
        
        try:
            # Create temporary directory for download
            with tempfile.TemporaryDirectory() as temp_dir:
                # Download to temp directory first
                download_result = download_snapshot_atomic(
                    bucket=self.bucket,
                    collection=self.collection,
                    local_path=temp_dir,
                    verify_integrity=True,
                )
                
                if download_result.get("status") != "downloaded":
                    return {
                        "status": "failed",
                        "error": f"Download failed: {download_result}",
                        "download_time_ms": (time.time() - start_time) * 1000
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
                    efs_path=snapshot_path
                )
                
                if self.metrics:
                    self.metrics.timer(
                        "EFSSnapshotDownloadTime",
                        download_time,
                        {"collection": self.collection}
                    )
                
                return {
                    "status": "downloaded",
                    "version": version,
                    "efs_path": snapshot_path,
                    "download_time_ms": download_time * 1000
                }
                
        except Exception as e:
            self.logger.error(
                "Failed to download snapshot to EFS",
                collection=self.collection,
                version=version,
                error=str(e)
            )
            
            if self.metrics:
                self.metrics.count(
                    "EFSSnapshotDownloadError",
                    1,
                    {"collection": self.collection, "error_type": type(e).__name__}
                )
            
            return {
                "status": "failed",
                "error": str(e),
                "download_time_ms": (time.time() - start_time) * 1000
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
                efs_path=snapshot_path
            )
            return {
                "status": "available",
                "version": version,
                "efs_path": snapshot_path,
                "source": "efs_cache"
            }
        
        # Download from S3
        self.logger.info(
            "Downloading snapshot from S3 to EFS",
            collection=self.collection,
            version=version
        )
        
        download_result = self.download_snapshot_to_efs(version)
        
        if download_result["status"] == "downloaded":
            return {
                "status": "available",
                "version": version,
                "efs_path": download_result["efs_path"],
                "source": "s3_download"
            }
        else:
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
                efs_path=snapshot_path
            )
            
            # Upload snapshot to S3
            upload_result = upload_snapshot_atomic(
                bucket=self.bucket,
                collection=self.collection,
                local_path=snapshot_path,
                version_id=version
            )
            
            if upload_result.get("status") == "uploaded":
                self.logger.info(
                    "S3 sync completed",
                    collection=self.collection,
                    version=version
                )
                
                if self.metrics:
                    self.metrics.count(
                        "EFSS3SyncSuccess",
                        1,
                        {"collection": self.collection}
                    )
            else:
                self.logger.error(
                    "S3 sync failed",
                    collection=self.collection,
                    version=version,
                    result=upload_result
                )
                
                if self.metrics:
                    self.metrics.count(
                        "EFSS3SyncError",
                        1,
                        {"collection": self.collection}
                    )
                    
        except Exception as e:
            self.logger.error(
                "S3 sync error",
                collection=self.collection,
                version=version,
                error=str(e)
            )
            
            if self.metrics:
                self.metrics.count(
                    "EFSS3SyncError",
                    1,
                    {"collection": self.collection, "error_type": type(e).__name__}
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
                    efs_path=snapshot_path
                )
                
                if self.metrics:
                    self.metrics.count(
                        "EFSSnapshotCleanup",
                        1,
                        {"collection": self.collection}
                    )
                    
        except Exception as e:
            self.logger.error(
                "Failed to cleanup old snapshots",
                collection=self.collection,
                error=str(e)
            )


def get_efs_snapshot_manager(collection: str, logger: Any, metrics: Any = None) -> EFSSnapshotManager:
    """
    Factory function to create EFS snapshot manager.
    
    Args:
        collection: Collection name (lines/words)
        logger: Logger instance
        metrics: Optional metrics collector
        
    Returns:
        EFSSnapshotManager instance
    """
    return EFSSnapshotManager(collection, logger, metrics)
