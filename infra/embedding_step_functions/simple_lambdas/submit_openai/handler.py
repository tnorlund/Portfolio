"""Simple Lambda handler for submitting batches to OpenAI.

This is a lightweight, zip-based Lambda function that reads from S3,
calls OpenAI API, and writes to DynamoDB. No container overhead needed.
"""

import logging
from typing import Any, Dict
from receipt_label.embedding.line import (
    add_batch_summary,
    create_batch_summary,
    deserialize_receipt_lines,
    download_serialized_lines,
    format_line_context_embedding,
    generate_batch_id,
    submit_openai_batch,
    update_line_embedding_status,
    upload_to_openai,
    write_ndjson,
)

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
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
        logger.info("Downloaded file to %s", filepath)

        # Deserialize the lines
        lines = deserialize_receipt_lines(filepath)
        logger.info("Deserialized %d lines", len(lines))

        # Format for embedding
        formatted = format_line_context_embedding(lines)
        logger.info("Formatted %d lines", len(formatted))

        # Write to NDJSON file
        input_file = write_ndjson(batch_id, formatted)
        logger.info("Wrote input file to %s", input_file)

        # Upload to OpenAI
        openai_file = upload_to_openai(input_file)
        logger.info("Uploaded input file to OpenAI")

        # Submit the batch
        openai_batch = submit_openai_batch(openai_file.id)
        logger.info("Submitted OpenAI batch %s", openai_batch.id)

        # Create and save batch summary
        batch_summary = create_batch_summary(
            batch_id, openai_batch.id, input_file
        )
        logger.info("Created batch summary with ID %s", batch_summary.batch_id)

        # Update line statuses
        update_line_embedding_status(lines)
        logger.info("Updated line embedding status")

        # Save batch summary to database
        add_batch_summary(batch_summary)
        logger.info("Added batch summary with ID %s", batch_summary.batch_id)

        return {"batch_id": batch_id}

    except Exception as e:
        logger.error("Error submitting to OpenAI: %s", str(e))
        raise RuntimeError(f"Error submitting to OpenAI: {str(e)}") from e
