"""
submit.py

This module handles the preparation, formatting, submission, and tracking of
embedding batch jobs for receipt lines to OpenAI's Batch API.

It includes functionality to:
- Fetch ReceiptLine entities from DynamoDB
- Format and structure the data into OpenAI-compatible embedding requests
- Write these requests to an NDJSON file
- Upload the NDJSON file to S3 and OpenAI
- Submit the batch embedding job to OpenAI
- Track job metadata and store summaries in DynamoDB

This script supports agentic document processing by facilitating scalable
embedding of receipt lines for section classification.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import boto3
from openai.resources.batches import Batch
from openai.types import FileObject
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import BatchSummary, ReceiptLine


def generate_batch_id() -> str:
    """Generate a unique batch ID as a UUID string."""
    return str(uuid4())


def list_receipt_lines_with_no_embeddings(
    client_manager: ClientManager = None,
) -> list[ReceiptLine]:
    """Fetch all ReceiptLine items with embedding_status == NONE."""
    if client_manager is None:
        client_manager = get_client_manager()
    return client_manager.dynamo.list_receipt_lines_by_embedding_status(
        EmbeddingStatus.NONE
    )


def chunk_into_line_embedding_batches(
    lines: list[ReceiptLine],
) -> dict[str, dict[int, list[ReceiptLine]]]:
    """Chunk the lines into embedding batches by image and receipt.

    Returns:
        dict mapping image_id (str) to dict mapping receipt_id (int) to
        list of ReceiptLine.
    """
    # Build a mapping image_id -> receipt_id -> dict[line_id -> ReceiptLine]
    # for uniqueness
    lines_by_image: dict[str, dict[int, dict[int, ReceiptLine]]] = {}
    for line in lines:
        image_dict = lines_by_image.setdefault(line.image_id, {})
        receipt_dict = image_dict.setdefault(line.receipt_id, {})
        # Use line_id as key to deduplicate
        receipt_dict[line.line_id] = line

    # Convert inner dicts back to lists
    result: dict[str, dict[int, list[ReceiptLine]]] = {}
    for image_id, receipt_map in lines_by_image.items():
        result[image_id] = {}
        for receipt_id, line_map in receipt_map.items():
            result[image_id][receipt_id] = list(line_map.values())
    return result


def format_line_context_embedding(
    lines_to_embed: list[ReceiptLine],
) -> list[dict]:
    """
    Format each ReceiptLine into a context-level entry for OpenAI embeddings.
    """
    inputs = []
    for line in lines_to_embed:
        # Build context around this line
        pinecone_id = (
            f"IMAGE#{line.image_id}#"
            f"RECEIPT#{line.receipt_id:05d}#"
            f"LINE#{line.line_id:05d}"
        )
        entry = {
            "custom_id": pinecone_id,
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "input": line.text,
                "model": "text-embedding-3-small",
            },
        }
        inputs.append(entry)
    return inputs


def serialize_receipt_lines(
    line_receipt_dict: dict[str, dict[int, list[ReceiptLine]]],
) -> list[dict]:
    """
    Serialize ReceiptLines into per-receipt NDJSON files.
    """
    results: list[dict] = []
    for image_id, receipts in line_receipt_dict.items():
        for receipt_id, lines in receipts.items():
            # Serialize each line as JSON
            ndjson_lines = [json.dumps(dict(line)) for line in lines]
            ndjson_content = "\n".join(ndjson_lines)
            # Write to a unique NDJSON file
            filepath = Path(
                f"/tmp/{image_id}_{receipt_id}_lines_{uuid4()}.ndjson"
            )
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


def upload_serialized_lines(
    serialized_lines: list[dict], s3_bucket: str, prefix="line_embeddings"
) -> list[dict]:
    """Upload the serialized lines to S3."""
    s3 = boto3.client("s3")
    for receipt_dict in serialized_lines:
        key = f"{prefix}/{Path(receipt_dict['ndjson_path']).name}"
        s3.upload_file(
            str(receipt_dict["ndjson_path"]),
            s3_bucket,
            key,
        )
        receipt_dict["s3_key"] = key
        receipt_dict["s3_bucket"] = s3_bucket
    return serialized_lines


def download_serialized_lines(s3_bucket: str, s3_key: str) -> Path:
    """Download the serialized lines from S3."""
    s3 = boto3.client("s3")
    s3.download_file(
        s3_bucket,
        s3_key,
        f"/tmp/{Path(s3_key).name}",
    )
    return Path(f"/tmp/{Path(s3_key).name}")


def deserialize_receipt_lines(filepath: Path) -> list[ReceiptLine]:
    """Deserialize an NDJSON file containing serialized ReceiptLines."""
    lines = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            lines.append(ReceiptLine(**json.loads(line)))
    return lines


def query_receipt_lines(
    image_id: str, receipt_id: int, client_manager: ClientManager = None
) -> list[ReceiptLine]:
    """Query the ReceiptLines from DynamoDB."""
    if client_manager is None:
        client_manager = get_client_manager()
    _, lines, _, _, _, _ = client_manager.dynamo.get_receipt_details(
        image_id, receipt_id
    )
    return lines


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
    line_count = 0

    # 2) Read and parse each line of the NDJSON file
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line_count += 1
            try:
                obj = json.loads(line)
                custom_id = obj.get("custom_id", "")
                parts = custom_id.split("#")
                # parts: ["IMAGE", image_id, "RECEIPT", receipt_id,
                #         "LINE", line_id]
                image_id = parts[1]
                receipt_id = int(parts[3])
                receipt_refs.add((image_id, receipt_id))
            except Exception:  # pylint: disable=broad-exception-caught
                continue

    # 3) Build and return the BatchSummary
    return BatchSummary(
        batch_id=batch_id,
        batch_type="LINE_EMBEDDING",
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


def update_line_embedding_status(
    lines: list[ReceiptLine], client_manager: ClientManager = None
) -> None:
    """Update the Embedding Status of the Lines"""
    if client_manager is None:
        client_manager = get_client_manager()
    for line in lines:
        # Set to the string value so GSI1PK is updated correctly
        line.embedding_status = EmbeddingStatus.PENDING.value
    client_manager.dynamo.update_receipt_lines(lines)
