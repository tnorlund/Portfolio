import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator

import boto3
import logging
from botocore.exceptions import ClientError, BotoCoreError

from receipt_label.vector_store import VectorClient
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime
from receipt_label.utils.chroma_s3_helpers import upload_bundled_delta_to_s3
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord


logger = logging.getLogger()
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def _iter_ndjson(bucket: str, key: str) -> Iterator[Dict[str, Any]]:
    logger.info("Reading NDJSON from s3://%s/%s", bucket, key)
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError) as e:
        logger.error("S3 get_object failed for s3://%s/%s: %s", bucket, key, e)
        try:
            sts = boto3.client("sts")
            ident = sts.get_caller_identity()
            logger.error(
                "Caller identity: account=%s arn=%s userId=%s",
                ident.get("Account"),
                ident.get("Arn"),
                ident.get("UserId"),
            )
        except Exception as id_err:
            logger.error("Failed to get STS caller identity: %s", id_err)
        raise
    for raw in obj["Body"].iter_lines():
        if not raw:
            continue
        yield json.loads(raw.decode("utf-8"))


def _rehydrate(lines_rows, words_rows):
    # Rehydrate to real entity classes so geometry methods exist
    lines = [ReceiptLine(**r) for r in lines_rows]
    words = [ReceiptWord(**r) for r in words_rows]
    return lines, words


def lambda_handler(event: Dict[str, Any], _ctx: Any):
    logger.info(
        "embed_from_ndjson invoked with event keys: %s",
        list(event.keys()) if isinstance(event, dict) else type(event),
    )
    if isinstance(event, dict) and event.get("Records"):
        results = []
        for rec in event.get("Records", []):
            msg = (
                json.loads(rec["body"])
                if isinstance(rec.get("body"), str)
                else rec.get("body")
            )
            logger.info(
                "Processing SQS record payload: %s",
                {
                    k: msg.get(k)
                    for k in [
                        "image_id",
                        "receipt_id",
                        "artifacts_bucket",
                        "lines_key",
                        "words_key",
                    ]
                },
            )
            results.append(_process_single(msg))
        return {"results": results}

    return _process_single(event)


