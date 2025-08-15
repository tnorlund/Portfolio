"""
ChromaDB Compactor with DynamoDB Lock Integration.

This module implements the compaction logic for merging ChromaDB deltas into
snapshots, using DynamoDB CompactionLock for distributed coordination.
"""

import os
import json
import logging
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.compaction_lock import CompactionLock
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError

from .chroma_client import ChromaDBClient

logger = logging.getLogger(__name__)


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    status: str  # "success", "busy", "error"
    snapshot_key: Optional[str] = None
    error_message: Optional[str] = None
    deltas_processed: int = 0
    duration_seconds: float = 0.0


class ChromaCompactor:
    """
    Manages ChromaDB compaction with distributed locking.

    This class handles:
    1. Acquiring/releasing DynamoDB locks
    2. Downloading current snapshot from S3
    3. Merging delta files
    4. Uploading new snapshot
    5. Updating snapshot/latest pointer
    """

    def __init__(
        self,
        dynamo_client: DynamoClient,
        bucket_name: str,
        lock_timeout_minutes: int = 15,
        s3_client: Optional[Any] = None,
    ):
        """
        Initialize the compactor.

        Args:
            dynamo_client: DynamoDB client for lock management
            bucket_name: S3 bucket containing snapshots and deltas
            lock_timeout_minutes: Lock timeout in minutes (default: 15)
            s3_client: Optional boto3 S3 client
        """
        self.dynamo_client = dynamo_client
        self.bucket_name = bucket_name
        self.lock_timeout_minutes = lock_timeout_minutes
        self._s3_client = s3_client
        self.lock_id = "chroma-main-snapshot"

    @property
    def s3_client(self):
        """Get or create S3 client."""
        if self._s3_client is None:
            self._s3_client = boto3.client("s3")

        return self._s3_client

    def compact_deltas(self, delta_keys: List[str]) -> CompactionResult:
        """
        Compact multiple delta files into the main snapshot.

        Args:
            delta_keys: List of S3 prefixes for delta files

        Returns:
            CompactionResult with status and details
        """
        start_time = datetime.now(timezone.utc)

        # Try to acquire lock
        lock = self._acquire_lock()
        if not lock:
            return CompactionResult(status="busy")

        try:
            with tempfile.TemporaryDirectory() as workdir:
                workdir_path = Path(workdir)

                # Download current snapshot
                snapshot_dir = workdir_path / "snapshot"
                self._download_s3_prefix("snapshot/latest/", str(snapshot_dir))

                # Create ChromaDB client in snapshot mode
                chroma = ChromaDBClient(
                    persist_directory=str(snapshot_dir), mode="snapshot"
                )

                # Process each delta
                deltas_processed = 0
                for delta_key in delta_keys:
                    try:
                        # Download delta
                        delta_dir = workdir_path / f"delta_{deltas_processed}"
                        self._download_s3_prefix(delta_key, str(delta_dir))

                        # Open delta ChromaDB
                        delta_client = ChromaDBClient(
                            persist_directory=str(delta_dir),
                            mode="read",  # Read-only for delta
                        )

                        # Merge collections
                        self._merge_collections(delta_client, chroma)

                        deltas_processed += 1

                        # Optional: Update heartbeat for long operations
                        if deltas_processed % 10 == 0:
                            # Update the lock's heartbeat timestamp
                            lock.heartbeat = datetime.now(timezone.utc)
                            self.dynamo_client.update_compaction_lock(lock)

                    except (OSError, ValueError, RuntimeError) as e:
                        logger.error(
                            "Error processing delta %s: %s", delta_key, e
                        )
                        # Continue with other deltas

                # ChromaDB PersistentClient auto-persists, no manual persist
                # needed

                # Upload new snapshot with timestamp
                new_snapshot_key = (
                    f"snapshot/{datetime.now(timezone.utc).isoformat()}/"
                )
                self._upload_directory(str(snapshot_dir), new_snapshot_key)

                # Update snapshot/latest pointer
                self._copy_s3_prefix(new_snapshot_key, "snapshot/latest/")

                duration = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()

                return CompactionResult(
                    status="success",
                    snapshot_key=new_snapshot_key,
                    deltas_processed=deltas_processed,
                    duration_seconds=duration,
                )

        except (OSError, ValueError, RuntimeError) as e:
            logger.error("Compaction failed: %s", e)
            return CompactionResult(
                status="error",
                error_message=str(e),
                duration_seconds=(
                    datetime.now(timezone.utc) - start_time
                ).total_seconds(),
            )
        finally:
            # Always release lock
            self._release_lock(lock)

    def _acquire_lock(self) -> Optional[CompactionLock]:
        """Acquire compaction lock from DynamoDB."""
        lock = CompactionLock(
            lock_id=self.lock_id,
            owner=str(uuid.uuid4()),
            expires=datetime.now(timezone.utc)
            + timedelta(minutes=self.lock_timeout_minutes),
        )

        try:
            self.dynamo_client.add_compaction_lock(lock)
            logger.info("Acquired compaction lock with owner %s", lock.owner)
            return lock
        except EntityAlreadyExistsError:
            logger.info("Compaction lock is held by another worker")
            return None

    def _release_lock(self, lock: CompactionLock) -> None:
        """Delete compaction lock."""
        try:
            self.dynamo_client.delete_compaction_lock(self.lock_id, lock.owner)
            logger.info("Released compaction lock %s", lock.owner)
        except (OSError, ValueError) as e:
            logger.error("Error releasing lock: %s", e)

    def _download_s3_prefix(self, prefix: str, local_dir: str) -> None:
        """Download all files under an S3 prefix to local directory."""
        Path(local_dir).mkdir(parents=True, exist_ok=True)

        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)

        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                key = obj["Key"]
                relative_path = key[len(prefix) :]
                local_path = Path(local_dir) / relative_path

                # Create parent directories
                local_path.parent.mkdir(parents=True, exist_ok=True)

                # Download file
                self.s3_client.download_file(
                    self.bucket_name, key, str(local_path)
                )

    def _upload_directory(self, local_dir: str, s3_prefix: str) -> None:
        """Upload a local directory to S3."""
        local_path = Path(local_dir)

        for file_path in local_path.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(local_path)
                s3_key = f"{s3_prefix}{relative_path}"
                self.s3_client.upload_file(
                    str(file_path), self.bucket_name, s3_key
                )

    def _copy_s3_prefix(self, source_prefix: str, dest_prefix: str) -> None:
        """Copy all objects from one S3 prefix to another."""
        # Delete existing files at destination
        self._delete_s3_prefix(dest_prefix)

        # Copy all files from source to destination
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=self.bucket_name, Prefix=source_prefix
        )

        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                source_key = obj["Key"]
                relative_path = source_key[len(source_prefix) :]
                dest_key = f"{dest_prefix}{relative_path}"

                # Copy object
                copy_source = {"Bucket": self.bucket_name, "Key": source_key}
                self.s3_client.copy_object(
                    CopySource=copy_source,
                    Bucket=self.bucket_name,
                    Key=dest_key,
                )

    def _delete_s3_prefix(self, prefix: str) -> None:
        """Delete all objects under an S3 prefix."""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)

        objects_to_delete = []
        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                objects_to_delete.append({"Key": obj["Key"]})

                # Delete in batches of 1000 (S3 limit)
                if len(objects_to_delete) >= 1000:
                    self.s3_client.delete_objects(
                        Bucket=self.bucket_name,
                        Delete={"Objects": objects_to_delete},
                    )
                    objects_to_delete = []

        # Delete remaining objects
        if objects_to_delete:
            self.s3_client.delete_objects(
                Bucket=self.bucket_name, Delete={"Objects": objects_to_delete}
            )

    def _merge_collections(
        self, source: ChromaDBClient, target: ChromaDBClient
    ) -> None:
        """
        Merge all collections from source into target.

        Args:
            source: Source ChromaDB client (read-only)
            target: Target ChromaDB client (writeable)
        """
        # Get collection names from source
        # Note: ChromaDB doesn't have a direct list_collections method,
        # so we'll use known collection names
        collection_names = ["words", "lines"]

        for collection_name in collection_names:
            try:
                # Get all data from source collection
                source_collection = source.get_collection(collection_name)

                # ChromaDB's get() returns all items when called without
                # filters
                data = source_collection.get(
                    include=["documents", "embeddings", "metadatas"]
                )

                if data["ids"]:
                    # Upsert into target collection
                    target.upsert_vectors(
                        collection_name=collection_name,
                        ids=data["ids"],
                        embeddings=data["embeddings"],
                        documents=data["documents"],
                        metadatas=data["metadatas"],
                    )

                    logger.info(
                        "Merged %d vectors from %s",
                        len(data["ids"]),
                        collection_name,
                    )

            except (OSError, ValueError, RuntimeError) as e:
                logger.warning(
                    "Error merging collection %s: %s", collection_name, e
                )


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Lambda handler for ChromaDB compaction.

    Expected event format:
    {
        "Records": [
            {
                "body": "{\"delta_key\": \"delta/2025-01-01T12:00:00Z/\"}"
            }
        ]
    }

    Environment variables:
    - DYNAMODB_TABLE_NAME: DynamoDB table for locks
    - VECTORS_BUCKET: S3 bucket for vectors
    """
    # Extract delta keys from SQS messages
    delta_keys = []
    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            delta_keys.append(body["delta_key"])
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Error parsing SQS message: %s", e)

    if not delta_keys:
        return {"statusCode": 200, "body": "No deltas to process"}

    # Initialize compactor
    dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    compactor = ChromaCompactor(
        dynamo_client=dynamo_client, bucket_name=os.environ["VECTORS_BUCKET"]
    )

    # Run compaction
    result = compactor.compact_deltas(delta_keys)

    # Return result
    return {
        "statusCode": 200 if result.status != "error" else 500,
        "body": json.dumps(
            {
                "status": result.status,
                "snapshot_key": result.snapshot_key,
                "deltas_processed": result.deltas_processed,
                "duration_seconds": result.duration_seconds,
                "error_message": result.error_message,
            }
        ),
    }
