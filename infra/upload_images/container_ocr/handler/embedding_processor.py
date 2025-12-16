"""
Embedding processor for upload OCR pipeline.

Generates OpenAI embeddings for receipt lines/words and emits Chroma deltas
for the enhanced compactor. Merchant and label enrichment are handled later
via Dynamo stream processors.
"""

import json
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

from receipt_chroma import ChromaClient
from receipt_chroma.embedding.openai import embed_texts
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    WordEmbeddingRecord,
    build_line_payload,
    build_word_payload,
)
from receipt_dynamo import DynamoClient

logger = logging.getLogger(__name__)


def _log(msg: str) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[EMBEDDING_PROCESSOR] {msg}", flush=True)
    logger.info(msg)


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


class EmbeddingProcessor:
    """Generate embeddings and emit Chroma deltas for a single receipt."""

    def __init__(
        self,
        table_name: str,
        chromadb_bucket: str,
        chroma_http_endpoint: Optional[str],
        google_places_api_key: Optional[str],
        openai_api_key: Optional[str],
        lines_queue_url: Optional[str] = None,
        words_queue_url: Optional[str] = None,
    ):
        self.dynamo = DynamoClient(table_name)
        self.chromadb_bucket = chromadb_bucket
        self.lines_queue_url = lines_queue_url
        self.words_queue_url = words_queue_url
        self.openai_api_key = openai_api_key

    def process_embeddings(
        self,
        image_id: str,
        receipt_id: int,
        lines: Optional[list] = None,
        words: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Generate embeddings and emit deltas for lines and words."""
        if lines is None or words is None:
            lines = self.dynamo.list_receipt_lines_from_receipt(
                image_id, receipt_id
            )
            words = self.dynamo.list_receipt_words_from_receipt(
                image_id, receipt_id
            )
            _log(
                f"Fetched {len(lines)} lines and {len(words)} words from DynamoDB"
            )
        else:
            _log(f"Using provided {len(lines)} lines and {len(words)} words")

        # Labels may not exist yet; stream processor will enrich later
        word_labels, _ = self.dynamo.list_receipt_word_labels_for_receipt(
            image_id, receipt_id
        )

        # Embed lines
        line_embeddings = embed_texts(
            client=None,  # client optional; model selected by env var
            texts=[ln.text for ln in lines],
            model=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            api_key=self.openai_api_key,
        )
        line_records = [
            LineEmbeddingRecord(line=ln, embedding=emb)
            for ln, emb in zip(lines, line_embeddings)
        ]
        line_payload = build_line_payload(
            line_records, lines, words, merchant_name=None
        )

        # Embed words
        word_embeddings = embed_texts(
            client=None,
            texts=[w.text for w in words],
            model=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            api_key=self.openai_api_key,
        )
        word_records = [
            WordEmbeddingRecord(word=w, embedding=emb)
            for w, emb in zip(words, word_embeddings)
        ]
        word_payload = build_word_payload(
            word_records, words, word_labels, merchant_name=None
        )

        delta_run_id = str(uuid.uuid4())

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

            line_client.upsert_vectors(
                collection_name="lines", **line_payload
            )
            word_client.upsert_vectors(
                collection_name="words", **word_payload
            )

            line_client.close()
            word_client.close()

            lines_prefix = f"lines/delta/{delta_run_id}/"
            words_prefix = f"words/delta/{delta_run_id}/"

            line_result = _upload_bundled_delta_to_s3(
                local_delta_dir=delta_lines_dir,
                bucket=self.chromadb_bucket,
                delta_prefix=lines_prefix,
                collection_name="lines",
                database_name="lines",
                sqs_queue_url=self.lines_queue_url,
                batch_id=delta_run_id,
                vector_count=len(line_payload["ids"]),
            )

            word_result = _upload_bundled_delta_to_s3(
                local_delta_dir=delta_words_dir,
                bucket=self.chromadb_bucket,
                delta_prefix=words_prefix,
                collection_name="words",
                database_name="words",
                sqs_queue_url=self.words_queue_url,
                batch_id=delta_run_id,
                vector_count=len(word_payload["ids"]),
            )
        finally:
            shutil.rmtree(delta_lines_dir, ignore_errors=True)
            shutil.rmtree(delta_words_dir, ignore_errors=True)

        return {
            "success": True,
            "run_id": delta_run_id,
            "lines_delta": line_result.get("delta_key"),
            "words_delta": word_result.get("delta_key"),
            "lines_count": len(lines),
            "words_count": len(words),
        }
