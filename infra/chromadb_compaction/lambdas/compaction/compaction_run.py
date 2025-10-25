"""Compaction run processing for COMPACTION_RUN entities (S3-only mode).

This module merges S3-hosted ChromaDB deltas into the main snapshot using
atomic pointer promotion. It supports per-collection processing (lines/words)
and updates COMPACTION_RUN states in DynamoDB.
"""

from typing import Any, Dict, List, Optional
import os
import tempfile
import tarfile
import shutil

import boto3

from receipt_dynamo.constants import ChromaDBCollection
from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_atomic,
    upload_snapshot_atomic,
)


def process_compaction_runs(
    compaction_runs: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    get_dynamo_client_func: Any = None,
    lock_manager: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Process compaction runs from SQS stream messages (S3-only).

    For each run message, this:
    - Resolves delta prefix from the message or DynamoDB
    - Downloads current snapshot from S3 (atomic pointer)
    - Downloads and extracts the delta tarball
    - Merges the target collection from delta into the snapshot
    - Uploads snapshot back to S3 using atomic pointer promotion
    - Marks the COMPACTION_RUN as started/completed
    """

    results: List[Dict[str, Any]] = []

    if not compaction_runs:
        return results

    bucket = os.environ["CHROMADB_BUCKET"]
    s3 = boto3.client("s3")

    # Optional Dynamo client provider (to update run status / fetch prefixes)
    dynamo_client = get_dynamo_client_func() if get_dynamo_client_func else None

    for msg in compaction_runs:
        try:
            entity = getattr(msg, "entity_data", {}) or {}
            run_id = entity.get("run_id")
            image_id = entity.get("image_id")
            receipt_id = entity.get("receipt_id")

            # Determine delta prefix to process for this collection
            # Prefer explicit delta_s3_prefix on INSERT messages
            delta_prefix = entity.get("delta_s3_prefix")

            if not delta_prefix and dynamo_client:
                # Fallback: fetch run entity from Dynamo to read prefixes
                run = dynamo_client.get_compaction_run(image_id, receipt_id, run_id)
                if run is not None:
                    delta_prefix = (
                        getattr(run, f"{collection.value}_delta_prefix", None)
                    )

            if not delta_prefix:
                logger.error(
                    "Missing delta prefix for compaction run",
                    run_id=run_id,
                    collection=collection.value,
                )
                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count(
                        "CompactionMissingDeltaPrefix", 1, {"collection": collection.value}
                    )
                results.append(
                    {
                        "run_id": run_id,
                        "collection": collection.value,
                        "status": "error",
                        "error": "missing_delta_prefix",
                    }
                )
                continue

            # Mark run started for this collection
            if dynamo_client:
                try:
                    dynamo_client.mark_compaction_run_started(
                        image_id, receipt_id, run_id, collection.value
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to mark run started", error=str(e))

            # Working directory per message
            with tempfile.TemporaryDirectory() as workdir:
                snapshot_dir = os.path.join(workdir, "snapshot")
                delta_dir = os.path.join(workdir, "delta")
                os.makedirs(delta_dir, exist_ok=True)

                # 1) Download current snapshot (atomic)
                dl_result = download_snapshot_atomic(
                    bucket=bucket,
                    collection=collection.value,
                    local_path=snapshot_dir,
                    verify_integrity=True,
                )
                if dl_result.get("status") != "downloaded":
                    logger.error(
                        "Failed to download snapshot", result=dl_result
                    )
                    if OBSERVABILITY_AVAILABLE and metrics:
                        metrics.count(
                            "CompactionSnapshotDownloadError",
                            1,
                            {"collection": collection.value},
                        )
                    raise RuntimeError(
                        f"Snapshot download failed: {dl_result}"
                    )

                # 2) Download delta tarball and extract
                tar_key = f"{delta_prefix.rstrip('/')}/delta.tar.gz"
                tar_path = os.path.join(workdir, "delta.tar.gz")
                try:
                    s3.download_file(bucket, tar_key, tar_path)
                except Exception as e:  # noqa: BLE001
                    logger.error("Failed to download delta tarball", error=str(e), key=tar_key)
                    if OBSERVABILITY_AVAILABLE and metrics:
                        metrics.count(
                            "CompactionDeltaDownloadError",
                            1,
                            {"collection": collection.value},
                        )
                    raise

                try:
                    with tarfile.open(tar_path, "r:gz") as tar:
                        tar.extractall(delta_dir)
                except Exception as e:  # noqa: BLE001
                    logger.error("Failed to extract delta tarball", error=str(e))
                    raise

                # 3) Merge the target collection from delta into snapshot
                merged_vectors = 0
                try:
                    snapshot_client = ChromaDBClient(
                        persist_directory=snapshot_dir, mode="snapshot"
                    )
                    delta_client = ChromaDBClient(
                        persist_directory=delta_dir, mode="read"
                    )

                    collection_name = collection.value
                    try:
                        source_col = delta_client.get_collection(collection_name)
                        data = source_col.get(
                            include=["documents", "embeddings", "metadatas"]
                        )
                        ids = data.get("ids", []) or []
                        if ids:
                            target = snapshot_client
                            target.upsert_vectors(
                                collection_name=collection_name,
                                ids=ids,
                                embeddings=data.get("embeddings"),
                                documents=data.get("documents"),
                                metadatas=data.get("metadatas"),
                            )
                            merged_vectors = len(ids)
                    except Exception as e:  # noqa: BLE001
                        # If delta has no such collection, treat as no-op
                        logger.info(
                            "Delta has no collection or failed to read",
                            collection=collection_name,
                            error=str(e),
                        )

                except Exception as e:  # noqa: BLE001
                    logger.error("Failed merging delta into snapshot", error=str(e))
                    raise

                # 4) Upload updated snapshot atomically
                up_result = upload_snapshot_atomic(
                    local_path=snapshot_dir,
                    bucket=bucket,
                    collection=collection.value,
                    lock_manager=lock_manager,
                    metadata={
                        "run_id": run_id or "",
                        "image_id": image_id or "",
                        "receipt_id": str(receipt_id or ""),
                        "merged_vectors": str(merged_vectors),
                        "source": "compaction_run",
                    },
                )
                if up_result.get("status") != "uploaded":
                    logger.error("Snapshot upload failed", result=up_result)
                    if OBSERVABILITY_AVAILABLE and metrics:
                        metrics.count(
                            "CompactionSnapshotUploadError",
                            1,
                            {"collection": collection.value},
                        )
                    raise RuntimeError(f"Snapshot upload failed: {up_result}")

                # 5) Mark run completed
                if dynamo_client:
                    try:
                        dynamo_client.mark_compaction_run_completed(
                            image_id,
                            receipt_id,
                            run_id,
                            collection.value,
                            merged_vectors=merged_vectors,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Failed to mark run completed", error=str(e))

                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count(
                        "CompactionRunProcessed",
                        1,
                        {"collection": collection.value},
                    )

                results.append(
                    {
                        "run_id": run_id,
                        "collection": collection.value,
                        "status": "uploaded",
                        "merged_vectors": merged_vectors,
                        "version_id": up_result.get("version_id"),
                    }
                )

        except Exception as e:  # noqa: BLE001
            # Mark failed if possible
            try:
                if dynamo_client and entity:
                    dynamo_client.mark_compaction_run_failed(
                        image_id,
                        receipt_id,
                        run_id,
                        collection.value,
                        error=str(e),
                    )
            except Exception:
                pass

            results.append(
                {
                    "run_id": entity.get("run_id"),
                    "collection": collection.value,
                    "status": "error",
                    "error": str(e),
                }
            )

    return results


def process_compaction_run_messages(
    compaction_runs: List[Any],  # StreamMessage type
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    lock_manager: Optional[Any] = None,
    get_dynamo_client_func: Any = None
) -> int:
    """Process COMPACTION_RUN messages for delta merging.
    
    Args:
        compaction_runs: List of StreamMessage objects for COMPACTION_RUN entities
        collection: ChromaDBCollection enum value
        logger: Logger instance
        metrics: Metrics collector (optional)
        OBSERVABILITY_AVAILABLE: Whether observability features are available
        lock_manager: Lock manager instance for atomic operations
        get_dynamo_client_func: Function to get DynamoDB client
        
    Returns:
        Total number of vectors merged across all compaction runs
    """
    if not compaction_runs:
        return 0
        
    logger.info(
        "Processing compaction runs",
        count=len(compaction_runs),
        collection=collection.value
    )
    
    results = process_compaction_runs(
        compaction_runs=compaction_runs,
        collection=collection,
        logger=logger,
        metrics=metrics,
        OBSERVABILITY_AVAILABLE=OBSERVABILITY_AVAILABLE,
        get_dynamo_client_func=get_dynamo_client_func,
        lock_manager=lock_manager
    )

    # Sum the number of merged vectors across successful runs for this collection
    total_merged = 0
    for item in results:
        try:
            if item.get("status") == "uploaded":
                total_merged += int(item.get("merged_vectors", 0) or 0)
        except Exception:
            # Be defensive: ignore malformed items
            continue

    return total_merged
