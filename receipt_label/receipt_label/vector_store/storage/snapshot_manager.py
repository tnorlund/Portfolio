"""
Snapshot management for vector store data.

This module provides high-level operations for creating, restoring,
and managing vector store snapshots with S3 synchronization.
"""

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional

from ..client.base import VectorStoreInterface
from ..client.factory import VectorClient
from .hash_calculator import HashCalculator, HashResult
from .s3_operations import S3Operations

logger = logging.getLogger(__name__)


class SnapshotManager:
    """
    High-level manager for vector store snapshots.

    This class provides a convenient interface for creating snapshots,
    uploading them to S3, downloading and restoring them, and managing
    snapshot lifecycle.
    """

    def __init__(
        self,
        bucket_name: str,
        s3_prefix: str = "snapshots/",
        s3_client: Optional[Any] = None,
    ):
        """
        Initialize the snapshot manager.

        Args:
            bucket_name: S3 bucket for snapshot storage
            s3_prefix: S3 prefix for snapshots (default: "snapshots/")
            s3_client: Optional boto3 S3 client
        """
        self.bucket_name = bucket_name
        self.s3_prefix = s3_prefix.rstrip("/") + "/"
        self.s3_ops = S3Operations(bucket_name, s3_client)

    def create_snapshot(
        self,
        vector_client: VectorStoreInterface,
        collection_name: str,
        local_directory: Optional[str] = None,
        database_name: Optional[str] = None,
        upload_to_s3: bool = True,
        include_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a snapshot of a vector store collection.

        Args:
            vector_client: Vector store client instance
            collection_name: Name of the collection to snapshot
            local_directory: Local directory for snapshot (creates temp if None)
            database_name: Optional database name for organization
            upload_to_s3: Whether to upload the snapshot to S3
            include_metadata: Optional metadata to include with snapshot

        Returns:
            Dict with snapshot creation results

        Raises:
            RuntimeError: If snapshot creation fails
        """
        # Validate collection exists
        if not vector_client.collection_exists(collection_name):
            raise RuntimeError(
                f"Collection '{collection_name}' does not exist"
            )

        # Use temporary directory if none provided
        cleanup_temp = False
        if local_directory is None:
            local_directory = tempfile.mkdtemp(
                prefix=f"snapshot_{collection_name}_"
            )
            cleanup_temp = True
            logger.info(
                "Created temporary snapshot directory: %s", local_directory
            )

        try:
            # For ChromaDB clients, we need to handle persistence differently
            if hasattr(vector_client, "persist_directory") and hasattr(
                vector_client, "client"
            ):
                # This is a ChromaDB client with persistent storage
                if vector_client.persist_directory:
                    # Copy existing persistent data
                    import shutil

                    if os.path.exists(vector_client.persist_directory):
                        shutil.copytree(
                            vector_client.persist_directory,
                            local_directory,
                            dirs_exist_ok=True,
                        )
                        logger.info(
                            "Copied persistent data to snapshot directory"
                        )
                    else:
                        # Force persistence to the snapshot directory
                        if hasattr(vector_client.client, "persist"):
                            try:
                                vector_client.client.persist()
                                logger.info("Persisted vector store data")
                            except Exception as e:
                                logger.warning(
                                    "Could not explicitly persist: %s", e
                                )

                        # If no persistent directory exists, we may need to create a new client
                        # that points to our snapshot directory
                        if not os.path.exists(vector_client.persist_directory):
                            logger.warning(
                                "No persistent data found, creating empty snapshot"
                            )
                else:
                    # In-memory client, need to create persistent version for snapshot
                    logger.warning("Cannot snapshot in-memory vector store")
                    raise RuntimeError(
                        "Cannot create snapshot from in-memory vector store"
                    )
            else:
                # Generic vector store interface - assume it handles persistence
                logger.info("Creating snapshot for generic vector store")

            # Calculate snapshot statistics
            collection_count = 0
            if hasattr(vector_client, "count"):
                try:
                    collection_count = vector_client.count(collection_name)
                except Exception as e:
                    logger.warning("Could not get collection count: %s", e)

            # Calculate directory hash
            hash_result = None
            if os.path.exists(local_directory) and any(
                os.scandir(local_directory)
            ):
                hash_result = HashCalculator.calculate_directory_hash(
                    local_directory, include_file_list=False
                )
                logger.info(
                    "Calculated snapshot hash: %s", hash_result.directory_hash
                )
            else:
                logger.warning("Snapshot directory is empty or doesn't exist")

            result = {
                "status": "success",
                "collection_name": collection_name,
                "database_name": database_name,
                "local_directory": local_directory,
                "collection_count": collection_count,
                "cleanup_temp": cleanup_temp,
            }

            if hash_result:
                result.update(
                    {
                        "directory_hash": hash_result.directory_hash,
                        "file_count": hash_result.file_count,
                        "total_size_bytes": hash_result.total_size_bytes,
                        "hash_algorithm": hash_result.hash_algorithm,
                    }
                )

            if include_metadata:
                result["metadata"] = include_metadata

            # Upload to S3 if requested
            if upload_to_s3 and hash_result:
                upload_result = self.s3_ops.upload_snapshot(
                    local_directory=local_directory,
                    s3_prefix=self.s3_prefix,
                    collection_name=collection_name,
                    database_name=database_name,
                    include_hash=True,
                    metadata=include_metadata,
                )
                result["s3_upload"] = upload_result
                result["s3_prefix"] = upload_result["snapshot_prefix"]
                logger.info(
                    "Uploaded snapshot to S3: %s",
                    upload_result["snapshot_prefix"],
                )

            logger.info(
                "Successfully created snapshot for collection '%s'",
                collection_name,
            )
            return result

        except Exception as e:
            logger.error("Failed to create snapshot: %s", e)
            # Cleanup temporary directory on failure
            if cleanup_temp and os.path.exists(local_directory):
                import shutil

                shutil.rmtree(local_directory)
            raise RuntimeError(f"Snapshot creation failed: {e}")

    def restore_snapshot(
        self,
        collection_name: str,
        local_directory: str,
        database_name: Optional[str] = None,
        download_from_s3: bool = True,
        s3_snapshot_prefix: Optional[str] = None,
        verify_hash: bool = True,
        create_client: bool = True,
        client_mode: str = "read",
    ) -> Dict[str, Any]:
        """
        Restore a vector store collection from a snapshot.

        Args:
            collection_name: Name of the collection to restore
            local_directory: Local directory to restore to
            database_name: Optional database name
            download_from_s3: Whether to download from S3 first
            s3_snapshot_prefix: Specific S3 snapshot prefix (auto-detects if None)
            verify_hash: Whether to verify hash after download
            create_client: Whether to create and return a vector client
            client_mode: Mode for created client ("read" or "write")

        Returns:
            Dict with restoration results and optional client

        Raises:
            RuntimeError: If restoration fails
        """
        result = {
            "status": "success",
            "collection_name": collection_name,
            "database_name": database_name,
            "local_directory": local_directory,
        }

        # Download from S3 if requested
        if download_from_s3:
            if s3_snapshot_prefix:
                # Use specific snapshot prefix
                download_prefix = s3_snapshot_prefix
            else:
                # Use default snapshot organization
                download_prefix = self.s3_prefix

            download_result = self.s3_ops.download_snapshot(
                s3_prefix=download_prefix,
                local_directory=local_directory,
                collection_name=collection_name,
                database_name=database_name,
                verify_hash=verify_hash,
                clear_destination=True,
            )
            result["s3_download"] = download_result
            logger.info("Downloaded snapshot from S3 to %s", local_directory)

        # Verify local directory has data
        if not os.path.exists(local_directory):
            raise RuntimeError(
                f"Snapshot directory does not exist: {local_directory}"
            )

        if not any(os.scandir(local_directory)):
            raise RuntimeError(
                f"Snapshot directory is empty: {local_directory}"
            )

        # Calculate local hash for verification
        local_hash = HashCalculator.calculate_directory_hash(
            local_directory, include_file_list=False
        )
        result.update(
            {
                "local_hash": local_hash.directory_hash,
                "file_count": local_hash.file_count,
                "total_size_bytes": local_hash.total_size_bytes,
            }
        )

        # Create vector client if requested
        if create_client:
            # Create ChromaDB client pointing to the restored directory
            client = VectorClient.create_chromadb_client(
                persist_directory=local_directory,
                mode=client_mode,
            )

            # Verify the collection is accessible
            if client.collection_exists(collection_name):
                collection_count = client.count(collection_name)
                result["collection_count"] = collection_count
                logger.info(
                    "Restored collection '%s' with %d items",
                    collection_name,
                    collection_count,
                )
            else:
                logger.warning(
                    "Collection '%s' not found in restored snapshot",
                    collection_name,
                )

            result["client"] = client

        logger.info(
            "Successfully restored snapshot for collection '%s'",
            collection_name,
        )
        return result

    def list_snapshots(
        self,
        collection_name: Optional[str] = None,
        database_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List available snapshots in S3.

        Args:
            collection_name: Optional collection name to filter by
            database_name: Optional database name to filter by

        Returns:
            List of snapshot metadata dictionaries
        """
        return self.s3_ops.list_snapshots(
            snapshot_prefix=self.s3_prefix,
            collection_name=collection_name,
            database_name=database_name,
        )

    def delete_snapshot(
        self,
        collection_name: str,
        database_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Delete a snapshot from S3.

        Args:
            collection_name: Collection name
            database_name: Optional database name

        Returns:
            Dict with deletion results
        """
        return self.s3_ops.delete_snapshot(
            s3_prefix=self.s3_prefix,
            collection_name=collection_name,
            database_name=database_name,
        )

    def compare_snapshots(
        self,
        local_directory: str,
        collection_name: str,
        database_name: Optional[str] = None,
        download_for_comparison: bool = False,
    ) -> Dict[str, Any]:
        """
        Compare a local directory with the latest S3 snapshot.

        Args:
            local_directory: Local directory to compare
            collection_name: Collection name
            database_name: Optional database name
            download_for_comparison: Whether to download S3 snapshot for comparison

        Returns:
            Dict with comparison results

        Raises:
            RuntimeError: If comparison fails
        """
        # Calculate local hash
        if not os.path.exists(local_directory):
            raise RuntimeError(
                f"Local directory does not exist: {local_directory}"
            )

        local_hash = HashCalculator.calculate_directory_hash(
            local_directory, include_file_list=False
        )

        # Get S3 hash metadata
        try:
            if database_name:
                hash_key = f"{self.s3_prefix}{database_name}/{collection_name}/.snapshot_hash"
            else:
                hash_key = f"{self.s3_prefix}{collection_name}/.snapshot_hash"

            hash_response = self.s3_ops.s3_client.get_object(
                Bucket=self.bucket_name, Key=hash_key
            )
            hash_content = hash_response["Body"].read().decode("utf-8")
            s3_hash = HashCalculator.parse_hash_metadata(hash_content)

        except Exception as e:
            logger.warning("Could not retrieve S3 hash metadata: %s", e)
            return {
                "status": "s3_hash_unavailable",
                "error": str(e),
                "local_hash": local_hash.directory_hash,
                "local_file_count": local_hash.file_count,
                "local_size_bytes": local_hash.total_size_bytes,
            }

        # Compare hashes
        comparison = HashCalculator.compare_hash_results(
            local_hash, s3_hash, strict_algorithm_check=True
        )

        result = {
            "status": "comparison_complete",
            "comparison": comparison,
            "local_directory": local_directory,
            "collection_name": collection_name,
            "database_name": database_name,
        }

        # Download for detailed comparison if requested and hashes differ
        if download_for_comparison and not comparison["is_identical"]:
            try:
                temp_dir = tempfile.mkdtemp(
                    prefix=f"snapshot_compare_{collection_name}_"
                )
                download_result = self.restore_snapshot(
                    collection_name=collection_name,
                    local_directory=temp_dir,
                    database_name=database_name,
                    download_from_s3=True,
                    verify_hash=False,
                    create_client=False,
                )
                result["downloaded_for_comparison"] = temp_dir
                result["download_result"] = download_result
            except Exception as e:
                logger.warning(
                    "Could not download snapshot for comparison: %s", e
                )
                result["download_error"] = str(e)

        return result
