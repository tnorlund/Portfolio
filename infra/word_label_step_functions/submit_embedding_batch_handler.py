from logging import INFO, Formatter, StreamHandler, getLogger
from pathlib import Path

from receipt_label.embedding.word import (
    add_batch_summary,
    create_batch_summary,
    deserialize_receipt_words,
    download_serialized_words,
    format_word_context_embedding,
    generate_batch_id,
    query_receipt_words,
    submit_openai_batch,
    update_word_embedding_status,
    upload_to_openai,
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


def submit_handler(event, context):
    """
    This function is used to submit an embedding batch for the word labeling
    step function.

    Args:
        event: The event object from the step function.
        context: The context object from the step function.

    Returns:
        A dictionary containing the status code and the batch ID.
    """
    logger.info("Starting submit_embedding_batch_handler")
    logger.info(f"Event: {event}")
    s3_bucket = event["s3_bucket"]
    s3_key = event["s3_key"]
    image_id = event["image_id"]
    receipt_id = event["receipt_id"]
    batch_id = generate_batch_id()

    # download the NDJSON from S3 back to local via serialized helper
    local_path = download_serialized_words(
        {
            "s3_bucket": s3_bucket,
            "s3_key": s3_key,
            # include the original ndjson path so the helper can write to it
            "ndjson_path": f"/tmp/{Path(s3_key).name}",
        }
    )
    logger.info(f"Downloaded file to {local_path}")

    deserialized_words = deserialize_receipt_words(local_path)
    logger.info(f"Deserialized {len(deserialized_words)} words")

    all_words_in_receipt = query_receipt_words(image_id, receipt_id)
    logger.info(
        f"Found {len(all_words_in_receipt)} words in receipt {receipt_id} of image {image_id}"
    )

    formatted_words = format_word_context_embedding(
        deserialized_words, all_words_in_receipt
    )
    logger.info(f"Formatted {len(formatted_words)} words")

    input_file = write_ndjson(batch_id, formatted_words)
    logger.info(f"Wrote input file to {input_file}")

    openai_file = upload_to_openai(input_file)
    logger.info(f"Uploaded input file to OpenAI")

    openai_batch = submit_openai_batch(openai_file.id)
    logger.info(f"Submitted OpenAI batch {openai_batch.id}")

    batch_summary = create_batch_summary(batch_id, openai_batch.id, input_file)
    logger.info(f"Created batch summary with ID {batch_summary.batch_id}")

    update_word_embedding_status(deserialized_words)
    logger.info(f"Updated word embedding status")

    add_batch_summary(batch_summary)
    logger.info(f"Added batch summary with ID {batch_summary.batch_id}")
    return {
        "statusCode": 200,
        "batch_id": batch_id,
        "openai_batch_id": openai_batch.id,
        "input_file": str(input_file),
        "openai_file_id": openai_file.id,
    }
