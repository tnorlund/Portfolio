import os
import time
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.completion import (
    add_batch_summary,
    chunk_into_completion_batches,
    create_batch_summary,
    deserialize_labels,
    download_openai_batch_result,
    download_serialized_labels,
    format_batch_completion_file,
    generate_completion_batch_id,
    get_labels_from_ndjson,
    get_openai_batch_status,
    get_receipt_details,
    list_labels_that_need_validation,
    list_pending_completion_batches,
    merge_ndjsons,
    serialize_labels,
    split_first_and_second_pass,
    submit_openai_batch,
    update_batch_summary,
    update_invalid_labels,
    update_label_validation_status,
    update_pending_labels,
    update_valid_labels,
    upload_completion_batch_file,
    upload_serialized_labels,
    upload_to_openai,
    write_completion_batch_results,
)
from receipt_label.utils import get_clients

from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.entities import BatchSummary, ReceiptWordLabel

_, openai_client, _ = get_clients()

S3_BUCKET = os.environ["S3_BUCKET"]
BATCH_STARTED_STATUSES = ["in_progress", "finalizing", "completed"]
MAX_BATCH_TIMEOUT = float(os.getenv("MAX_BATCH_TIMEOUT", "300"))
WAIT_INTERVAL = 1  # seconds between polls

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


def submit_list_handler(event, context):
    """
    Upload the serialized labels to S3.
    """
    logger.info("Starting submit_list_handler")
    return {
        "statusCode": 200,
        "batches": upload_serialized_labels(
            serialize_labels(
                chunk_into_completion_batches(
                    list_labels_that_need_validation()
                )
            ),
            S3_BUCKET,
        ),
    }


def submit_format_handler(event, context):
    """
    Generate a batch completion file and upload it to S3.
    """
    logger.info("Starting submit_format_handler")
    image_id = event["image_id"]
    s3_key = event["s3_key"]
    s3_bucket = event["s3_bucket"]
    receipt_id = int(event["receipt_id"])
    logger.info(f"Processing image_id: {image_id}, receipt_id: {receipt_id}")
    labels_need_validation = deserialize_labels(
        download_serialized_labels(event)
    )
    lines, words, metadata, labels = get_receipt_details(image_id, receipt_id)
    first_pass_labels, second_pass_labels = split_first_and_second_pass(
        labels_need_validation, labels
    )
    filepath = format_batch_completion_file(
        lines, words, labels, first_pass_labels, second_pass_labels, metadata
    )
    s3_key = upload_completion_batch_file(filepath, s3_bucket)
    return {
        "statusCode": 200,
        "s3_key": s3_key,
    }


def submit_openai_handler(event, context):
    """
    Submit the batch completion file to OpenAI.
    """
    logger.info("Starting submit_openai_handler")
    s3_keys = event["s3_keys"]
    merged_files = merge_ndjsons(
        S3_BUCKET, s3_keys, max_size_bytes=0.5 * 1024 * 1024  # 0.5 MB
    )
    batch_ids = []
    for merged_file, consumed_keys in merged_files:
        # Upload merged NDJSON to OpenAI files
        file_object = upload_to_openai(merged_file)
        # Submit a new batch
        batch = submit_openai_batch(file_object.id)
        open_ai_batch_id = batch.id
        batch_id = generate_completion_batch_id()
        # Poll for status with timeout
        start_time = time.time()
        while True:
            status = get_openai_batch_status(open_ai_batch_id)
            if status == "failed":
                try:
                    details = openai_client.batches.retrieve(open_ai_batch_id)
                    error_message = getattr(
                        details, "status_details", None
                    ) or getattr(details, "error", str(details))
                except Exception as e:
                    error_message = str(e)
                raise RuntimeError(
                    f"OpenAI batch {open_ai_batch_id} failed: {error_message}"
                )
            if status in BATCH_STARTED_STATUSES:
                # batch has started processing or is already complete
                break
            if time.time() - start_time > MAX_BATCH_TIMEOUT:
                raise TimeoutError(
                    f"OpenAI batch {open_ai_batch_id} timed out after {MAX_BATCH_TIMEOUT} seconds"
                )
            time.sleep(WAIT_INTERVAL)
        # Update labels and store summary
        labels, receipt_refs = get_labels_from_ndjson(merged_file)
        update_label_validation_status(labels)
        add_batch_summary(
            create_batch_summary(batch_id, open_ai_batch_id, receipt_refs)
        )
        batch_ids.append(batch_id)
    return {
        "statusCode": 200,
        "batch_ids": batch_ids,
    }


def poll_list_handler(event, context):
    """
    List all pending completion batches that need to be processed.
    """
    logger.info("Starting poll_list_handler")
    return {
        "statusCode": 200,
        "batches": [
            dict(batch) for batch in list_pending_completion_batches()
        ],
    }


def poll_download_handler(event, context):
    """
    Download the results of all "completed" completion batches.
    """
    logger.info("Starting poll_download_handler")
    batch = BatchSummary(**event)
    if get_openai_batch_status(batch.openai_batch_id) == "completed":
        pending_labels_to_update, valid_labels, invalid_labels = (
            download_openai_batch_result(batch)
        )
        logger.info(
            f"Pending labels to update: {len(pending_labels_to_update)}"
        )
        update_pending_labels(pending_labels_to_update)
        logger.info(f"Valid labels: {len(valid_labels)}")
        update_valid_labels(valid_labels)
        logger.info(f"Invalid labels: {len(invalid_labels)}")
        update_invalid_labels(invalid_labels)
        write_completion_batch_results(batch, valid_labels, invalid_labels)
        batch.status = BatchStatus.COMPLETED.value
        update_batch_summary(batch)
    return {
        "statusCode": 200,
        "batch_id": batch.batch_id,
    }
