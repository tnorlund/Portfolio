"""S3 operations for downloading and uploading ChromaDB snapshots and deltas."""

import os
import shutil
import tempfile
import tarfile
from typing import Any, Dict, List, Optional

import boto3

from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_atomic,
    upload_snapshot_atomic,
)


def download_s3_prefix(bucket: str, prefix: str, dest_dir: str) -> int:
    """Download all files from an S3 prefix to a local directory.
    
    First tries to download a bundled tarball for efficiency, then falls back
    to individual file downloads.
    """
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    downloaded = 0
    
    # Fast path: if bundled tarball exists, download and extract
    try:
        tar_key = f"{prefix.rstrip('/')}/delta.tar.gz"
        s3.head_object(Bucket=bucket, Key=tar_key)
        local_tar = os.path.join(dest_dir, "delta.tar.gz")
        os.makedirs(dest_dir, exist_ok=True)
        s3.download_file(bucket, tar_key, local_tar)

        with tarfile.open(local_tar, "r:gz") as tar:
            tar.extractall(dest_dir)
        downloaded = 1
        return downloaded
    except Exception:
        # Fallback to listing all files
        pass
        
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel_path = key[len(prefix) :].lstrip("/")
            if not rel_path:
                continue
            local_path = os.path.join(dest_dir, rel_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3.download_file(bucket, key, local_path)
            downloaded += 1
    return downloaded


def merge_chroma_delta_into_snapshot(
    delta_dir: str, collection_name: str, snapshot_dir: str
) -> int:
    """Merge a ChromaDB delta into a snapshot collection.
    
    Args:
        delta_dir: Directory containing the delta ChromaDB
        collection_name: Name of the collection to merge
        snapshot_dir: Directory containing the target snapshot
        
    Returns:
        Number of vectors merged
    """
    delta_client = ChromaDBClient(
        persist_directory=delta_dir, mode="read", metadata_only=False
    )
    snapshot_client = ChromaDBClient(
        persist_directory=snapshot_dir, mode="write", metadata_only=False
    )

    delta_collection = delta_client.get_collection(collection_name)
    target_collection = snapshot_client.get_collection(collection_name)

    total_merged = 0
    batch_size = 1000
    offset = 0

    while True:
        res = delta_collection.get(
            include=["embeddings", "metadatas", "documents"],
            limit=batch_size,
            offset=offset,
        )
        ids = res.get("ids", [])
        if not ids:
            break
        embeddings = res.get("embeddings", None)
        metadatas = res.get("metadatas", None)
        documents = res.get("documents", None)

        target_collection.upsert(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents,
        )

        total_merged += len(ids)
        offset += len(ids)

    return total_merged


def process_compaction_runs(
    compaction_runs: List[Any],  # StreamMessage type
    collection: Any,  # ChromaDBCollection type
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None,
    lock_manager: Any = None
) -> int:
    """Process COMPACTION_RUN messages by downloading deltas and merging them into snapshots.
    
    Args:
        compaction_runs: List of StreamMessage objects for COMPACTION_RUN entities
        collection: ChromaDBCollection enum value
        logger: Logger instance
        metrics: Metrics collector (optional)
        OBSERVABILITY_AVAILABLE: Whether observability features are available
        get_dynamo_client_func: Function to get DynamoDB client
        lock_manager: Lock manager instance for atomic operations
        
    Returns:
        Total number of vectors merged across all compaction runs
    """
    bucket = os.environ["CHROMADB_BUCKET"]
    
    if get_dynamo_client_func:
        dynamo = get_dynamo_client_func()
    else:
        from receipt_dynamo.data.dynamo_client import DynamoClient
        dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    
    merged_total = 0

    for msg in compaction_runs:
        data = msg.entity_data
        run_id = data.get("run_id")
        image_id = data.get("image_id")
        receipt_id = int(data.get("receipt_id", 0))
        delta_prefix = data.get("delta_s3_prefix")

        logger.info(
            "Processing COMPACTION_RUN",
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            collection=collection.value,
            delta_prefix=delta_prefix,
        )

        try:
            dynamo.mark_compaction_run_started(
                image_id, receipt_id, run_id, collection.value
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to mark run started", error=str(e))

        temp_root = tempfile.mkdtemp()
        delta_dir = os.path.join(temp_root, "delta")
        os.makedirs(delta_dir, exist_ok=True)

        downloaded = download_s3_prefix(bucket, delta_prefix, delta_dir)
        logger.info(
            "Downloaded delta",
            files=downloaded,
            prefix=delta_prefix,
            local_dir=delta_dir,
        )

        # Download current snapshot
        snapshot_dir = tempfile.mkdtemp()
        snap_result = download_snapshot_atomic(
            bucket=bucket,
            collection=collection.value,
            local_path=snapshot_dir,
            verify_integrity=True,
        )
        if snap_result.get("status") != "downloaded":
            logger.error("Failed to download snapshot", result=snap_result)
            raise RuntimeError("Snapshot download failed")

        merged = merge_chroma_delta_into_snapshot(
            delta_dir, collection.value, snapshot_dir
        )

        if lock_manager and not lock_manager.validate_ownership():
            raise RuntimeError("Lock validation failed before snapshot upload")

        upload_result = upload_snapshot_atomic(
            local_path=snapshot_dir,
            bucket=bucket,
            collection=collection.value,
            lock_manager=lock_manager,
            metadata={
                "update_type": "delta_merge",
                "run_id": run_id,
                "merged_vectors": str(merged),
            },
        )

        if upload_result.get("status") != "uploaded":
            logger.error("Snapshot upload failed", result=upload_result)
            raise RuntimeError("Snapshot upload failed")

        try:
            dynamo.mark_compaction_run_completed(
                image_id,
                receipt_id,
                run_id,
                collection.value,
                merged_vectors=merged,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to mark run completed", error=str(e))

        merged_total += merged

        shutil.rmtree(temp_root, ignore_errors=True)
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    # Include run_id of the last processed message for easier log correlation.
    logger.info(
        "Completed COMPACTION_RUN processing",
        collection=collection.value,
        merged_total=merged_total,
        run_id=run_id,
    )
    return merged_total
