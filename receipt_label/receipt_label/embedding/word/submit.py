"""
submit_batch.py

This module handles the preparation, formatting, submission, and tracking of
embedding batch jobs to OpenAI's Batch API.

It includes functionality to:
- Fetch ReceiptWordLabel and ReceiptWord entities from DynamoDB
- Join and structure the data into OpenAI-compatible embedding requests
- Write these requests to an NDJSON file
- Upload the NDJSON file to S3 and OpenAI
- Submit the batch embedding job to OpenAI
- Track job metadata and store summaries in DynamoDB

This script supports agentic document labeling and validation pipelines
by facilitating scalable embedding of labeled receipt tokens.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import boto3
from openai.resources.batches import Batch
from openai.types import FileObject
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import BatchSummary, ReceiptWord

from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


def serialize_receipt_words(
    word_receipt_dict: dict[str, dict[int, list[ReceiptWord]]],
) -> list[dict]:
    """
    Serialize ReceiptWords into per-receipt NDJSON files.

    Args:
        word_receipt_dict: mapping image_id -> receipt_id -> list of
            ReceiptWord.

    Returns:
        A list of dicts, each containing:
            - image_id (str)
            - receipt_id (int)
            - ndjson_path (Path to the NDJSON file)
    """
    results: list[dict] = []
    for image_id, receipts in word_receipt_dict.items():
        for receipt_id, words in receipts.items():
            # Serialize each word as JSON (using its __dict__)
            ndjson_lines = [json.dumps(word.__dict__) for word in words]
            ndjson_content = "\n".join(ndjson_lines)
            # Write to a unique NDJSON file
            filepath = Path(f"/tmp/{image_id}_{receipt_id}_{uuid4()}.ndjson")
            with filepath.open("w", encoding="utf-8") as f:
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
    serialized_words: list[dict], s3_bucket: str, prefix="embeddings"
) -> list[dict]:
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
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            word = json.loads(line)
            words.append(ReceiptWord(**word))
    return words


def query_receipt_words(
    image_id: str, receipt_id: int, client_manager: ClientManager = None
) -> list[ReceiptWord]:
    """Query the ReceiptWords from DynamoDB."""
    if client_manager is None:
        client_manager = get_client_manager()
    receipt_details = client_manager.dynamo.get_receipt_details(
        image_id, receipt_id
    )
    return receipt_details.words


def chunk_into_embedding_batches(
    words: list[ReceiptWord],
) -> dict[str, dict[int, list[ReceiptWord]]]:
    """Chunk the words into embedding batches by image and receipt.

    Returns:
        dict mapping image_id (str) to dict mapping receipt_id (int) to
        list of ReceiptWord.
    """
    # Build a mapping image_id -> receipt_id ->
    # dict[(line_id, word_id) -> ReceiptWord] for uniqueness
    words_by_image: dict[
        str, dict[int, dict[tuple[int, int], ReceiptWord]]
    ] = {}
    for word in words:
        image_dict = words_by_image.setdefault(word.image_id, {})
        receipt_dict = image_dict.setdefault(word.receipt_id, {})
        # Use (line_id, word_id) as key to dedupe
        key = (word.line_id, word.word_id)
        receipt_dict[key] = word

    # Convert inner dicts back to lists
    result: dict[str, dict[int, list[ReceiptWord]]] = {}
    for image_id, receipt_map in words_by_image.items():
        result[image_id] = {}
        for receipt_id, word_map in receipt_map.items():
            result[image_id][receipt_id] = list(word_map.values())
    return result


def generate_batch_id() -> str:
    """Generate a unique batch ID as a UUID string."""
    return str(uuid4())


def list_receipt_words_with_no_embeddings(
    client_manager: ClientManager = None,
) -> list[ReceiptWord]:
    """Fetch all ReceiptWord items with embedding_status == NONE and is_noise == False."""
    if client_manager is None:
        client_manager = get_client_manager()
    all_words = client_manager.dynamo.list_receipt_words_by_embedding_status(
        EmbeddingStatus.NONE
    )
    # Filter out noise words
    return [word for word in all_words if not word.is_noise]


def _format_word_context_embedding_input(
    word: ReceiptWord, words: list[ReceiptWord]
) -> str:
    # 1) Compute the target word's vertical span (accounting for origin at
    # bottom). Use bounding_box for consistent span
    target_bottom = word.bounding_box["y"]
    target_top = word.bounding_box["y"] + word.bounding_box["height"]

    # 2) Sort everything by X so we can walk left/right
    sorted_all = sorted(words, key=lambda w: w.calculate_centroid()[0])
    idx = next(
        i
        for i, w in enumerate(sorted_all)
        if (w.image_id, w.receipt_id, w.line_id, w.word_id)
        == (word.image_id, word.receipt_id, word.line_id, word.word_id)
    )

    # 3) Filter to only those words whose vertical span lies within the same
    # line height
    candidates = []
    for w in sorted_all:
        if w is word:
            continue
        w_top = w.top_left["y"]
        w_bottom = w.bottom_left["y"]
        # they "fit" if their top isn't below the target bottom,
        # and their bottom isn't above the target top,
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

    return (
        f"<TARGET>{word.text}</TARGET> <POS>{_get_word_position(word)}</POS> "
        f"<CONTEXT>{left_text} {right_text}</CONTEXT>"
    )


def _get_word_position(word: ReceiptWord) -> str:
    """
    Define a human-readable position tag for the word based on its centroid.
    Buckets the word into one of nine zones: top/middle/bottom x left/center/right.
    """
    # Calculate centroid coordinates (normalized 0.0–1.0)
    x_center, y_center = word.calculate_centroid()
    # Determine vertical bucket (y=0 at bottom)
    if y_center > 0.66:
        vertical = "top"
    elif y_center > 0.33:
        vertical = "middle"
    else:
        vertical = "bottom"
    # Determine horizontal bucket
    if x_center < 0.33:
        horizontal = "left"
    elif x_center < 0.66:
        horizontal = "center"
    else:
        horizontal = "right"
    return f"{vertical}-{horizontal}"


def format_word_context_embedding(
    words_to_embed: list[ReceiptWord],
    all_words_in_receipt: list[ReceiptWord],
) -> list[dict]:
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
        body_input = _format_word_context_embedding_input(
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


def write_ndjson(batch_id: str, input_data: list[dict]) -> Path:
    """Write the OpenAI embedding input to an NDJSON file."""
    filepath = Path(f"/tmp/{batch_id}.ndjson")
    with filepath.open("w", encoding="utf-8") as f:
        for row in input_data:
            f.write(json.dumps(row) + "\n")
    return filepath


def upload_to_openai(
    filepath: Path, client_manager: ClientManager = None
) -> FileObject:
    """Upload the NDJSON file to OpenAI."""
    if client_manager is None:
        client_manager = get_client_manager()
    return client_manager.openai.files.create(
        file=filepath.open("rb"), purpose="batch"
    )


def submit_openai_batch(
    file_id: str, client_manager: ClientManager = None
) -> Batch:
    """Submit a batch embedding job to OpenAI using the uploaded file."""
    if client_manager is None:
        client_manager = get_client_manager()
    return client_manager.openai.batches.create(
        input_file_id=file_id,
        endpoint="/v1/embeddings",
        completion_window="24h",
        metadata={"model": "text-embedding-3-small"},
    )


def create_batch_summary(
    batch_id: str,
    open_ai_batch_id: str,
    file_path: str,
) -> BatchSummary:
    """
    Construct a BatchSummary for the submitted embedding batch using the
    NDJSON file.
    """
    # 1) Initialize counters and refs
    receipt_refs: set[tuple[str, int]] = set()
    word_count = 0

    # 2) Read and parse each line of the NDJSON file
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            word_count += 1
            try:
                obj = json.loads(line)
                custom_id = obj.get("custom_id", "")
                parts = custom_id.split("#")
                # parts: ["IMAGE", image_id, "RECEIPT", receipt_id, ...]
                image_id = parts[1]
                receipt_id = int(parts[3])
                receipt_refs.add((image_id, receipt_id))
            except Exception:  # pylint: disable=broad-exception-caught
                continue

    # 3) Build and return the BatchSummary
    return BatchSummary(
        batch_id=batch_id,
        batch_type="WORD_EMBEDDING",
        openai_batch_id=open_ai_batch_id,
        submitted_at=datetime.now(timezone.utc),
        status="PENDING",
        result_file_id="N/A",
        receipt_refs=list(receipt_refs),
    )


def add_batch_summary(
    summary: BatchSummary, client_manager: ClientManager = None
) -> None:
    """Write the BatchSummary entity to DynamoDB."""
    if client_manager is None:
        client_manager = get_client_manager()
    client_manager.dynamo.add_batch_summary(summary)


def update_word_embedding_status(
    words: list[ReceiptWord], client_manager: ClientManager = None
) -> None:
    """Update the Embedding Status of the Words"""
    if client_manager is None:
        client_manager = get_client_manager()
    for word in words:
        # Set to the string value so GSI1PK is updated correctly
        word.embedding_status = EmbeddingStatus.PENDING.value
    client_manager.dynamo.update_receipt_words(words)
