"""Delta merge processing for COMPACTION_RUN entities."""

import os
import tarfile
import tempfile
from typing import Any, Dict, List, Tuple

import boto3
from botocore.exceptions import ClientError
from receipt_chroma.data.chroma_client import ChromaClient

from receipt_dynamo.constants import ChromaDBCollection


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
    per_run_results = []

    with tempfile.TemporaryDirectory() as workdir:
        for msg in compaction_runs:
            try:
                entity = getattr(msg, "entity_data", {}) or {}
                run_id = entity.get("run_id")
                image_id = entity.get("image_id")
                receipt_id = entity.get("receipt_id")
                delta_prefix = entity.get("delta_s3_prefix")

                logger.info(
                    f"Processing compaction run: run_id={run_id}, image_id={image_id}, receipt_id={receipt_id}, delta_prefix={delta_prefix}"
                )

                # If delta prefix not provided on message, skip
                if not delta_prefix:
                    logger.warning("Skipping compaction run without delta prefix")
                    continue

                # Download delta to temporary directory (unique per run)
                delta_subdir = f"delta_{run_id}" if run_id else f"delta_{hash(msg)}"
                delta_dir = os.path.join(workdir, delta_subdir)
                os.makedirs(delta_dir, exist_ok=True)

                try:
                    logger.info(
                        f"Downloading delta: delta_prefix={delta_prefix}, dest_dir={delta_dir}, bucket={bucket}"
                    )
                    _download_delta_to_dir(
                        s3_client=s3,
                        default_bucket=bucket,
                        delta_prefix=delta_prefix,
                        dest_dir=delta_dir,
                        logger=logger,
                    )
                    logger.info("Delta download completed successfully")
                except Exception:
                    logger.exception(
                        f"Failed to download or extract delta: {delta_prefix}"
                    )
                    continue

                # Merge delta into snapshot
                merged_count = 0
                try:
                    collection_name = collection.value
                    logger.info(
                        f"Opening delta client: delta_dir={delta_dir}, collection_name={collection_name}"
                    )
                    delta_client = ChromaClient(
                        persist_directory=delta_dir, mode="read"
                    )

                    try:
                        logger.info(
                            f"Getting collection from delta: collection_name={collection_name}"
                        )
                        src = delta_client.get_collection(collection_name)
                        logger.info("Successfully got collection, reading data")
                        data = src.get(include=["documents", "embeddings", "metadatas"])
                        ids = data.get("ids", []) or []
                        logger.info(
                            f"Read data from delta collection: id_count={len(ids)}"
                        )

                        if ids:
                            logger.info(
                                f"Upserting vectors from delta: run_id={run_id}, image_id={image_id}, receipt_id={receipt_id}, collection={collection_name}, vector_count={len(ids)}, sample_id={ids[0] if ids else None}"
                            )

                            chroma_client.upsert(
                                collection_name=collection_name,
                                ids=ids,
                                embeddings=data.get("embeddings"),
                                documents=data.get("documents"),
                                metadatas=data.get("metadatas"),
                            )

                            # Verify upsert succeeded by querying back
                            verify_collection = chroma_client.get_collection(
                                collection_name
                            )
                            verify_result = verify_collection.get(
                                ids=ids[:10], include=["metadatas"]
                            )
                            verified_count = len(verify_result.get("ids", []))

                            if verified_count < len(ids[:10]):
                                logger.warning(
                                    f"Upsert verification failed: run_id={run_id}, image_id={image_id}, receipt_id={receipt_id}, collection={collection_name}, expected={len(ids[:10])}, verified={verified_count}"
                                )
                            else:
                                logger.info(
                                    f"Upsert verified successfully: run_id={run_id}, image_id={image_id}, receipt_id={receipt_id}, collection={collection_name}, verified_count={verified_count}"
                                )

                            merged_count = len(ids)
                            total_merged += merged_count
                        else:
                            logger.warning(
                                f"Delta collection has no IDs: run_id={run_id}, image_id={image_id}, receipt_id={receipt_id}, collection={collection_name}"
                            )

                    except Exception as e:
                        logger.info(
                            f"Delta has no collection or failed to read: collection={collection_name}, run_id={run_id}, image_id={image_id}, receipt_id={receipt_id}, error={str(e)}"
                        )
                        logger.exception("Exception details for delta collection read")
                    finally:
                        # Ensure delta_client is closed to prevent file handle leaks
                        delta_client.close()

                except Exception:
                    logger.exception(
                        f"Failed merging delta into snapshot: run_id={run_id}, image_id={image_id}, receipt_id={receipt_id}"
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

            except Exception:
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

    Args:
        s3_client: Boto3 S3 client
        default_bucket: Default S3 bucket name
        delta_prefix: S3 prefix (or s3:// URI) for delta files
        dest_dir: Local directory to download delta to
        logger: Logger instance

    Raises:
        FileNotFoundError: If no delta files found at the specified prefix
        ValueError: If tarball contains unsafe paths
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
        logger.info(f"Downloading legacy tarball delta: tar_key={tar_key}")
        s3_client.download_file(bucket, tar_key, tar_path)

        with tarfile.open(tar_path, "r:gz") as tar:
            # Validate all members are safe before extraction
            dest_dir_abs = os.path.abspath(dest_dir)
            for member in tar.getmembers():
                member_path_abs = os.path.abspath(
                    os.path.join(dest_dir_abs, member.name)
                )
                if os.path.commonpath([dest_dir_abs, member_path_abs]) != dest_dir_abs:
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
            f"Tarball not found, falling back to directory layout: prefix={prefix}"
        )
    except Exception:
        # If tarball exists but extraction fails, propagate
        raise

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
            target_path_abs = os.path.abspath(os.path.join(dest_dir_abs, relative_path))

            if os.path.commonpath([dest_dir_abs, target_path_abs]) != dest_dir_abs:
                logger.warning(
                    f"Skipping unsafe delta object path: key={key}, dest_dir={dest_dir}"
                )
                continue

            target_path = target_path_abs
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            s3_client.download_file(bucket, key, target_path)

    if not found_any:
        raise FileNotFoundError(f"No delta files found at s3://{bucket}/{prefix}")
