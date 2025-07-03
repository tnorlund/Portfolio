import os
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.submit_line_embedding_batch.submit_line_batch import (
    chunk_into_line_embedding_batches,
    generate_batch_id,
    list_receipt_lines_with_no_embeddings,
    serialize_receipt_lines,
    upload_serialized_lines,
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
    This function is used to prepare the line embedding batch for the step function.

    Args:
        event: The event object from the step function.
        context: The context object from the step function.

    Returns:
        A dictionary containing the status code and the batches.
    """
    logger.info("Starting prepare_line_embedding_batch_handler")
    lines_without_embeddings = list_receipt_lines_with_no_embeddings()
    logger.info(
        f"Found {len(lines_without_embeddings)} lines without embeddings"
    )
    batches = chunk_into_line_embedding_batches(lines_without_embeddings)
    logger.info(f"Chunked into {len(batches)} batches")

    # Debugging logs for chunked batches
    for image_id, receipts in batches.items():
        for receipt_id, lines in receipts.items():
            total = len(lines)
            unique = len({line.line_id for line in lines})
            if total != unique:
                logger.warning(
                    f"Duplicate lines in image {image_id}, receipt {receipt_id}: total {total}, unique {unique}"
                )
            else:
                logger.info(
                    f"Lines count OK for image {image_id}, receipt {receipt_id}: {total} lines"
                )

    uploaded = upload_serialized_lines(
        serialize_receipt_lines(batches), bucket
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
