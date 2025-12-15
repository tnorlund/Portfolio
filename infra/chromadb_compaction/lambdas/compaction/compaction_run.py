"""Compaction run processing for COMPACTION_RUN entities (S3-only mode).

This module merges S3-hosted ChromaDB deltas into the main snapshot using
atomic pointer promotion. It supports per-collection processing (lines/words)
and updates COMPACTION_RUN states in DynamoDB.
"""

import os
import shutil
import tarfile
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError
from receipt_chroma import ChromaClient
from receipt_chroma.s3 import (
    download_snapshot_atomic,
    upload_snapshot_atomic,
)
from receipt_dynamo.constants import ChromaDBCollection


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
    dynamo_client = (
        get_dynamo_client_func() if get_dynamo_client_func else None
    )

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
                run = dynamo_client.get_compaction_run(
                    image_id, receipt_id, run_id
                )
                if run is not None:
                    delta_prefix = getattr(
                        run, f"{collection.value}_delta_prefix", None
                    )

            if not delta_prefix:
                logger.error(
                    "Missing delta prefix for compaction run",
                    run_id=run_id,
                    collection=collection.value,
                )
                if OBSERVABILITY_AVAILABLE and metrics:
                    metrics.count(
                        "CompactionMissingDeltaPrefix",
                        1,
                        {"collection": collection.value},
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

                # 2) Download delta (supports tarball and receipt_chroma directory layout)
                try:
                    _download_delta_to_dir(
                        s3_client=s3,
                        default_bucket=bucket,
                        delta_prefix=delta_prefix,
                        dest_dir=delta_dir,
                        logger=logger,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to download delta", delta_prefix=delta_prefix
                    )
                    if OBSERVABILITY_AVAILABLE and metrics:
                        metrics.count(
                            "CompactionDeltaDownloadError",
                            1,
                            {"collection": collection.value},
                        )
                    raise

                # 3) Merge the target collection from delta into snapshot
                merged_vectors = 0
                try:
                    snapshot_client = ChromaClient(
                        persist_directory=snapshot_dir,
                        mode="snapshot",
                        metadata_only=True,  # No embeddings needed for snapshot operations
                    )
                    delta_client = ChromaClient(
                        persist_directory=delta_dir, mode="read"
                    )

                    collection_name = collection.value
                    try:
                        source_col = delta_client.get_collection(
                            collection_name
                        )
                        data = source_col.get(
                            include=["documents", "embeddings", "metadatas"]
                        )
                        ids = data.get("ids", []) or []
                        if ids:
                            target = snapshot_client
                            target.upsert(
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

                except Exception:  # noqa: BLE001
                    logger.exception("Failed merging delta into snapshot")
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
                        logger.exception("Failed to mark run completed")

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


def merge_compaction_deltas(
    chroma_client: "ChromaClient",
    compaction_runs: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Merge multiple delta tarballs into an already-open Chroma snapshot client.

    Returns:
        Tuple of (total_vectors_merged, list of per-run merge results).
        Each result dict has: run_id, image_id, receipt_id, merged_count
    """
    if not compaction_runs:
        return 0, []

    bucket = os.environ["CHROMADB_BUCKET"]
    s3 = boto3.client("s3")

    total_merged = 0
    per_run_results = []

    with tempfile.TemporaryDirectory() as workdir:
        for msg in compaction_runs:
            try:
                entity = getattr(msg, "entity_data", {}) or {}
                run_id = entity.get("run_id")
                image_id = entity.get("image_id")
                receipt_id = entity.get("receipt_id")
                delta_prefix = entity.get("delta_s3_prefix")
                # If not provided on message, skip (Phase A shouldn't fetch Dynamo)
                if not delta_prefix:
                    logger.warning(
                        "Skipping compaction run without delta prefix"
                    )
                    continue

                delta_dir = os.path.join(workdir, "delta")
                os.makedirs(delta_dir, exist_ok=True)
                try:
                    _download_delta_to_dir(
                        s3_client=s3,
                        default_bucket=bucket,
                        delta_prefix=delta_prefix,
                        dest_dir=delta_dir,
                        logger=logger,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Failed to download or extract delta",
                        delta_prefix=delta_prefix,
                    )
                    continue

                merged_count = 0
                try:
                    collection_name = collection.value
                    delta_client = ChromaClient(
                        persist_directory=delta_dir, mode="read"
                    )
                    try:
                        src = delta_client.get_collection(collection_name)
                        data = src.get(
                            include=["documents", "embeddings", "metadatas"]
                        )
                        ids = data.get("ids", []) or []
                        if ids:
                            logger.info(
                                "Upserting vectors from delta",
                                run_id=run_id,
                                image_id=image_id,
                                receipt_id=receipt_id,
                                collection=collection_name,
                                vector_count=len(ids),
                                sample_id=ids[0] if ids else None,
                            )
                            chroma_client.upsert(
                                collection_name=collection_name,
                                ids=ids,
                                embeddings=data.get("embeddings"),
                                documents=data.get("documents"),
                                metadatas=data.get("metadatas"),
                            )
                            # ChromaDB PersistentClient auto-persists, no explicit persist needed
                            # The close() method will ensure proper flushing

                            # Verify upsert succeeded by querying back
                            verify_collection = chroma_client.get_collection(
                                collection_name
                            )
                            verify_result = verify_collection.get(
                                ids=ids[:10], include=["metadatas"]
                            )  # Check first 10
                            verified_count = len(verify_result.get("ids", []))
                            if verified_count < len(ids[:10]):
                                logger.warning(
                                    "Upsert verification failed",
                                    run_id=run_id,
                                    image_id=image_id,
                                    receipt_id=receipt_id,
                                    collection=collection_name,
                                    expected=len(ids[:10]),
                                    verified=verified_count,
                                )
                            else:
                                logger.info(
                                    "Upsert verified successfully",
                                    run_id=run_id,
                                    image_id=image_id,
                                    receipt_id=receipt_id,
                                    collection=collection_name,
                                    verified_count=verified_count,
                                )
                            merged_count = len(ids)
                            total_merged += merged_count
                        else:
                            logger.warning(
                                "Delta collection has no IDs",
                                run_id=run_id,
                                image_id=image_id,
                                receipt_id=receipt_id,
                                collection=collection_name,
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.info(
                            "Delta has no collection or failed to read",
                            collection=collection_name,
                            run_id=run_id,
                            image_id=image_id,
                            receipt_id=receipt_id,
                            error=str(e),
                            exc_info=True,
                        )
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        "Failed merging delta into snapshot",
                        run_id=run_id,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        error=str(e),
                        exc_info=True,
                    )
                    continue

                # Track per-run result for DynamoDB updates
                if run_id and image_id is not None and receipt_id is not None:
                    per_run_results.append(
                        {
                            "run_id": run_id,
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "merged_count": merged_count,
                        }
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Failed processing compaction run")
                continue

    return total_merged, per_run_results


def _download_delta_to_dir(
    s3_client: Any,
    default_bucket: str,
    delta_prefix: str,
    dest_dir: str,
    logger: Any,
) -> None:
    """
    Download a delta from S3 into dest_dir.

    Supports legacy tarball format (delta.tar.gz) and the receipt_chroma
    directory layout uploaded via persist_and_upload_delta.
    """
    bucket = default_bucket
    prefix = delta_prefix

    if delta_prefix.startswith("s3://"):
        parts = delta_prefix.replace("s3://", "", 1).split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""

    prefix = prefix.lstrip("/")
    tar_key = f"{prefix.rstrip('/')}/delta.tar.gz"
    tar_path = os.path.join(dest_dir, "delta.tar.gz")

    # Try legacy tarball first
    try:
        s3_client.head_object(Bucket=bucket, Key=tar_key)
        logger.info("Downloading legacy tarball delta", tar_key=tar_key)
        s3_client.download_file(bucket, tar_key, tar_path)
        with tarfile.open(tar_path, "r:gz") as tar:
            # Validate all members are safe before extraction
            dest_dir_abs = os.path.abspath(dest_dir)
            for member in tar.getmembers():
                member_path_abs = os.path.abspath(
                    os.path.join(dest_dir_abs, member.name)
                )
                if (
                    os.path.commonpath([dest_dir_abs, member_path_abs])
                    != dest_dir_abs
                ):
                    raise ValueError(f"Unsafe path in tarball: {member.name}")
            tar.extractall(dest_dir_abs)
        logger.info("Successfully extracted tarball delta")
        return
    except ClientError as err:
        error_code = err.response.get("Error", {}).get("Code")
        if error_code not in ("404", "NoSuchKey", "NotFound"):
            raise
        # Tarball not found; fall back to directory layout
        logger.info(
            "Tarball not found, falling back to directory layout",
            prefix=prefix,
        )
    except Exception:
        # If tarball exists but extraction fails, propagate
        raise

    # Fallback: download all objects under prefix into dest_dir
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
    found_any = False
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            found_any = True
            relative_path = key[len(prefix) :].lstrip("/")
            target_path = os.path.join(dest_dir, relative_path)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            s3_client.download_file(bucket, key, target_path)

    if not found_any:
        raise FileNotFoundError(
            f"No delta files found at s3://{bucket}/{prefix}"
        )


def process_compaction_run_messages(
    compaction_runs: List[Any],  # StreamMessage type
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Any = None,
    OBSERVABILITY_AVAILABLE: bool = False,
    lock_manager: Optional[Any] = None,
    get_dynamo_client_func: Any = None,
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
        collection=collection.value,
    )

    results = process_compaction_runs(
        compaction_runs=compaction_runs,
        collection=collection,
        logger=logger,
        metrics=metrics,
        OBSERVABILITY_AVAILABLE=OBSERVABILITY_AVAILABLE,
        get_dynamo_client_func=get_dynamo_client_func,
        lock_manager=lock_manager,
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
