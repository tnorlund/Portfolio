import os
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.embedding.word import (
    chunk_into_embedding_batches,
    generate_batch_id,
    list_receipt_words_with_no_embeddings,
    serialize_receipt_words,
    upload_serialized_words,
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


def submit_handler(event, context):
    """
    This function is used to prepare the embedding batch for the word labeling
    step function.

    Args:
        event: The event object from the step function.
        context: The context object from the step function.

    Returns:
        A dictionary containing the status code and the batches.
    """
    logger.info("Starting prepare_embedding_batch_handler")
    words_without_embeddings = list_receipt_words_with_no_embeddings()
    logger.info(
        f"Found {len(words_without_embeddings)} words without embeddings (noise words filtered)"
    )
    batches = chunk_into_embedding_batches(words_without_embeddings)
    logger.info(f"Chunked into {len(batches)} batches")
    # Debugging logs for chunked batches
    for image_id, receipts in batches.items():
        for receipt_id, words in receipts.items():
            total = len(words)
            unique = len({(w.line_id, w.word_id) for w in words})
            if total != unique:
                logger.warning(
                    f"Duplicate words in image {image_id}, receipt {receipt_id}: total {total}, unique {unique}"
                )
            else:
                logger.info(
                    f"Words count OK for image {image_id}, receipt {receipt_id}: {total} words"
                )
    uploaded = upload_serialized_words(
        serialize_receipt_words(batches), bucket
    )
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
