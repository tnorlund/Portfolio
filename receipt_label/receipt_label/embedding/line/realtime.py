"""Real-time line embedding that matches batch embedding structure."""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pinecone.grpc import Vector
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptLine

from receipt_label.client_manager import get_client_manager

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
    target_y = target_line.calculate_centroid()["y"]

    prev_line = "<EDGE>"
    next_line = "<EDGE>"

    # Sort lines by y-coordinate to find neighbors
    sorted_lines = sorted(all_lines, key=lambda l: l.calculate_centroid()["y"])

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
    centroid = line.calculate_centroid()

    # Calculate average word confidence if words are available
    avg_word_confidence = line.confidence  # Default to line confidence
    word_count = len(line.text.split())  # Approximate word count

    metadata = {
        "image_id": line.image_id,
        "receipt_id": str(line.receipt_id),  # Ensure string for consistency
        "line_id": line.line_id,
        "source": "openai_line_embedding_realtime",  # Different from batch
        "text": line.text,
        "x": centroid["x"],
        "y": centroid["y"],
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
) -> Dict[str, List[float]]:
    """
    Embed lines in real-time using batch-compatible formatting.

    Args:
        lines: List of receipt lines to embed
        merchant_name: Optional merchant name for context
        model: OpenAI model to use for embeddings

    Returns:
        Dictionary mapping line text to embedding vectors
    """
    client_manager = get_client_manager()
    openai_client = client_manager.openai

    if not lines:
        logger.info("No lines to embed")
        return {}

    # Format texts using batch embedding structure
    formatted_texts = []
    for line in lines:
        formatted_text = _format_line_context_embedding_input(line, lines)
        formatted_texts.append(formatted_text)

    try:
        # Get embeddings from OpenAI
        response = openai_client.embeddings.create(
            model=model,
            input=formatted_texts,
        )

        # Map embeddings back to lines
        embeddings = {}
        for i, line in enumerate(lines):
            embeddings[line.text] = response.data[i].embedding

        logger.info(
            f"Successfully embedded {len(embeddings)} lines with vertical context"
        )
        return embeddings

    except Exception as e:
        logger.error(f"Error embedding lines: {str(e)}")
        raise


def embed_receipt_lines_realtime(
    receipt_id: str,
    merchant_name: Optional[str] = None,
) -> List[Tuple[ReceiptLine, List[float]]]:
    """
    Embed all lines from a receipt and store to Pinecone using batch-compatible structure.

    Args:
        receipt_id: ID of the receipt to process
        merchant_name: Optional merchant name for context

    Returns:
        List of (line, embedding) tuples
    """
    client_manager = get_client_manager()
    dynamo_client = client_manager.dynamo
    pinecone_client = client_manager.pinecone

    # Get receipt lines from DynamoDB
    lines = dynamo_client.list_receipt_lines_by_receipt(receipt_id)

    if not lines:
        logger.warning(f"No lines found for receipt {receipt_id}")
        return []

    # Get embeddings with vertical context
    embeddings = embed_lines_realtime(lines, merchant_name)

    # Prepare vectors for Pinecone using batch structure
    vectors = []
    line_embedding_pairs = []

    for line in lines:
        if line.text in embeddings:
            # Extract vertical context for metadata
            formatted_input = _format_line_context_embedding_input(line, lines)

            # Parse prev/next lines from formatted input
            import re

            context_match = re.search(
                r"<CONTEXT>([^<]*)</CONTEXT>", formatted_input
            )
            if context_match:
                context_content = context_match.group(1).strip()
                if not context_content:
                    prev_line = next_line = "<EDGE>"
                else:
                    # Split into exactly 2 parts to handle spaces in lines correctly
                    context_parts = context_content.split(' ', 1)
                    prev_line = context_parts[0] if len(context_parts) > 0 else "<EDGE>"
                    next_line = context_parts[1] if len(context_parts) > 1 else "<EDGE>"
            else:
                prev_line = next_line = "<EDGE>"

            # Create vector ID matching batch format
            # Ensure IDs are integers for proper formatting
            receipt_id_int = int(line.receipt_id) if isinstance(line.receipt_id, str) else line.receipt_id
            line_id_int = int(line.line_id) if isinstance(line.line_id, str) else line.line_id
            
            vector_id = f"IMAGE#{line.image_id}#RECEIPT#{receipt_id_int:05d}#LINE#{line_id_int:05d}"

            # Create metadata matching batch structure
            metadata = _create_line_metadata(
                line=line,
                prev_line=prev_line,
                next_line=next_line,
                merchant_name=merchant_name,
                section="UNLABELED",  # Default section
            )

            # Create Pinecone vector
            vectors.append(
                Vector(
                    id=vector_id,
                    values=embeddings[line.text],
                    metadata=metadata,
                )
            )

            line_embedding_pairs.append((line, embeddings[line.text]))

    # Store to Pinecone using correct namespace
    if vectors:
        try:
            index = pinecone_client.Index("receipt-embeddings")
            index.upsert(
                vectors=vectors, namespace="lines"
            )  # Match batch namespace

            logger.info(
                f"Stored {len(vectors)} line embeddings to Pinecone for receipt {receipt_id}"
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
                    f"Embeddings stored to Pinecone but DynamoDB update failed: {batch_error}"
                )

        except Exception as e:
            logger.error(f"Error storing to Pinecone: {str(e)}")
            raise

    return line_embedding_pairs
