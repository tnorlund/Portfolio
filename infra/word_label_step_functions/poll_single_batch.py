from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_label.poll_embedding_batch import (
    list_pending_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    upsert_embeddings_to_pinecone,
    write_embedding_results_to_dynamo,
    mark_batch_complete,
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
    logger.info("Starting poll_single_batch_handler")
    openai_batch_id = event["openai_batch_id"]
    batch_id = event["batch_id"]
    logger.info(f"Batch ID: {batch_id}, OpenAI Batch ID: {openai_batch_id}")
    batch_status = get_openai_batch_status(openai_batch_id)
    logger.info(f"Batch status: {batch_status}")

    if batch_status == "completed":
        logger.info(f"Batch {batch_id} is completed")

        embeddings = download_openai_batch_result(openai_batch_id)
        logger.info(f"Downloaded {len(embeddings)} embeddings from OpenAI")

        upserted_count = upsert_embeddings_to_pinecone(embeddings)
        logger.info(f"Upserted {upserted_count} embeddings to Pinecone")

        # mark_batch_complete(batch_id)
        # logger.info(f"Marked batch {batch_id} as complete")
