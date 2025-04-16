from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_label.submit_embedding_batch import (
    upload_ndjson_file,
    submit_openai_batch,
    create_batch_summary,
    add_batch_summary,
    update_receipt_word_labels,
    fetch_original_receipt_word_labels,
    download_from_s3,
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


def submit_handler(event, context):

    logger.info("Starting submit_embedding_batch_handler")
    if "batch_id" not in event:
        raise ValueError("batch_id is required")
    if "s3_bucket" not in event:
        raise ValueError("s3_bucket is required")
    if "s3_key" not in event:
        raise ValueError("s3_key is required")
    batch_id = event["batch_id"]
    s3_bucket = event["s3_bucket"]
    s3_key = event["s3_key"]

    local_path = download_from_s3(s3_key, s3_bucket)
    logger.info(f"Downloaded file to {local_path}")

    file_object = upload_ndjson_file(local_path)
    logger.info("Uploaded file to OpenAI")

    batch = submit_openai_batch(file_object.id)
    logger.info("Submitted batch to OpenAI")

    batch_summary = create_batch_summary(
        batch_id=batch_id,
        open_ai_batch_id=batch.id,
        file_path=local_path,
    )
    logger.info(f"Batch summary: {batch_summary}")

    add_batch_summary(batch_summary)
    logger.info("Added batch summary to DynamoDB")

    original_receipt_word_labels = fetch_original_receipt_word_labels(
        local_path
    )
    logger.info(
        f"Original receipt word labels: {original_receipt_word_labels}"
    )

    for rwl in original_receipt_word_labels:
        rwl.validation_status = "PENDING"
    update_receipt_word_labels(original_receipt_word_labels)
    logger.info("Updated receipt word labels in DynamoDB")

    return {"statusCode": 200, "batch_id": batch_id}
