from datetime import datetime, timezone
import json
from pathlib import Path

from receipt_dynamo.entities import OCRJob, OCRRoutingDecision
from receipt_dynamo.constants import OCRStatus
from receipt_dynamo import DynamoClient
from receipt_upload.utils import download_file_from_s3
from receipt_upload.ocr import process_ocr_dict_as_receipt


def refine_receipt(
    raw_bucket: str,
    site_bucket: str,
    dynamo_table_name: str,
    ocr_job_queue_url: str,
    ocr_routing_decision: OCRRoutingDecision,
    ocr_job: OCRJob,
):
    dynamo_client = DynamoClient(dynamo_table_name)
    image_id = ocr_job.image_id

    # Download the OCR JSON
    json_s3_key = ocr_routing_decision.s3_key
    json_s3_bucket = ocr_routing_decision.s3_bucket
    ocr_json_path = download_file_from_s3(
        json_s3_bucket, json_s3_key, Path("/tmp")
    )
    with open(ocr_json_path, "r") as f:
        ocr_json = json.load(f)
    ocr_lines, ocr_words, ocr_letters = process_ocr_dict_as_receipt(
        ocr_json, image_id, ocr_job.receipt_id
    )

    # Add the receipt OCR data to the DynamoDB
    dynamo_client.addReceiptLines(ocr_lines)
    dynamo_client.addReceiptWords(ocr_words)
    dynamo_client.addReceiptLetters(ocr_letters)

    # Update the OCR routing decision
    ocr_routing_decision.status = OCRStatus.COMPLETED.value
    ocr_routing_decision.receipt_count = 1
    ocr_routing_decision.updated_at = datetime.now(timezone.utc)
    dynamo_client.updateOCRRoutingDecision(ocr_routing_decision)
