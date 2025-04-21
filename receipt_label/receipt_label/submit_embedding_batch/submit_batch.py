from typing import List, Tuple
from uuid import uuid4
from datetime import datetime, timezone
import json
import os
import math
import boto3
from openai.resources.batches import Batch
from openai.types import FileObject
from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel
from receipt_dynamo.constants import EmbeddingStatus
from pathlib import Path

"""
submit_batch.py

This module handles the preparation, formatting, submission, and tracking of
embedding batch jobs to OpenAI's Batch API. It includes functionality to:

- Fetch ReceiptWordLabel and ReceiptWord entities from DynamoDB
- Join and structure the data into OpenAI-compatible embedding requests
- Write these requests to an NDJSON file
- Upload the NDJSON file to S3 and OpenAI
- Submit the batch embedding job to OpenAI
- Track job metadata and store summaries in DynamoDB

This script supports agentic document labeling and validation pipelines
by facilitating scalable embedding of labeled receipt tokens.
"""

from receipt_dynamo.entities import ReceiptWord, BatchSummary
from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()


def serialize_receipt_words(
    word_receipt_dict: dict[str, dict[int, list[ReceiptWord]]],
) -> List[dict]:
    """
    Serialize ReceiptWords into per-receipt NDJSON files.

    Args:
        word_receipt_dict: mapping image_id -> receipt_id -> list of ReceiptWord.

    Returns:
        A list of dicts, each containing:
            - image_id (str)
            - receipt_id (int)
            - ndjson_path (Path to the NDJSON file)
    """
    results: List[dict] = []
    for image_id, receipts in word_receipt_dict.items():
        for receipt_id, words in receipts.items():
            # Serialize each word as JSON (using its __dict__)
            ndjson_lines = [json.dumps(word.__dict__) for word in words]
            ndjson_content = "\n".join(ndjson_lines)
            # Write to a unique NDJSON file
            filepath = Path(f"/tmp/{image_id}_{receipt_id}_{uuid4()}.ndjson")
            with filepath.open("w") as f:
                f.write(ndjson_content)
            # Keep metadata about which receipt this file represents
            results.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "ndjson_path": filepath,
                }
            )
    return results


def upload_serialized_words(
    serialized_words: List[dict], s3_bucket: str, prefix="embeddings"
) -> List[dict]:
    """Upload the serialized words to S3."""
    s3 = boto3.client("s3")
    for receipt_dict in serialized_words:
        key = f"{prefix}/{Path(receipt_dict['ndjson_path']).name}"
        s3.upload_file(
            str(receipt_dict["ndjson_path"]),
            s3_bucket,
            key,
        )
        receipt_dict["s3_key"] = key
        receipt_dict["s3_bucket"] = s3_bucket
    return serialized_words


def download_serialized_words(serialized_word: dict) -> Path:
    """Download the serialized word from S3."""
    s3 = boto3.client("s3")
    s3.download_file(
        serialized_word["s3_bucket"],
        serialized_word["s3_key"],
        serialized_word["ndjson_path"],
    )
    return Path(serialized_word["ndjson_path"])


def deserialize_receipt_words(filepath: Path) -> list[ReceiptWord]:
    """Deserialize an NDJSON file containing serialized ReceiptWords."""
    words = []
    with open(filepath, "r") as f:
        for line in f:
            word = json.loads(line)
            words.append(ReceiptWord(**word))
    return words


