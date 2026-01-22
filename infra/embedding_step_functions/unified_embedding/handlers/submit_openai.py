"""Handler for submitting line embedding batches to OpenAI.

This handler reads from S3, formats the data using receipt_chroma,
and submits to OpenAI's Batch API.

Uses row-based visual grouping: lines that appear on the same visual row
(based on vertical overlap) are grouped together into a single embedding.
The embedding includes context from the row above and row below.
"""

import os
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from embedding_ingest import (  # pylint: disable=import-error
    deserialize_receipt_lines,
    download_serialized_file,
    query_receipt_lines,
    set_pending_and_update_lines,
    write_ndjson,
)
from openai import OpenAI
from receipt_chroma.embedding.formatting.line_format import (
    get_row_embedding_inputs,
)
from receipt_chroma.embedding.openai import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)
from receipt_dynamo.data.dynamo_client import DynamoClient

import utils.logging  # pylint: disable=import-error
from utils.env_vars import get_required_env  # pylint: disable=import-error

get_logger = utils.logging.get_logger
get_operation_logger = utils.logging.get_operation_logger

logger = get_operation_logger(__name__)


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # pylint: disable=unused-argument
    """Submit a line embedding batch to OpenAI's Batch API.

    Args:
        event: Contains s3_key, s3_bucket
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
        filepath = download_serialized_file(s3_bucket=s3_bucket, s3_key=s3_key)
        logger.info("Downloaded file", filepath=filepath)

        # Deserialize the lines
        lines_to_embed = deserialize_receipt_lines(filepath)
        logger.info("Deserialized lines", count=len(lines_to_embed))

        # Get image_id and receipt_id from first line (all lines are from same receipt)
        if not lines_to_embed:
            raise ValueError("No lines to embed")
        image_id = lines_to_embed[0].image_id
        receipt_id = lines_to_embed[0].receipt_id

        dynamo_client = DynamoClient(get_required_env("DYNAMODB_TABLE_NAME"))

        # Query all lines in the receipt for context
        all_lines_in_receipt = query_receipt_lines(
            dynamo_client, image_id, receipt_id
        )
        logger.info(
            "Found lines in receipt",
            count=len(all_lines_in_receipt),
            receipt_id=receipt_id,
            image_id=image_id,
        )

        # Build set of line_ids that need embeddings for quick lookup
        lines_to_embed_ids = {line.line_id for line in lines_to_embed}

        # Group all lines into visual rows and generate row-based embedding inputs
        # Each row includes context from rows above and below
        row_inputs = get_row_embedding_inputs(all_lines_in_receipt)

        # Format rows with context for embedding
        # Only create entries for rows that contain at least one line needing embedding
        formatted_lines = []
        rows_processed = 0
        # Track ALL line IDs in processed rows (for consistent PENDING status)
        all_line_ids_in_processed_rows: set[int] = set()

        for embedding_input, row_line_ids in row_inputs:
            # Check if any line in this row needs embedding
            if not any(lid in lines_to_embed_ids for lid in row_line_ids):
                continue

            # Track all lines in this row - they'll all be marked SUCCESS when
            # the embedding completes, so mark them all PENDING now
            all_line_ids_in_processed_rows.update(row_line_ids)

            # Use the primary (first/leftmost) line's ID for the custom_id
            primary_line_id = row_line_ids[0]

            # Build custom_id (ChromaDB format) - uses primary line ID
            custom_id = (
                f"IMAGE#{image_id}#"
                f"RECEIPT#{receipt_id:05d}#"
                f"LINE#{primary_line_id:05d}"
            )

            # Format as OpenAI batch API request
            entry = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/embeddings",
                "body": {
                    "input": embedding_input,
                    "model": "text-embedding-3-small",
                },
            }
            formatted_lines.append(entry)
            rows_processed += 1

        logger.info(
            "Formatted visual rows with context",
            rows_processed=rows_processed,
            total_lines=len(lines_to_embed),
            lines_in_processed_rows=len(all_line_ids_in_processed_rows),
        )

        # Write formatted data to NDJSON file
        input_file = Path(f"/tmp/{batch_id}.ndjson")
        write_ndjson(input_file, formatted_lines)
        logger.info("Wrote input file", filepath=str(input_file))

        # Initialize clients
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

        # Update line embedding status in DynamoDB for ALL lines in processed rows
        # This ensures consistent status transitions: all lines in a visual row
        # go NONE -> PENDING -> SUCCESS together (since line_polling marks all
        # lines in a row as SUCCESS when the embedding completes)
        lines_to_mark_pending = [
            line
            for line in all_lines_in_receipt
            if line.line_id in all_line_ids_in_processed_rows
        ]
        set_pending_and_update_lines(dynamo_client, lines_to_mark_pending)
        logger.info(
            "Updated embedding status for lines",
            lines_marked_pending=len(lines_to_mark_pending),
            original_batch_size=len(lines_to_embed),
        )

        # Save batch summary to database
        add_batch_summary(batch_summary, dynamo_client)
        logger.info("Added batch summary", batch_id=batch_summary.batch_id)

        return {"batch_id": batch_id}

    except Exception as e:
        logger.error("Error submitting to OpenAI", error=str(e))
        raise RuntimeError(f"Error submitting to OpenAI: {str(e)}") from e
