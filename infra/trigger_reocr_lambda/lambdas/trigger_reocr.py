"""
Trigger Regional Re-OCR Lambda Handler.

Input:
    {
        "image_id": "uuid-string",
        "receipt_id": 1,
        "reocr_region": {"x": 0.65, "y": 0.0, "width": 0.35, "height": 1.0},
        "reocr_reason": "manual_trigger"  // optional, defaults to "manual_trigger"
    }

Output:
    {
        "success": true,
        "job_id": "uuid-string",
        "region": {...}
    }

Environment Variables:
    DYNAMODB_TABLE_NAME: DynamoDB table name
    OCR_JOB_QUEUE_URL: SQS queue URL for OCR jobs
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.entities import OCRJob

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_input(event: dict[str, Any]) -> str | None:
    """Validate Lambda input. Returns error message or None if valid."""
    # image_id: valid UUID
    image_id = event.get("image_id")
    if not isinstance(image_id, str) or not UUID_PATTERN.match(image_id):
        return "image_id must be a valid UUID string"

    # receipt_id: non-negative int
    receipt_id = event.get("receipt_id")
    if not isinstance(receipt_id, int) or receipt_id < 0:
        return "receipt_id must be a non-negative integer"

    # reocr_region: dict with x/y/width/height
    region = event.get("reocr_region")
    if not isinstance(region, dict):
        return "reocr_region must be an object with x, y, width, height"

    for key in ("x", "y", "width", "height"):
        val = region.get(key)
        if not isinstance(val, (int, float)):
            return f"reocr_region.{key} must be a number"
        if val < 0 or val > 1:
            return f"reocr_region.{key} must be between 0 and 1"

    if region["width"] <= 0:
        return "reocr_region.width must be greater than 0"
    if region["height"] <= 0:
        return "reocr_region.height must be greater than 0"

    if region["x"] + region["width"] > 1:
        return "reocr_region.x + width must be <= 1"
    if region["y"] + region["height"] > 1:
        return "reocr_region.y + height must be <= 1"

    reason = event.get("reocr_reason")
    if reason is not None and not isinstance(reason, str):
        return "reocr_reason must be a string"

    return None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for triggering regional re-OCR."""
    logger.info("trigger_reocr invoked: %s", json.dumps(event))

    # Validate input
    error = _validate_input(event)
    if error:
        return {"success": False, "error": error}

    # After validation, these are guaranteed to be the right types
    image_id: str = event["image_id"]
    receipt_id: int = event["receipt_id"]
    reocr_region: dict[str, float] = event["reocr_region"]
    reocr_reason: str = event.get("reocr_reason", "manual_trigger")

    table_name = os.environ["DYNAMODB_TABLE_NAME"]
    queue_url = os.environ["OCR_JOB_QUEUE_URL"]

    try:
        # Look up receipt to get S3 info
        dynamo_client = DynamoClient(table_name)
        receipt = dynamo_client.get_receipt(image_id, receipt_id)

        # Create OCR job
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        ocr_job = OCRJob(
            image_id=image_id,
            job_id=job_id,
            s3_bucket=receipt.raw_s3_bucket,
            s3_key=receipt.raw_s3_key,
            created_at=now,
            updated_at=now,
            status=OCRStatus.PENDING.value,
            job_type=OCRJobType.REGIONAL_REOCR.value,
            receipt_id=receipt_id,
            reocr_region={
                "x": float(reocr_region["x"]),
                "y": float(reocr_region["y"]),
                "width": float(reocr_region["width"]),
                "height": float(reocr_region["height"]),
            },
            reocr_reason=reocr_reason,
        )

        # Write to DynamoDB
        dynamo_client.add_ocr_job(ocr_job)
        logger.info("Created OCR job %s for image %s receipt %d", job_id, image_id, receipt_id)

        # Send SQS message
        sqs = boto3.client("sqs")
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({
                "job_id": job_id,
                "image_id": image_id,
            }),
        )
        logger.info("Sent SQS message to %s", queue_url)

        return {
            "success": True,
            "job_id": job_id,
            "region": ocr_job.reocr_region,
        }

    except Exception as e:
        logger.exception("Error triggering re-OCR")
        return {"success": False, "error": str(e)}
