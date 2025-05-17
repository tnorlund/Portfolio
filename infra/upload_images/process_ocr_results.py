import os
import json
import uuid
import boto3
import logging
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import OCRJob
from receipt_dynamo.constants import OCRStatus, OCRJobType
from receipt_upload.receipt_processing.photo import process_photo
from receipt_upload.receipt_processing.receipt import refine_receipt
from receipt_upload.receipt_processing.scan import process_scan
from receipt_upload.utils import get_ocr_job, get_ocr_routing_decision

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
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


def handler(event, context):
    # SQS can send multiple records
    for record in event.get("Records", []):
        # Get the message body
        body = json.loads(record["body"])
        job_id = body["job_id"]
        image_id = body["image_id"]
        # Get the OCR job and routing decision
        ocr_job = get_ocr_job(TABLE_NAME, image_id, job_id)
        ocr_routing_decision = get_ocr_routing_decision(TABLE_NAME, image_id, job_id)
        logger.info(f"OCR job: {ocr_job.job_type}")
        logger.info(f"OCR routing decision: {ocr_routing_decision.status}")

        if ocr_job.job_type == OCRJobType.REFINEMENT.value:
            logger.info(f"Refining receipt {ocr_job.receipt_id}")
            refine_receipt(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
            )
        else:
            logger.info(f"Processing scan {ocr_job.image_id}")
            process_scan(
                raw_bucket=RAW_BUCKET,
                site_bucket=SITE_BUCKET,
                dynamo_table_name=TABLE_NAME,
                ocr_job_queue_url=ocr_job_queue_url,
                ocr_routing_decision=ocr_routing_decision,
                ocr_job=ocr_job,
            )
        # else:
        #     logger.info(f"Processing photo {ocr_job.image_id}")
        #     process_photo(
        #         raw_bucket=RAW_BUCKET,
        #         site_bucket=SITE_BUCKET,
        #         dynamo_table_name=TABLE_NAME,
        #         ocr_job_queue_url=ocr_job_queue_url,
        #         ocr_routing_decision=ocr_routing_decision,
        #         ocr_job=ocr_job,
        #     )

        sqs.delete_message(
            QueueUrl=ocr_results_queue_url,
            ReceiptHandle=record["receiptHandle"],
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "OCR results processed"}),
    }
