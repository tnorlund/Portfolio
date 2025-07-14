"""
poll_batch.py

This module handles the polling, result retrieval, and ingestion pipeline for
embedding batch jobs submitted to OpenAI's Batch API.

It is designed to be used in conjunction with a Step Function that monitors
the status of submitted batches and processes them once complete.

Functions in this module perform the following tasks:
- List all embedding batches with status "PENDING" from DynamoDB.
- Poll OpenAI's Batch API to determine if a batch job is complete.
- Download the NDJSON-formatted embedding results once a batch completes.
- Parse and upsert the embeddings to Pinecone using structured metadata.
- Write embedding results to a DynamoDB table for tracking.
- Mark completed batches in DynamoDB to prevent reprocessing.

This supports scalable, event-driven processing of large embedding jobs in a
distributed receipt labeling and validation workflow.
"""

import json
import re
from typing import List

from receipt_dynamo.constants import BatchType, ValidationStatus
from receipt_dynamo.entities import BatchSummary, EmbeddingBatchResult

from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


def _parse_left_right_from_formatted(fmt: str) -> tuple[str, str]:
    """
    Given a string like
      "<TARGET>WORD</TARGET> <POS>…</POS> <CONTEXT>LEFT RIGHT</CONTEXT>"
    return ("LEFT", "RIGHT").
    """
    m = re.search(r"<CONTEXT>(.*?)</CONTEXT>", fmt)
    if not m:
        raise ValueError(f"No <CONTEXT>…</CONTEXT> in {fmt!r}")
    cont = m.group(1).strip()
    # Assuming exactly two tokens separated by whitespace
    parts = cont.split(maxsplit=1)
    left = parts[0] if parts else "<EDGE>"
    right = parts[1] if len(parts) > 1 else "<EDGE>"
    return left, right


def _parse_metadata_from_custom_id(custom_id: str) -> dict:
    parts = custom_id.split("#")
    return {
        "image_id": parts[1],
        "receipt_id": int(parts[3]),
        "line_id": int(parts[5]),
        "word_id": int(parts[7]),
        "source": "openai_embedding_batch",
    }


