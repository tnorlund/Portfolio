import json
import logging
import os
import urllib.parse
import uuid
from datetime import datetime

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities import OCRJob

BUCKET_NAME = os.environ["BUCKET_NAME"]
TABLE_NAME = os.environ["DYNAMO_TABLE_NAME"]
OCR_QUEUE = os.environ["OCR_JOB_QUEUE_URL"]

logger = logging.getLogger(__name__)

s3 = boto3.client("s3")
sqs = boto3.client("sqs")
dynamodb = boto3.client("dynamodb")
dynamo = DynamoClient(TABLE_NAME)

# Status priority: terminal states cannot be overwritten by non-terminal ones.
# Higher value = higher priority.
_STATUS_PRIORITY = {"PENDING": 0, "COMPLETED": 1, "FAILED": 1}


def handler(event, _):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "POST")
    path = http.get("path", "/upload-receipt")

    if method == "GET" and path == "/upload-status":
        return _handle_status(event)
    elif method == "POST" and path == "/upload-receipt":
        return _handle_upload(event)
    else:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Not found"}),
        }


def _handle_upload(event):
    body = json.loads(event["body"])
    filename_raw = body["filename"]
    content_type = body.get("content_type", "image/jpeg")
    filename = urllib.parse.unquote(filename_raw)

    key = f"raw-receipts/{filename}"

    # 1. presign PUT
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=900,
    )

    # 2. create OCR job record *now*
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

    # 3. push message onto the job queue
    sqs.send_message(
        QueueUrl=OCR_QUEUE,
        MessageBody=json.dumps(
            {"job_id": job.job_id, "image_id": job.image_id}
        ),
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "upload_url": url,
                "s3_key": key,
                "job_id": job.job_id,
                "image_id": job.image_id,
            }
        ),
    }


def _handle_status(event):
    qs = event.get("queryStringParameters") or {}
    image_id = qs.get("image_id")
    if not image_id:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "image_id query parameter required"}),
        }

    # Query all relevant entity types for this image
    items = []
    last_key = None
    while True:
        kwargs = {
            "TableName": TABLE_NAME,
            "KeyConditionExpression": "PK = :pk",
            "FilterExpression": "#t IN (:t1, :t2, :t3, :t4, :t5)",
            "ProjectionExpression": "SK, #t, #s, receipt_count, validation_status, merchant_name, job_type",
            "ExpressionAttributeNames": {"#t": "TYPE", "#s": "status"},
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":t1": {"S": "OCR_JOB"},
                ":t2": {"S": "OCR_ROUTING_DECISION"},
                ":t3": {"S": "RECEIPT"},
                ":t4": {"S": "RECEIPT_PLACE"},
                ":t5": {"S": "RECEIPT_WORD_LABEL"},
            },
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = dynamodb.query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    # Derive status from entities
    ocr_status = "PENDING"
    receipt_count = 0
    receipts_map = {}  # receipt_id -> {merchant_found, merchant_name, total_labels, validated_labels}

    for item in items:
        item_type = item.get("TYPE", {}).get("S", "")
        sk = item.get("SK", {}).get("S", "")

        if item_type == "OCR_ROUTING_DECISION":
            status = item.get("status", {}).get("S", "").upper()
            if _STATUS_PRIORITY.get(status, 0) > _STATUS_PRIORITY.get(ocr_status, 0):
                ocr_status = status

        elif item_type == "RECEIPT":
            # SK format: RECEIPT#<receipt_id>
            parts = sk.split("#")
            if len(parts) >= 2:
                try:
                    rid = int(parts[1])
                except ValueError:
                    continue
                receipt_count += 1
                if rid not in receipts_map:
                    receipts_map[rid] = {
                        "merchant_found": False,
                        "merchant_name": None,
                        "total_labels": 0,
                        "validated_labels": 0,
                    }

        elif item_type == "RECEIPT_PLACE":
            # SK format: RECEIPT_PLACE#<receipt_id>
            parts = sk.split("#")
            if len(parts) >= 2:
                try:
                    rid = int(parts[1])
                except ValueError:
                    continue
                merchant = item.get("merchant_name", {}).get("S")
                if rid not in receipts_map:
                    receipts_map[rid] = {
                        "merchant_found": bool(merchant),
                        "merchant_name": merchant,
                        "total_labels": 0,
                        "validated_labels": 0,
                    }
                else:
                    receipts_map[rid]["merchant_found"] = bool(merchant)
                    receipts_map[rid]["merchant_name"] = merchant

        elif item_type == "RECEIPT_WORD_LABEL":
            # Expected SK pattern: RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LABEL#<label>
            parts = sk.split("#")
            rid = None
            for i, p in enumerate(parts):
                if p == "RECEIPT" and i + 1 < len(parts):
                    try:
                        rid = int(parts[i + 1])
                    except ValueError:
                        logger.warning(
                            "Failed to parse receipt_id from RECEIPT_WORD_LABEL SK: %s", sk
                        )
                    break
            if rid is None:
                logger.warning(
                    "Could not extract receipt_id from RECEIPT_WORD_LABEL SK: %s", sk
                )
                continue
            if rid not in receipts_map:
                receipts_map[rid] = {
                    "merchant_found": False,
                    "merchant_name": None,
                    "total_labels": 0,
                    "validated_labels": 0,
                }
            receipts_map[rid]["total_labels"] += 1
            vs = item.get("validation_status", {}).get("S", "")
            if vs in ("VALID", "INVALID", "NEEDS_REVIEW"):
                receipts_map[rid]["validated_labels"] += 1

    receipts_list = []
    for rid in sorted(receipts_map.keys()):
        r = receipts_map[rid]
        receipts_list.append({
            "receipt_id": rid,
            "merchant_found": r["merchant_found"],
            "merchant_name": r["merchant_name"],
            "total_labels": r["total_labels"],
            "validated_labels": r["validated_labels"],
        })

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "image_id": image_id,
            "ocr_status": ocr_status,
            "receipt_count": receipt_count,
            "receipts": receipts_list,
        }),
    }
