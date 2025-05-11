import os
from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_label.embedding.line import (
    list_receipt_lines_with_no_embeddings,
    chunk_into_line_embedding_batches,
    serialize_receipt_lines,
    upload_serialized_lines,
    list_pending_line_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    get_receipt_descriptions,
    upsert_line_embeddings_to_pinecone,
    write_line_embedding_results_to_dynamo,
    mark_batch_complete,
    download_serialized_lines,
    deserialize_receipt_lines,
    format_line_context_embedding,
    write_ndjson,
    upload_to_openai,
    submit_openai_batch,
    create_batch_summary,
    add_batch_summary,
    update_line_embedding_status,
    generate_batch_id,
    update_line_embedding_status_to_success,
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


def embedding_submit_list_handler(event, context):
    """
    This function is used to prepare the embedding batch for the line embedding
    step function.
    """
    logger.info("Starting embedding_submit_list_handler")
    lines_without_embeddings = list_receipt_lines_with_no_embeddings()
    logger.info(f"Found {len(lines_without_embeddings)} lines without embeddings")
    batches = chunk_into_line_embedding_batches(lines_without_embeddings)
    logger.info(f"Chunked into {len(batches)} batches")
    uploaded = upload_serialized_lines(serialize_receipt_lines(batches), bucket)
    logger.info(f"Uploaded {len(uploaded)} files")
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


def embedding_submit_upload_handler(event, context):
    """
    This function is used to submit the embedding batch for the line embedding
    step function.
    """
    logger.info("Starting embedding_submit_upload_handler")
    s3_key = event["s3_key"]
    s3_bucket = event["s3_bucket"]
    image_id = event["image_id"]
    receipt_id = int(event["receipt_id"])
    batch_id = generate_batch_id()

    # Download the serialized lines
    filepath = download_serialized_lines(s3_bucket=s3_bucket, s3_key=s3_key)
    logger.info(f"Downloaded file to {filepath}")

    lines = deserialize_receipt_lines(filepath)
    logger.info(f"Deserialized {len(lines)} lines")

    # Develop the embedding input
    formatted = format_line_context_embedding(lines)
    logger.info(f"Formatted {len(formatted)} lines")

    # Write the formatted lines to a file
    input_file = write_ndjson(batch_id, formatted)
    logger.info(f"Wrote input file to {input_file}")

    # Upload the input file to OpenAI
    openai_file = upload_to_openai(input_file)
    logger.info(f"Uploaded input file to OpenAI")

    # Submit the OpenAI batch
    openai_batch = submit_openai_batch(openai_file.id)
    logger.info(f"Submitted OpenAI batch {openai_batch.id}")

    # Create a batch summary
    batch_summary = create_batch_summary(batch_id, openai_batch.id, input_file)
    logger.info(f"Created batch summary with ID {batch_summary.batch_id}")

    # Update the line embedding status
    update_line_embedding_status(lines)
    logger.info(f"Updated line embedding status")

    # Add the batch summary to the database
    add_batch_summary(batch_summary)
    logger.info(f"Added batch summary with ID {batch_summary.batch_id}")

    return {"statusCode": 200, "batch_id": batch_id}


def embedding_poll_list_handler(event, context):
    """
    This function is used to poll the embedding batch for the line embedding
    step function.
    """
    logger.info("Starting embedding_poll_list_handler")
    pending_batches = list_pending_line_embedding_batches()
    logger.info(f"Found {len(pending_batches)} pending batches")
    return {
        "statusCode": 200,
        "batches": [
            {"openai_batch_id": batch.openai_batch_id, "batch_id": batch.batch_id}
            for batch in pending_batches
        ],
    }


def embedding_poll_download_handler(batch, context):
    """
    This function is used to download the embedding batch for the line embedding
    step function.
    """
    logger.info("Starting embedding_poll_download_handler")
    batch_id = batch["batch_id"]
    openai_batch_id = batch["openai_batch_id"]
    if get_openai_batch_status(openai_batch_id) == "completed":
        logger.info(f"OpenAI batch {openai_batch_id} has completed")
        results = download_openai_batch_result(openai_batch_id)
        logger.info(f"Downloaded {len(results)} results")
        descriptions = get_receipt_descriptions(results)
        logger.info(f"Upserting {len(results)} line embeddings to Pinecone")
        upsert_line_embeddings_to_pinecone(results, descriptions)
        logger.info(f"Upserted {len(results)} line embeddings to Pinecone")
        mark_batch_complete(batch_id)
        logger.info(f"Marked batch {batch_id} as complete")
        update_line_embedding_status_to_success(results, descriptions)
        logger.info(f"Updated line embedding status to success")
        return {"statusCode": 200, "completed": True}
    else:
        logger.info(f"OpenAI batch {openai_batch_id} is still pending")
        return {"statusCode": 200, "completed": False}
