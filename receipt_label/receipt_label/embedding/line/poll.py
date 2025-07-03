"""
poll_line_batch.py

This module handles the polling, result retrieval, and ingestion pipeline for
line embedding batch jobs submitted to OpenAI's Batch API.

It is designed to be used in conjunction with a Step Function that monitors
the status of submitted batches and processes them once complete.

Functions in this module perform the following tasks:
- List all line embedding batches with status "PENDING" from DynamoDB
- Poll OpenAI's Batch API to determine if a batch job is complete
- Download the NDJSON-formatted embedding results once a batch completes
- Parse and upsert the embeddings to Pinecone using structured metadata
- Write embedding results to a DynamoDB table for tracking
- Mark completed batches in DynamoDB to prevent reprocessing

This supports scalable, event-driven processing of line embedding jobs for
receipt section classification.
"""

import json
import re
from typing import List

from receipt_dynamo.constants import BatchType, EmbeddingStatus
from receipt_dynamo.entities import (
    BatchSummary,
    EmbeddingBatchResult,
    ReceiptSection,
)

from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


def _parse_prev_next_from_formatted(fmt: str) -> tuple[str, str]:
    """
    Given a string like
      "<TARGET>LINE TEXT</TARGET> <POS>…</POS> "
      "<CONTEXT>PREV_LINE NEXT_LINE</CONTEXT>"
    return ("PREV_LINE", "NEXT_LINE").
    """
    m = re.search(r"<CONTEXT>(.*?)</CONTEXT>", fmt)
    if not m:
        raise ValueError(f"No <CONTEXT>…</CONTEXT> in {fmt!r}")
    cont = m.group(1).strip()
    # Assuming exactly two tokens separated by whitespace
    parts = cont.split(maxsplit=1)
    prev = parts[0] if parts else "<EDGE>"
    next_line = parts[1] if len(parts) > 1 else "<EDGE>"
    return prev, next_line


def _parse_metadata_from_line_id(custom_id: str) -> dict:
    """Parse metadata from a line ID in the format IMAGE#uuid#RECEIPT#00001#LINE#00001"""
    parts = custom_id.split("#")
    return {
        "image_id": parts[1],
        "receipt_id": int(parts[3]),
        "line_id": int(parts[5]),
        "source": "openai_line_embedding_batch",
    }


def list_pending_line_embedding_batches(
    client_manager: ClientManager = None,
) -> List[BatchSummary]:
    """
    List line embedding batches that are pending processing.
    Returns a list of pending batch identifiers.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    summaries, lek = client_manager.dynamo.get_batch_summaries_by_status(
        status="PENDING",
        batch_type=BatchType.LINE_EMBEDDING,
        limit=25,
        lastEvaluatedKey=None,
    )
    while lek:
        next_summaries, lek = (
            client_manager.dynamo.get_batch_summaries_by_status(
                status="PENDING",
                batch_type=BatchType.LINE_EMBEDDING,
                limit=25,
                lastEvaluatedKey=lek,
            )
        )
        summaries.extend(next_summaries)
    return summaries


def get_openai_batch_status(
    openai_batch_id: str, client_manager: ClientManager = None
) -> str:
    """Retrieve the status of an OpenAI embedding batch job."""
    if client_manager is None:
        client_manager = get_client_manager()
    return client_manager.openai.batches.retrieve(openai_batch_id).status


def download_openai_batch_result(
    openai_batch_id: str, client_manager: ClientManager = None
) -> List[dict]:
    """
    Download and parse the results of an OpenAI embedding batch job.
    Returns a list of embedding result objects with `custom_id` and
    `embedding`.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    batch = client_manager.openai.batches.retrieve(openai_batch_id)
    output_file_id = batch.output_file_id
    response = client_manager.openai.files.content(output_file_id)

    # If the content is raw bytes, decode it:
    if hasattr(response, "read"):
        lines = response.read().decode("utf-8").splitlines()
    elif isinstance(response, bytes):
        lines = response.decode("utf-8").splitlines()
    elif isinstance(response, str):
        lines = response.splitlines()
    else:
        raise ValueError("Unexpected OpenAI file content type")

    results: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        embedding = (
            record.get("response", {})
            .get("body", {})
            .get("data", [{}])[0]
            .get("embedding")
        )
        results.append(
            {
                "custom_id": record.get("custom_id"),
                "embedding": embedding,
            }
        )
    return results


