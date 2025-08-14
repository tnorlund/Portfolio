"""Compaction handler for ChromaDB deltas - unified container version."""

import json
import os
import tempfile
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List

import boto3
import chromadb
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.utils.lock_manager import LockManager

from .base import BaseLambdaHandler


class CompactionHandler(BaseLambdaHandler):
    """Handler for compacting multiple ChromaDB deltas.

    This is a direct port of the original chromadb_compaction_lambda/handler.py
    to work within the unified container architecture.

    This handler is called at the end of the step function to compact all deltas
    created during parallel embedding processing.
    """

    def __init__(self):
        super().__init__("Compaction")
        # Initialize clients
        self.s3_client = boto3.client("s3")
        self.dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

        # Get configuration from environment
        self.heartbeat_interval = int(
            os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "60")
        )
        self.lock_duration_minutes = int(
            os.environ.get("LOCK_DURATION_MINUTES", "5")
        )

    def handle(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Compact multiple delta files into ChromaDB using chunked processing.

        This handler supports two modes:
        1. Chunked processing: Process deltas in chunks without locks
        2. Final merge: Acquire lock and merge all intermediate chunks

        Input event format for chunked processing:
        {
            "operation": "process_chunk",
            "batch_id": "batch-uuid",
            "chunk_index": 0,
            "delta_results": [
                {
                    "delta_key": "delta/abc123/",
                    "delta_id": "abc123",
                    "embedding_count": 100
                },
                ...
            ]
        }

        Input event format for final merge:
        {
            "operation": "final_merge",
            "batch_id": "batch-uuid",
            "total_chunks": 5
        }
        """
        self.logger.info("Starting ChromaDB compaction handler")
        self.logger.info("Event: %s", json.dumps(event))

        # Determine operation mode
        operation = event.get("operation")

        if operation == "process_chunk":
            return self.process_chunk_handler(event)
        if operation == "final_merge":
            return self.final_merge_handler(event)

        self.logger.error(
            "Invalid operation: %s. Expected 'process_chunk' or 'final_merge'",
            operation,
        )
        raise ValueError(
            f"Invalid operation: {operation}. "
            "Operation must be 'process_chunk' or 'final_merge'"
        )

    def process_chunk_handler(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Process a chunk of deltas without acquiring locks.

        Writes output to intermediate/{batch_id}/chunk-{index}/ in S3.
        """
        self.logger.info("Processing chunk compaction")

        batch_id = event.get("batch_id")
        chunk_index = event.get("chunk_index")
        delta_results = event.get("delta_results", [])

        if not batch_id:
            raise ValueError("batch_id is required for chunk processing")

        if chunk_index is None:
            raise ValueError("chunk_index is required for chunk processing")

        if not delta_results:
            self.logger.info(
                "No delta results in chunk %d, skipping", chunk_index
            )
            return {
                "batch_id": batch_id,
                "chunk_index": chunk_index,
                "embeddings_processed": 0,
                "message": "Empty chunk processed",
            }

        # Limit chunk size to 10 deltas as required
        chunk_deltas = delta_results[:10]
        remaining_deltas = delta_results[10:]

        self.logger.info(
            "Processing chunk %d with %d deltas (batch_id: %s)",
            chunk_index,
            len(chunk_deltas),
            batch_id,
        )

        try:
            # Process chunk deltas
            chunk_result = self.process_chunk_deltas(
                batch_id, chunk_index, chunk_deltas
            )

            # Prepare response
            response = {
                "batch_id": batch_id,
                "chunk_index": chunk_index,
                "intermediate_key": chunk_result["intermediate_key"],
                "embeddings_processed": chunk_result["embeddings_processed"],
                "processing_time_seconds": chunk_result["processing_time"],
                "message": "Chunk processed successfully",
            }

            # Add continuation data if there are remaining deltas
            if remaining_deltas:
                response["next_chunk_index"] = chunk_index + 1
                response["remaining_deltas"] = remaining_deltas
                response["has_more_chunks"] = True
            else:
                response["has_more_chunks"] = False

            self.logger.info(
                "Chunk %d processing completed: %s", chunk_index, response
            )
            return response

        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.error(
                "Chunk %d processing failed: %s", chunk_index, str(e)
            )
            raise RuntimeError(
                f"Chunk {chunk_index} processing failed: {str(e)}"
            ) from e

    def final_merge_handler(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Final merge step that acquires lock and combines intermediate chunks.

        Preserves existing heartbeat support for the final merge operation.
        """
        self.logger.info("Starting final merge operation")

        batch_id = event.get("batch_id")
        total_chunks = event.get("total_chunks")

        if not batch_id:
            raise ValueError("batch_id is required for final merge")

        if total_chunks is None:
            raise ValueError("total_chunks is required for final merge")

        self.logger.info(
            "Final merge for batch %s with %d chunks", batch_id, total_chunks
        )

        # Create a fresh lock manager for this invocation
        lock_manager = LockManager(
            dynamo_client=self.dynamo_client,
            heartbeat_interval=self.heartbeat_interval,
            lock_duration_minutes=self.lock_duration_minutes,
        )

        try:
            # Acquire compaction lock for final merge
            if not lock_manager.acquire():
                self.logger.warning(
                    "Could not acquire compaction lock for final merge - "
                    "another compaction is in progress"
                )
                # Return a special status that Step Functions can handle
                return {
                    "locked": True,
                    "message": "Final merge blocked - compaction already in progress",
                }

            # Start heartbeat thread to keep lock alive during processing
            lock_manager.start_heartbeat()

            # Perform final merge
            merge_result = self.merge_intermediate_chunks(
                batch_id, total_chunks
            )

            # Stop heartbeat thread and release lock
            lock_manager.stop_heartbeat()
            lock_manager.release()

            self.logger.info(
                "Final merge completed successfully: %s", merge_result
            )

            return {
                "compaction_method": "chunked",
                "batch_id": batch_id,
                "chunks_merged": total_chunks,
                "total_embeddings": merge_result["total_embeddings"],
                "snapshot_key": merge_result["snapshot_key"],
                "processing_time_seconds": merge_result["processing_time"],
                "message": "Final merge completed successfully",
            }

        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.error("Final merge failed: %s", str(e))
            # Stop heartbeat thread and try to release lock on error
            lock_manager.stop_heartbeat()
            lock_manager.release()

            raise RuntimeError(f"Final merge failed: {str(e)}") from e

    def _process_single_delta(
        self,
        delta_key: str,
        temp_dir: str,
        bucket_name: str,
        chunk_collection: Any,
    ) -> int:
        """Process a single delta and add to chunk collection."""
        self.logger.info("Processing delta: %s", delta_key)

        # Download delta to temporary directory
        delta_dir = os.path.join(
            temp_dir, "delta", os.path.basename(delta_key.rstrip("/"))
        )
        os.makedirs(delta_dir, exist_ok=True)

        # List and download all files in delta
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket_name, Prefix=delta_key)

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                local_path = os.path.join(
                    delta_dir, os.path.relpath(key, delta_key)
                )
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                self.s3_client.download_file(bucket_name, key, local_path)

        # Load delta ChromaDB
        delta_client = chromadb.PersistentClient(path=delta_dir)
        delta_collection = delta_client.get_collection("receipt_words")

        # Get all data from delta
        delta_data = delta_collection.get(
            include=["embeddings", "documents", "metadatas"]
        )

        if delta_data["ids"]:
            # Add to chunk collection
            embeddings_count = len(delta_data["ids"])
            chunk_collection.add(
                ids=delta_data["ids"],
                embeddings=delta_data["embeddings"],
                documents=delta_data["documents"],
                metadatas=delta_data["metadatas"],
            )
            return embeddings_count
        return 0

    def _upload_chunk_to_s3(
        self,
        temp_dir: str,
        batch_id: str,
        chunk_index: int,
        bucket_name: str,
    ) -> str:
        """Upload chunk to S3 and return the intermediate key."""
        intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
        chunk_dir = os.path.join(temp_dir, "chunk")

        for root, _, files in os.walk(chunk_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, chunk_dir)
                s3_key = intermediate_key + relative_path
                self.s3_client.upload_file(local_path, bucket_name, s3_key)

        self.logger.info(
            "Uploaded chunk %d to s3://%s/%s",
            chunk_index,
            bucket_name,
            intermediate_key,
        )
        return intermediate_key

    def process_chunk_deltas(
        self,
        batch_id: str,
        chunk_index: int,
        delta_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Process a chunk of deltas and write to intermediate S3 location.

        This function does not acquire locks and processes deltas independently.
        """
        start_time = time.time()
        bucket_name = os.environ["CHROMADB_BUCKET"]
        total_embeddings_processed = 0

        # Extract delta keys from results
        delta_keys = [result["delta_key"] for result in delta_results]

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create ChromaDB instance for this chunk
            chunk_client = chromadb.PersistentClient(
                path=os.path.join(temp_dir, "chunk")
            )
            chunk_collection = chunk_client.get_or_create_collection(
                name="receipt_words",
                metadata={
                    "created_at": datetime.utcnow().isoformat(),
                    "batch_id": batch_id,
                    "chunk_index": chunk_index,
                },
            )

            # Process each delta in the chunk
            for delta_key in delta_keys:
                embeddings_count = self._process_single_delta(
                    delta_key, temp_dir, bucket_name, chunk_collection
                )
                total_embeddings_processed += embeddings_count
                if embeddings_count > 0:
                    self.logger.info(
                        "Added %d embeddings from delta to chunk %d "
                        "(total in chunk: %d)",
                        embeddings_count,
                        chunk_index,
                        total_embeddings_processed,
                    )

            # Upload chunk to intermediate S3 location
            intermediate_key = self._upload_chunk_to_s3(
                temp_dir, batch_id, chunk_index, bucket_name
            )

        elapsed_time = time.time() - start_time
        self.logger.info(
            "Chunk %d processing completed in %.2f seconds with %d embeddings",
            chunk_index,
            elapsed_time,
            total_embeddings_processed,
        )

        return {
            "intermediate_key": intermediate_key,
            "embeddings_processed": total_embeddings_processed,
            "deltas_processed": len(delta_keys),
            "processing_time": elapsed_time,
        }

    def _process_intermediate_chunk(
        self,
        chunk_index: int,
        batch_id: str,
        temp_dir: str,
        bucket_name: str,
        main_collection: Any,
    ) -> int:
        """Process a single intermediate chunk and merge into main collection."""
        intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
        self.logger.info("Merging intermediate chunk: %s", intermediate_key)

        # Download chunk to temporary directory
        chunk_dir = os.path.join(temp_dir, "chunks", f"chunk-{chunk_index}")
        os.makedirs(chunk_dir, exist_ok=True)

        # List and download all files in chunk
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket_name, Prefix=intermediate_key)

        chunk_has_data = False
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                local_path = os.path.join(
                    chunk_dir, os.path.relpath(key, intermediate_key)
                )
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                self.s3_client.download_file(bucket_name, key, local_path)
                chunk_has_data = True

        if not chunk_has_data:
            self.logger.warning(
                "No data found for chunk %d, skipping", chunk_index
            )
            return 0

        # Load chunk ChromaDB
        chunk_client = chromadb.PersistentClient(path=chunk_dir)
        chunk_collection = chunk_client.get_collection("receipt_words")

        # Get all data from chunk
        chunk_data = chunk_collection.get(
            include=["embeddings", "documents", "metadatas"]
        )

        if chunk_data["ids"]:
            # Add to main collection
            embeddings_count = len(chunk_data["ids"])
            main_collection.add(
                ids=chunk_data["ids"],
                embeddings=chunk_data["embeddings"],
                documents=chunk_data["documents"],
                metadatas=chunk_data["metadatas"],
            )
            return embeddings_count
        return 0

    def _cleanup_intermediate_chunks(
        self, batch_id: str, total_chunks: int, bucket_name: str
    ) -> None:
        """Clean up intermediate chunks after merge."""
        if (
            os.environ.get("DELETE_INTERMEDIATE_CHUNKS", "true").lower()
            != "true"
        ):
            return

        for chunk_index in range(total_chunks):
            intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
            # Delete all objects with this prefix
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(
                Bucket=bucket_name, Prefix=intermediate_key
            )

            for page in pages:
                if "Contents" in page:
                    objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                    if objects:  # Only delete if objects exist
                        # Fix for mypy typeddict issue
                        delete_batch = {"Objects": objects}
                        self.s3_client.delete_objects(
                            Bucket=bucket_name, Delete=delete_batch
                        )
            self.logger.info(
                "Cleaned up intermediate chunk: %s", intermediate_key
            )

    def merge_intermediate_chunks(
        self, batch_id: str, total_chunks: int
    ) -> Dict[str, Any]:
        """Merge all intermediate chunks into a final ChromaDB snapshot.

        This function is called during the final merge step with lock protection.
        """
        start_time = time.time()
        bucket_name = os.environ["CHROMADB_BUCKET"]
        snapshot_id = str(uuid.uuid4())
        total_embeddings_processed = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create main ChromaDB instance for final merge
            main_client = chromadb.PersistentClient(
                path=os.path.join(temp_dir, "main")
            )
            main_collection = main_client.get_or_create_collection(
                name="receipt_words",
                metadata={
                    "created_at": datetime.utcnow().isoformat(),
                    "batch_id": batch_id,
                    "snapshot_id": snapshot_id,
                },
            )

            # Process each intermediate chunk
            for chunk_index in range(total_chunks):
                embeddings_count = self._process_intermediate_chunk(
                    chunk_index,
                    batch_id,
                    temp_dir,
                    bucket_name,
                    main_collection,
                )
                total_embeddings_processed += embeddings_count
                if embeddings_count > 0:
                    self.logger.info(
                        "Merged %d embeddings from chunk %d (total: %d)",
                        embeddings_count,
                        chunk_index,
                        total_embeddings_processed,
                    )

            # Upload final snapshot to S3
            snapshot_key = f"snapshot/{snapshot_id}/"
            main_dir = os.path.join(temp_dir, "main")

            for root, _, files in os.walk(main_dir):
                for file in files:
                    local_path = os.path.join(root, file)
                    relative_path = os.path.relpath(local_path, main_dir)
                    s3_key = snapshot_key + relative_path
                    self.s3_client.upload_file(local_path, bucket_name, s3_key)

            self.logger.info(
                "Uploaded final snapshot %s to s3://%s/%s",
                snapshot_id,
                bucket_name,
                snapshot_key,
            )

            # Clean up intermediate chunks
            self._cleanup_intermediate_chunks(
                batch_id, total_chunks, bucket_name
            )

        elapsed_time = time.time() - start_time
        self.logger.info(
            "Final merge completed in %.2f seconds. "
            "Merged %d chunks with %d total embeddings",
            elapsed_time,
            total_chunks,
            total_embeddings_processed,
        )

        return {
            "snapshot_id": snapshot_id,
            "snapshot_key": snapshot_key,
            "chunks_merged": total_chunks,
            "total_embeddings": total_embeddings_processed,
            "processing_time": elapsed_time,
        }
