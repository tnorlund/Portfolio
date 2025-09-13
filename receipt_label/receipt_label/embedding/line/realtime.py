"""Real-time line embedding that matches batch embedding structure."""

import logging
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptLine

from receipt_label.utils import get_client_manager
from receipt_label.merchant_validation.normalize import (
    normalize_phone,
    build_full_address_from_words,
    build_full_address_from_lines,
)

logger = logging.getLogger(__name__)


def _format_line_context_embedding_input(
    target_line: ReceiptLine, all_lines: List[ReceiptLine]
) -> str:
    """
    Format line with vertical context matching batch embedding structure.

    Replicates the format from embedding/line/submit.py:
    <TARGET>line text</TARGET> <POS>position</POS> <CONTEXT>prev_line next_line</CONTEXT>
    """
    # Calculate position using same logic as batch system
    position = _get_line_position(target_line)

    # Find previous and next lines by y-coordinate
    _, target_y = target_line.calculate_centroid()

    prev_line = "<EDGE>"
    next_line = "<EDGE>"

    # Sort lines by y-coordinate to find neighbors
    sorted_lines = sorted(all_lines, key=lambda l: l.calculate_centroid()[1])

    target_index = None
    for i, line in enumerate(sorted_lines):
        if line.line_id == target_line.line_id:
            target_index = i
            break

    if target_index is not None:
        if target_index > 0:
            prev_line = sorted_lines[target_index - 1].text
        if target_index < len(sorted_lines) - 1:
            next_line = sorted_lines[target_index + 1].text

    return f"<TARGET>{target_line.text}</TARGET> <POS>{position}</POS> <CONTEXT>{prev_line} {next_line}</CONTEXT>"


def _get_line_neighbors(
    target_line: ReceiptLine, all_lines: List[ReceiptLine]
) -> Tuple[str, str]:
    """
    Get the previous and next lines for the target line.

    This is the same logic as _format_line_context_embedding_input but
    returns the neighbors directly instead of formatting them.

    Returns:
        Tuple of (prev_line, next_line)
    """
    # Find previous and next lines by y-coordinate
    _, target_y = target_line.calculate_centroid()

    prev_line = "<EDGE>"
    next_line = "<EDGE>"

    # Sort lines by y-coordinate to find neighbors
    sorted_lines = sorted(all_lines, key=lambda l: l.calculate_centroid()[1])

    target_index = None
    for i, line in enumerate(sorted_lines):
        if line.line_id == target_line.line_id:
            target_index = i
            break

    if target_index is not None:
        if target_index > 0:
            prev_line = sorted_lines[target_index - 1].text
        if target_index < len(sorted_lines) - 1:
            next_line = sorted_lines[target_index + 1].text

    return prev_line, next_line


def _get_line_position(line: ReceiptLine) -> str:
    """
    Get line position in vertical zones using normalized coordinates.

    Uses normalized coordinates (0.0-1.0) from calculate_centroid()
    matching the coordinate system used throughout the system.
    """
    # Calculate centroid coordinates (normalized 0.0–1.0)
    x_center, y_center = line.calculate_centroid()

    # Vertical position (3-zone system for lines)
    # y=0 at bottom in receipt coordinate system
    if y_center > 0.66:
        return "top"
    elif y_center > 0.33:
        return "middle"
    else:
        return "bottom"


def _create_line_metadata(
    line: ReceiptLine,
    prev_line: str,
    next_line: str,
    merchant_name: Optional[str] = None,
    section: str = "UNLABELED",
) -> dict:
    """
    Create comprehensive metadata matching batch embedding structure.

    Replicates metadata from embedding/line/poll.py
    """
    x_center, y_center = line.calculate_centroid()

    # Calculate average word confidence if words are available
    avg_word_confidence = line.confidence  # Default to line confidence
    word_count = len(line.text.split())  # Approximate word count

    metadata = {
        "image_id": line.image_id,
        "receipt_id": str(line.receipt_id),  # Ensure string for consistency
        "line_id": line.line_id,
        "source": "openai_line_embedding_realtime",  # Different from batch
        "text": line.text,
        "x": x_center,
        "y": y_center,
        "width": line.bounding_box["width"],
        "height": line.bounding_box["height"],
        "confidence": line.confidence,
        "avg_word_confidence": avg_word_confidence,
        "word_count": word_count,
        "prev_line": prev_line,
        "next_line": next_line,
        "angle_degrees": line.angle_degrees,
        "section": section,
        "embedding_type": "line",  # For filtering
    }

    # Add merchant info if available
    if merchant_name:
        metadata["merchant_name"] = merchant_name

    return metadata


