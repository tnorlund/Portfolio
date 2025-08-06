"""
Lambda handler for finding receipt lines that need embeddings.

This Lambda queries DynamoDB for lines with embedding_status=NONE and
prepares them for batch submission to OpenAI.
"""

import os
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.embedding.line import (
    chunk_into_line_embedding_batches,
    list_receipt_lines_with_no_embeddings,
    serialize_receipt_lines,
    upload_serialized_lines,
)

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

bucket = os.environ["S3_BUCKET"]


def lambda_handler(_event, _context):
    """
    Find receipt lines without embeddings and prepare batches for submission.

    Returns:
        dict: Contains statusCode and batches ready for processing
    """
    logger.info("Starting find_unembedded_lines handler")

    try:
        lines_without_embeddings = list_receipt_lines_with_no_embeddings()
        logger.info(
            "Found %d lines without embeddings", len(lines_without_embeddings)
        )

        batches = chunk_into_line_embedding_batches(lines_without_embeddings)
        logger.info("Chunked into %d batches", len(batches))

        uploaded = upload_serialized_lines(
            serialize_receipt_lines(batches), bucket
        )
        logger.info("Uploaded %d files", len(uploaded))

        cleaned = [
            {
                "s3_key": e["s3_key"],
                "s3_bucket": e["s3_bucket"],
                "image_id": e["image_id"],
                "receipt_id": e["receipt_id"],
            }
            for e in uploaded
        ]

        return {"statusCode": 200, "batches": cleaned}

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error finding unembedded lines: %s", str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "batches": [],
        }