def get_receipt_descriptions(
    results: List[dict],
    client_manager: ClientManager = None,
) -> dict[str, dict[int, dict]]:
    """
    Get the receipt descriptions from the embedding results, grouped by image
    and receipt.

    Returns:
        A dict mapping each image_id (str) to a dict that maps each
        receipt_id (int) to a dict containing:
            - receipt
            - lines
            - words
            - letters
            - labels
            - metadata
            - sections
    """
    if client_manager is None:
        client_manager = get_client_manager()
    descriptions: dict[str, dict[int, dict]] = {}
    for receipt_id, image_id in _get_unique_receipt_and_image_ids(results):
        receipt, lines, words, letters, labels = (
            client_manager.dynamo.get_receipt_details(
                image_id=image_id,
                receipt_id=receipt_id,
            )
        )
        receipt_metadata = client_manager.dynamo.get_receipt_metadata(
            image_id=image_id,
            receipt_id=receipt_id,
        )
        receipt_sections = (
            client_manager.dynamo.get_receipt_sections_from_receipt(
                image_id=image_id,
                receipt_id=receipt_id,
            )
        )
        descriptions.setdefault(image_id, {})[receipt_id] = {
            "receipt": receipt,
            "lines": lines,
            "words": words,
            "letters": letters,
            "labels": labels,
            "metadata": receipt_metadata,
            "sections": receipt_sections,
        }
    return descriptions


def _get_unique_receipt_and_image_ids(
    results: List[dict],
) -> List[tuple[int, str]]:
    """
    Get the unique receipt ids and image ids from the embedding results.

    Returns a list of tuples, each containing a receipt id and image id.
    """
    return list(
        set(
            (int(r["custom_id"].split("#")[3]), r["custom_id"].split("#")[1])
            for r in results
        )
    )


def _get_section_by_line_id(
    sections: list[ReceiptSection], line_id: int
) -> str | None:
    """
    Get the section for a given line id.
    """
    return next(
        (s.section_type for s in sections if line_id in s.line_ids), None
    )


def upsert_line_embeddings_to_pinecone(
    results: List[dict],
    descriptions: dict[str, dict[int, dict]],
    client_manager: ClientManager = None,
):
    """
    Upsert line embedding results to Pinecone with rich metadata.
    """
    vectors = []

    for result in results:
        # Parse metadata fields
        meta = _parse_metadata_from_line_id(result["custom_id"])
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        line_id = meta["line_id"]
        source = meta["source"]

        # From the descriptions, get the receipt details
        receipt_details = descriptions[image_id][receipt_id]
        _ = receipt_details["receipt"]  # receipt
        lines = receipt_details["lines"]
        words = receipt_details["words"]
        metadata = receipt_details["metadata"]
        sections = receipt_details["sections"]
        section = _get_section_by_line_id(sections, line_id)
        # Find the target line
        target_line = next((l for l in lines if l.line_id == line_id), None)

        if target_line is None:
            raise ValueError(
                f"No ReceiptLine found for image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id}"
            )

        # Calculate line metrics
        line_words = [w for w in words if w.line_id == line_id]
        avg_confidence = (
            sum(w.confidence for w in line_words) / len(line_words)
            if line_words
            else target_line.confidence
        )
        word_count = len(line_words)

        # Get line centroid and dimensions
        x_center, y_center = target_line.calculate_centroid()
        width = target_line.bounding_box["width"]
        height = target_line.bounding_box["height"]

        # Format the line context to extract prev/next lines
        # Import locally to avoid circular import
        from receipt_label.submit_line_embedding_batch.submit_line_batch import (  # pylint: disable=import-outside-toplevel,line-too-long  # noqa: E501
            _format_line_context_embedding_input,
        )

        embedding_input = _format_line_context_embedding_input(
            target_line, lines
        )
        prev_line, next_line = _parse_prev_next_from_formatted(embedding_input)

        # Merchant name handling - same as word embeddings
        if (
            hasattr(metadata, "canonical_merchant_name")
            and metadata.canonical_merchant_name
        ):
            merchant_name = metadata.canonical_merchant_name
        else:
            merchant_name = metadata.merchant_name

        # Standardize merchant name format
        if merchant_name:
            merchant_name = merchant_name.strip().title()

        # Build the vector entry
        vectors.append(
            {
                "id": result["custom_id"],
                "values": result["embedding"],
                "metadata": {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "line_id": line_id,
                    "source": source,
                    "text": target_line.text,
                    "x": x_center,
                    "y": y_center,
                    "width": width,
                    "height": height,
                    "confidence": target_line.confidence,
                    "avg_word_confidence": avg_confidence,
                    "word_count": word_count,
                    "prev_line": prev_line,
                    "next_line": next_line,
                    "merchant_name": merchant_name,
                    "angle_degrees": target_line.angle_degrees,
                    "section": section if section is not None else "UNLABELED",
                },
            }
        )

    # Upsert batches to Pinecone with 'lines' namespace
    batch_size = 100
    upserted_count = 0

    for i in range(0, len(vectors), batch_size):
        chunk = vectors[i : i + batch_size]
        try:
            if client_manager is None:
                client_manager = get_client_manager()
            response = client_manager.pinecone.upsert(
                vectors=chunk, namespace="lines"
            )
            upserted_count += response.get("upserted_count", 0)
        except Exception as e:
            print(f"Failed to upsert chunk to Pinecone: {e}")
            raise e

    return upserted_count


