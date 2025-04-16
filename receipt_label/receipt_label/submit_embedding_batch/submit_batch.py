from typing import List, Tuple
from uuid import uuid4
from datetime import datetime, timezone
import json
import os
from openai.types.beta.batches import Batch

from receipt_dynamo.entities import ReceiptWordLabel, ReceiptWord, BatchSummary
from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()


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
    keys = [
        json.loads(j) for j in {json.dumps(d, sort_keys=True) for d in keys}
    ]
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
    """Format (ReceiptWordLabel, ReceiptWord) pairs into OpenAI-compatible embedding inputs."""
    inputs = []
    for rwl, word in joined_batch:
        text = f"{word.text} [label={rwl.label}] (pos={word.x_center:.4f},{word.y_center:.4f}) angle={word.angle_degrees:.2f} conf={word.confidence:.2f}"
        pinecone_id = (
            f"RECEIPT#{rwl.receipt_id}#LINE#{rwl.line_id}#WORD#{rwl.word_id}"
        )
        inputs.append({"input": text, "id": pinecone_id})
    return inputs


def write_ndjson(batch_id: str, input_data: List[dict]) -> str:
    """Write the OpenAI embedding input to an NDJSON file."""
    filepath = f"/tmp/{batch_id}.ndjson"
    with open(filepath, "w") as f:
        for row in input_data:
            f.write(json.dumps(row) + "\n")
    return filepath


def upload_ndjson_file(filepath: str):
    """Upload the NDJSON file to OpenAI."""
    return openai_client.files.create(
        file=open(filepath, "rb"), purpose="batch"
    )


def submit_openai_batch(file_id: str) -> Batch:
    """Submit a batch embedding job to OpenAI using the uploaded file."""
    return openai_client.batches.create(
        input_file_id=file_id,
        endpoint="/v1/embeddings",
        parameters={"model": "text-embedding-3-small"},
    )


def create_batch_summary(
    batch_id: str,
    open_ai_batch_id: str,
    joined: List[Tuple[ReceiptWordLabel, ReceiptWord]],
) -> BatchSummary:
    """Construct a BatchSummary for the submitted embedding batch."""
    return BatchSummary(
        batch_id=batch_id,
        batch_type="EMBEDDING",
        openai_batch_id=open_ai_batch_id,
        submitted_at=datetime.now(timezone.utc),
        status="PENDING",
        word_count=len(joined),
        result_file_id="N/A",
        receipt_refs=list(
            set((rwl.image_id, rwl.receipt_id) for rwl, _ in joined)
        ),
    )
