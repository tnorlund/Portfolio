"""
Embedding utilities for receipt combination.

This module uses the receipt_chroma package plus the OpenAI SDK directly
to generate realtime embeddings and produce ChromaDB deltas. It creates
tar.gz files using upload_bundled_delta_to_s3 (defined locally) that the
compaction handler expects.
"""

import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import boto3

from receipt_dynamo.entities import (
    CompactionRun,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
)

try:
    from openai import OpenAI

    from receipt_chroma.data.chroma_client import ChromaClient
    from receipt_chroma.embedding.formatting.line_format import (
        format_line_context_embedding_input,
        get_line_neighbors,
    )
    from receipt_chroma.embedding.formatting.word_format import (
        format_word_context_embedding_input,
        get_word_neighbors,
    )
    from receipt_chroma.embedding.metadata.line_metadata import (
        create_line_metadata,
        enrich_line_metadata_with_anchors,
    )
    from receipt_chroma.embedding.metadata.word_metadata import (
        create_word_metadata,
        enrich_word_metadata_with_anchors,
    )

    EMBEDDING_AVAILABLE = True
except ImportError:  # pragma: no cover - handled gracefully at runtime
    EMBEDDING_AVAILABLE = False


logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"


def _embed_texts(
    client: OpenAI, inputs: Sequence[str], model: str = EMBEDDING_MODEL
) -> List[List[float]]:
    """Embed a list of input strings using the OpenAI embeddings API."""
    response = client.embeddings.create(model=model, input=list(inputs))
    return [item.embedding for item in response.data]


def _average_word_confidence(words: List[ReceiptWord]) -> Optional[float]:
    if not words:
        return None
    return sum(w.confidence for w in words) / len(words)


def _build_line_vectors(
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    merchant_name: Optional[str],
    client: OpenAI,
) -> Optional[Dict[str, List]]:
    """Create line vector payloads for ChromaDB upsert."""
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
    embeddings = _embed_texts(client, formatted_inputs)

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
    client: OpenAI,
) -> Optional[Dict[str, List]]:
    """Create word vector payloads for ChromaDB upsert."""
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
    embeddings = _embed_texts(client, formatted_inputs)

    ids: List[str] = []
    metadatas: List[Dict] = []
    documents: List[str] = []

    for word, embedding in zip(meaningful_words, embeddings):
        try:
            left_words, right_words = get_word_neighbors(word, receipt_words)
            # Handle empty lists (no neighbors found) - use "<EDGE>" as fallback
            left_word = (
                left_words[0]
                if (isinstance(left_words, list) and left_words)
                else "<EDGE>"
            )
            right_word = (
                right_words[0]
                if (isinstance(right_words, list) and right_words)
                else "<EDGE>"
            )
        except (
            IndexError,
            KeyError,
            AttributeError,
            TypeError,
            StopIteration,
        ) as e:
            # Fallback if get_word_neighbors fails (e.g., missing geometry attributes,
            # word not found in list, or calculation errors)
            # Note: words don't need to be in DynamoDB - they're passed as in-memory objects
            logger.warning(
                "Failed to get neighbors for word %s/%s/%s/%s: %s. Using <EDGE> fallback.",
                word.image_id,
                word.receipt_id,
                word.line_id,
                word.word_id,
                e,
            )
            left_word = "<EDGE>"
            right_word = "<EDGE>"

        metadata = create_word_metadata(
            word=word,
            left_word=left_word or "<EDGE>",
            right_word=right_word or "<EDGE>",
            merchant_name=merchant_name,
            label_status="unvalidated",
            source="openai_embedding_realtime",
        )
        metadata = enrich_word_metadata_with_anchors(metadata, word)

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
    if not EMBEDDING_AVAILABLE:
        logger.info(
            "Embedding dependencies not available; skipping embeddings"
        )
        return None

    if not os.environ.get("OPENAI_API_KEY"):
        logger.info("OPENAI_API_KEY not set; skipping embeddings")
        return None

    openai_client = OpenAI()
    merchant_name = (
        receipt_metadata.merchant_name if receipt_metadata else None
    )

    line_payload = _build_line_vectors(
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
        merchant_name=merchant_name,
        client=openai_client,
    )
    word_payload = _build_word_vectors(
        receipt_words=receipt_words,
        merchant_name=merchant_name,
        client=openai_client,
    )

    if not line_payload or not word_payload:
        logger.info(
            "Embeddings unavailable for lines or words; skipping delta upload"
        )
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

    if line_payload:
        line_client.upsert_vectors(collection_name="lines", **line_payload)
    if word_payload:
        word_client.upsert_vectors(collection_name="words", **word_payload)

    # Close clients before uploading to ensure files are flushed
    line_client.close()
    word_client.close()

    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"

    # Upload bundled delta as tar.gz files that the compaction handler expects
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

    return CompactionRun(
        run_id=run_id,
        image_id=image_id,
        receipt_id=new_receipt_id,
        lines_delta_prefix=lines_delta_key,
        words_delta_prefix=words_delta_key,
    )


def bundle_directory_to_tar_gz(src_dir: str, out_tar_path: str) -> int:
    """Create a gzip-compressed tarball from a directory. Returns size in bytes."""
    src = Path(src_dir)
    if not src.exists() or not src.is_dir():
        raise ValueError(
            f"Source directory does not exist or is not a dir: {src_dir}"
        )
    with tarfile.open(out_tar_path, "w:gz") as tar:
        tar.add(src_dir, arcname=".")
    return os.path.getsize(out_tar_path)


def upload_bundled_delta_to_s3(
    local_delta_dir: str,
    bucket: str,
    delta_prefix: str,
    metadata: Optional[Dict[str, Any]] = None,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    """Tar/gzip a delta directory and upload as a single object delta.tar.gz under delta_prefix."""
    logger.info(
        "Bundling and uploading delta tarball: dir=%s bucket=%s prefix=%s",
        local_delta_dir,
        bucket,
        delta_prefix,
    )
    try:
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)

        # Create tar.gz in temp
        tmp_dir = tempfile.mkdtemp()
        tar_path = os.path.join(tmp_dir, "delta.tar.gz")
        size_bytes = bundle_directory_to_tar_gz(local_delta_dir, tar_path)

        s3_key = f"{delta_prefix.rstrip('/')}/delta.tar.gz"

        # Prepare metadata
        s3_metadata = {"delta_key": delta_prefix}
        if metadata:
            s3_metadata.update({k: str(v) for k, v in metadata.items()})

        extra_args = {
            "Metadata": s3_metadata,
            "ContentType": "application/gzip",
            "ContentEncoding": "gzip",
        }

        s3.upload_file(tar_path, bucket, s3_key, ExtraArgs=extra_args)

        # Cleanup temp
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

        return {
            "status": "uploaded",
            "delta_key": delta_prefix,
            "object_key": s3_key,
            "tar_size_bytes": size_bytes,
        }
    except Exception as e:  # noqa: BLE001
        logger.error("Error uploading bundled delta to S3: %s", e)
        return {"status": "failed", "error": str(e)}
