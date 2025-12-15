"""
Embedding processor for upload OCR pipeline.

Generates OpenAI embeddings for receipt lines/words and emits Chroma deltas
for the enhanced compactor. Merchant and label enrichment are handled later
via Dynamo stream processors.
"""

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from receipt_chroma.embedding.delta.producer import produce_embedding_delta
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

        line_result = produce_embedding_delta(
            ids=line_payload["ids"],
            embeddings=line_payload["embeddings"],
            documents=line_payload["documents"],
            metadatas=line_payload["metadatas"],
            bucket_name=self.chromadb_bucket,
            collection_name="lines",
            database_name="lines",
            sqs_queue_url=self.lines_queue_url,
            batch_id=delta_run_id,
        )

        word_result = produce_embedding_delta(
            ids=word_payload["ids"],
            embeddings=word_payload["embeddings"],
            documents=word_payload["documents"],
            metadatas=word_payload["metadatas"],
            bucket_name=self.chromadb_bucket,
            collection_name="words",
            database_name="words",
            sqs_queue_url=self.words_queue_url,
            batch_id=delta_run_id,
        )

        return {
            "success": True,
            "run_id": delta_run_id,
            "lines_delta": line_result.get("delta_key"),
            "words_delta": word_result.get("delta_key"),
            "lines_count": len(lines),
            "words_count": len(words),
        }