def list_pending_embedding_batches(
    client_manager: ClientManager = None,
) -> List[BatchSummary]:
    """
    List embedding batches that are pending processing.
    Returns a list of pending batch identifiers.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    summaries, lek = client_manager.dynamo.get_batch_summaries_by_status(
        status="PENDING",
        batch_type=BatchType.EMBEDDING,
        limit=25,
        last_evaluated_key=None,
    )
    while lek:
        next_summaries, lek = (
            client_manager.dynamo.get_batch_summaries_by_status(
                status="PENDING",
                batch_type=BatchType.EMBEDDING,
                limit=25,
                last_evaluated_key=lek,
            )
        )
        summaries.extend(next_summaries)
    return summaries


def get_openai_batch_status(
    openai_batch_id: str, client_manager: ClientManager = None
) -> str:
    """
    Retrieve the status of an OpenAI embedding batch job.

    Args:
        openai_batch_id (str): The identifier of the batch.

    Returns the current status of the batch.
    """
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
        descriptions.setdefault(image_id, {})[receipt_id] = {
            "receipt": receipt,
            "lines": lines,
            "words": words,
            "letters": letters,
            "labels": labels,
            "metadata": receipt_metadata,
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


def upsert_embeddings_to_pinecone(  # pylint: disable=too-many-statements
    results: List[dict],
    descriptions: dict[str, dict[int, dict]],
    client_manager: ClientManager = None,
):
    """
    Upsert embedding results to Pinecone with rich metadata.

    Each result entry in `results` is transformed into a Pinecone vector with:
      - id: the original custom_id string (e.g. "IMAGE#...#RECEIPT#...#LINE#...#WORD#...").
      - values: the embedding array for that word.

    The metadata dict for each vector includes:
      - image_id (str): the UUID of the source image.
      - receipt_id (int): the numeric ID of the receipt within that
        image.
      - line_id (int): the line number from the OCR output.
      - word_id (int): the position of the word in the line.
      - source (str): fixed value "openai_embedding_batch" to track provenance.
      - text (str): the OCR-extracted word string.
      - confidence (float): OCR confidence score for the word.
      - left (str): the adjacent word to the left (or "<EDGE>" if none).
      - right (str): the adjacent word to the right (or "<EDGE>" if none).
      - merchant_name (str, optional): the merchant name for this receipt,
        if available.

    Args:
        results (List[dict]): The list of embedding results, each containing:
            - custom_id (str)
            - embedding (List[float])
        descriptions (dict): A nested dict of receipt details keyed by
            image_id and receipt_id.

    Returns:
        int: The total number of vectors successfully upserted to Pinecone.
    """
    vectors = []
    for result in results:

        # parse our metadata fields
        _meta = _parse_metadata_from_custom_id(result["custom_id"])
        image_id = _meta["image_id"]
        receipt_id = _meta["receipt_id"]
        line_id = _meta["line_id"]
        word_id = _meta["word_id"]
        source = "openai_embedding_batch"
        # From the descriptions, get the receipt details for this result
        receipt_details = descriptions[image_id][receipt_id]
        _ = receipt_details["receipt"]  # receipt
        _ = receipt_details["lines"]  # lines
        words = receipt_details["words"]
        _ = receipt_details["letters"]  # letters
        labels = receipt_details["labels"]
        metadata = receipt_details["metadata"]
        # Get the target word from the list of words
        target_word = next(
            (
                w
                for w in words
                if w.line_id == line_id and w.word_id == word_id
            ),
            None,
        )
        if target_word is None:
            raise ValueError(
                f"No ReceiptWord found for image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id}, "
                f"word_id={word_id}"
            )
        _ = [  # target_word_labels
            label
            for label in labels
            if label.image_id == image_id
            and label.receipt_id == receipt_id
            and label.line_id == line_id
            and label.word_id == word_id
        ]
        # label_status — overall state for this word:
        #    "unvalidated" if none VALID,
        #    "auto_suggested" if ANY PENDING and none VALID,
        #    "validated" if at least one VALID
        if any(
            lbl.validation_status == ValidationStatus.VALID.value
            for lbl in labels
        ):
            label_status = "validated"
        elif any(
            lbl.validation_status == ValidationStatus.PENDING.value
            for lbl in labels
        ):
            label_status = "auto_suggested"
        else:
            label_status = "unvalidated"
        auto_suggestions = [
            lbl
            for lbl in labels
            if lbl.validation_status == ValidationStatus.PENDING.value
        ]
        # label_confidence & label_proposed_by — pick from the most recent
        # auto‑suggestion:
        if auto_suggestions:
            # assume the last‑added pending label is the one your LLM just
            # suggested
            last = sorted(auto_suggestions, key=lambda l: l.timestamp_added)[
                -1
            ]
            label_confidence = getattr(
                last, "confidence", None
            )  # if you store it on the label
            label_proposed_by = last.label_proposed_by
        else:
            label_confidence = None
            label_proposed_by = None
        # validated_labels — all labels with status VALID
        validated_labels = [
            lbl.label
            for lbl in labels
            if lbl.validation_status == ValidationStatus.VALID.value
        ]
        # label_validated_at — timestamp of the most recent VALID
        valids = [
            lbl
            for lbl in labels
            if lbl.validation_status == ValidationStatus.VALID.value
        ]
        label_validated_at = (
            sorted(valids, key=lambda l: l.timestamp_added)[-1].timestamp_added
            if valids
            else None
        )

        # Re format the embedding to get the words left and right of the
        # target word
        text = target_word.text
        _word_centroid = target_word.calculate_centroid()
        x_center, y_center = _word_centroid
        width = target_word.bounding_box["width"]
        height = target_word.bounding_box["height"]
        confidence = target_word.confidence
        # Import locally to avoid circular import
        from receipt_label.embedding.word.submit import (  # pylint: disable=import-outside-toplevel
            _format_word_context_embedding_input,
        )

        _embedding = _format_word_context_embedding_input(target_word, words)
        left_text, right_text = _parse_left_right_from_formatted(_embedding)

        # Priority: canonical name > regular merchant name
        if (
            hasattr(metadata, "canonical_merchant_name")
            and metadata.canonical_merchant_name
        ):
            merchant_name = metadata.canonical_merchant_name
        else:
            merchant_name = metadata.merchant_name

        # Standardize the merchant name format
        if merchant_name:
            merchant_name = merchant_name.strip().title()

        # build the vector entry
        vectors.append(
            {
                "id": result["custom_id"],
                "values": result["embedding"],
                "metadata": {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "line_id": line_id,
                    "word_id": word_id,
                    "source": source,
                    "text": text,
                    "x": x_center,
                    "y": y_center,
                    "width": width,
                    "height": height,
                    "confidence": confidence,
                    "left": left_text,
                    "right": right_text,
                    "merchant_name": merchant_name,
                    "label_status": label_status,
                    **(
                        {"label_confidence": label_confidence}
                        if label_confidence is not None
                        else {}
                    ),
                    **(
                        {"label_proposed_by": label_proposed_by}
                        if label_proposed_by is not None
                        else {}
                    ),
                    **(
                        {"validated_labels": validated_labels}
                        if validated_labels
                        else {}
                    ),
                    **(
                        {"label_validated_at": label_validated_at}
                        if label_validated_at is not None
                        else {}
                    ),
                },
            }
        )

    batch_size = 100
    upserted_count = 0
    for i in range(0, len(vectors), batch_size):
        chunk = vectors[i : i + batch_size]
        try:
            if client_manager is None:
                client_manager = get_client_manager()
            response = client_manager.pinecone.upsert(
                vectors=chunk, namespace="words"
            )
            upserted_count += response.get("upserted_count", 0)
        except Exception as e:
            print(f"Failed to upsert chunk to Pinecone: {e}")
            raise e
    return upserted_count


def write_embedding_results_to_dynamo(
    results: List[dict],
    descriptions: dict[str, dict[int, dict]],
    batch_id: str,
    client_manager: ClientManager = None,
) -> int:
    """
    Write the embedding results to DynamoDB using pre-fetched descriptions.

    Args:
        results (List[dict]): The list of embedding results containing:
            - custom_id (str)
            - embedding (List[float])
        descriptions (dict): Nested dict from get_receipt_descriptions, keyed by image_id then receipt_id.
        batch_id (str): The identifier of the batch for EmbeddingBatchResult.

    Returns:
        int: Number of embedding results written to DynamoDB.
    """
    embedding_results: list[EmbeddingBatchResult] = []
    for record in results:
        custom_id = record["custom_id"]
        # Parse metadata from custom_id
        meta = _parse_metadata_from_custom_id(custom_id)
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        line_id = meta["line_id"]
        word_id = meta["word_id"]
        # Find the ReceiptWord object to get text
        words = descriptions[image_id][receipt_id]["words"]
        target_word = next(
            (
                w
                for w in words
                if w.line_id == line_id and w.word_id == word_id
            ),
            None,
        )
        if target_word is None:
            raise ValueError(f"No ReceiptWord found for {custom_id}")
        # Build EmbeddingBatchResult
        embedding_results.append(
            EmbeddingBatchResult(
                batch_id=batch_id,
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=line_id,
                word_id=word_id,
                pinecone_id=custom_id,
                status="SUCCESS",
                text=target_word.text,
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
