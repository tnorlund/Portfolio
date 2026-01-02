"""Receipt, Word, and Line deletion logic for ChromaDB compaction."""

from typing import Any, List, Optional

from receipt_chroma.compaction.models import ReceiptDeletionResult
from receipt_chroma.data.chroma_client import ChromaClient

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient


def _build_chromadb_id(
    image_id: str,
    receipt_id: int,
    line_id: Optional[int] = None,
    word_id: Optional[int] = None,
) -> str:
    """Build a ChromaDB ID from entity components.

    ChromaDB ID format:
    - Lines: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
    - Words: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
    """
    base = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
    if line_id is not None:
        base += f"#LINE#{line_id:05d}"
        if word_id is not None:
            base += f"#WORD#{word_id:05d}"
    return base


def apply_receipt_deletions(
    chroma_client: ChromaClient,
    receipt_messages: List[Any],
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Optional[Any] = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> List[ReceiptDeletionResult]:
    """Delete embeddings for receipts, words, or lines that were removed from DynamoDB.

    This function handles three entity types:
    - RECEIPT: Delete all embeddings for the receipt (tries DynamoDB lookup first)
    - RECEIPT_WORD: Delete the specific word embedding (direct ID)
    - RECEIPT_LINE: Delete the specific line embedding (direct ID)

    Args:
        chroma_client: Open ChromaDB client with snapshot loaded
        receipt_messages: List of StreamMessage objects for RECEIPT/WORD/LINE deletions
        collection: Target collection (LINES or WORDS)
        logger: Logger instance for observability
        metrics: Optional metrics collector
        dynamo_client: DynamoDB client for querying entity IDs (required for RECEIPT)

    Returns:
        List of ReceiptDeletionResult with counts and errors
    """
    results: List[ReceiptDeletionResult] = []

    if not receipt_messages:
        return results

    logger.info(
        "Processing entity deletions",
        collection=collection.value,
        message_count=len(receipt_messages),
    )

    # Get the ChromaDB collection
    chroma_collection = chroma_client.get_collection(collection.value)
    if not chroma_collection:
        logger.error(
            "Failed to get ChromaDB collection",
            collection=collection.value,
        )
        for msg in receipt_messages:
            entity_data = msg.entity_data
            results.append(
                ReceiptDeletionResult(
                    image_id=str(entity_data.get("image_id", "")),
                    receipt_id=int(entity_data.get("receipt_id", 0)),
                    deleted_count=0,
                    error="Failed to get ChromaDB collection",
                )
            )
        return results

    for msg in receipt_messages:
        entity_data = msg.entity_data
        entity_type = entity_data.get("entity_type", msg.entity_type)
        image_id = str(entity_data.get("image_id", ""))
        receipt_id = int(entity_data.get("receipt_id", 0))

        try:
            if entity_type == "RECEIPT_WORD":
                result = _delete_word_embedding(
                    chroma_collection=chroma_collection,
                    entity_data=entity_data,
                    collection=collection,
                    logger=logger,
                    metrics=metrics,
                )
                results.append(result)

            elif entity_type == "RECEIPT_LINE":
                result = _delete_line_embedding(
                    chroma_collection=chroma_collection,
                    entity_data=entity_data,
                    collection=collection,
                    logger=logger,
                    metrics=metrics,
                )
                results.append(result)

            elif entity_type == "RECEIPT":
                result = _delete_receipt_embeddings(
                    chroma_collection=chroma_collection,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    collection=collection,
                    logger=logger,
                    metrics=metrics,
                    dynamo_client=dynamo_client,
                )
                results.append(result)

            else:
                logger.warning(
                    "Unknown entity type for deletion",
                    entity_type=entity_type,
                    image_id=image_id,
                    receipt_id=receipt_id,
                )

        except Exception as exc:
            error_msg = str(exc)
            logger.error(
                "Failed to delete embeddings",
                entity_type=entity_type,
                image_id=image_id,
                receipt_id=receipt_id,
                collection=collection.value,
                error=error_msg,
            )
            if metrics:
                metrics.count(
                    "EmbeddingDeletionError",
                    1,
                    {"collection": collection.value, "entity_type": entity_type},
                )
            results.append(
                ReceiptDeletionResult(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    deleted_count=0,
                    error=error_msg,
                )
            )

    return results


def _delete_word_embedding(
    chroma_collection: Any,
    entity_data: dict,
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Optional[Any] = None,
) -> ReceiptDeletionResult:
    """Delete a single word embedding by direct ID lookup."""
    image_id = str(entity_data.get("image_id", ""))
    receipt_id = int(entity_data.get("receipt_id", 0))
    line_id = int(entity_data.get("line_id", 0))
    word_id = int(entity_data.get("word_id", 0))

    # Only process WORDS collection for word deletions
    if collection != ChromaDBCollection.WORDS:
        return ReceiptDeletionResult(
            image_id=image_id,
            receipt_id=receipt_id,
            deleted_count=0,
        )

    chromadb_id = _build_chromadb_id(image_id, receipt_id, line_id, word_id)

    try:
        chroma_collection.delete(ids=[chromadb_id])
        logger.info(
            "Deleted word embedding",
            chromadb_id=chromadb_id,
            collection=collection.value,
        )
        if metrics:
            metrics.count("WordEmbeddingDeleted", 1, {"collection": collection.value})
        return ReceiptDeletionResult(
            image_id=image_id,
            receipt_id=receipt_id,
            deleted_count=1,
        )
    except Exception as exc:
        # ChromaDB may not error if ID doesn't exist, but log just in case
        logger.warning(
            "Could not delete word embedding (may not exist)",
            chromadb_id=chromadb_id,
            error=str(exc),
        )
        return ReceiptDeletionResult(
            image_id=image_id,
            receipt_id=receipt_id,
            deleted_count=0,
        )


def _delete_line_embedding(
    chroma_collection: Any,
    entity_data: dict,
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Optional[Any] = None,
) -> ReceiptDeletionResult:
    """Delete a single line embedding by direct ID lookup."""
    image_id = str(entity_data.get("image_id", ""))
    receipt_id = int(entity_data.get("receipt_id", 0))
    line_id = int(entity_data.get("line_id", 0))

    # Only process LINES collection for line deletions
    if collection != ChromaDBCollection.LINES:
        return ReceiptDeletionResult(
            image_id=image_id,
            receipt_id=receipt_id,
            deleted_count=0,
        )

    chromadb_id = _build_chromadb_id(image_id, receipt_id, line_id)

    try:
        chroma_collection.delete(ids=[chromadb_id])
        logger.info(
            "Deleted line embedding",
            chromadb_id=chromadb_id,
            collection=collection.value,
        )
        if metrics:
            metrics.count("LineEmbeddingDeleted", 1, {"collection": collection.value})
        return ReceiptDeletionResult(
            image_id=image_id,
            receipt_id=receipt_id,
            deleted_count=1,
        )
    except Exception as exc:
        # ChromaDB may not error if ID doesn't exist, but log just in case
        logger.warning(
            "Could not delete line embedding (may not exist)",
            chromadb_id=chromadb_id,
            error=str(exc),
        )
        return ReceiptDeletionResult(
            image_id=image_id,
            receipt_id=receipt_id,
            deleted_count=0,
        )


def _delete_receipt_embeddings(
    chroma_collection: Any,
    image_id: str,
    receipt_id: int,
    collection: ChromaDBCollection,
    logger: Any,
    metrics: Optional[Any] = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> ReceiptDeletionResult:
    """Delete all embeddings for a receipt.

    Strategy:
    1. Try to get words/lines from DynamoDB to build exact IDs
    2. If DynamoDB entities are gone (already deleted), fall back to prefix scan
    """
    ids_to_delete: List[str] = []

    # Try DynamoDB lookup first (entities may still exist)
    if dynamo_client:
        if collection == ChromaDBCollection.WORDS:
            try:
                words = dynamo_client.list_receipt_words_from_receipt(
                    image_id, receipt_id
                )
                ids_to_delete = [
                    _build_chromadb_id(
                        w.image_id, w.receipt_id, w.line_id, w.word_id
                    )
                    for w in words
                ]
                if ids_to_delete:
                    logger.info(
                        "Found words via DynamoDB lookup",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        word_count=len(ids_to_delete),
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to query words from DynamoDB, will use prefix scan",
                    image_id=image_id,
                    receipt_id=receipt_id,
                    error=str(exc),
                )

        elif collection == ChromaDBCollection.LINES:
            try:
                lines = dynamo_client.list_receipt_lines_from_receipt(
                    image_id, receipt_id
                )
                ids_to_delete = [
                    _build_chromadb_id(ln.image_id, ln.receipt_id, ln.line_id)
                    for ln in lines
                ]
                if ids_to_delete:
                    logger.info(
                        "Found lines via DynamoDB lookup",
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_count=len(ids_to_delete),
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to query lines from DynamoDB, will use prefix scan",
                    image_id=image_id,
                    receipt_id=receipt_id,
                    error=str(exc),
                )

    # Fallback: use metadata-based query if DynamoDB lookup returned nothing
    # This is more efficient than a full collection scan as ChromaDB filters server-side
    if not ids_to_delete:
        logger.info(
            "Using metadata-based query for receipt deletion",
            image_id=image_id,
            receipt_id=receipt_id,
            collection=collection.value,
        )
        if metrics:
            metrics.count(
                "ReceiptDeletionMetadataQuery",
                1,
                {"collection": collection.value},
            )

        try:
            # Query ChromaDB using metadata fields (stored during embedding creation)
            results = chroma_collection.get(
                where={
                    "$and": [
                        {"image_id": {"$eq": image_id}},
                        {"receipt_id": {"$eq": receipt_id}},
                    ]
                },
                include=[],  # Only get IDs, not embeddings or metadata
            )
            ids_to_delete = results.get("ids", [])
        except Exception as exc:
            logger.error(
                "Metadata query failed",
                image_id=image_id,
                receipt_id=receipt_id,
                error=str(exc),
            )
            return ReceiptDeletionResult(
                image_id=image_id,
                receipt_id=receipt_id,
                deleted_count=0,
                error=f"Metadata query failed: {exc}",
            )

    if not ids_to_delete:
        logger.info(
            "No embeddings found for receipt",
            image_id=image_id,
            receipt_id=receipt_id,
            collection=collection.value,
        )
        return ReceiptDeletionResult(
            image_id=image_id,
            receipt_id=receipt_id,
            deleted_count=0,
        )

    # Delete the embeddings
    chroma_collection.delete(ids=ids_to_delete)
    deleted_count = len(ids_to_delete)

    logger.info(
        "Deleted receipt embeddings",
        image_id=image_id,
        receipt_id=receipt_id,
        collection=collection.value,
        deleted_count=deleted_count,
    )

    if metrics:
        metrics.count(
            "ReceiptEmbeddingsDeleted",
            deleted_count,
            {"collection": collection.value},
        )

    return ReceiptDeletionResult(
        image_id=image_id,
        receipt_id=receipt_id,
        deleted_count=deleted_count,
    )


__all__ = ["apply_receipt_deletions"]
