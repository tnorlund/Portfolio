"""
S3 operations for vector store data management.

This module consolidates S3-related operations for vector stores,
including uploading/downloading snapshots, deltas, and metadata.
"""

import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from .hash_calculator import HashCalculator, HashResult

logger = logging.getLogger(__name__)


class S3Operations:
    """
    Handles all S3 operations for vector store data.
    
    This class provides methods for uploading and downloading vector store
    snapshots, deltas, and associated metadata to/from S3.
    """

    def __init__(self, bucket_name: str, s3_client: Optional[Any] = None):
        """
        Initialize S3 operations.
        
        Args:
            bucket_name: S3 bucket name for vector store data
            s3_client: Optional boto3 S3 client (creates one if not provided)
        """
        self.bucket_name = bucket_name
        self.s3_client = s3_client or boto3.client("s3")

    def upload_snapshot(
        self,
        local_directory: str,
        s3_prefix: str,
        collection_name: str,
        database_name: Optional[str] = None,
        include_hash: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Upload a vector store snapshot to S3.
        
        Args:
            local_directory: Path to local directory containing snapshot
            s3_prefix: S3 prefix for the snapshot (e.g., "snapshots/")
            collection_name: Collection name (e.g., "words", "lines")
            database_name: Optional database name for organization
            include_hash: Whether to calculate and upload hash metadata
            metadata: Optional additional metadata
            
        Returns:
            Dict with upload results and metadata
            
        Raises:
            RuntimeError: If upload fails or directory doesn't exist
        """
        if not os.path.exists(local_directory):
            raise RuntimeError(f"Local directory does not exist: {local_directory}")

        if not os.path.isdir(local_directory):
            raise RuntimeError(f"Path is not a directory: {local_directory}")

        # Build S3 key prefix
        if database_name:
            key_prefix = f"{s3_prefix.rstrip('/')}/{database_name}/{collection_name}/"
        else:
            key_prefix = f"{s3_prefix.rstrip('/')}/{collection_name}/"

        logger.info("Uploading snapshot from %s to s3://%s/%s", 
                   local_directory, self.bucket_name, key_prefix)

        # Calculate hash if requested
        hash_result = None
        if include_hash:
            hash_result = HashCalculator.calculate_directory_hash(
                local_directory, include_file_list=False
            )

        # Find all files to upload
        local_path = Path(local_directory)
        files_to_upload = list(local_path.rglob("*"))
        files_to_upload = [f for f in files_to_upload if f.is_file()]

        if not files_to_upload:
            raise RuntimeError(f"No files found to upload in {local_directory}")

        # Upload all files
        uploaded_files = []
        total_size = 0
        
        for file_path in files_to_upload:
            try:
                relative_path = file_path.relative_to(local_path)
                s3_key = f"{key_prefix}{relative_path}"
                
                logger.debug("Uploading %s to s3://%s/%s", file_path, self.bucket_name, s3_key)
                self.s3_client.upload_file(str(file_path), self.bucket_name, s3_key)
                
                file_size = file_path.stat().st_size
                uploaded_files.append({
                    "local_path": str(relative_path),
                    "s3_key": s3_key,
                    "size": file_size
                })
                total_size += file_size
                
            except Exception as e:
                logger.error("Failed to upload %s to S3: %s", file_path, e)
                raise RuntimeError(f"S3 upload failed for {file_path}: {e}")

        # Upload hash metadata if calculated
        hash_key = None
        if hash_result:
            hash_content = HashCalculator.create_hash_metadata(hash_result)
            hash_key = f"{key_prefix}.snapshot_hash"
            
            try:
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=hash_key,
                    Body=hash_content,
                    ContentType="application/json"
                )
                logger.info("Uploaded hash metadata to s3://%s/%s", self.bucket_name, hash_key)
            except Exception as e:
                logger.error("Failed to upload hash metadata: %s", e)
                # Don't fail the entire operation for hash upload failure
                hash_key = None

        result = {
            "status": "success",
            "snapshot_prefix": key_prefix,
            "files_uploaded": len(uploaded_files),
            "total_size_bytes": total_size,
            "hash_key": hash_key,
            "uploaded_files": uploaded_files,
        }

        if hash_result:
            result["directory_hash"] = hash_result.directory_hash
            result["hash_algorithm"] = hash_result.hash_algorithm

        if metadata:
            result["metadata"] = metadata

        logger.info(
            "Successfully uploaded snapshot: %d files, %d bytes to %s",
            len(uploaded_files), total_size, key_prefix
        )

        return result

    def download_snapshot(
        self,
        s3_prefix: str,
        local_directory: str,
        collection_name: str,
        database_name: Optional[str] = None,
        verify_hash: bool = True,
        clear_destination: bool = False,
    ) -> Dict[str, Any]:
        """
        Download a vector store snapshot from S3.
        
        Args:
            s3_prefix: S3 prefix for the snapshot
            local_directory: Local directory to download to
            collection_name: Collection name
            database_name: Optional database name
            verify_hash: Whether to verify hash after download
            clear_destination: Whether to clear destination directory first
            
        Returns:
            Dict with download results and verification status
            
        Raises:
            RuntimeError: If download fails or verification fails
        """
        # Build S3 key prefix
        if database_name:
            key_prefix = f"{s3_prefix.rstrip('/')}/{database_name}/{collection_name}/"
        else:
            key_prefix = f"{s3_prefix.rstrip('/')}/{collection_name}/"

        logger.info("Downloading snapshot from s3://%s/%s to %s", 
                   self.bucket_name, key_prefix, local_directory)

        # Clear destination if requested
        if clear_destination and os.path.exists(local_directory):
            shutil.rmtree(local_directory)
            logger.info("Cleared destination directory: %s", local_directory)

        # Ensure destination directory exists
        os.makedirs(local_directory, exist_ok=True)

        # List all objects with the prefix
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=key_prefix)
            
            objects_to_download = []
            hash_key = None
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.endswith('.snapshot_hash'):
                            hash_key = key
                        elif key != key_prefix:  # Skip the prefix itself if it's a "directory"
                            objects_to_download.append(obj)
                            
        except Exception as e:
            raise RuntimeError(f"Failed to list objects in S3: {e}")

        if not objects_to_download:
            raise RuntimeError(f"No snapshot files found at s3://{self.bucket_name}/{key_prefix}")

        # Download all files
        downloaded_files = []
        total_size = 0
        
        for obj in objects_to_download:
            try:
                s3_key = obj['Key']
                # Calculate local file path
                relative_path = s3_key[len(key_prefix):]  # Remove prefix
                local_file_path = os.path.join(local_directory, relative_path)
                
                # Ensure directory exists for nested files
                os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                
                logger.debug("Downloading s3://%s/%s to %s", self.bucket_name, s3_key, local_file_path)
                self.s3_client.download_file(self.bucket_name, s3_key, local_file_path)
                
                downloaded_files.append({
                    "s3_key": s3_key,
                    "local_path": local_file_path,
                    "size": obj['Size']
                })
                total_size += obj['Size']
                
            except Exception as e:
                logger.error("Failed to download %s from S3: %s", s3_key, e)
                raise RuntimeError(f"S3 download failed for {s3_key}: {e}")

        result = {
            "status": "success",
            "files_downloaded": len(downloaded_files),
            "total_size_bytes": total_size,
            "local_directory": local_directory,
            "downloaded_files": downloaded_files,
        }

        # Verify hash if requested and available
        if verify_hash and hash_key:
            try:
                # Download and parse hash metadata
                hash_response = self.s3_client.get_object(Bucket=self.bucket_name, Key=hash_key)
                hash_content = hash_response['Body'].read().decode('utf-8')
                s3_hash_result = HashCalculator.parse_hash_metadata(hash_content)
                
                # Calculate local hash
                local_hash_result = HashCalculator.calculate_directory_hash(
                    local_directory, algorithm=s3_hash_result.hash_algorithm, include_file_list=False
                )
                
                # Compare hashes
                comparison = HashCalculator.compare_hash_results(
                    local_hash_result, s3_hash_result, strict_algorithm_check=True
                )
                
                result["hash_verification"] = comparison
                result["local_hash"] = local_hash_result.directory_hash
                result["s3_hash"] = s3_hash_result.directory_hash
                
                if not comparison["is_identical"]:
                    logger.warning("Hash verification failed: %s", comparison["message"])
                    result["status"] = "downloaded_with_verification_failure"
                else:
                    logger.info("Hash verification successful")
                    
            except Exception as e:
                logger.warning("Hash verification failed: %s", e)
                result["hash_verification_error"] = str(e)

        logger.info(
            "Successfully downloaded snapshot: %d files, %d bytes to %s",
            len(downloaded_files), total_size, local_directory
        )

        return result

    def upload_delta(
        self,
        local_directory: str,
        delta_prefix: str = "deltas/",
        collection_name: Optional[str] = None,
        database_name: Optional[str] = None,
    ) -> str:
        """
        Upload a delta directory to S3 with a unique identifier.
        
        Args:
            local_directory: Path to local directory containing delta
            delta_prefix: S3 prefix for deltas (default: "deltas/")
            collection_name: Optional collection name for organization
            database_name: Optional database name for organization
            
        Returns:
            S3 prefix where the delta was uploaded
            
        Raises:
            RuntimeError: If upload fails or directory doesn't exist
        """
        if not os.path.exists(local_directory):
            raise RuntimeError(f"Local directory does not exist: {local_directory}")

        # Build S3 key prefix with unique ID
        unique_id = uuid.uuid4().hex
        if database_name and collection_name:
            key_prefix = f"{delta_prefix.rstrip('/')}/{database_name}/{collection_name}/{unique_id}/"
        elif collection_name:
            key_prefix = f"{delta_prefix.rstrip('/')}/{collection_name}/{unique_id}/"
        else:
            key_prefix = f"{delta_prefix.rstrip('/')}/{unique_id}/"

        logger.info("Uploading delta from %s to s3://%s/%s", 
                   local_directory, self.bucket_name, key_prefix)

        # Find all files to upload
        local_path = Path(local_directory)
        files_to_upload = list(local_path.rglob("*"))
        files_to_upload = [f for f in files_to_upload if f.is_file()]

        if not files_to_upload:
            raise RuntimeError(f"No files found to upload in {local_directory}")

        # Upload all files
        uploaded_count = 0
        for file_path in files_to_upload:
            try:
                relative_path = file_path.relative_to(local_path)
                s3_key = f"{key_prefix}{relative_path}"
                
                logger.debug("Uploading %s to s3://%s/%s", file_path, self.bucket_name, s3_key)
                self.s3_client.upload_file(str(file_path), self.bucket_name, s3_key)
                uploaded_count += 1
                
            except Exception as e:
                logger.error("Failed to upload %s to S3: %s", file_path, e)
                raise RuntimeError(f"S3 upload failed for {file_path}: {e}")

        logger.info("Successfully uploaded %d files to S3 at %s", uploaded_count, key_prefix)
        return key_prefix

    def list_snapshots(
        self,
        snapshot_prefix: str = "snapshots/",
        collection_name: Optional[str] = None,
        database_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List available snapshots in S3.
        
        Args:
            snapshot_prefix: S3 prefix for snapshots
            collection_name: Optional collection name to filter by
            database_name: Optional database name to filter by
            
        Returns:
            List of snapshot metadata dictionaries
        """
        # Build search prefix
        if database_name and collection_name:
            search_prefix = f"{snapshot_prefix.rstrip('/')}/{database_name}/{collection_name}/"
        elif collection_name:
            search_prefix = f"{snapshot_prefix.rstrip('/')}/{collection_name}/"
        else:
            search_prefix = snapshot_prefix.rstrip('/') + '/'

        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=search_prefix)
            
            snapshots = []
            
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.endswith('.snapshot_hash'):
                            # This is hash metadata for a snapshot
                            snapshot_prefix_key = key[:-15]  # Remove '.snapshot_hash'
                            snapshots.append({
                                "snapshot_prefix": snapshot_prefix_key,
                                "hash_key": key,
                                "last_modified": obj['LastModified'].isoformat(),
                                "size": obj['Size']
                            })
                            
            return snapshots
            
        except Exception as e:
            logger.error("Failed to list snapshots: %s", e)
            raise RuntimeError(f"Failed to list snapshots in S3: {e}")

    def delete_snapshot(
        self,
        s3_prefix: str,
        collection_name: str,
        database_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Delete a snapshot from S3.
        
        Args:
            s3_prefix: S3 prefix for the snapshot
            collection_name: Collection name
            database_name: Optional database name
            
        Returns:
            Dict with deletion results
            
        Raises:
            RuntimeError: If deletion fails
        """
        # Build S3 key prefix
        if database_name:
            key_prefix = f"{s3_prefix.rstrip('/')}/{database_name}/{collection_name}/"
        else:
            key_prefix = f"{s3_prefix.rstrip('/')}/{collection_name}/"

        logger.info("Deleting snapshot at s3://%s/%s", self.bucket_name, key_prefix)

        try:
            # List all objects to delete
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=key_prefix)
            
            objects_to_delete = []
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects_to_delete.append({'Key': obj['Key']})

            if not objects_to_delete:
                logger.warning("No objects found to delete at prefix: %s", key_prefix)
                return {"status": "no_objects_found", "deleted_count": 0}

            # Delete objects in batches
            deleted_count = 0
            batch_size = 1000  # S3 delete limit
            
            for i in range(0, len(objects_to_delete), batch_size):
                batch = objects_to_delete[i:i+batch_size]
                
                response = self.s3_client.delete_objects(
                    Bucket=self.bucket_name,
                    Delete={'Objects': batch}
                )
                
                deleted_count += len(response.get('Deleted', []))
                
                # Check for errors
                if 'Errors' in response and response['Errors']:
                    for error in response['Errors']:
                        logger.error("Failed to delete %s: %s", error['Key'], error['Message'])

            logger.info("Successfully deleted %d objects from %s", deleted_count, key_prefix)
            
            return {
                "status": "success",
                "deleted_count": deleted_count,
                "snapshot_prefix": key_prefix
            }
            
        except Exception as e:
            logger.error("Failed to delete snapshot: %s", e)
            raise RuntimeError(f"Failed to delete snapshot from S3: {e}")


# Helper functions for backward compatibility

def upload_snapshot_with_hash(
    local_directory: str,
    bucket_name: str,
    s3_prefix: str,
    collection_name: str = "collection",
    s3_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Upload a snapshot with hash calculation (backward compatibility)."""
    s3_ops = S3Operations(bucket_name, s3_client)
    return s3_ops.upload_snapshot(
        local_directory=local_directory,
        s3_prefix=s3_prefix,
        collection_name=collection_name,
        include_hash=True
    )


def download_snapshot_from_s3(
    bucket_name: str,
    s3_prefix: str,
    local_directory: str,
    collection_name: str = "collection",
    s3_client: Optional[Any] = None,
    clear_destination: bool = False,
) -> Dict[str, Any]:
    """Download a snapshot from S3 (backward compatibility)."""
    s3_ops = S3Operations(bucket_name, s3_client)
    return s3_ops.download_snapshot(
        s3_prefix=s3_prefix,
        local_directory=local_directory,
        collection_name=collection_name,
        clear_destination=clear_destination
    )


def upload_delta_to_s3(
    local_directory: str,
    bucket_name: str,
    delta_prefix: str = "deltas/",
    s3_client: Optional[Any] = None,
) -> str:
    """Upload a delta to S3 (backward compatibility)."""
    s3_ops = S3Operations(bucket_name, s3_client)
    return s3_ops.upload_delta(
        local_directory=local_directory,
        delta_prefix=delta_prefix
    )