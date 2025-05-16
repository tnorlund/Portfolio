import os
import json
import uuid
import boto3
from datetime import datetime

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import OCRJob
from receipt_dynamo.constants import OCRStatus, OCRJobType
from receipt_upload.receipt_processing.photo import process_photo

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


def handler(event, context):
    print(event)
    # SQS can send multiple records
    for record in event.get("Records", []):
        # Get the message body
        body = json.loads(record["body"])
        job_id = body["job_id"]
        image_id = body["image_id"]
        # Use the correct field names from the message
        ocr_json_s3_bucket = body["s3_bucket"]
        ocr_json_s3_key = body["s3_key"]

        process_photo(
            raw_bucket=RAW_BUCKET,
            site_bucket=SITE_BUCKET,
            dynamo_table_name=TABLE_NAME,
            ocr_job_queue_url=ocr_job_queue_url,
            ocr_json_s3_key=ocr_json_s3_key,
            ocr_json_s3_bucket=ocr_json_s3_bucket,
            job_id=job_id,
            image_id=image_id,
        )

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "OCR results processed"}),
    }
