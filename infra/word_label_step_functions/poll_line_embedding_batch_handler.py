from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.poll_line_embedding_batch.poll_line_batch import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    update_line_embedding_status_to_success,
    upsert_line_embeddings_to_pinecone,
    write_line_embedding_results_to_dynamo,
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


def poll_handler(event, context):
    """
    This function is used to poll an embedding batch for the step function.

    Args:
        event: The event object from the step function.
        context: The context object from the step function.

    Returns:
        A dictionary containing the status code and the batch status.
    """
    logger.info("Starting poll_line_embedding_batch_handler")
    logger.info(f"Event: {event}")

    batch_id = event["batch_id"]
    openai_batch_id = event["openai_batch_id"]

    # Check the batch status
    batch_status = get_openai_batch_status(openai_batch_id)
    logger.info(f"Batch {openai_batch_id} status: {batch_status}")

    if batch_status == "completed":
        # Download the batch results
        results = download_openai_batch_result(openai_batch_id)
        logger.info(f"Downloaded {len(results)} embedding results")

        # Get receipt details for all involved receipts
        descriptions = get_receipt_descriptions(results)
        logger.info(f"Retrieved details for {len(descriptions)} receipts")

        # Upsert to Pinecone
        upserted = upsert_line_embeddings_to_pinecone(results, descriptions)
        logger.info(f"Upserted {upserted} embeddings to Pinecone")

        # Write results to DynamoDB
        written = write_line_embedding_results_to_dynamo(
            results, descriptions, batch_id
        )
        logger.info(f"Wrote {written} embedding results to DynamoDB")

        # Update the embedding status of the lines
        update_line_embedding_status_to_success(results, descriptions)
        logger.info("Updated line embedding status to SUCCESS")

        # Mark the batch as complete
        mark_batch_complete(batch_id)
        logger.info(f"Marked batch {batch_id} as complete")

        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "results_count": len(results),
            "upserted_count": upserted,
        }

    return {
        "statusCode": 200,
        "batch_id": batch_id,
        "openai_batch_id": openai_batch_id,
        "batch_status": batch_status,
    }
