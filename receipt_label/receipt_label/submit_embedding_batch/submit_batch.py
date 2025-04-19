from typing import List, Tuple
from uuid import uuid4
from datetime import datetime, timezone
import json
import os
import boto3
from openai.resources.batches import Batch
from openai.types import FileObject

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

from receipt_dynamo.entities import ReceiptWordLabel, ReceiptWord, BatchSummary
from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()


def get_hybrid_context(
    word: ReceiptWord,
    words: list[ReceiptWord],
    max_words_line: int = 10,
    y_thresh: float = 0.02,
) -> list[ReceiptWord]:
    """
    Returns a mix of all words on the same line (if that line is short enough),
    plus any off‑line words whose vertical position is within y_thresh of the target.

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


def list_receipt_word_labels() -> List[ReceiptWordLabel]:
    """Fetch ReceiptWordLabel items with validation_status == 'NONE'."""
    all_labels = []
    lek = None
    while True:
        labels, lek = dynamo_client.getReceiptWordLabelsByValidationStatus(
            validation_status="NONE",
            limit=25,
            lastEvaluatedKey=lek,
        )
        all_labels.extend(labels)
        if not lek:
            break
    return list(set(all_labels))


def fetch_receipt_words(labels: List[ReceiptWordLabel]) -> List[ReceiptWord]:
    """Batch fetch ReceiptWord entities that match the given labels."""
    keys = [rwl.to_ReceiptWord_key() for rwl in labels]
    keys = list({json.dumps(k, sort_keys=True): k for k in keys}.values())
    results = []
    for i in range(0, len(keys), 25):
        chunk = keys[i : i + 25]
        results.extend(dynamo_client.getReceiptWordsByKeys(chunk))
    return results


def join_labels_with_words(
    labels: List[ReceiptWordLabel], words: List[ReceiptWord]
) -> List[Tuple[ReceiptWordLabel, ReceiptWord]]:
    """Join each ReceiptWordLabel with its corresponding ReceiptWord."""
    word_map = {
        (w.image_id, w.receipt_id, w.line_id, w.word_id): w for w in words
    }
    joined = []
    for rwl in labels:
        key = (rwl.image_id, rwl.receipt_id, rwl.line_id, rwl.word_id)
        if key in word_map:
            joined.append((rwl, word_map[key]))
    return joined


def fetch_original_receipt_word_labels(
    filepath: str,
) -> List[ReceiptWordLabel]:
    """Extract ReceiptWordLabel entities from NDJSON based on Pinecone IDs."""
    with open(filepath, "r") as f:
        pinecone_ids = [json.loads(line)["custom_id"] for line in f]

    keys = []
    for pinecone_id in pinecone_ids:
        if pinecone_id.startswith("WORD#"):
            suffix = pinecone_id[len("WORD#") :]
        elif pinecone_id.startswith("CTX#"):
            suffix = pinecone_id[len("CTX#") :]
        else:
            suffix = pinecone_id

        parts = suffix.split("#")
        image_id = parts[1]
        receipt_id = int(parts[3])
        line_id = int(parts[5])
        word_id = int(parts[7])
        label = parts[-1]
        keys.append(
            {
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}#"
                        f"LINE#{line_id:05d}#"
                        f"WORD#{word_id:05d}#"
                        f"LABEL#{label}"
                    )
                },
            }
        )

    # Deduplicate by serializing to JSON, then back to dicts
    keys = list({json.dumps(k, sort_keys=True): k for k in keys}.values())
    results = []
    for i in range(0, len(keys), 25):
        chunk = keys[i : i + 25]
        results.extend(dynamo_client.getReceiptWordLabelsByKeys(chunk))
    return results


def chunk_joined_pairs(
    joined: List[Tuple[ReceiptWordLabel, ReceiptWord]], batch_size: int = 500
) -> List[List[Tuple[ReceiptWordLabel, ReceiptWord]]]:
    """Chunk the joined list into batches of a given size."""
    return [
        joined[i : i + batch_size] for i in range(0, len(joined), batch_size)
    ]


def format_openai_input(
    joined_batch: List[Tuple[ReceiptWordLabel, ReceiptWord]],
) -> List[dict]:
    """
    Format (ReceiptWordLabel, ReceiptWord) pairs into OpenAI-compatible
    embedding inputs.
    """
    inputs = []
    for rwl, word in joined_batch:
        centroid = word.calculate_centroid()
        x_center = centroid[0]
        y_center = centroid[1]
        pinecone_id = (
            "WORD#"
            f"IMAGE#{rwl.image_id}#"
            f"RECEIPT#{rwl.receipt_id:05d}#"
            f"LINE#{rwl.line_id:05d}#"
            f"WORD#{rwl.word_id:05d}#"
            f"LABEL#{rwl.label}"
        )
        entry = {
            "custom_id": pinecone_id,
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "input": (
                    f"{word.text} [label={rwl.label}] "
                    f"(pos={x_center:.4f},{y_center:.4f}) "
                    f"angle={word.angle_degrees:.2f} "
                    f"conf={word.confidence:.2f}"
                ),
                "model": "text-embedding-3-small",
            },
        }
        inputs.append(entry)
    return inputs


def format_context_openai_input(
    joined_batch: List[Tuple[ReceiptWordLabel, ReceiptWord]],
) -> List[dict]:
    """
    Format each (ReceiptWordLabel, ReceiptWord) pair into a context-level entry
    for OpenAI embeddings, using a hybrid line+spatial window.
    """
    inputs = []
    for rwl, word in joined_batch:
        # Build hybrid context around this word
        context_words = get_hybrid_context(word, [w for _, w in joined_batch])
        context_text = " ".join(w.text for w in context_words)
        pinecone_id = (
            "CTX#"
            f"IMAGE#{rwl.image_id}#"
            f"RECEIPT#{rwl.receipt_id:05d}#"
            f"LINE#{rwl.line_id:05d}#"
            f"WORD#{rwl.word_id:05d}#"
            f"LABEL#{rwl.label}"
        )
        entry = {
            "custom_id": pinecone_id,
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "input": context_text + f" [label={rwl.label}]",
                "model": "text-embedding-3-small",
            },
        }
        inputs.append(entry)
    return inputs


def write_ndjson(batch_id: str, input_data: List[dict]) -> str:
    """Write the OpenAI embedding input to an NDJSON file."""
    filepath = f"/tmp/{batch_id}.ndjson"
    with open(filepath, "w") as f:
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


def upload_ndjson_file(filepath: str) -> FileObject:
    """Upload the NDJSON file to OpenAI."""
    return openai_client.files.create(
        file=open(filepath, "rb"), purpose="batch"
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
