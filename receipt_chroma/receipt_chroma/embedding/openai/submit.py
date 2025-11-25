"""OpenAI batch submission functions.

This module provides functions for uploading files to OpenAI and submitting
batch embedding jobs.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from openai import OpenAI
from openai.types.batch import Batch
from openai.types.file_object import FileObject
from receipt_dynamo.constants import BatchType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import BatchSummary

logger = logging.getLogger(__name__)


def upload_to_openai(filepath: Path, openai_client: OpenAI) -> FileObject:
    """
    Upload the NDJSON file to OpenAI.

    Args:
        filepath: Path to the NDJSON file to upload
        openai_client: OpenAI client instance

    Returns:
        FileObject from OpenAI API
    """
    return openai_client.files.create(
        file=filepath.open("rb"), purpose="batch"
    )


def submit_openai_batch(file_id: str, openai_client: OpenAI) -> Batch:
    """
    Submit a batch embedding job to OpenAI using the uploaded file.

    Args:
        file_id: OpenAI file ID from upload_to_openai
        openai_client: OpenAI client instance

    Returns:
        Batch object from OpenAI API
    """
    return openai_client.batches.create(
        input_file_id=file_id,
        endpoint="/v1/embeddings",
        completion_window="24h",
        metadata={"model": "text-embedding-3-small"},
    )


def create_batch_summary(
    batch_id: str,
    openai_batch_id: str,
    file_path: str,
    batch_type: Literal["LINE_EMBEDDING", "WORD_EMBEDDING"],
) -> BatchSummary:
    """
    Construct a BatchSummary for the submitted embedding batch using the
    NDJSON file.

    Args:
        batch_id: Internal batch identifier
        openai_batch_id: OpenAI batch identifier
        file_path: Path to the NDJSON file
        batch_type: Type of batch (LINE_EMBEDDING or WORD_EMBEDDING)

    Returns:
        BatchSummary entity
    """
    # Initialize counters and refs
    receipt_refs: set[tuple[str, int]] = set()
    item_count = 0

    # Read and parse each line of the NDJSON file
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            item_count += 1
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

    # Build and return the BatchSummary
    return BatchSummary(
        batch_id=batch_id,
        batch_type=batch_type,
        openai_batch_id=openai_batch_id,
        submitted_at=datetime.now(timezone.utc),
        status="PENDING",
        result_file_id="N/A",
        receipt_refs=list(receipt_refs),
    )


def add_batch_summary(
    summary: BatchSummary, dynamo_client: DynamoClient
) -> None:
    """
    Write the BatchSummary entity to DynamoDB.

    Args:
        summary: BatchSummary entity to write
        dynamo_client: DynamoDB client instance
    """
    dynamo_client.add_batch_summary(summary)