def query_receipt_words(image_id: str, receipt_id: int) -> list[ReceiptWord]:
    """Query the ReceiptWords from DynamoDB."""
    (
        _,
        _,
        words,
        _,
        _,
        _,
    ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
    return words


def chunk_into_embedding_batches(
    words: list[ReceiptWord],
) -> dict[str, dict[int, list[ReceiptWord]]]:
    """Chunk the words into embedding batches by image and receipt.

    Returns:
        dict mapping image_id (str) to dict mapping receipt_id (int) to list of ReceiptWord.
    """
    # Make a dictionary of words by image_id and receipt_id
    words_by_image = {}
    for word in words:
        # Get or create the sub-dictionary for this image_id
        image_dict = words_by_image.setdefault(word.image_id, {})
        # Get or create the list for this receipt_id within that image
        receipt_list = image_dict.setdefault(word.receipt_id, [])
        # Append the word to the list
        receipt_list.append(word)
    return words_by_image


def get_hybrid_context(
    word: ReceiptWord,
    words: list[ReceiptWord],
    max_words_line: int = 10,
    y_thresh: float = 0.02,
) -> list[ReceiptWord]:
    """
    Returns a mix of all words on the same line (if that line is short enough),
    plus any off-line words whose vertical position is within y_thresh of the target.

    - max_words_line: if the line has <= this many words, include the whole line.
    - y_thresh: fraction of page height (or normalized coords) to capture near‑by rows.
    """
    # 1) Line‑based group
    same_line = [w for w in words if w.line_id == word.line_id]
    if len(same_line) <= max_words_line:
        context = same_line.copy()
    else:
        # Fall back to sliding window on the line
        same_line_sorted = sorted(same_line, key=lambda w: w.word_id)
        idx = [
            i
            for i, w in enumerate(same_line_sorted)
            if w.word_id == word.word_id
        ][0]
        left = max(0, idx - 2)
        right = min(len(same_line_sorted), idx + 3)
        context = same_line_sorted[left:right]

    # 2) Spatial neighbors off the line
    _, y0 = word.calculate_centroid()
    # Collect words whose vertical centroid is within y_thresh
    spatial_neighbors = [
        w
        for w in words
        if w.line_id != word.line_id
        and abs(w.calculate_centroid()[1] - y0) < y_thresh
    ]

    # Merge and dedupe
    key = lambda w: (w.line_id, w.word_id)
    merged = {key(w): w for w in context + spatial_neighbors}
    return list(merged.values())


def generate_batch_id() -> str:
    """Generate a unique batch ID as a UUID string."""
    return str(uuid4())


def list_receipt_words_with_no_embeddings() -> List[ReceiptWord]:
    """Fetch all ReceiptWord items with embedding_status == NONE."""
    return dynamo_client.listReceiptWordsByEmbeddingStatus(
        EmbeddingStatus.NONE
    )


def chunk_receipt_words(
    words: List[ReceiptWord], batch_size: int = 500
) -> List[List[ReceiptWord]]:
    """Chunk ReceiptWord list into batches of given size."""
    return [
        words[i : i + batch_size] for i in range(0, len(words), batch_size)
    ]


def format_word_context_embedding_input(
    word: ReceiptWord, words: List[ReceiptWord]
) -> str:
    # 1) Compute the target word’s vertical span (accounting for origin at bottom)
    # Use bounding_box for consistent span
    target_bottom = word.bounding_box["y"]
    target_top = word.bounding_box["y"] + word.bounding_box["height"]
    line_height = target_top - target_bottom

    # 2) Sort everything by X so we can walk left/right
    sorted_all = sorted(words, key=lambda w: w.calculate_centroid()[0])
    x0, _ = word.calculate_centroid()
    idx = next(
        i
        for i, w in enumerate(sorted_all)
        if (w.image_id, w.receipt_id, w.line_id, w.word_id)
        == (word.image_id, word.receipt_id, word.line_id, word.word_id)
    )

    # 3) Filter to only those words whose vertical span lies within the same line height
    candidates = []
    for w in sorted_all:
        if w is word:
            continue
        w_top = w.top_left["y"]
        w_bottom = w.bottom_left["y"]
        # they “fit” if their top isn’t below the target bottom,
        # and their bottom isn’t above the target top,
        # within a small epsilon if you like
        if w_bottom >= target_bottom and w_top <= target_top:
            candidates.append(w)

    # 4) Walk left
    left_text = "<EDGE>"
    for w in reversed(sorted_all[:idx]):
        if w in candidates:
            left_text = w.text
            break

    # 5) Walk right
    right_text = "<EDGE>"
    for w in sorted_all[idx + 1 :]:
        if w in candidates:
            right_text = w.text
            break

    return f"<TARGET>{word.text}</TARGET> <POS>{get_word_position(word)}</POS> <CONTEXT>{left_text} {right_text}</CONTEXT>"


def get_word_position(word: ReceiptWord) -> str:
    """
    Define a human-readable position tag for the word based on its centroid.
    Buckets the word into one of nine zones: top/middle/bottom x left/center/right.
    """
    # Calculate centroid coordinates (normalized 0.0–1.0)
    x_center, y_center = word.calculate_centroid()
    # Determine vertical bucket (y=0 at bottom)
    if y_center > 0.66:
        vert = "top"
    elif y_center > 0.33:
        vert = "middle"
    else:
        vert = "bottom"
    # Determine horizontal bucket
    if x_center < 0.33:
        horiz = "left"
    elif x_center < 0.66:
        horiz = "center"
    else:
        horiz = "right"
    return f"{vert}-{horiz}"


def format_word_context_embedding(
    words_to_embed: List[ReceiptWord],
    all_words_in_receipt: List[ReceiptWord],
) -> List[dict]:
    """
    Format each (ReceiptWordLabel, ReceiptWord) pair into a context-level entry
    for OpenAI embeddings, using a hybrid line+spatial window.
    """
    inputs = []
    for word in words_to_embed:
        # Build hybrid context around this word
        pinecone_id = (
            f"IMAGE#{word.image_id}#"
            f"RECEIPT#{word.receipt_id:05d}#"
            f"LINE#{word.line_id:05d}#"
            f"WORD#{word.word_id:05d}"
        )
        body_input = format_word_context_embedding_input(
            word, all_words_in_receipt
        )
        entry = {
            "custom_id": pinecone_id,
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "input": body_input,
                "model": "text-embedding-3-small",
            },
        }
        inputs.append(entry)
    return inputs


