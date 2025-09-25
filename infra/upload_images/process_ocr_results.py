import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Formatter, StreamHandler
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image as PIL_Image
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_upload.receipt_processing.native import process_native
from receipt_upload.receipt_processing.photo import process_photo
from receipt_upload.receipt_processing.receipt import refine_receipt
from receipt_upload.receipt_processing.scan import process_scan
from receipt_upload.route_images import classify_image_layout
from receipt_upload.utils import (
    download_file_from_s3,
    download_image_from_s3,
    get_ocr_job,
    get_ocr_routing_decision,
    image_ocr_to_receipt_ocr,
)

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType, OCRJobType, OCRStatus
from receipt_dynamo.entities import Letter, Line, Word

TABLE_NAME = os.environ["DYNAMO_TABLE_NAME"]
if TABLE_NAME is None:
    raise ValueError("DYNAMO_TABLE_NAME is not set")

S3_BUCKET = os.environ["S3_BUCKET"]
if S3_BUCKET is None:
    raise ValueError("S3_BUCKET is not set")

RAW_BUCKET = os.environ["RAW_BUCKET"]
if RAW_BUCKET is None:
    raise ValueError("RAW_BUCKET is not set")

SITE_BUCKET = os.environ["SITE_BUCKET"]
if SITE_BUCKET is None:
    raise ValueError("SITE_BUCKET is not set")

ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", SITE_BUCKET)

sqs = boto3.client("sqs")
ocr_job_queue_url = os.environ["OCR_JOB_QUEUE_URL"]
ocr_results_queue_url = os.environ["OCR_RESULTS_QUEUE_URL"]
embed_ndjson_queue_url = os.environ.get("EMBED_NDJSON_QUEUE_URL", "")
_sqs_embed = boto3.client("sqs")


def _s3_put_ndjson(bucket: str, key: str, rows: list[dict]):
    body = "\n".join(json.dumps(r) for r in rows)
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/x-ndjson",
    )


def _export_receipt_ndjson_and_queue(image_id: str, receipt_id: int):
    if not embed_ndjson_queue_url:
        logger.info("EMBED_NDJSON_QUEUE_URL not set; skipping embedding queue")
        return
    dynamo = DynamoClient(TABLE_NAME)
    # Fetch authoritative words/lines from DynamoDB
    receipt_words = dynamo.list_receipt_words_from_receipt(
        image_id, int(receipt_id)
    )
    receipt_lines = dynamo.list_receipt_lines_from_receipt(
        image_id, int(receipt_id)
    )

    prefix = f"receipts/{image_id}/receipt-{int(receipt_id):05d}/"
    lines_key = prefix + "lines.ndjson"
    words_key = prefix + "words.ndjson"

    # Serialize full dataclass objects so the consumer can rehydrate with
    # ReceiptLine(**d)/ReceiptWord(**d) preserving geometry and methods
    line_rows = [dict(l) for l in (receipt_lines or [])]
    word_rows = [dict(w) for w in (receipt_words or [])]

    _s3_put_ndjson(ARTIFACTS_BUCKET, lines_key, line_rows)
    _s3_put_ndjson(ARTIFACTS_BUCKET, words_key, word_rows)

    # Enqueue for batched embedding from NDJSON via SQS
    payload = {
        "image_id": image_id,
        "receipt_id": int(receipt_id),
        "artifacts_bucket": ARTIFACTS_BUCKET,
        "lines_key": lines_key,
        "words_key": words_key,
    }
    _sqs_embed.send_message(
        QueueUrl=embed_ndjson_queue_url, MessageBody=json.dumps(payload)
    )


logger = logging.getLogger()
logger.setLevel(logging.INFO)

if len(logger.handlers) == 0:
    log_handler = StreamHandler()
    log_handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(log_handler)


@dataclass
class OCRData:
    """Container for OCR processing results."""

    lines: list[Line]
    words: list[Word]
    letters: list[Letter]


