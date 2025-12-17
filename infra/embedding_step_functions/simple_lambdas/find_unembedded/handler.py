"""Simple Lambda handler for finding items that need embeddings.

This is a lightweight, zip-based Lambda function that reads from DynamoDB
and writes to S3. No container overhead needed.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

import boto3

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_line import ReceiptLine

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def list_receipt_lines_with_no_embeddings() -> list[ReceiptLine]:
    """Fetch all ReceiptLine items with embedding_status == NONE and is_noise == False."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo_client = DynamoClient(table_name)
    all_lines = dynamo_client.list_receipt_lines_by_embedding_status(
        EmbeddingStatus.NONE
    )
    # Filter out noise lines
    return [line for line in all_lines if not line.is_noise]


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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Find receipt lines without embeddings and prepare batches.

    Args:
        event: Lambda event (unused in current implementation)
        context: Lambda context (unused)

    Returns:
        Dictionary containing batches ready for processing

    Raises:
        RuntimeError: If there's an error processing
    """
    logger.info("Starting find_unembedded_lines handler")

    try:
        # Get S3 bucket from environment
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable not set")

        logger.info("Using S3 bucket: %s", bucket)

        # Get lines without embeddings
        lines = list_receipt_lines_with_no_embeddings()
        logger.info("Found %d lines without embeddings", len(lines))

        if not lines:
            logger.info("No lines need embeddings")
            return {"batches": []}

        # Chunk lines into batches (returns nested dict structure)
        batches = chunk_into_line_embedding_batches(lines)
        logger.info("Chunked into %d batches", len(batches))

        # Serialize and upload in one step (like the working version)
        uploaded = upload_serialized_lines(
            serialize_receipt_lines(batches), bucket
        )
        logger.info("Uploaded %d files", len(uploaded))

        # Clean the output to match expected format
        cleaned = [
            {
                "s3_key": e["s3_key"],
                "s3_bucket": e["s3_bucket"],
                "image_id": e["image_id"],
                "receipt_id": e["receipt_id"],
            }
            for e in uploaded
        ]

        logger.info(
            "Successfully prepared %d batches for processing", len(cleaned)
        )

        return {
            "batches": cleaned,
            "total_lines": len(lines),
            "batch_count": len(cleaned),
        }

    except AttributeError as e:
        logger.error("Client manager configuration error: %s", str(e))
        raise RuntimeError(f"Configuration error: {str(e)}") from e

    except KeyError as e:
        logger.error("Missing expected field in data: %s", str(e))
        raise RuntimeError(f"Data format error: {str(e)}") from e

    except Exception as e:
        logger.error("Unexpected error finding unembedded lines: %s", str(e))
        raise RuntimeError(f"Internal error: {str(e)}") from e
