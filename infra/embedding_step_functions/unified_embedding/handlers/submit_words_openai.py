"""Handler for submitting word embedding batches to OpenAI.

This handler reads from S3, formats the data, and submits to OpenAI's Batch API.
"""

import os
from pathlib import Path
from typing import Any, Dict
from receipt_label.embedding.word import (
    add_batch_summary,
    create_batch_summary,
    deserialize_receipt_words,
    download_serialized_words,
    format_word_context_embedding,
    generate_batch_id,
    query_receipt_words,
    submit_openai_batch,
    update_word_embedding_status,
    upload_to_openai,
    write_ndjson,
)

import utils.logging

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
# pylint: disable=unused-argument
    """Submit a word embedding batch to OpenAI.

    This function downloads word data from S3, formats it for OpenAI's
    embedding API, and submits it as a batch job.

    Args:
        event: Lambda event containing:
            - s3_bucket: S3 bucket containing the serialized words
            - s3_key: S3 key for the serialized words file
            - image_id: ID of the image
            - receipt_id: ID of the receipt
        context: Lambda context (unused)

    Returns:
        Dictionary containing batch submission details

    Raises:
        RuntimeError: If there's an error processing
    """
    logger.info("Starting submit_words_openai handler")
    logger.info("Event", event=event)

    try:
        # Extract parameters from event
        s3_bucket = event["s3_bucket"]
        s3_key = event["s3_key"]
        image_id = event["image_id"]
        receipt_id = event["receipt_id"]

        # Generate unique batch ID
        batch_id = generate_batch_id()
        logger.info("Generated batch ID", batch_id=batch_id)

        # Download the NDJSON from S3 back to local via serialized helper
        local_path = download_serialized_words(
            {
                "s3_bucket": s3_bucket,
                "s3_key": s3_key,

                # Include the original ndjson path so the helper can write to it
                "ndjson_path": f"/tmp/{Path(s3_key).name}",
            }
        )
        logger.info("Downloaded file", local_path=local_path)

        # Deserialize the words from the downloaded file
        deserialized_words = deserialize_receipt_words(local_path)
        logger.info("Deserialized words", count=len(deserialized_words))

        # Query all words in the receipt for context
        all_words_in_receipt = query_receipt_words(image_id, receipt_id)
        logger.info(
            "Found words in receipt",
            count=len(all_words_in_receipt),
            receipt_id=receipt_id,
            image_id=image_id
        )

        # Format words with context for embedding
        formatted_words = format_word_context_embedding(
            deserialized_words, all_words_in_receipt
        )
        logger.info("Formatted words with context", count=len(formatted_words))

        # Write formatted data to NDJSON file
        input_file = write_ndjson(batch_id, formatted_words)
        logger.info("Wrote input file", filepath=input_file)

        # Upload NDJSON file to OpenAI
        openai_file = upload_to_openai(input_file)
        logger.info("Uploaded input file to OpenAI", file_id=openai_file.id)

        # Submit batch job to OpenAI
        openai_batch = submit_openai_batch(openai_file.id)
        logger.info("Submitted OpenAI batch", batch_id=openai_batch.id)

        # Create batch summary for tracking
        batch_summary = create_batch_summary(
            batch_id, openai_batch.id, input_file
        )
        logger.info("Created batch summary", batch_id=batch_summary.batch_id)

        # Update word embedding status in DynamoDB
        update_word_embedding_status(deserialized_words)
        logger.info(
            "Updated embedding status for words",
            count=len(deserialized_words)
        )

        # Store batch summary in DynamoDB
        add_batch_summary(batch_summary)
        logger.info("Added batch summary to DynamoDB")

        return {
            "batch_id": batch_id,
            "openai_batch_id": openai_batch.id,
            "input_file": str(input_file),
            "openai_file_id": openai_file.id,
            "word_count": len(deserialized_words),
            "status": "submitted",
        }

    except KeyError as e:
        logger.error("Missing required field in event", error=str(e))
        raise RuntimeError(f"Invalid event format: {str(e)}") from e

    except Exception as e:
        logger.error("Unexpected error submitting word batch", error=str(e))
        raise RuntimeError(f"Internal error: {str(e)}") from e
