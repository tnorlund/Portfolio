"""
Embedding management orchestration for receipts.

Uses receipt_chroma's realtime OpenAI embedding helpers to generate deltas and
create CompactionRun records. Cleanup remains the responsibility of the
enhanced compactor.
"""

import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3
import json

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.embedding.openai import embed_texts
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    WordEmbeddingRecord,
    build_line_payload,
    build_word_payload,
)
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    CompactionRun,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"


def _upload_bundled_delta_to_s3(
    local_delta_dir: str,
    bucket: str,
    delta_prefix: str,
    collection_name: str,
    database_name: str,
    sqs_queue_url: Optional[str],
    batch_id: str,
    vector_count: int,
) -> Dict[str, str]:
    """Tar/gzip a delta directory, upload to S3, optionally notify SQS."""
    logger.info(
        "Bundling and uploading delta tarball: dir=%s bucket=%s prefix=%s",
        local_delta_dir,
        bucket,
        delta_prefix,
    )
    tmp_dir = tempfile.mkdtemp()
    try:
        tar_path = os.path.join(tmp_dir, "delta.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(local_delta_dir, arcname=".")

        s3_key = f"{delta_prefix.rstrip('/')}/delta.tar.gz"
        s3 = boto3.client("s3")
        s3.upload_file(
            tar_path,
            bucket,
            s3_key,
            ExtraArgs={
                "Metadata": {"delta_key": delta_prefix},
                "ContentType": "application/gzip",
                "ContentEncoding": "gzip",
            },
        )

        if sqs_queue_url:
            sqs = boto3.client("sqs")
            message_body = {
                "delta_key": delta_prefix,
                "collection": collection_name,
                "database": database_name,
                "vector_count": vector_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "batch_id": batch_id,
            }
            # Provide stable group/dedup for FIFO queues
            message_group_id = f"{collection_name}:{batch_id or 'default'}"
            message_dedup_id = f"{collection_name}:{batch_id}:{s3_key}"
            sqs.send_message(
                QueueUrl=sqs_queue_url,
                MessageBody=json.dumps(message_body),
                MessageGroupId=message_group_id,
                MessageDeduplicationId=message_dedup_id,
                MessageAttributes={
                    "collection": {
                        "StringValue": collection_name,
                        "DataType": "String",
                    },
                    "batch_id": {
                        "StringValue": batch_id or "none",
                        "DataType": "String",
                    },
                },
            )

        return {"status": "uploaded", "delta_key": delta_prefix, "s3_key": s3_key}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def create_embeddings_and_compaction_run(
    client: DynamoClient,
    chromadb_bucket: str,
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[List[ReceiptLine]] = None,
    receipt_words: Optional[List[ReceiptWord]] = None,
    receipt_metadata: Optional[ReceiptMetadata] = None,
    receipt_word_labels: Optional[List[ReceiptWordLabel]] = None,
    merchant_name: Optional[str] = None,
    add_to_dynamo: bool = False,
) -> Optional[CompactionRun]:
    """
    Create realtime embeddings, upload ChromaDB deltas, and build a CompactionRun.

    Args:
        client: DynamoDB client (used for fetches and optional CompactionRun write)
        chromadb_bucket: S3 bucket for ChromaDB deltas
        image_id: Image ID
        receipt_id: Receipt ID
        receipt_lines: Optional lines (fetched if None)
        receipt_words: Optional words (fetched if None)
        receipt_metadata: Optional metadata for merchant context
        receipt_word_labels: Optional word labels (fetched if None)
        merchant_name: Explicit merchant name override
        add_to_dynamo: If True, persist the CompactionRun via the client
    """
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        logger.warning("Embedding dependencies unavailable: %s", exc)
        return None

    if not os.environ.get("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY not set; skipping embeddings")
        return None

    if receipt_lines is None:
        receipt_lines = client.list_receipt_lines_from_receipt(
            image_id, receipt_id
        )
    if receipt_words is None:
        receipt_words = client.list_receipt_words_from_receipt(
            image_id, receipt_id
        )
    if receipt_word_labels is None:
        receipt_word_labels, _ = client.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )

    if not receipt_lines or not receipt_words:
        logger.info(
            "No lines/words found for receipt %s; skipping embedding",
            receipt_id,
        )
        return None

    merchant_name = merchant_name or (
        receipt_metadata.merchant_name if receipt_metadata else None
    )

    run_id = str(uuid.uuid4())
    openai_client = OpenAI()

    line_embeddings = embed_texts(
        client=openai_client,
        texts=[ln.text for ln in receipt_lines],
        model=os.environ.get("OPENAI_EMBEDDING_MODEL", EMBEDDING_MODEL),
    )
    line_records = [
        LineEmbeddingRecord(line=ln, embedding=emb)
        for ln, emb in zip(receipt_lines, line_embeddings)
    ]
    line_payload = build_line_payload(
        line_records, receipt_lines, receipt_words, merchant_name=merchant_name
    )

    word_embeddings = embed_texts(
        client=openai_client,
        texts=[w.text for w in receipt_words],
        model=os.environ.get("OPENAI_EMBEDDING_MODEL", EMBEDDING_MODEL),
    )
    word_records = [
        WordEmbeddingRecord(word=w, embedding=emb)
        for w, emb in zip(receipt_words, word_embeddings)
    ]
    word_payload = build_word_payload(
        word_records,
        receipt_words,
        receipt_word_labels or [],
        merchant_name=merchant_name,
    )

    # Write deltas to local Chroma DBs
    delta_lines_dir = tempfile.mkdtemp(prefix="lines_delta_")
    delta_words_dir = tempfile.mkdtemp(prefix="words_delta_")

    try:
        line_client = ChromaClient(
            persist_directory=delta_lines_dir,
            mode="delta",
            metadata_only=True,
        )
        word_client = ChromaClient(
            persist_directory=delta_words_dir,
            mode="delta",
            metadata_only=True,
        )

        line_client.upsert_vectors(collection_name="lines", **line_payload)
        word_client.upsert_vectors(collection_name="words", **word_payload)

        # Ensure files are flushed
        line_client.close()
        word_client.close()

        lines_prefix = f"lines/delta/{run_id}/"
        words_prefix = f"words/delta/{run_id}/"

        lines_result = _upload_bundled_delta_to_s3(
            local_delta_dir=delta_lines_dir,
            bucket=chromadb_bucket,
            delta_prefix=lines_prefix,
            collection_name="lines",
            database_name="lines",
            sqs_queue_url=os.environ.get("CHROMADB_LINES_QUEUE_URL"),
            batch_id=run_id,
            vector_count=len(line_payload["ids"]),
        )
        if lines_result.get("status") != "uploaded":
            logger.error("Failed to upload lines delta: %s", lines_result)
            return None

        words_result = _upload_bundled_delta_to_s3(
            local_delta_dir=delta_words_dir,
            bucket=chromadb_bucket,
            delta_prefix=words_prefix,
            collection_name="words",
            database_name="words",
            sqs_queue_url=os.environ.get("CHROMADB_WORDS_QUEUE_URL"),
            batch_id=run_id,
            vector_count=len(word_payload["ids"]),
        )
        if words_result.get("status") != "uploaded":
            logger.error("Failed to upload words delta: %s", words_result)
            return None
    finally:
        shutil.rmtree(delta_lines_dir, ignore_errors=True)
        shutil.rmtree(delta_words_dir, ignore_errors=True)

    compaction_run = CompactionRun(
        run_id=run_id,
        image_id=image_id,
        receipt_id=receipt_id,
        lines_delta_prefix=lines_prefix,
        words_delta_prefix=words_prefix,
    )

    if add_to_dynamo:
        client.add_compaction_run(compaction_run)

    logger.info("Created CompactionRun %s for receipt %s", run_id, receipt_id)
    return compaction_run


def create_embeddings(
    client: DynamoClient,
    chromadb_bucket: str,
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[List[ReceiptLine]] = None,
    receipt_words: Optional[List[ReceiptWord]] = None,
    receipt_metadata: Optional[ReceiptMetadata] = None,
    merchant_name: Optional[str] = None,
) -> Optional[str]:
    """
    Backwards-compatible wrapper that creates embeddings and persists CompactionRun.
    Returns the run_id if successful.
    """
    compaction_run = create_embeddings_and_compaction_run(
        client=client,
        chromadb_bucket=chromadb_bucket,
        image_id=image_id,
        receipt_id=receipt_id,
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
        receipt_metadata=receipt_metadata,
        merchant_name=merchant_name,
        add_to_dynamo=True,
    )
    return compaction_run.run_id if compaction_run else None
