"""Handler for finding words that need embeddings."""

import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import boto3
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptWord

import utils.logging

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Find receipt words without embeddings and prepare batches.

    Args:
        event: Lambda event (unused in current implementation)
        context: Lambda context (unused)

    Returns:
        Dictionary containing batches ready for processing

    Raises:
        RuntimeError: If there's an error processing
    """
    logger.info("Starting find_unembedded_words handler")

    try:
        # Get S3 bucket from environment
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable not set")

        dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))

        words = _list_words_without_embeddings(dynamo_client)
        logger.info(
            "Found words without embeddings (noise words filtered)",
            count=len(words),
        )

        if not words:
            logger.info("No words need embeddings")
            return {"batches": []}

        batches = _chunk_into_word_embedding_batches(words)
        logger.info("Chunked into batches", count=len(batches))

        uploaded = _upload_serialized_words(
            _serialize_receipt_words(batches), bucket
        )
        logger.info("Uploaded files", count=len(uploaded))

        # Clean the output to match expected format
        cleaned = [
            {
                "s3_key": e["s3_key"],
                "s3_bucket": e["s3_bucket"],
                "image_id": e["image_id"],
                "receipt_id": e["receipt_id"],
            }
            for e in uploaded
        ]

        logger.info(
            "Successfully prepared batches for processing", count=len(cleaned)
        )

        return {
            "batches": cleaned,
            "total_words": len(words),
            "batch_count": len(cleaned),
        }

    except Exception as e:
        logger.error("Unexpected error finding unembedded words", error=str(e))
        raise RuntimeError(f"Internal error: {str(e)}") from e


def _list_words_without_embeddings(
    dynamo_client: DynamoClient, page_limit: int = 500
) -> List[ReceiptWord]:
    """Paginate Dynamo to find words with EmbeddingStatus.NONE."""
    words: List[ReceiptWord] = []
    last_key: Dict[str, Any] | None = None
    while True:
        batch, last_key = dynamo_client.list_receipt_words_by_embedding_status(
            EmbeddingStatus.NONE, limit=page_limit, last_evaluated_key=last_key
        )
        words.extend(batch)
        if not last_key:
            break
    return words


def _chunk_into_word_embedding_batches(
    words: List[ReceiptWord], batch_size: int = 100
) -> List[List[ReceiptWord]]:
    """Chunk ReceiptWords into batches."""
    batches: List[List[ReceiptWord]] = []
    current: List[ReceiptWord] = []
    for word in words:
        current.append(word)
        if len(current) >= batch_size:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def _serialize_receipt_words(
    batches: List[List[ReceiptWord]],
) -> List[dict]:
    """Serialize batches of ReceiptWord entities to NDJSON-ready dicts."""
    serialized: List[dict] = []
    for batch in batches:
        if not batch:
            continue
        image_id = batch[0].image_id
        receipt_id = batch[0].receipt_id
        ndjson_path = f"/tmp/words-{image_id}-{receipt_id}.ndjson"
        rows = []
        for word in batch:
            rows.append(
                {
                    "receipt_id": word.receipt_id,
                    "image_id": word.image_id,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "text": word.text,
                    "bounding_box": word.bounding_box,
                    "confidence": word.confidence,
                    "embedding_status": word.embedding_status,
                    "label": getattr(word, "label", None),
                }
            )
        from embedding_ingest import write_ndjson

        write_ndjson(Path(ndjson_path), rows)
        serialized.append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "ndjson_path": ndjson_path,
            }
        )
    return serialized


def _upload_serialized_words(
    serialized_words: List[dict],
    s3_bucket: str,
    prefix: str = "word_embeddings",
) -> List[dict]:
    """Upload serialized word NDJSON files to S3."""
    s3 = boto3.client("s3")
    for entry in serialized_words:
        key = f"{prefix}/{Path(entry['ndjson_path']).name}"
        s3.upload_file(entry["ndjson_path"], s3_bucket, key)
        entry["s3_key"] = key
        entry["s3_bucket"] = s3_bucket
    return serialized_words
