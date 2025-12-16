"""
Embedding utilities for receipt combination.

Bundles deltas as tar.gz for upload (to minimize S3 IO) and creates a
CompactionRun so the enhanced compactor can process them.
"""

import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import boto3
from openai import OpenAI

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.embedding.openai import embed_texts
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    WordEmbeddingRecord,
    build_line_payload,
    build_word_payload,
)
from receipt_chroma.s3.helpers import upload_delta_tarball
from receipt_dynamo.entities import (
    CompactionRun,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
)

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.environ.get(
    "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
)


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
    """
    Upload delta directory as tarball to S3, optionally notify SQS.

    Uses receipt_chroma.s3.helpers.upload_delta_tarball() for standardized
    tarball creation and upload.
    """
    logger.info(
        "Bundling and uploading delta tarball: dir=%s bucket=%s prefix=%s",
        local_delta_dir,
        bucket,
        delta_prefix,
    )

    # Use standardized upload function
    upload_result = upload_delta_tarball(
        local_delta_dir=local_delta_dir,
        bucket=bucket,
        delta_prefix=delta_prefix,
        metadata={"delta_key": delta_prefix},  # Match old metadata format
        region=None,  # Use default boto3 region
        s3_client=None,  # Create new S3 client
    )

    # Check upload status
    if upload_result.get("status") != "uploaded":
        error_msg = upload_result.get("error", "Unknown error")
        logger.error("Failed to upload delta tarball: %s", error_msg)
        return {
            "status": "failed",
            "error": error_msg,
            "delta_key": delta_prefix,
        }

    s3_key = upload_result["object_key"]

    # Publish SQS message if queue URL provided (maintain existing behavior)
    if sqs_queue_url:
        try:
            sqs = boto3.client("sqs")
            message_body = {
                "delta_key": delta_prefix,
                "collection": collection_name,
                "database": database_name,
                "vector_count": vector_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "batch_id": batch_id,
            }
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
            logger.info("Published SQS message for delta: %s", delta_prefix)
        except Exception as e:
            logger.error("Failed to publish SQS message: %s", e, exc_info=True)
            # Don't fail the whole operation if SQS publish fails

    return {
        "status": "uploaded",
        "delta_key": delta_prefix,
        "s3_key": s3_key,
        "tar_size_bytes": upload_result.get("tar_size_bytes", 0),
    }


def create_embeddings_and_compaction_run(
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    receipt_metadata: Optional[ReceiptMetadata],
    image_id: str,
    new_receipt_id: int,
    chromadb_bucket: str,
) -> Optional[CompactionRun]:
    """
    Create embeddings and ChromaDB deltas for combined receipt.

    Returns:
        CompactionRun entity if embeddings were created, None otherwise.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY not set; skipping embeddings")
        return None

    openai_client = OpenAI()
    run_id = str(uuid.uuid4())
    merchant_name = receipt_metadata.merchant_name if receipt_metadata else None

    line_embeddings = embed_texts(
        client=openai_client,
        texts=[ln.text for ln in receipt_lines],
        model=EMBEDDING_MODEL,
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
        model=EMBEDDING_MODEL,
    )
    word_records = [
        WordEmbeddingRecord(word=w, embedding=emb)
        for w, emb in zip(receipt_words, word_embeddings)
    ]
    word_payload = build_word_payload(
        word_records, receipt_words, [], merchant_name=merchant_name
    )

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

    return CompactionRun(
        run_id=run_id,
        image_id=image_id,
        receipt_id=new_receipt_id,
        lines_delta_prefix=lines_prefix,
        words_delta_prefix=words_prefix,
    )
