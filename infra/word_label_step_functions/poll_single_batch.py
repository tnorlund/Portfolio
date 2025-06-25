import json
from logging import INFO, Formatter, StreamHandler, getLogger

import pulumi
from receipt_label.embedding.word import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    list_pending_embedding_batches,
    mark_batch_complete,
    upsert_embeddings_to_pinecone,
    write_embedding_results_to_dynamo,
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

        downloaded_results = download_openai_batch_result(openai_batch_id)
        pulumi.log.info(f"Got {len(downloaded_results)} results")

        receipt_descriptions = get_receipt_descriptions(downloaded_results)
        pulumi.log.info(
            f"Got {len(receipt_descriptions)} receipt descriptions"
        )

        upserted_vectors_count = upsert_embeddings_to_pinecone(
            downloaded_results, receipt_descriptions
        )
        pulumi.log.info(
            f"Upserted {upserted_vectors_count} vectors to Pinecone"
        )

        embedding_results_count = write_embedding_results_to_dynamo(
            downloaded_results, receipt_descriptions, batch_id
        )
        pulumi.log.info(
            f"Wrote {embedding_results_count} embedding results to DynamoDB"
        )

        mark_batch_complete(batch_id)
        pulumi.log.info(f"Marked batch {batch_id} as complete")

        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Batch processed successfully"}),
        }
    else:
        return {
            "statusCode": 200,
            "body": json.dumps({"message": "Batch not completed"}),
        }
