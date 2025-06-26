import json
import logging
import os
import uuid
from datetime import datetime
from logging import Formatter, StreamHandler

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities import OCRJob
from receipt_upload.utils import send_message_to_sqs

TABLE_NAME = os.environ["DYNAMO_TABLE_NAME"]
if TABLE_NAME is None:
    raise ValueError("DYNAMO_TABLE_NAME is not set")

S3_BUCKET = os.environ["S3_BUCKET"]
if S3_BUCKET is None:
    raise ValueError("S3_BUCKET is not set")

sqs = boto3.client("sqs")
ocr_job_queue_url = os.environ["OCR_JOB_QUEUE_URL"]

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
    try:
        body = json.loads(event["body"])
        s3_key = body["s3_key"]
        original_filename = body["original_filename"]
        content_type = body.get("content_type", "image/jpeg")
        logger.info(f"Received request to submit job for {s3_key}")

        # Add the OCR job to the database
        dynamo_client = DynamoClient(TABLE_NAME)
        job = OCRJob(
            image_id=str(uuid.uuid4()),
            job_id=str(uuid.uuid4()),
            s3_bucket=S3_BUCKET,
            s3_key=s3_key,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            status=OCRStatus.PENDING,
            job_type=OCRJobType.FIRST_PASS,
            receipt_id=None,
        )
        dynamo_client.addOCRJob(job)
        logger.info(f"Added OCR job for image {job.image_id}")

        # Send a message to the OCR job queue
        send_message_to_sqs(
            ocr_job_queue_url,
            json.dumps(
                {
                    "job_id": job.job_id,
                    "image_id": job.image_id,
                }
            ),
        )
        logger.info(f"Sent message to OCR job queue: {job.job_id}")
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"job_id": job.job_id}),
        }

    except Exception as e:
        logger.error(f"Error submitting job: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