def _process_single(payload: Dict[str, Any]):
    image_id = payload["image_id"]
    receipt_id = int(payload["receipt_id"])
    artifacts_bucket = payload["artifacts_bucket"]
    lines_key = payload["lines_key"]
    words_key = payload["words_key"]

    chroma_bucket = os.environ.get("CHROMADB_BUCKET", "")
    dynamo_table = os.environ["DYNAMO_TABLE_NAME"]

    logger.info(
        "Start embedding for image_id=%s receipt_id=%s artifacts_bucket=%s lines_key=%s words_key=%s chroma_bucket=%s table=%s",
        image_id,
        receipt_id,
        artifacts_bucket,
        lines_key,
        words_key,
        chroma_bucket,
        dynamo_table,
    )

    if not chroma_bucket:
        logger.error(
            "CHROMADB_BUCKET environment variable is not set; cannot upload deltas"
        )
        raise RuntimeError("CHROMADB_BUCKET is not set")

    lines_rows = list(_iter_ndjson(artifacts_bucket, lines_key))
    words_rows = list(_iter_ndjson(artifacts_bucket, words_key))
    lines, words = _rehydrate(lines_rows, words_rows)
    logger.info(
        "Loaded NDJSON rows: lines=%d words=%d",
        len(lines_rows),
        len(words_rows),
    )

    # Resolve merchant before embedding using LangGraph workflow
    merchant_name = None
    try:
        import asyncio
        from receipt_label.langchain.metadata_creation import create_receipt_metadata_simple

        dynamo = DynamoClient(dynamo_table)

        # Get API keys
        google_places_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        ollama_key = os.environ.get("OLLAMA_API_KEY")
        langchain_key = os.environ.get("LANGCHAIN_API_KEY")

        if not ollama_key:
            raise ValueError("OLLAMA_API_KEY is required for LangGraph metadata creation")
        if not langchain_key:
            raise ValueError("LANGCHAIN_API_KEY is required for LangGraph metadata creation")

        # Optional ChromaDB client for fast-path (EFS → S3 → HTTP fallback)
        chroma_line_client = None
        embed_fn = None
        storage_mode = os.environ.get("CHROMADB_STORAGE_MODE", "auto").lower()
        efs_root = os.environ.get("CHROMA_ROOT")
        chroma_http = os.environ.get("CHROMA_HTTP_ENDPOINT") or os.environ.get(
            "CHROMA_HTTP_URL"
        )

        # Determine storage mode
        if storage_mode == "s3":
            use_efs = False
            mode_reason = "explicitly set to S3-only"
        elif storage_mode == "efs":
            use_efs = True
            mode_reason = "explicitly set to EFS"
        elif storage_mode == "auto":
            # Auto-detect based on EFS availability
            use_efs = efs_root and efs_root != "/tmp/chroma"
            mode_reason = f"auto-detected (efs_root={'available' if use_efs else 'not available'})"
        else:
            # Default to S3-only for unknown modes
            use_efs = False
            mode_reason = f"unknown mode '{storage_mode}', defaulting to S3-only"

        logger.info(f"Storage mode: {storage_mode}, use_efs: {use_efs}, reason: {mode_reason}")

        # Try EFS first if available
        if use_efs:
            try:
                # Try to use EFS snapshot (similar to upload OCR handler)
                # Note: We could import UploadEFSSnapshotManager, but for simplicity,
                # we'll try direct EFS access first, then fall back to S3
                efs_path = efs_root or "/mnt/chroma"
                if os.path.exists(efs_path):
                    # Check if there's a snapshot directory
                    snapshot_dirs = [d for d in os.listdir(efs_path) if os.path.isdir(os.path.join(efs_path, d))]
                    if snapshot_dirs:
                        # Use the most recent snapshot (or a specific one)
                        # For now, try the EFS path directly
                        try:
                            chroma_line_client = VectorClient.create_chromadb_client(
                                persist_directory=efs_path,
                                mode="read",
                            )
                            logger.info(f"Created ChromaDB client from EFS: {efs_path}")
                        except Exception as efs_error:
                            logger.warning(f"Failed to create ChromaDB client from EFS: {efs_error}. Falling back to S3.")
                            use_efs = False
                    else:
                        logger.info("No snapshot directories found in EFS, falling back to S3")
                        use_efs = False
                else:
                    logger.info(f"EFS path does not exist: {efs_path}, falling back to S3")
                    use_efs = False
            except Exception as e:
                logger.warning(f"Failed to use EFS for ChromaDB access: {e}. Falling back to S3.")
                use_efs = False

        # Fallback to S3 download if EFS failed or disabled
        if not use_efs:
            logger.info("Using S3 download for ChromaDB snapshot access (optional)")
            try:
                # Download latest ChromaDB snapshot from S3
                pointer_key = "lines/snapshot/latest-pointer.txt"

                response = s3.get_object(
                    Bucket=chroma_bucket, Key=pointer_key
                )
                timestamp = response["Body"].read().decode().strip()
                logger.info(f"Latest snapshot timestamp from pointer: {timestamp}")

                # Create temporary directory for snapshot
                snapshot_dir = tempfile.mkdtemp(prefix="chroma_snapshot_")

                # Download the timestamped snapshot files from S3
                prefix = f"lines/snapshot/timestamped/{timestamp}/"

                paginator = s3.get_paginator("list_objects_v2")
                pages = paginator.paginate(
                    Bucket=chroma_bucket, Prefix=prefix
                )

                downloaded_files = 0
                for page in pages:
                    if "Contents" not in page:
                        continue

                    for obj in page["Contents"]:
                        key = obj["Key"]
                        # Skip the .snapshot_hash file
                        if key.endswith(".snapshot_hash"):
                            continue

                        # Get the relative path within the snapshot
                        relative_path = key[len(prefix):]
                        if not relative_path:
                            continue

                        # Create local directory structure
                        local_path = Path(snapshot_dir) / relative_path
                        local_path.parent.mkdir(parents=True, exist_ok=True)

                        # Download the file
                        s3.download_file(
                            chroma_bucket, key, str(local_path)
                        )
                        downloaded_files += 1

                logger.info(
                    f"Downloaded {downloaded_files} snapshot files to {snapshot_dir}"
                )

                if downloaded_files > 0:
                    # Create local ChromaDB client pointing to snapshot
                    chroma_line_client = VectorClient.create_chromadb_client(
                        persist_directory=snapshot_dir,
                        mode="read",
                    )
                    logger.info(
                        "Created local ChromaDB client from snapshot for optional fast-path"
                    )

            except Exception as e:
                logger.warning(
                    f"Failed to download ChromaDB snapshot from S3: {e}. "
                    f"Will attempt HTTP connection as fallback."
                )
                # Fall back to HTTP if snapshot download fails
                if chroma_http:
                    try:
                        chroma_line_client = VectorClient.create_chromadb_client(
                            mode="read",
                            http_url=chroma_http
                        )
                        logger.info(
                            f"Created HTTP ChromaDB client: {chroma_http}"
                        )
                    except Exception as http_error:
                        logger.warning(
                            f"Failed to create HTTP ChromaDB client: {http_error}. "
                            f"Proceeding without ChromaDB fast-path."
                        )

        # Create embedding function for ChromaDB (if available)
        if chroma_line_client:
            def _embed_texts(texts):
                if not texts:
                    return []
                if not os.environ.get("OPENAI_API_KEY"):
                    raise RuntimeError("OPENAI_API_KEY is not set")
                from receipt_label.utils import get_client_manager

                openai_client = get_client_manager().openai
                resp = openai_client.embeddings.create(
                    model="text-embedding-3-small", input=list(texts)
                )
                logger.info(
                    "OpenAI embeddings created",
                    extra={"num_embeddings": len(resp.data)},
                )
                return [d.embedding for d in resp.data]
            embed_fn = _embed_texts

        # Create metadata using LangGraph workflow
        # Pass lines and words directly (already loaded from NDJSON) to avoid re-reading from DynamoDB
        logger.info("Creating ReceiptMetadata using LangGraph workflow")
        metadata = asyncio.run(
            create_receipt_metadata_simple(
                client=dynamo,
                image_id=image_id,
                receipt_id=receipt_id,
                google_places_api_key=google_places_key,
                ollama_api_key=ollama_key,
                langsmith_api_key=langchain_key,
                thinking_strength="medium",
                receipt_lines=lines,  # Pass entities directly from NDJSON
                receipt_words=words,  # Pass entities directly from NDJSON
                chroma_line_client=chroma_line_client,  # Optional fast-path
                embed_fn=embed_fn,  # Optional embedding function
            )
        )

        if metadata:
            merchant_name = metadata.merchant_name
            logger.info(
                "Successfully created ReceiptMetadata: %s (place_id: %s)",
                merchant_name,
                metadata.place_id,
            )
        else:
            logger.warning("LangGraph workflow did not create ReceiptMetadata")
    except Exception as e:
        logger.error("Merchant resolution failed: %s", str(e), exc_info=True)
        merchant_name = None

    run_id = str(uuid.uuid4())
    delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
    delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

    line_client = VectorClient.create_chromadb_client(
        persist_directory=delta_lines_dir, mode="delta", metadata_only=True
    )
    word_client = VectorClient.create_chromadb_client(
        persist_directory=delta_words_dir, mode="delta", metadata_only=True
    )

    upsert_embeddings(
        line_client=line_client,
        word_client=word_client,
        line_embed_fn=embed_lines_realtime,
        word_embed_fn=embed_words_realtime,
        ctx={"lines": lines, "words": words},
        merchant_name=merchant_name,
    )

    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"
    logger.info(
        "Uploading line delta to s3://%s/%s", chroma_bucket, lines_prefix
    )
    upload_bundled_delta_to_s3(
        local_delta_dir=delta_lines_dir,
        bucket=chroma_bucket,
        delta_prefix=lines_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "lines",
        },
    )
    logger.info(
        "Uploading word delta to s3://%s/%s", chroma_bucket, words_prefix
    )
    upload_bundled_delta_to_s3(
        local_delta_dir=delta_words_dir,
        bucket=chroma_bucket,
        delta_prefix=words_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "words",
        },
    )

    dynamo = DynamoClient(dynamo_table)
    dynamo.add_compaction_run(
        CompactionRun(
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            lines_delta_prefix=lines_prefix,
            words_delta_prefix=words_prefix,
        )
    )

    result = {
        "run_id": run_id,
        "lines_prefix": lines_prefix,
        "words_prefix": words_prefix,
    }
    logger.info("Completed embed_from_ndjson: %s", result)
    return result