def _process_record(record):
    """Process a single SQS record."""
    body = json.loads(record["body"])
    job_id = body["job_id"]
    image_id = body["image_id"]
    logger.info(
        "Processing OCR results for image %s with job %s", image_id, job_id
    )

    ocr_job = get_ocr_job(TABLE_NAME, image_id, job_id)
    ocr_routing_decision = get_ocr_routing_decision(
        TABLE_NAME, image_id, job_id
    )
    logger.info("Got OCR routing decision %s", ocr_routing_decision)

    # Download OCR JSON
    ocr_json_path = download_file_from_s3(
        ocr_routing_decision.s3_bucket,
        ocr_routing_decision.s3_key,
        Path("/tmp"),
    )
    with open(ocr_json_path, "r", encoding="utf-8") as f:
        ocr_json = json.load(f)
    ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(
        ocr_json, image_id
    )
    logger.info("Got job with type %s", ocr_job.job_type)

    # Create OCR data container
    ocr_data = OCRData(lines=ocr_lines, words=ocr_words, letters=ocr_letters)

    # Download image
    raw_image_path = download_image_from_s3(
        ocr_job.s3_bucket, ocr_job.s3_key, image_id
    )
    image = PIL_Image.open(raw_image_path)

    if ocr_job.job_type == OCRJobType.REFINEMENT.value:
        ok = _process_refinement_job(ocr_job, ocr_data, ocr_routing_decision)
        # For refinement jobs, receipt_id is expected on job
        if ok and ocr_job.receipt_id is not None:
            try:
                _export_receipt_ndjson_and_queue(
                    ocr_job.image_id, int(ocr_job.receipt_id)
                )
            except Exception as e:  # Best-effort: do not fail main processing
                logger.error("Failed to enqueue embed for refinement: %s", e)
        return ok

    return _process_first_pass_job(
        image, ocr_data, ocr_job, ocr_routing_decision
    )


def _process_refinement_job(ocr_job, ocr_data, ocr_routing_decision):
    """Process a refinement OCR job."""
    logger.info("Refining receipt %s", ocr_job.image_id)
    if ocr_job.receipt_id is None:
        logger.error(
            "Receipt ID is None for refinement job %s", ocr_job.job_id
        )
        return False

    receipt_lines, receipt_words, receipt_letters = image_ocr_to_receipt_ocr(
        lines=ocr_data.lines,
        words=ocr_data.words,
        letters=ocr_data.letters,
        receipt_id=ocr_job.receipt_id,
    )
    refine_receipt(
        dynamo_table_name=TABLE_NAME,
        receipt_lines=receipt_lines,
        receipt_words=receipt_words,
        receipt_letters=receipt_letters,
        ocr_routing_decision=ocr_routing_decision,
    )
    return True


def _process_first_pass_job(image, ocr_data, ocr_job, ocr_routing_decision):
    """Process a first-pass OCR job."""
    image_type = classify_image_layout(
        lines=ocr_data.lines,
        image_height=image.height,
        image_width=image.width,
    )
    logger.info(
        "Image %s classified as %s (dimensions: %sx%s)",
        ocr_job.image_id,
        image_type,
        image.width,
        image.height,
    )

    if image_type == ImageType.NATIVE:
        logger.info("Refining receipt %s", ocr_job.image_id)
        try:
            process_native(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                image=image,
                lines=ocr_data.lines,
                words=ocr_data.words,
                letters=ocr_data.letters,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
            )
            # NATIVE produces a single receipt with id 1
            try:
                _export_receipt_ndjson_and_queue(ocr_job.image_id, 1)
            except Exception as e:
                logger.error("Failed to enqueue embed for native: %s", e)
        except ValueError as e:
            logger.error(
                "Geometry error in process_native for image %s: %s",
                ocr_job.image_id,
                e,
            )
            _update_routing_decision_with_error(ocr_routing_decision)
    elif image_type == ImageType.PHOTO:
        logger.info("Processing photo %s", ocr_job.image_id)
        try:
            process_photo(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
                image=image,
            )
        except ValueError as e:
            logger.error(
                "Geometry error in process_photo for image %s: %s",
                ocr_job.image_id,
                e,
            )
            _update_routing_decision_with_error(ocr_routing_decision)
    elif image_type == ImageType.SCAN:
        logger.info("Processing scan %s", ocr_job.image_id)
        try:
            process_scan(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
                image=image,
            )
        except ValueError as e:
            logger.error(
                "Geometry error in process_scan for image %s: %s",
                ocr_job.image_id,
                e,
            )
            _update_routing_decision_with_error(ocr_routing_decision)
    else:
        logger.error(
            "Unknown image type %s, updating routing decision to completed anyway",
            image_type,
        )
        _update_routing_decision_with_error(ocr_routing_decision)

    return False


def _update_routing_decision_with_error(ocr_routing_decision):
    """Updates the OCR routing decision with an error status."""
    dynamo_client = DynamoClient(TABLE_NAME)
    ocr_routing_decision.status = OCRStatus.FAILED.value
    ocr_routing_decision.receipt_count = 0
    ocr_routing_decision.updated_at = datetime.now(timezone.utc)
    dynamo_client.update_ocr_routing_decision(ocr_routing_decision)


def handler(event, _context):
    """Process OCR results from SQS messages and update routing decisions."""
    for record in event.get("Records", []):
        if _process_record(record):
            sqs.delete_message(
                QueueUrl=ocr_results_queue_url,
                ReceiptHandle=record["receiptHandle"],
            )
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "OCR results processed"}),
            }

        sqs.delete_message(
            QueueUrl=ocr_results_queue_url,
            ReceiptHandle=record["receiptHandle"],
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "OCR results processed"}),
    }
