"""Handler for submitting batches to OpenAI.

Pure business logic - no Lambda-specific code.
"""

import os
from pathlib import Path
from typing import Any, Dict

from openai import OpenAI
from receipt_chroma.embedding.openai import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.embedding.line import (
    deserialize_receipt_lines,
    download_serialized_lines,
    format_line_context_embedding,
    generate_batch_id,
    update_line_embedding_status,
    write_ndjson,
)

import utils.logging

get_logger = utils.logging.get_logger

logger = get_logger(__name__)


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
        batch_id = generate_batch_id()

        # Download the serialized lines from S3
        filepath = download_serialized_lines(
            s3_bucket=s3_bucket, s3_key=s3_key
        )
        logger.info("Downloaded file", filepath=filepath)

        # Deserialize the lines
        lines = deserialize_receipt_lines(filepath)
        logger.info("Deserialized lines", count=len(lines))

        # Format for embedding
        formatted = format_line_context_embedding(lines)
        logger.info("Formatted lines", count=len(formatted))

        # Write to NDJSON file
        input_file = write_ndjson(batch_id, formatted)
        logger.info("Wrote input file", filepath=input_file)

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
            file_path=input_file,
            batch_type="LINE_EMBEDDING",
        )
        logger.info("Created batch summary", batch_id=batch_summary.batch_id)

        # Update line statuses
        update_line_embedding_status(lines)
        logger.info("Updated line embedding status")

        # Save batch summary to database
        add_batch_summary(batch_summary, dynamo_client)
        logger.info("Added batch summary", batch_id=batch_summary.batch_id)

        return {"batch_id": batch_id}

    except Exception as e:
        logger.error("Error submitting to OpenAI", error=str(e))
        raise RuntimeError(f"Error submitting to OpenAI: {str(e)}") from e