def write_ndjson(batch_id: str, input_data: List[dict]) -> Path:
    """Write the OpenAI embedding input to an NDJSON file."""
    filepath = Path(f"/tmp/{batch_id}.ndjson")
    with filepath.open("w") as f:
        for row in input_data:
            f.write(json.dumps(row) + "\n")
    return filepath


def upload_to_s3(
    local_path: str,
    batch_id: str,
    bucket: str,
    prefix: str = "embedding_batches/",
) -> str:
    """Upload the NDJSON file to S3."""
    s3 = boto3.client("s3")
    key = f"{prefix}{batch_id}.ndjson"
    s3.upload_file(local_path, bucket, key)
    return key


def upload_to_openai(filepath: Path) -> FileObject:
    """Upload the NDJSON file to OpenAI."""
    return openai_client.files.create(
        file=filepath.open("rb"), purpose="batch"
    )


def submit_openai_batch(file_id: str) -> Batch:
    """Submit a batch embedding job to OpenAI using the uploaded file."""
    return openai_client.batches.create(
        input_file_id=file_id,
        endpoint="/v1/embeddings",
        completion_window="24h",
        metadata={"model": "text-embedding-3-small"},
    )


def download_from_s3(key: str, bucket: str) -> str:
    """Download the NDJSON file from S3 and return the local path."""
    s3 = boto3.client("s3")
    local_path = f"/tmp/{os.path.basename(key)}"
    s3.download_file(bucket, key, local_path)
    return local_path


def create_batch_summary(
    batch_id: str,
    open_ai_batch_id: str,
    file_path: str,
) -> BatchSummary:
    """
    Construct a BatchSummary for the submitted embedding batch using the
    NDJSON file.
    """
    receipt_refs = set()
    with open(file_path, "r") as f:
        for line in f:
            item = json.loads(line)
            try:
                parts = item["id"].split("#")
                receipt_id = int(parts[1])
                image_id = item.get(
                    "image_id", "unknown"
                )  # fallback if not stored explicitly
                receipt_refs.add((image_id, receipt_id))
            except Exception:
                continue

    return BatchSummary(
        batch_id=batch_id,
        batch_type="EMBEDDING",
        openai_batch_id=open_ai_batch_id,
        submitted_at=datetime.now(timezone.utc),
        status="PENDING",
        word_count=len(receipt_refs),
        result_file_id="N/A",
        receipt_refs=list(receipt_refs),
    )


def add_batch_summary(summary: BatchSummary):
    """Write the BatchSummary entity to DynamoDB."""
    dynamo_client.addBatchSummary(summary)


def update_receipt_word_labels(labels: List[ReceiptWordLabel]):
    """Update the ReceiptWordLabel entities in DynamoDB."""
    dynamo_client.updateReceiptWordLabels(labels)
