"""EFS snapshot manager for upload lambda read-only ChromaDB access.

This module provides utilities for managing ChromaDB snapshots using EFS as
primary storage for read-only access. It implements:
- EFS snapshot caching and version tracking
- EFS-to-local copy for ChromaDB performance
- S3 fallback for initial population
- Read-only access (no writes back to EFS)
"""

import os
import json
import time
import shutil
import tempfile
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


class UploadEFSSnapshotManager:
    """Manages ChromaDB snapshots using EFS for read-only access."""
    
    def __init__(self, collection: str, logger: Any):
        """
        Initialize EFS snapshot manager for upload lambda.
        
        Args:
            collection: Collection name (lines/words)
            logger: Logger instance
        """
        self.collection = collection
        self.logger = logger
        
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
            self.logger.warning(f"Failed to read EFS version file: {e}")
        return None
    
    def get_latest_s3_version(self) -> Optional[str]:
        """Get the latest snapshot version from S3."""
        try:
            pointer_key = f"{self.collection}/snapshot/latest-pointer.txt"
            response = self.s3_client.get_object(
                Bucket=self.bucket, 
                Key=pointer_key
            )
            return response["Body"].read().decode().strip()
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                self.logger.warning(f"No latest-pointer.txt found for {self.collection}")
                return None
            raise
        except Exception as e:
            self.logger.error(f"Failed to get latest S3 version: {e}")
            return None
    
    def ensure_snapshot_on_efs(self, version: str) -> Dict[str, Any]:
        """
        Ensure the specified snapshot version is available on EFS.
        Downloads from S3 if not present.
        
        Args:
            version: Snapshot version to ensure
            
        Returns:
            Dict with snapshot info: {
                "efs_path": str,
                "version": str,
                "source": "efs" | "s3_download"
            }
        """
        efs_snapshot_path = os.path.join(self.efs_snapshots_dir, version)
        
        # Check if snapshot already exists on EFS
        if os.path.exists(efs_snapshot_path) and os.path.isdir(efs_snapshot_path):
            self.logger.info(
                f"Snapshot {version} already exists on EFS",
                collection=self.collection,
                efs_path=efs_snapshot_path
            )
            return {
                "efs_path": efs_snapshot_path,
                "version": version,
                "source": "efs"
            }
        
        # Download from S3 to EFS
        self.logger.info(
            f"Downloading snapshot {version} from S3 to EFS",
            collection=self.collection,
            efs_path=efs_snapshot_path
        )
        
        download_start_time = time.time()
        
        # Create EFS directory
        os.makedirs(efs_snapshot_path, exist_ok=True)
        
        # Download snapshot files from S3
        prefix = f"{self.collection}/snapshot/timestamped/{version}/"
        
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)
        
        downloaded_files = 0
        for page in pages:
            if "Contents" not in page:
                continue
            
            for obj in page["Contents"]:
                key = obj["Key"]
                # Skip the .snapshot_hash file
                if key.endswith(".snapshot_hash"):
                    continue
                
                # Get the relative path within the snapshot
                relative_path = key[len(prefix):]
                if not relative_path:
                    continue
                
                # Create local directory structure
                local_path = Path(efs_snapshot_path) / relative_path
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Download the file
                self.s3_client.download_file(
                    self.bucket, key, str(local_path)
                )
                downloaded_files += 1
        
        download_time_ms = (time.time() - download_start_time) * 1000
        
        # Update version file
        with open(self.version_file, 'w') as f:
            f.write(version)
        
        self.logger.info(
            f"Downloaded {downloaded_files} files to EFS",
            collection=self.collection,
            version=version,
            efs_path=efs_snapshot_path,
            download_time_ms=download_time_ms
        )
        
        return {
            "efs_path": efs_snapshot_path,
            "version": version,
            "source": "s3_download",
            "download_time_ms": download_time_ms,
            "files_count": downloaded_files
        }
    
    def copy_to_local(self, efs_snapshot_path: str) -> str:
        """
        Copy snapshot from EFS to local storage for ChromaDB operations.
        
        Args:
            efs_snapshot_path: Path to snapshot on EFS
            
        Returns:
            Path to local copy of snapshot
        """
        local_snapshot_path = tempfile.mkdtemp(prefix=f"chroma_local_{self.collection}_")
        
        copy_start_time = time.time()
        shutil.copytree(efs_snapshot_path, local_snapshot_path, dirs_exist_ok=True)
        copy_time_ms = (time.time() - copy_start_time) * 1000
        
        self.logger.info(
            f"Copied snapshot from EFS to local",
            collection=self.collection,
            efs_path=efs_snapshot_path,
            local_path=local_snapshot_path,
            copy_time_ms=copy_time_ms
        )
        
        return local_snapshot_path
    
    def get_snapshot_for_chromadb(self) -> Optional[Dict[str, Any]]:
        """
        Get a snapshot ready for ChromaDB operations.
        Returns local path for optimal ChromaDB performance.
        
        Returns:
            Dict with snapshot info: {
                "local_path": str,
                "version": str,
                "source": str,
                "copy_time_ms": float
            }
        """
        # Get latest version from S3
        latest_version = self.get_latest_s3_version()
        if not latest_version:
            self.logger.warning(f"No latest version found for {self.collection}")
            return None
        
        # Ensure snapshot is on EFS
        snapshot_info = self.ensure_snapshot_on_efs(latest_version)
        
        # Copy to local for ChromaDB operations
        local_path = self.copy_to_local(snapshot_info["efs_path"])
        
        return {
            "local_path": local_path,
            "version": latest_version,
            "source": snapshot_info["source"],
            "copy_time_ms": snapshot_info.get("download_time_ms", 0)
        }
