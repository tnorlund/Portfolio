"""Upload receipt Lambda handler.

This Lambda handles:
1. Generating presigned S3 URLs for receipt image uploads
2. Creating OCR job records in DynamoDB
3. Sending job messages to the OCR queue
"""

import json
import os
import urllib.parse
import uuid
from datetime import datetime

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities import OCRJob
from receipt_upload.utils import send_message_to_sqs

BUCKET_NAME = os.environ["BUCKET_NAME"]
TABLE_NAME = os.environ["DYNAMO_TABLE_NAME"]
OCR_QUEUE = os.environ["OCR_JOB_QUEUE_URL"]

s3 = boto3.client("s3")
dynamo = DynamoClient(TABLE_NAME)


def lambda_handler(event, _context):
    """Handle upload receipt request.

    Args:
        event: API Gateway event with body containing filename and content_type
        _context: Lambda context (unused)

    Returns:
        API Gateway response with presigned URL, S3 key, and job ID
    """
    body = json.loads(event["body"])
    filename_raw = body["filename"]
    content_type = body.get("content_type", "image/jpeg")
    filename = urllib.parse.unquote(filename_raw)

    key = f"raw-receipts/{filename}"

    # 1. Generate presigned PUT URL
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=900,
    )

    # 2. Create OCR job record
    job = OCRJob(
        image_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        s3_bucket=BUCKET_NAME,
        s3_key=key,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        status=OCRStatus.PENDING,
        job_type=OCRJobType.FIRST_PASS,
        receipt_id=None,
    )
    dynamo.add_ocr_job(job)

    # 3. Send message to OCR job queue
    send_message_to_sqs(
        OCR_QUEUE, json.dumps({"job_id": job.job_id, "image_id": job.image_id})
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "upload_url": url,
                "s3_key": key,
                "job_id": job.job_id,
            }
        ),
    }
