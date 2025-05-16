import os
import json
import uuid
import boto3
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import OCRJob
from receipt_dynamo.constants import OCRStatus, OCRJobType
from receipt_upload.utils import send_message_to_sqs

TABLE_NAME = os.environ["DYNAMO_TABLE_NAME"]
if TABLE_NAME is None:
    raise ValueError("DYNAMO_TABLE_NAME is not set")

S3_BUCKET = os.environ["S3_BUCKET"]
if S3_BUCKET is None:
    raise ValueError("S3_BUCKET is not set")

sqs = boto3.client("sqs")
ocr_job_queue_url = os.environ["OCR_JOB_QUEUE_URL"]


def handler(event, context):
    try:
        body = json.loads(event["body"])
        s3_key = body["s3_key"]
        original_filename = body["original_filename"]
        content_type = body.get("content_type", "image/jpeg")

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

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"job_id": job.job_id}),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
