"""
Embedding management orchestration for receipts.

Handles realtime embedding generation with OpenAI, creation of ChromaDB deltas
via receipt_chroma, upload to S3, and CompactionRun creation. Embedding
cleanup remains the responsibility of the enhanced compactor.
"""

import logging
import os
import tempfile
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

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


def _embed_texts(
    openai_client, inputs: Sequence[str], model: str = EMBEDDING_MODEL
) -> List[List[float]]:
    response = openai_client.embeddings.create(model=model, input=list(inputs))
    return [item.embedding for item in response.data]


def _average_word_confidence(words: List[ReceiptWord]) -> Optional[float]:
    if not words:
        return None
    return sum(word.confidence for word in words) / len(words)


def _build_line_vectors(
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    merchant_name: Optional[str],
    openai_client,
) -> Optional[Dict[str, List]]:
    try:
        from receipt_chroma.embedding.formatting.line_format import (
            format_line_context_embedding_input,
            get_line_neighbors,
        )
        from receipt_chroma.embedding.metadata.line_metadata import (
            create_line_metadata,
            enrich_line_metadata_with_anchors,
        )
    except ImportError as exc:  # pragma: no cover
        logger.warning("Line embedding helpers unavailable: %s", exc)
        return None

    meaningful_lines = [
        line for line in receipt_lines if not getattr(line, "is_noise", False)
    ]
    if not meaningful_lines:
        logger.info("No lines to embed")
        return None

    formatted_inputs = [
        format_line_context_embedding_input(line, receipt_lines)
        for line in meaningful_lines
    ]
    embeddings = _embed_texts(openai_client, formatted_inputs)

    words_by_line: Dict[int, List[ReceiptWord]] = defaultdict(list)
    for word in receipt_words:
        words_by_line[int(word.line_id)].append(word)

    ids: List[str] = []
    metadatas: List[Dict] = []
    documents: List[str] = []

    for line, embedding in zip(meaningful_lines, embeddings):
        prev_line, next_line = get_line_neighbors(line, receipt_lines)
        avg_conf = _average_word_confidence(
            words_by_line.get(int(line.line_id), [])
        )
        metadata = create_line_metadata(
            line=line,
            prev_line=prev_line,
            next_line=next_line,
            merchant_name=merchant_name,
            avg_word_confidence=avg_conf,
            source="openai_embedding_realtime",
        )
        metadata = enrich_line_metadata_with_anchors(
            metadata, words_by_line.get(int(line.line_id), [])
        )

        vector_id = (
            f"IMAGE#{line.image_id}"
            f"#RECEIPT#{int(line.receipt_id):05d}"
            f"#LINE#{int(line.line_id):05d}"
        )

        ids.append(vector_id)
        metadatas.append(metadata)
        documents.append(line.text)

    return {
        "ids": ids,
        "embeddings": embeddings,
        "metadatas": metadatas,
        "documents": documents,
    }


def _build_word_vectors(
    receipt_words: List[ReceiptWord],
    merchant_name: Optional[str],
    openai_client,
    receipt_word_labels: Optional[List[ReceiptWordLabel]] = None,
) -> Optional[Dict[str, List]]:
    try:
        from receipt_chroma.embedding.formatting.word_format import (
            format_word_context_embedding_input,
            get_word_neighbors,
        )
        from receipt_chroma.embedding.metadata.word_metadata import (
            create_word_metadata,
            enrich_word_metadata_with_anchors,
            enrich_word_metadata_with_labels,
        )
    except ImportError as exc:  # pragma: no cover
        logger.warning("Word embedding helpers unavailable: %s", exc)
        return None

    meaningful_words = [
        word for word in receipt_words if not getattr(word, "is_noise", False)
    ]
    if not meaningful_words:
        logger.info("No words to embed")
        return None

    formatted_inputs = [
        format_word_context_embedding_input(word, receipt_words)
        for word in meaningful_words
    ]
    embeddings = _embed_texts(openai_client, formatted_inputs)

    ids: List[str] = []
    metadatas: List[Dict] = []
    documents: List[str] = []

    for word, embedding in zip(meaningful_words, embeddings):
        left_words, right_words = get_word_neighbors(word, receipt_words)
        left_word = (
            left_words[0]
            if isinstance(left_words, list) and len(left_words) > 0
            else (left_words if not isinstance(left_words, list) else None)
        )
        right_word = (
            right_words[0]
            if isinstance(right_words, list) and len(right_words) > 0
            else (right_words if not isinstance(right_words, list) else None)
        )

        metadata = create_word_metadata(
            word=word,
            left_word=left_word or "<EDGE>",
            right_word=right_word or "<EDGE>",
            merchant_name=merchant_name,
            label_status="unvalidated",
            source="openai_embedding_realtime",
        )
        metadata = enrich_word_metadata_with_anchors(metadata, word)

        # Enrich with labels if provided
        if receipt_word_labels:
            word_labels = [
                lbl
                for lbl in receipt_word_labels
                if (
                    lbl.image_id == word.image_id
                    and lbl.receipt_id == word.receipt_id
                    and lbl.line_id == word.line_id
                    and lbl.word_id == word.word_id
                )
            ]
            if word_labels:
                metadata = enrich_word_metadata_with_labels(
                    metadata, word_labels
                )

        vector_id = (
            f"IMAGE#{word.image_id}"
            f"#RECEIPT#{int(word.receipt_id):05d}"
            f"#LINE#{int(word.line_id):05d}"
            f"#WORD#{int(word.word_id):05d}"
        )

        ids.append(vector_id)
        metadatas.append(metadata)
        documents.append(word.text)

    return {
        "ids": ids,
        "embeddings": embeddings,
        "metadatas": metadatas,
        "documents": documents,
    }


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

        from receipt_chroma.data.chroma_client import ChromaClient
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

    openai_client = OpenAI()
    line_payload = _build_line_vectors(
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
        merchant_name=merchant_name,
        openai_client=openai_client,
    )
    word_payload = _build_word_vectors(
        receipt_words=receipt_words,
        merchant_name=merchant_name,
        openai_client=openai_client,
        receipt_word_labels=receipt_word_labels,
    )

    if not line_payload or not word_payload:
        logger.info("Embedding payloads missing; skipping delta upload")
        return None

    run_id = str(uuid.uuid4())
    delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
    delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

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

    # Close clients before uploading to ensure files are flushed
    line_client.close()
    word_client.close()

    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"

    # Use upload_bundled_delta_to_s3 to create tar.gz files that the compaction handler expects
    from receipt_label.utils.chroma_s3_helpers import (
        upload_bundled_delta_to_s3,
    )

    lines_result = upload_bundled_delta_to_s3(
        local_delta_dir=delta_lines_dir,
        bucket=chromadb_bucket,
        delta_prefix=lines_prefix,
    )
    if lines_result.get("status") != "uploaded":
        logger.error("Failed to upload lines delta: %s", lines_result)
        return None

    words_result = upload_bundled_delta_to_s3(
        local_delta_dir=delta_words_dir,
        bucket=chromadb_bucket,
        delta_prefix=words_prefix,
    )
    if words_result.get("status") != "uploaded":
        logger.error("Failed to upload words delta: %s", words_result)
        return None

    # Return the prefix (not the full key) as the delta_prefix
    lines_delta_key = lines_prefix
    words_delta_key = words_prefix

    compaction_run = CompactionRun(
        run_id=run_id,
        image_id=image_id,
        receipt_id=receipt_id,
        lines_delta_prefix=lines_delta_key,
        words_delta_prefix=words_delta_key,
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
