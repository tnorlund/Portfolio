"""Simple Lambda handler for finding words that need embeddings.

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
from receipt_dynamo.entities.receipt_word import ReceiptWord

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def list_receipt_words_with_no_embeddings() -> list[ReceiptWord]:
    """Fetch all ReceiptWord items with embedding_status == NONE and is_noise == False."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")

    dynamo_client = DynamoClient(table_name)
    all_words = dynamo_client.list_receipt_words_by_embedding_status(
        EmbeddingStatus.NONE
    )
    # Filter out noise words
    return [word for word in all_words if not word.is_noise]


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
    words_by_image: dict[str, dict[int, dict[tuple[int, int], ReceiptWord]]] = {}
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


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    """Find receipt words without embeddings and prepare batches.

    Args:
        event: Lambda event (unused in current implementation)
        context: Lambda context (unused)

    Returns:
        Dictionary containing batches ready for processing

    Raises:
        RuntimeError: If there's an error processing
    """
    logger.info("Starting find_unembedded_words handler")

    try:
        # Get S3 bucket from environment
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise ValueError("S3_BUCKET environment variable not set")

        logger.info("Using S3 bucket: %s", bucket)

        # Get words without embeddings (noise words are already filtered)
        words = list_receipt_words_with_no_embeddings()
        logger.info(
            "Found %d words without embeddings (noise words filtered)",
            len(words),
        )

        if not words:
            logger.info("No words need embeddings")
            return {"batches": []}

        # Chunk words into batches (returns nested dict structure)
        batches = chunk_into_embedding_batches(words)
        logger.info("Chunked into %d batches", len(batches))

        # Log batch details for debugging
        for image_id, receipts in batches.items():
            for receipt_id, words_list in receipts.items():
                total = len(words_list)
                unique = len({(w.line_id, w.word_id) for w in words_list})
                if total != unique:
                    logger.warning(
                        "Duplicate words in image %s, receipt %s: "
                        "total %d, unique %d",
                        image_id,
                        receipt_id,
                        total,
                        unique,
                    )
                else:
                    logger.info(
                        "Words count OK for image %s, receipt %s: %d words",
                        image_id,
                        receipt_id,
                        total,
                    )

        # Serialize and upload in one step
        uploaded = upload_serialized_words(serialize_receipt_words(batches), bucket)
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

        logger.info("Successfully prepared %d batches for processing", len(cleaned))

        return {
            "batches": cleaned,
            "total_words": len(words),
            "batch_count": len(cleaned),
        }

    except AttributeError as e:
        logger.error("Client manager configuration error: %s", str(e))
        raise RuntimeError(f"Configuration error: {str(e)}") from e

    except KeyError as e:
        logger.error("Missing expected field in data: %s", str(e))
        raise RuntimeError(f"Data format error: {str(e)}") from e

    except Exception as e:
        logger.error("Unexpected error finding unembedded words: %s", str(e))
        raise RuntimeError(f"Internal error: {str(e)}") from e