def embed_lines_realtime(
    lines: List[ReceiptLine],
    merchant_name: Optional[str] = None,
    model: str = "text-embedding-3-small",
) -> List[Tuple[ReceiptLine, List[float]]]:
    """
    Embed lines in real-time using batch-compatible formatting.

    Args:
        lines: List of receipt lines to embed
        merchant_name: Optional merchant name for context
        model: OpenAI model to use for embeddings

    Returns:
        List of (line, embedding) tuples
    """
    client_manager = get_client_manager()
    openai_client = client_manager.openai

    if not lines:
        logger.info("No lines to embed")
        return []

    # Filter out noise lines to match batch behavior
    meaningful_lines = [l for l in lines if not getattr(l, "is_noise", False)]
    if not meaningful_lines:
        logger.info("No meaningful lines to embed (all filtered as noise)")
        return []

    # Build list of (line, plain_text) pairs (no context) to match batch input
    line_text_pairs = []
    for line in meaningful_lines:
        line_text_pairs.append((line, line.text))

    try:
        # Get embeddings from OpenAI using plain line.text inputs
        response = openai_client.embeddings.create(
            model=model,
            input=[pair[1] for pair in line_text_pairs],
        )

        # Build list of (line, embedding) tuples
        line_embeddings = []
        for i, (line, _) in enumerate(line_text_pairs):
            line_embeddings.append((line, response.data[i].embedding))

        logger.info(
            f"Successfully embedded {len(line_embeddings)} lines with vertical context"
        )
        return line_embeddings

    except Exception as e:
        logger.error(f"Error embedding lines: {str(e)}")
        raise


def embed_receipt_lines_realtime(
    receipt_id: str,
    merchant_name: Optional[str] = None,
) -> List[Tuple[ReceiptLine, List[float]]]:
    """
    Embed all lines from a receipt and store to ChromaDB using batch-compatible structure.

    Args:
        receipt_id: ID of the receipt to process
        merchant_name: Optional merchant name for context

    Returns:
        List of (line, embedding) tuples
    """
    client_manager = get_client_manager()
    dynamo_client = client_manager.dynamo
    chroma_client = client_manager.chroma

    # Get receipt lines from DynamoDB
    lines = dynamo_client.list_receipt_lines_by_receipt(receipt_id)

    if not lines:
        logger.warning(f"No lines found for receipt {receipt_id}")
        return []

    # Get embeddings with vertical context
    line_embeddings = embed_lines_realtime(lines, merchant_name)

    # Prepare data for ChromaDB
    ids = []
    embeddings = []
    metadatas = []
    documents = []
    line_embedding_pairs = []

    # Compute receipt-level normalized fields once
    try:
        words_for_receipt = dynamo_client.list_receipt_words_by_receipt(
            receipt_id
        )
    except Exception:  # Safe fallback if unavailable
        words_for_receipt = []

    normalized_phone_10 = ""
    for w in words_for_receipt:
        try:
            ext = getattr(w, "extracted_data", None) or {}
            if ext.get("type") == "phone":
                candidate = ext.get("value") or getattr(w, "text", "")
                ph = normalize_phone(candidate)
                if ph:
                    normalized_phone_10 = ph
                    break
        except Exception:
            continue

    normalized_full_address = (
        build_full_address_from_words(words_for_receipt)
        if words_for_receipt
        else ""
    )
    if not normalized_full_address:
        normalized_full_address = build_full_address_from_lines(lines)

    # Create a mapping for quick lookup of embeddings by line
    embedding_map = {line.line_id: emb for line, emb in line_embeddings}

    for line in lines:
        if line.line_id in embedding_map:
            # Get prev and next lines directly using the same logic as formatting
            # This avoids parsing issues with spaces in lines
            prev_line, next_line = _get_line_neighbors(line, lines)

            # Create vector ID matching batch format
            # IDs are always integers per entity definitions
            vector_id = f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#{line.line_id:05d}"

            # Create metadata matching batch structure
            metadata = _create_line_metadata(
                line=line,
                prev_line=prev_line,
                next_line=next_line,
                merchant_name=merchant_name,
                section="UNLABELED",  # Default section
            )

            # Attach normalized receipt-level fields
            metadata["normalized_phone_10"] = normalized_phone_10
            metadata["normalized_full_address"] = normalized_full_address

            # Prepare data for ChromaDB
            ids.append(vector_id)
            embeddings.append(embedding_map[line.line_id])
            metadatas.append(metadata)
            documents.append(line.text)  # Store line text as document

            line_embedding_pairs.append((line, embedding_map[line.line_id]))

    # Store to ChromaDB using correct collection
    if ids:
        try:
            chroma_client.upsert_vectors(
                collection_name="lines",
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

            logger.info(
                f"Stored {len(ids)} line embeddings to ChromaDB for receipt {receipt_id}"
            )

            # Update embedding status in DynamoDB
            try:
                updated_lines = []
                current_time = datetime.utcnow()

                for line, _ in line_embedding_pairs:
                    line.embedding_status = EmbeddingStatus.SUCCESS
                    line.embedded_at = current_time
                    updated_lines.append(line)

                # Update lines individually with error handling
                failed_updates = []
                for line in updated_lines:
                    try:
                        dynamo_client.put_receipt_line(line)
                    except Exception as update_error:
                        failed_updates.append((line, update_error))
                        logger.error(
                            f"Failed to update embedding status for line {line.line_id}: {update_error}"
                        )

                if failed_updates:
                    logger.warning(
                        f"Failed to update {len(failed_updates)} out of {len(updated_lines)} lines"
                    )

            except Exception as batch_error:
                logger.error(
                    f"Error during DynamoDB batch update: {batch_error}"
                )
                raise RuntimeError(
                    f"Embeddings stored to ChromaDB but DynamoDB update failed: {batch_error}"
                )

        except Exception as e:
            logger.error(f"Error storing to ChromaDB: {str(e)}")
            raise

    return line_embedding_pairs
