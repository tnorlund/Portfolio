"""Simple Lambda handler for finding items that need embeddings.

This is a lightweight, zip-based Lambda function that reads from DynamoDB
and writes to S3. No container overhead needed.
"""

import os
import logging
from typing import Any, Dict
from receipt_label.embedding.line import (
    chunk_into_line_embedding_batches,
    list_receipt_lines_with_no_embeddings,
    serialize_receipt_lines,
    upload_serialized_lines,
)

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


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
