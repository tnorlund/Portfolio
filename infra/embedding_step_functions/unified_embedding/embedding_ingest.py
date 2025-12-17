"""
Lightweight helpers for embedding ingest Lambdas (zip-based).

These replace the receipt_label embedding helpers to avoid pulling that package
into the ingest path. They handle:
- S3 download of serialized NDJSON files
- Deserialization to ReceiptLine/ReceiptWord entities
- DynamoDB fetches for full context
- Simple NDJSON writing and batch id generation
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable, List

import boto3

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptLine, ReceiptWord


def generate_batch_id() -> str:
    """Generate a unique batch id."""
    return str(uuid.uuid4())


def download_serialized_file(*, s3_bucket: str, s3_key: str) -> Path:
    """
    Download a serialized NDJSON file from S3 to /tmp.

    Returns the local path to the downloaded file.
    """
    import tempfile

    suffix = Path(s3_key).suffix or ".ndjson"
    fd, tmp_path = tempfile.mkstemp(
        prefix="embedding-ingest-", suffix=suffix, dir="/tmp"
    )
    import os
    os.close(fd)  # Close file descriptor, let boto3 create the file
    boto3.client("s3").download_file(s3_bucket, s3_key, tmp_path)
    return Path(tmp_path)


def _deserialize_entities(filepath: Path, cls) -> List:
    """Deserialize NDJSON rows into the given entity class."""
    items: List = []
    with filepath.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                items.append(cls(**data))
            except Exception as e:
                raise ValueError(
                    f"Failed to deserialize {cls.__name__} from {filepath} "
                    f"at line {lineno}"
                ) from e
    return items


def deserialize_receipt_lines(filepath: Path) -> List[ReceiptLine]:
    """Deserialize NDJSON into ReceiptLine entities."""
    return _deserialize_entities(filepath, ReceiptLine)


def deserialize_receipt_words(filepath: Path) -> List[ReceiptWord]:
    """Deserialize NDJSON into ReceiptWord entities."""
    return _deserialize_entities(filepath, ReceiptWord)


def query_receipt_lines(
    dynamo_client: DynamoClient, image_id: str, receipt_id: int
) -> list[ReceiptLine]:
    """Fetch all lines for a receipt."""
    return dynamo_client.list_receipt_lines_from_receipt(image_id, receipt_id)


def query_receipt_words(
    dynamo_client: DynamoClient, image_id: str, receipt_id: int
) -> list[ReceiptWord]:
    """Fetch all words for a receipt."""
    return dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)


def set_pending_and_update_lines(
    dynamo_client: DynamoClient, lines: Iterable[ReceiptLine]
) -> None:
    """Set lines to PENDING and persist to Dynamo."""
    lines_list = list(lines)
    for line in lines_list:
        line.embedding_status = EmbeddingStatus.PENDING.value
    dynamo_client.update_receipt_lines(lines_list)


def set_pending_and_update_words(
    dynamo_client: DynamoClient, words: Iterable[ReceiptWord]
) -> None:
    """Set words to PENDING and persist to Dynamo."""
    words_list = list(words)
    for word in words_list:
        word.embedding_status = EmbeddingStatus.PENDING.value
    dynamo_client.update_receipt_words(words_list)


def write_ndjson(filepath: Path, rows: Iterable[dict]) -> None:
    """Write rows to an NDJSON file."""
    with filepath.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
