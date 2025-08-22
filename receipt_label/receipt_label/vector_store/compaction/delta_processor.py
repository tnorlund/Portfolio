"""
Delta processing utilities for vector store compaction.

This module handles the processing of delta files in the vector store
compaction pipeline, including downloading, merging, and cleanup operations.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3

from ..client.base import VectorStoreInterface
from ..client.factory import VectorClient
from ..storage.s3_operations import S3Operations

logger = logging.getLogger(__name__)


class DeltaProcessor:
    """
    Processes delta files for vector store compaction.

    This class handles downloading delta files from S3, merging them
    with existing collections, and managing the delta lifecycle.
    """

    def __init__(
        self,
        bucket_name: str,
        delta_prefix: str = "deltas/",
        s3_client: Optional[Any] = None,
    ):
        """
        Initialize the delta processor.

        Args:
            bucket_name: S3 bucket containing delta files
            delta_prefix: S3 prefix for delta files (default: "deltas/")
            s3_client: Optional boto3 S3 client
        """
        self.bucket_name = bucket_name
        self.delta_prefix = delta_prefix.rstrip("/") + "/"
        self.s3_ops = S3Operations(bucket_name, s3_client)
        self.s3_client = s3_client or boto3.client("s3")

    def list_delta_files(
        self,
        collection_name: Optional[str] = None,
        database_name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        List available delta files in S3.

        Args:
            collection_name: Optional collection name to filter by
            database_name: Optional database name to filter by
            limit: Optional limit on number of results

        Returns:
            List of delta file metadata dictionaries
        """
        # Build search prefix
        if database_name and collection_name:
            search_prefix = (
                f"{self.delta_prefix}{database_name}/{collection_name}/"
            )
        elif collection_name:
            search_prefix = f"{self.delta_prefix}{collection_name}/"
        else:
            search_prefix = self.delta_prefix

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")

            if limit:
                pages = paginator.paginate(
                    Bucket=self.bucket_name,
                    Prefix=search_prefix,
                    PaginationConfig={"MaxItems": limit},
                )
            else:
                pages = paginator.paginate(
                    Bucket=self.bucket_name, Prefix=search_prefix
                )

            deltas = []
            current_delta = None

            for page in pages:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        key = obj["Key"]

                        # Parse delta structure - deltas are organized in directories
                        # Format: deltas/[database/][collection/]uuid/files...
                        parts = key.split("/")
                        if len(parts) >= 3:  # At minimum: deltas/uuid/file
                            # Extract delta ID (the UUID directory)
                            if database_name and collection_name:
                                if (
                                    len(parts) >= 5
                                ):  # deltas/db/collection/uuid/file
                                    delta_id = parts[3]
                                    delta_prefix_key = (
                                        "/".join(parts[:4]) + "/"
                                    )
                                else:
                                    continue
                            elif collection_name:
                                if (
                                    len(parts) >= 4
                                ):  # deltas/collection/uuid/file
                                    delta_id = parts[2]
                                    delta_prefix_key = (
                                        "/".join(parts[:3]) + "/"
                                    )
                                else:
                                    continue
                            else:
                                delta_id = parts[1]
                                delta_prefix_key = "/".join(parts[:2]) + "/"

                            # Group files by delta ID
                            if (
                                current_delta is None
                                or current_delta["delta_id"] != delta_id
                            ):
                                if current_delta is not None:
                                    deltas.append(current_delta)

                                current_delta = {
                                    "delta_id": delta_id,
                                    "delta_prefix": delta_prefix_key,
                                    "files": [],
                                    "total_size": 0,
                                    "last_modified": obj["LastModified"],
                                    "collection_name": collection_name,
                                    "database_name": database_name,
                                }

                            # Add file to current delta
                            current_delta["files"].append(
                                {
                                    "key": key,
                                    "size": obj["Size"],
                                    "last_modified": obj["LastModified"],
                                }
                            )
                            current_delta["total_size"] += obj["Size"]

                            # Update last_modified to the most recent file
                            if (
                                obj["LastModified"]
                                > current_delta["last_modified"]
                            ):
                                current_delta["last_modified"] = obj[
                                    "LastModified"
                                ]

            # Add the last delta
            if current_delta is not None:
                deltas.append(current_delta)

            # Sort by last_modified (oldest first for processing order)
            deltas.sort(key=lambda x: x["last_modified"])

            logger.info("Found %d delta files for processing", len(deltas))
            return deltas

        except Exception as e:
            logger.error("Failed to list delta files: %s", e)
            raise RuntimeError(f"Failed to list delta files in S3: {e}")

    def download_delta(
        self,
        delta_prefix: str,
        local_directory: str,
        clear_destination: bool = True,
    ) -> Dict[str, Any]:
        """
        Download a delta from S3 to a local directory.

        Args:
            delta_prefix: S3 prefix for the delta (e.g., "deltas/uuid/")
            local_directory: Local directory to download to
            clear_destination: Whether to clear destination directory first

        Returns:
            Dict with download results

        Raises:
            RuntimeError: If download fails
        """
        # Clear destination if requested
        if clear_destination and os.path.exists(local_directory):
            shutil.rmtree(local_directory)
            logger.debug("Cleared destination directory: %s", local_directory)

        # Ensure destination directory exists
        os.makedirs(local_directory, exist_ok=True)

        # List all objects with the prefix
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(
                Bucket=self.bucket_name, Prefix=delta_prefix
            )

            objects_to_download = []
            for page in pages:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        key = obj["Key"]
                        if (
                            key != delta_prefix
                        ):  # Skip the prefix itself if it's a "directory"
                            objects_to_download.append(obj)

        except Exception as e:
            raise RuntimeError(f"Failed to list delta objects in S3: {e}")

        if not objects_to_download:
            raise RuntimeError(
                f"No delta files found at s3://{self.bucket_name}/{delta_prefix}"
            )

        # Download all files
        downloaded_files = []
        total_size = 0

        for obj in objects_to_download:
            try:
                s3_key = obj["Key"]
                # Calculate local file path (remove delta prefix)
                relative_path = s3_key[len(delta_prefix) :]
                local_file_path = os.path.join(local_directory, relative_path)

                # Ensure directory exists for nested files
                os.makedirs(os.path.dirname(local_file_path), exist_ok=True)

                logger.debug(
                    "Downloading s3://%s/%s to %s",
                    self.bucket_name,
                    s3_key,
                    local_file_path,
                )
                self.s3_client.download_file(
                    self.bucket_name, s3_key, local_file_path
                )

                downloaded_files.append(
                    {
                        "s3_key": s3_key,
                        "local_path": local_file_path,
                        "size": obj["Size"],
                    }
                )
                total_size += obj["Size"]

            except Exception as e:
                logger.error("Failed to download %s from S3: %s", s3_key, e)
                raise RuntimeError(f"S3 download failed for {s3_key}: {e}")

        result = {
            "status": "success",
            "delta_prefix": delta_prefix,
            "local_directory": local_directory,
            "files_downloaded": len(downloaded_files),
            "total_size_bytes": total_size,
            "downloaded_files": downloaded_files,
        }

        logger.info(
            "Successfully downloaded delta: %d files, %d bytes from %s",
            len(downloaded_files),
            total_size,
            delta_prefix,
        )

        return result

    def merge_delta_into_collection(
        self,
        delta_directory: str,
        target_client: VectorStoreInterface,
        collection_name: str,
        create_collection_if_missing: bool = True,
    ) -> Dict[str, Any]:
        """
        Merge a delta directory into an existing collection.

        Args:
            delta_directory: Local directory containing the delta
            target_client: Vector store client to merge into
            collection_name: Name of the collection to merge into
            create_collection_if_missing: Whether to create collection if it doesn't exist

        Returns:
            Dict with merge results and statistics

        Raises:
            RuntimeError: If merge fails
        """
        if not os.path.exists(delta_directory):
            raise RuntimeError(
                f"Delta directory does not exist: {delta_directory}"
            )

        # Create a temporary client for the delta
        try:
            delta_client = VectorClient.create_chromadb_client(
                persist_directory=delta_directory,
                mode="read",
            )
        except Exception as e:
            raise RuntimeError(f"Failed to create delta client: {e}")

        # Verify delta has the collection
        if not delta_client.collection_exists(collection_name):
            available_collections = delta_client.list_collections()
            raise RuntimeError(
                f"Delta does not contain collection '{collection_name}'. "
                f"Available collections: {available_collections}"
            )

        # Get or create target collection
        try:
            if not target_client.collection_exists(collection_name):
                if create_collection_if_missing:
                    # Create the collection by attempting to get it with create_if_missing
                    target_client.get_collection(
                        collection_name, create_if_missing=True
                    )
                    logger.info(
                        "Created target collection: %s", collection_name
                    )
                else:
                    raise RuntimeError(
                        f"Target collection '{collection_name}' does not exist"
                    )
        except Exception as e:
            raise RuntimeError(f"Failed to access target collection: {e}")

        # Get statistics before merge
        initial_count = target_client.count(collection_name)
        delta_count = delta_client.count(collection_name)

        logger.info(
            "Merging delta: %d items into collection with %d items",
            delta_count,
            initial_count,
        )

        # Get all items from delta collection
        try:
            # For ChromaDB, we need to get all items
            # Note: This is a simplified approach - for large collections,
            # we'd need to implement pagination
            delta_collection = delta_client.get_collection(collection_name)

            # Get all IDs from delta (ChromaDB doesn't have a direct "get all" method)
            # We'll use a query with a very large limit as a workaround
            all_items = delta_collection.get(
                include=["metadatas", "documents", "embeddings"]
            )

            if not all_items["ids"]:
                logger.warning("Delta collection is empty")
                return {
                    "status": "success",
                    "items_merged": 0,
                    "initial_count": initial_count,
                    "final_count": initial_count,
                    "delta_count": 0,
                }

            # Merge items into target collection
            target_client.upsert_vectors(
                collection_name=collection_name,
                ids=all_items["ids"],
                embeddings=all_items.get("embeddings"),
                documents=all_items.get("documents"),
                metadatas=all_items.get("metadatas"),
            )

            # Get final count
            final_count = target_client.count(collection_name)
            items_merged = len(all_items["ids"])

            result = {
                "status": "success",
                "collection_name": collection_name,
                "items_merged": items_merged,
                "initial_count": initial_count,
                "final_count": final_count,
                "delta_count": delta_count,
                "delta_directory": delta_directory,
            }

            logger.info(
                "Successfully merged delta: %d items merged, final count: %d",
                items_merged,
                final_count,
            )

            return result

        except Exception as e:
            logger.error("Failed to merge delta into collection: %s", e)
            raise RuntimeError(f"Delta merge failed: {e}")

    def process_delta_batch(
        self,
        delta_list: List[Dict[str, Any]],
        target_client: VectorStoreInterface,
        collection_name: str,
        max_deltas: Optional[int] = None,
        cleanup_temp_dirs: bool = True,
    ) -> Dict[str, Any]:
        """
        Process a batch of deltas by downloading and merging them.

        Args:
            delta_list: List of delta metadata from list_delta_files()
            target_client: Vector store client to merge into
            collection_name: Name of the collection to merge into
            max_deltas: Optional limit on number of deltas to process
            cleanup_temp_dirs: Whether to cleanup temporary directories

        Returns:
            Dict with batch processing results

        Raises:
            RuntimeError: If batch processing fails
        """
        if not delta_list:
            return {
                "status": "success",
                "deltas_processed": 0,
                "total_items_merged": 0,
                "skipped_deltas": 0,
                "failed_deltas": 0,
            }

        # Limit number of deltas if specified
        if max_deltas:
            delta_list = delta_list[:max_deltas]

        processed_deltas = []
        failed_deltas = []
        skipped_deltas = []
        total_items_merged = 0
        temp_dirs = []

        logger.info("Processing batch of %d deltas", len(delta_list))

        for i, delta_info in enumerate(delta_list):
            delta_id = delta_info["delta_id"]
            delta_prefix = delta_info["delta_prefix"]

            logger.info(
                "Processing delta %d/%d: %s", i + 1, len(delta_list), delta_id
            )

            # Create temporary directory for this delta
            temp_dir = tempfile.mkdtemp(prefix=f"delta_{delta_id}_")
            temp_dirs.append(temp_dir)

            try:
                # Download delta
                download_result = self.download_delta(
                    delta_prefix=delta_prefix,
                    local_directory=temp_dir,
                    clear_destination=True,
                )

                # Merge delta
                merge_result = self.merge_delta_into_collection(
                    delta_directory=temp_dir,
                    target_client=target_client,
                    collection_name=collection_name,
                    create_collection_if_missing=True,
                )

                processed_deltas.append(
                    {
                        "delta_id": delta_id,
                        "delta_prefix": delta_prefix,
                        "download_result": download_result,
                        "merge_result": merge_result,
                    }
                )

                total_items_merged += merge_result["items_merged"]
                logger.info(
                    "Successfully processed delta %s: %d items merged",
                    delta_id,
                    merge_result["items_merged"],
                )

            except Exception as e:
                logger.error("Failed to process delta %s: %s", delta_id, e)
                failed_deltas.append(
                    {
                        "delta_id": delta_id,
                        "delta_prefix": delta_prefix,
                        "error": str(e),
                    }
                )

        # Cleanup temporary directories if requested
        if cleanup_temp_dirs:
            for temp_dir in temp_dirs:
                try:
                    if os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
                        logger.debug("Cleaned up temp directory: %s", temp_dir)
                except Exception as e:
                    logger.warning(
                        "Failed to cleanup temp directory %s: %s", temp_dir, e
                    )

        result = {
            "status": "success" if not failed_deltas else "partial_success",
            "deltas_processed": len(processed_deltas),
            "total_items_merged": total_items_merged,
            "skipped_deltas": len(skipped_deltas),
            "failed_deltas": len(failed_deltas),
            "processed_deltas": processed_deltas,
            "failed_deltas": failed_deltas,
            "skipped_deltas": skipped_deltas,
        }

        logger.info(
            "Completed batch processing: %d processed, %d failed, %d items merged",
            len(processed_deltas),
            len(failed_deltas),
            total_items_merged,
        )

        return result

    def delete_processed_deltas(
        self,
        delta_prefixes: List[str],
    ) -> Dict[str, Any]:
        """
        Delete processed delta files from S3.

        Args:
            delta_prefixes: List of S3 prefixes for deltas to delete

        Returns:
            Dict with deletion results
        """
        if not delta_prefixes:
            return {
                "status": "success",
                "deltas_deleted": 0,
                "objects_deleted": 0,
                "failed_deletions": 0,
            }

        deleted_deltas = []
        failed_deletions = []
        total_objects_deleted = 0

        for delta_prefix in delta_prefixes:
            try:
                # List all objects with this prefix
                paginator = self.s3_client.get_paginator("list_objects_v2")
                pages = paginator.paginate(
                    Bucket=self.bucket_name, Prefix=delta_prefix
                )

                objects_to_delete = []
                for page in pages:
                    if "Contents" in page:
                        for obj in page["Contents"]:
                            objects_to_delete.append({"Key": obj["Key"]})

                if objects_to_delete:
                    # Delete objects in batches
                    batch_size = 1000  # S3 delete limit
                    objects_deleted = 0

                    for i in range(0, len(objects_to_delete), batch_size):
                        batch = objects_to_delete[i : i + batch_size]

                        response = self.s3_client.delete_objects(
                            Bucket=self.bucket_name, Delete={"Objects": batch}
                        )

                        objects_deleted += len(response.get("Deleted", []))

                        # Check for errors
                        if "Errors" in response and response["Errors"]:
                            for error in response["Errors"]:
                                logger.error(
                                    "Failed to delete %s: %s",
                                    error["Key"],
                                    error["Message"],
                                )

                    deleted_deltas.append(
                        {
                            "delta_prefix": delta_prefix,
                            "objects_deleted": objects_deleted,
                        }
                    )
                    total_objects_deleted += objects_deleted

                    logger.info(
                        "Deleted delta %s: %d objects",
                        delta_prefix,
                        objects_deleted,
                    )
                else:
                    logger.warning(
                        "No objects found to delete for prefix: %s",
                        delta_prefix,
                    )

            except Exception as e:
                logger.error("Failed to delete delta %s: %s", delta_prefix, e)
                failed_deletions.append(
                    {
                        "delta_prefix": delta_prefix,
                        "error": str(e),
                    }
                )

        result = {
            "status": "success" if not failed_deletions else "partial_success",
            "deltas_deleted": len(deleted_deltas),
            "objects_deleted": total_objects_deleted,
            "failed_deletions": len(failed_deletions),
            "deleted_deltas": deleted_deltas,
            "failed_deletions": failed_deletions,
        }

        logger.info(
            "Completed delta deletion: %d deltas deleted, %d objects deleted, %d failures",
            len(deleted_deltas),
            total_objects_deleted,
            len(failed_deletions),
        )

        return result
