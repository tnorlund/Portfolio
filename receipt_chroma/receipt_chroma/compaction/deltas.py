"""Delta merge processing for COMPACTION_RUN entities."""

import os
import tarfile
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_dynamo.constants import ChromaDBCollection

# Chroma Cloud enforces a 300-record per-request limit for all operations.
# Use 250 to stay safely under the limit (matching sync_collection_to_cloud).
_UPSERT_BATCH_SIZE = 250

# Number of retry attempts when post-upsert verification finds missing records.
_VERIFY_MAX_RETRIES = 2

# Seconds to wait before re-verifying (Chroma Cloud eventual consistency).
_VERIFY_RETRY_DELAY = 1.0


def merge_compaction_deltas(
    chroma_client: ChromaClient,
    compaction_runs: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
    bucket: str,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Merge multiple delta tarballs into an open Chroma snapshot client.

    This function processes COMPACTION_RUN messages, downloads the delta files
    from S3, and merges them into the given ChromaDB client. The caller is
    responsible for downloading the snapshot, opening the client, uploading
    the updated snapshot, and managing locks.

    Args:
        chroma_client: Open ChromaDB client with snapshot loaded
        compaction_runs: StreamMessage objects for COMPACTION_RUN entities
        collection: Target collection (LINES or WORDS)
        logger: Logger instance for observability
        bucket: S3 bucket containing delta files

    Returns:
        Tuple of (total_vectors_merged, list of per-run merge results).
        Each result dict has: run_id, image_id, receipt_id, merged_count
    """
    if not compaction_runs:
        return 0, []

    s3 = boto3.client("s3")
    total_merged = 0
    per_run_results: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as workdir:
        for msg in compaction_runs:
            merged = _process_single_run(
                msg=msg,
                chroma_client=chroma_client,
                collection=collection,
                logger=logger,
                s3_client=s3,
                bucket=bucket,
                workdir=workdir,
            )
            if merged is not None:
                total_merged += merged["merged_count"]
                per_run_results.append(merged)

    return total_merged, per_run_results


def _process_single_run(
    msg: Any,
    chroma_client: ChromaClient,
    collection: ChromaDBCollection,
    logger: Any,
    s3_client: Any,
    bucket: str,
    workdir: str,
) -> Optional[Dict[str, Any]]:
    """Process one COMPACTION_RUN message: download delta and merge vectors.

    Returns:
        Result dict with run_id, image_id, receipt_id, merged_count on
        success, or ``None`` if the run was skipped or failed.
    """
    try:
        entity = getattr(msg, "entity_data", {}) or {}
        run_id = entity.get("run_id")
        image_id = entity.get("image_id")
        receipt_id = entity.get("receipt_id")
        delta_prefix = entity.get("delta_s3_prefix")

        logger.info(
            "Processing compaction run: run_id=%s, image_id=%s, "
            "receipt_id=%s, delta_prefix=%s",
            run_id,
            image_id,
            receipt_id,
            delta_prefix,
        )

        if not delta_prefix:
            logger.warning("Skipping compaction run without delta prefix")
            return None

        # Download delta to a unique sub-directory
        delta_subdir = f"delta_{run_id}" if run_id else f"delta_{hash(msg)}"
        delta_dir = os.path.join(workdir, delta_subdir)
        os.makedirs(delta_dir, exist_ok=True)

        try:
            _download_delta_to_dir(
                s3_client=s3_client,
                default_bucket=bucket,
                delta_prefix=delta_prefix,
                dest_dir=delta_dir,
                logger=logger,
            )
        except Exception:
            logger.exception(
                "Failed to download or extract delta: %s", delta_prefix
            )
            return None

        # Merge delta vectors into snapshot
        merged_count = _merge_delta_into_snapshot(
            delta_dir=delta_dir,
            chroma_client=chroma_client,
            collection_name=collection.value,
            logger=logger,
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
        )

        if run_id and image_id is not None and receipt_id is not None:
            return {
                "run_id": run_id,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merged_count": merged_count,
            }
        return None

    except Exception:
        logger.exception("Failed processing compaction run")
        return None


def _merge_delta_into_snapshot(
    delta_dir: str,
    chroma_client: ChromaClient,
    collection_name: str,
    logger: Any,
    run_id: Any = None,
    image_id: Any = None,
    receipt_id: Any = None,
) -> int:
    """Open a local delta directory and upsert its vectors into the snapshot.

    Returns:
        Number of vectors merged (0 if the delta was empty or unreadable).
    """
    delta_client = ChromaClient(persist_directory=delta_dir, mode="read")
    try:
        src = delta_client.get_collection(collection_name)
        data = src.get(include=["documents", "embeddings", "metadatas"])
        ids = data.get("ids", []) or []

        if not ids:
            logger.warning(
                "Delta collection has no IDs: run_id=%s, image_id=%s, "
                "receipt_id=%s, collection=%s",
                run_id,
                image_id,
                receipt_id,
                collection_name,
            )
            return 0

        embeddings = data.get("embeddings")
        documents = data.get("documents")
        metadatas = data.get("metadatas")

        logger.info(
            "Upserting vectors from delta: run_id=%s, image_id=%s, "
            "receipt_id=%s, collection=%s, vector_count=%d",
            run_id,
            image_id,
            receipt_id,
            collection_name,
            len(ids),
        )

        # Batch upserts to stay within Chroma Cloud 300-record limit.
        for start in range(0, len(ids), _UPSERT_BATCH_SIZE):
            end = min(start + _UPSERT_BATCH_SIZE, len(ids))
            chroma_client.upsert(
                collection_name=collection_name,
                ids=ids[start:end],
                embeddings=(
                    embeddings[start:end] if embeddings is not None else None
                ),
                documents=(
                    documents[start:end] if documents is not None else None
                ),
                metadatas=(
                    metadatas[start:end] if metadatas is not None else None
                ),
            )

        # Verify ALL records (batched to respect Cloud limit).
        missing_ids = _verify_upsert(
            chroma_client=chroma_client,
            collection_name=collection_name,
            ids=ids,
            logger=logger,
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
        )

        if missing_ids:
            logger.error(
                "Upsert verification failed — returning 0 so the run "
                "is not marked as merged and can be retried: "
                "run_id=%s, image_id=%s, receipt_id=%s, "
                "collection=%s, total=%d, missing=%d",
                run_id,
                image_id,
                receipt_id,
                collection_name,
                len(ids),
                len(missing_ids),
            )
            return 0

        logger.info(
            "Upsert verified successfully: run_id=%s, image_id=%s, "
            "receipt_id=%s, collection=%s, verified_count=%d",
            run_id,
            image_id,
            receipt_id,
            collection_name,
            len(ids),
        )

        return len(ids)

    except Exception as exc:
        logger.info(
            "Delta has no collection or failed to read: collection=%s, "
            "run_id=%s, image_id=%s, receipt_id=%s, error=%s",
            collection_name,
            run_id,
            image_id,
            receipt_id,
            str(exc),
        )
        logger.exception("Exception details for delta collection read")
        return 0
    finally:
        delta_client.close()


def _verify_upsert(
    chroma_client: ChromaClient,
    collection_name: str,
    ids: List[str],
    logger: Any,
    run_id: Any = None,
    image_id: Any = None,
    receipt_id: Any = None,
) -> List[str]:
    """Verify all IDs exist in the collection after upsert.

    Queries in batches of ``_UPSERT_BATCH_SIZE`` to stay within Chroma Cloud's
    300-record per-request limit.  Retries up to ``_VERIFY_MAX_RETRIES`` times
    with a delay, re-upserting any missing records is **not** attempted here
    (the caller already upserted; this just reports gaps).

    Returns:
        List of IDs that are still missing after all retry attempts.
    """
    pending = list(ids)

    for attempt in range(1 + _VERIFY_MAX_RETRIES):
        missing: List[str] = []

        for start in range(0, len(pending), _UPSERT_BATCH_SIZE):
            batch = pending[start : start + _UPSERT_BATCH_SIZE]
            try:
                result = chroma_client.get(
                    collection_name=collection_name,
                    ids=batch,
                    include=[],  # Only need IDs, skip heavy payload
                )
                found = set(result.get("ids", []) or [])
                missing.extend(vid for vid in batch if vid not in found)
            except Exception:
                logger.exception(
                    "Verification GET failed: run_id=%s, image_id=%s, "
                    "receipt_id=%s, batch_size=%d, attempt=%d",
                    run_id,
                    image_id,
                    receipt_id,
                    len(batch),
                    attempt,
                )
                # Treat entire batch as missing so we retry
                missing.extend(batch)

        if not missing:
            return []

        if attempt < _VERIFY_MAX_RETRIES:
            logger.info(
                "Verification retry: run_id=%s, attempt=%d/%d, "
                "missing=%d/%d",
                run_id,
                attempt + 1,
                _VERIFY_MAX_RETRIES,
                len(missing),
                len(ids),
            )
            time.sleep(_VERIFY_RETRY_DELAY)
            pending = missing  # Only re-check the missing ones

    return missing


def _download_delta_to_dir(
    s3_client: Any,
    default_bucket: str,
    delta_prefix: str,
    dest_dir: str,
    logger: Any,
) -> None:
    """Download a delta from S3 into *dest_dir*.

    Supports legacy tarball format (delta.tar.gz) and the receipt_chroma
    directory layout uploaded via persist_and_upload_delta.

    Raises:
        FileNotFoundError: If no delta files found at the specified prefix.
        ValueError: If tarball contains unsafe paths.
    """
    bucket = default_bucket
    prefix = delta_prefix

    # Parse s3:// URI if provided
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
        logger.info("Downloading legacy tarball delta: tar_key=%s", tar_key)
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
        if error_code not in ("NoSuchKey", "404"):
            raise
        # Tarball not found; fall back to directory layout
        logger.info(
            "Tarball not found, falling back to directory layout: "
            "prefix=%s",
            prefix,
        )

    # Fallback: download all objects under prefix into dest_dir
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
    found_any = False
    dest_dir_abs = os.path.abspath(dest_dir)

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue

            found_any = True
            relative_path = key[len(prefix) :].lstrip("/")
            target_path_abs = os.path.abspath(
                os.path.join(dest_dir_abs, relative_path)
            )

            if (
                os.path.commonpath([dest_dir_abs, target_path_abs])
                != dest_dir_abs
            ):
                logger.warning(
                    "Skipping unsafe delta object path: key=%s, "
                    "dest_dir=%s",
                    key,
                    dest_dir,
                )
                continue

            os.makedirs(os.path.dirname(target_path_abs), exist_ok=True)
            s3_client.download_file(bucket, key, target_path_abs)

    if not found_any:
        raise FileNotFoundError(
            f"No delta files found at s3://{bucket}/{prefix}"
        )
