"""Handler for submitting line embedding batches to OpenAI.

This handler reads from S3, formats the data using receipt_chroma, and submits to OpenAI's Batch API.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from openai import OpenAI
from receipt_chroma.embedding.formatting.line_format import (
    format_line_context_embedding_input,
)
from receipt_chroma.embedding.openai import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from embedding_ingest import (
    deserialize_receipt_lines,
    download_serialized_file,
    query_receipt_lines,
    set_pending_and_update_lines,
    write_ndjson,
)

import utils.logging

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Submit a line embedding batch to OpenAI's Batch API.

    Args:
        event: Contains s3_key, s3_bucket, image_id, receipt_id
        context: Lambda context (unused)

    Returns:
        Dictionary containing batch_id

    Raises:
        RuntimeError: If submission fails
    """
    logger.info("Starting submit_to_openai handler")

    try:
        # Extract parameters from event
        s3_key = event["s3_key"]
        s3_bucket = event["s3_bucket"]
        batch_id = str(uuid4())

        # Download the serialized lines from S3
        filepath = download_serialized_file(
            s3_bucket=s3_bucket, s3_key=s3_key
        )
        logger.info("Downloaded file", filepath=filepath)

        # Deserialize the lines
        lines_to_embed = deserialize_receipt_lines(filepath)
        logger.info("Deserialized lines", count=len(lines_to_embed))

        # Get image_id and receipt_id from first line (all lines are from same receipt)
        if not lines_to_embed:
            raise ValueError("No lines to embed")
        image_id = lines_to_embed[0].image_id
        receipt_id = lines_to_embed[0].receipt_id

        # Query all lines in the receipt for context
        all_lines_in_receipt = query_receipt_lines(image_id, receipt_id)
        logger.info(
            "Found lines in receipt",
            count=len(all_lines_in_receipt),
            receipt_id=receipt_id,
            image_id=image_id,
        )

        # Format lines with context for embedding using receipt_chroma
        formatted_lines = []
        for line in lines_to_embed:
            # Use receipt_chroma's format_line_context_embedding_input
            formatted_input = format_line_context_embedding_input(
                target_line=line,
                all_lines=all_lines_in_receipt,
            )

            # Build custom_id (ChromaDB format)
            custom_id = (
                f"IMAGE#{line.image_id}#"
                f"RECEIPT#{line.receipt_id:05d}#"
                f"LINE#{line.line_id:05d}"
            )

            # Format as OpenAI batch API request
            entry = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/embeddings",
                "body": {
                    "input": formatted_input,
                    "model": "text-embedding-3-small",
                },
            }
            formatted_lines.append(entry)

        logger.info("Formatted lines with context", count=len(formatted_lines))

        # Write formatted data to NDJSON file
        input_file = Path(f"/tmp/{batch_id}.ndjson")
        write_ndjson(input_file, formatted_lines)
        logger.info("Wrote input file", filepath=str(input_file))

        # Initialize clients
        dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))
        openai_client = OpenAI()

        # Upload to OpenAI
        openai_file = upload_to_openai(Path(input_file), openai_client)
        logger.info("Uploaded input file to OpenAI", file_id=openai_file.id)

        # Submit the batch
        openai_batch = submit_openai_batch(openai_file.id, openai_client)
        logger.info("Submitted OpenAI batch", batch_id=openai_batch.id)

        # Create and save batch summary
        batch_summary = create_batch_summary(
            batch_id=batch_id,
            openai_batch_id=openai_batch.id,
            file_path=str(input_file),
            batch_type="LINE_EMBEDDING",
        )
        logger.info("Created batch summary", batch_id=batch_summary.batch_id)

        # Update line embedding status in DynamoDB
        set_pending_and_update_lines(dynamo_client, lines_to_embed)
        logger.info(
            "Updated embedding status for lines", count=len(lines_to_embed)
        )

        # Save batch summary to database
        add_batch_summary(batch_summary, dynamo_client)
        logger.info("Added batch summary", batch_id=batch_summary.batch_id)

        return {"batch_id": batch_id}

    except Exception as e:
        logger.error("Error submitting to OpenAI", error=str(e))
        raise RuntimeError(f"Error submitting to OpenAI: {str(e)}") from e