def write_line_embedding_results_to_dynamo(
    results: List[dict],
    descriptions: dict[str, dict[int, dict]],
    batch_id: str,
    client_manager: ClientManager = None,
) -> int:
    """
    Write the line embedding results to DynamoDB using pre-fetched descriptions.

    Args:
        results (List[dict]): The list of embedding results containing:
            - custom_id (str)
            - embedding (List[float])
        descriptions (dict): Nested dict from get_receipt_descriptions,
            keyed by image_id then receipt_id.
        batch_id (str): The identifier of the batch for EmbeddingBatchResult.

    Returns:
        int: Number of embedding results written to DynamoDB.
    """
    embedding_results: list[EmbeddingBatchResult] = []
    for record in results:
        custom_id = record["custom_id"]
        # Parse metadata from custom_id
        meta = _parse_metadata_from_line_id(custom_id)
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        line_id = meta["line_id"]
        # Find the ReceiptLine object to get text
        lines = descriptions[image_id][receipt_id]["lines"]
        target_line = next(
            (l for l in lines if l.line_id == line_id),
            None,
        )
        if target_line is None:
            raise ValueError(f"No ReceiptLine found for {custom_id}")
        # Build EmbeddingBatchResult
        embedding_results.append(
            EmbeddingBatchResult(
                batch_id=batch_id,
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=0,  # Not applicable for lines, use 0
                pinecone_id=custom_id,
                status="SUCCESS",
                text=target_line.text,
            )
        )

    # Write results in chunks
    written = 0
    for i in range(0, len(embedding_results), 25):
        chunk = embedding_results[i : i + 25]
        if client_manager is None:
            client_manager = get_client_manager()
        client_manager.dynamo.add_embedding_batch_results(chunk)
        written += len(chunk)
    return written


def mark_batch_complete(batch_id: str, client_manager: ClientManager = None):
    """
    Mark the embedding batch as complete in the system.
    Args:
        batch_id (str): The identifier of the batch.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    batch_summary = client_manager.dynamo.get_batch_summary(batch_id)
    batch_summary.status = "COMPLETED"
    client_manager.dynamo.update_batch_summary(batch_summary)


def update_line_embedding_status_to_success(
    results: List[dict],
    descriptions: dict[str, dict[int, dict]],
    client_manager: ClientManager = None,
):
    """
    Update the embedding status of the lines to SUCCESS.

    Args:
        results: The list of embedding results.
        descriptions: The nested dict of receipt descriptions.
    """
    # Group lines by receipt for efficient updates
    lines_by_receipt: dict[str, dict[int, list]] = {}

    for result in results:
        meta = _parse_metadata_from_line_id(result["custom_id"])
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        line_id = meta["line_id"]

        # Get the lines for this receipt from descriptions
        receipt_lines = descriptions[image_id][receipt_id]["lines"]

        # Find the target line by direct line_id match
        target_line = next(
            (line for line in receipt_lines if line.line_id == line_id), None
        )

        if target_line:
            # Initialize the receipt dict if needed
            if image_id not in lines_by_receipt:
                lines_by_receipt[image_id] = {}
            if receipt_id not in lines_by_receipt[image_id]:
                lines_by_receipt[image_id][receipt_id] = []

            # Update the embedding status to SUCCESS
            target_line.embedding_status = EmbeddingStatus.SUCCESS.value
            lines_by_receipt[image_id][receipt_id].append(target_line)
        else:
            raise ValueError(
                f"No line found with ID {line_id} in receipt {receipt_id} "
                f"from image {image_id}"
            )

    # Update lines in DynamoDB by receipt
    for image_id, receipt_dict in lines_by_receipt.items():
        for receipt_id, lines in receipt_dict.items():
            if lines:
                if client_manager is None:
                    client_manager = get_client_manager()
                client_manager.dynamo.update_receipt_lines(lines)
