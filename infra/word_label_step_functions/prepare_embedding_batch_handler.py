import os
from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_label.submit_embedding_batch import (
    list_receipt_word_labels,
    fetch_receipt_words,
    join_labels_with_words,
    chunk_joined_pairs,
    format_openai_input,
    write_ndjson,
    generate_batch_id,
    upload_to_s3,
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
    labels = list_receipt_word_labels()
    logger.info(f"Found {len(labels)} labels")
    words = fetch_receipt_words(labels)
    logger.info(f"Found {len(words)} words")
    joined = join_labels_with_words(labels, words)
    logger.info(f"Joined {len(joined)} pairs")
    batches = chunk_joined_pairs(joined, batch_size=500)
    logger.info(f"Chunks {len(batches)} batches")

    output = []
    for batch in batches:
        batch_id = generate_batch_id()
        formatted = format_openai_input(batch)
        filepath = write_ndjson(batch_id, formatted)
        s3_key = upload_to_s3(filepath, batch_id, bucket=bucket)
        output.append(
            {"batch_id": batch_id, "s3_key": s3_key, "s3_bucket": bucket}
        )

    return {"statusCode": 200, "batches": output}
