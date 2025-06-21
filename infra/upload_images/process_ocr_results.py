import os
import json
import uuid
import boto3
import logging
from datetime import datetime
from logging import StreamHandler, Formatter

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import OCRJob
from receipt_dynamo.constants import OCRStatus, OCRJobType, ImageType
from PIL import Image as PIL_Image
from pathlib import Path
from receipt_upload.ocr import process_ocr_dict_as_image
from receipt_upload.route_images import classify_image_layout
from receipt_upload.receipt_processing.photo import process_photo
from receipt_upload.receipt_processing.receipt import refine_receipt
from receipt_upload.receipt_processing.scan import process_scan
from receipt_upload.receipt_processing.native import process_native
from receipt_upload.utils import (
    get_ocr_job,
    get_ocr_routing_decision,
    download_image_from_s3,
    download_file_from_s3,
    image_ocr_to_receipt_ocr,
)

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

sqs = boto3.client("sqs")
ocr_job_queue_url = os.environ["OCR_JOB_QUEUE_URL"]
ocr_results_queue_url = os.environ["OCR_RESULTS_QUEUE_URL"]


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


def handler(event, context):
    # SQS can send multiple records
    for record in event.get("Records", []):
        # Get the message body
        body = json.loads(record["body"])
        job_id = body["job_id"]
        image_id = body["image_id"]
        logger.info(
            f"Processing OCR results for image {image_id} with job {job_id}"
        )
        # Get the OCR job and routing decision
        ocr_job = get_ocr_job(TABLE_NAME, image_id, job_id)
        ocr_routing_decision = get_ocr_routing_decision(
            TABLE_NAME, image_id, job_id
        )
        logger.info(f"Got OCR routing decision {ocr_routing_decision}")

        # Download the OCR JSON
        json_s3_key = ocr_routing_decision.s3_key
        json_s3_bucket = ocr_routing_decision.s3_bucket
        ocr_json_path = download_file_from_s3(
            json_s3_bucket, json_s3_key, Path("/tmp")
        )
        with open(ocr_json_path, "r") as f:
            ocr_json = json.load(f)
        ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_image(
            ocr_json, image_id
        )
        logger.info(f"Got job with type {ocr_job.job_type}")

        # Download the image from S3
        raw_image_path = download_image_from_s3(
            s3_bucket=ocr_job.s3_bucket,
            s3_key=ocr_job.s3_key,
            temp_dir=Path("/tmp"),
            image_id=image_id,
        )
        image = PIL_Image.open(raw_image_path)
        if ocr_job.job_type == OCRJobType.REFINEMENT.value:
            logger.info(f"Refining receipt {ocr_job.image_id}")
            if ocr_job.receipt_id is None:
                logger.error(f"Receipt ID is None for refinement job {job_id}")
                continue
            receipt_lines, receipt_words, receipt_letters = (
                image_ocr_to_receipt_ocr(
                    lines=ocr_lines,
                    words=ocr_words,
                    letters=ocr_letters,
                    receipt_id=ocr_job.receipt_id,
                )
            )
            refine_receipt(
                dynamo_table_name=TABLE_NAME,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                receipt_letters=receipt_letters,
                ocr_routing_decision=ocr_routing_decision,
            )
            sqs.delete_message(
                QueueUrl=ocr_results_queue_url,
                ReceiptHandle=record["receiptHandle"],
            )
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "OCR results processed"}),
            }

        # Classify the image
        image_type = classify_image_layout(
            lines=ocr_lines,
            image_height=image.height,
            image_width=image.width,
        )

        if image_type == ImageType.NATIVE:
            # The image is of a receipt. The
            logger.info(f"Refining receipt {ocr_job.image_id}")
            process_native(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                image=image,
                lines=ocr_lines,
                words=ocr_words,
                letters=ocr_letters,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
            )
        elif image_type == ImageType.PHOTO:
            logger.info(f"Processing photo {ocr_job.image_id}")
            process_photo(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
                image=image,
            )
        elif image_type == ImageType.SCAN:
            logger.info(f"Processing scan {ocr_job.image_id}")
            process_scan(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
                image=image,
            )
        else:
            logger.info(f"Unknown image type {image_type}")

        sqs.delete_message(
            QueueUrl=ocr_results_queue_url,
            ReceiptHandle=record["receiptHandle"],
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "OCR results processed"}),
    }
