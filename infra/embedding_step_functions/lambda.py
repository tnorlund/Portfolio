import os
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.embedding.line import (
    add_batch_summary,
    chunk_into_line_embedding_batches,
    create_batch_summary,
    deserialize_receipt_lines,
    download_openai_batch_result,
    download_serialized_lines,
    format_line_context_embedding,
    generate_batch_id,
    get_openai_batch_status,
    get_receipt_descriptions,
    list_pending_line_embedding_batches,
    list_receipt_lines_with_no_embeddings,
    mark_batch_complete,
    serialize_receipt_lines,
    submit_openai_batch,
    update_line_embedding_status,
    update_line_embedding_status_to_success,
    upload_serialized_lines,
    upload_to_openai,
    upsert_line_embeddings_to_pinecone,
    write_ndjson,
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


def embedding_submit_list_handler(_event, _context):
    """
    This function is used to prepare the embedding batch for the line embedding
    step function.
    """
    logger.info("Starting embedding_submit_list_handler")
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


def embedding_submit_upload_handler(event, _context):
    """
    This function is used to submit the embedding batch for the line embedding
    step function.
    """
    logger.info("Starting embedding_submit_upload_handler")
    s3_key = event["s3_key"]
    s3_bucket = event["s3_bucket"]
    # These are passed in event but not used in current implementation
    # image_id = event["image_id"]
    # receipt_id = int(event["receipt_id"])
    batch_id = generate_batch_id()

    # Download the serialized lines
    filepath = download_serialized_lines(s3_bucket=s3_bucket, s3_key=s3_key)
    logger.info("Downloaded file to %s", filepath)

    lines = deserialize_receipt_lines(filepath)
    logger.info("Deserialized %d lines", len(lines))

    # Develop the embedding input
    formatted = format_line_context_embedding(lines)
    logger.info("Formatted %d lines", len(formatted))

    # Write the formatted lines to a file
    input_file = write_ndjson(batch_id, formatted)
    logger.info("Wrote input file to %s", input_file)

    # Upload the input file to OpenAI
    openai_file = upload_to_openai(input_file)
    logger.info("Uploaded input file to OpenAI")

    # Submit the OpenAI batch
    openai_batch = submit_openai_batch(openai_file.id)
    logger.info("Submitted OpenAI batch %s", openai_batch.id)

    # Create a batch summary
    batch_summary = create_batch_summary(batch_id, openai_batch.id, input_file)
    logger.info("Created batch summary with ID %s", batch_summary.batch_id)

    # Update the line embedding status
    update_line_embedding_status(lines)
    logger.info("Updated line embedding status")

    # Add the batch summary to the database
    add_batch_summary(batch_summary)
    logger.info("Added batch summary with ID %s", batch_summary.batch_id)

    return {"statusCode": 200, "batch_id": batch_id}


def embedding_poll_list_handler(_event, _context):
    """
    This function is used to poll the embedding batch for the line embedding
    step function.
    """
    logger.info("Starting embedding_poll_list_handler")
    pending_batches = list_pending_line_embedding_batches()
    logger.info("Found %d pending batches", len(pending_batches))
    return {
        "statusCode": 200,
        "batches": [
            {
                "openai_batch_id": batch.openai_batch_id,
                "batch_id": batch.batch_id,
            }
            # TODO: Remove this once we have a proper polling mechanism  # pylint: disable=fixme
            for batch in pending_batches[0:5]
        ],
    }


def embedding_poll_download_handler(batch, _context):
    """
    This function is used to download the embedding batch for the line
    embedding step function.
    """
    logger.info("Starting embedding_poll_download_handler")
    batch_id = batch["batch_id"]
    openai_batch_id = batch["openai_batch_id"]
    if get_openai_batch_status(openai_batch_id) == "completed":
        logger.info("OpenAI batch %s has completed", openai_batch_id)
        results = download_openai_batch_result(openai_batch_id)
        logger.info("Downloaded %d results", len(results))
        descriptions = get_receipt_descriptions(results)
        logger.info("Upserting %d line embeddings to Pinecone", len(results))
        upsert_line_embeddings_to_pinecone(results, descriptions)
        logger.info("Upserted %d line embeddings to Pinecone", len(results))
        mark_batch_complete(batch_id)
        logger.info("Marked batch %s as complete", batch_id)
        update_line_embedding_status_to_success(results, descriptions)
        logger.info("Updated line embedding status to success")
        return {"statusCode": 200, "completed": True}

    logger.info("OpenAI batch %s is still pending", openai_batch_id)
    return {"statusCode": 200, "completed": False}
