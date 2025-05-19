from datetime import datetime, timezone
from typing import List

from PIL import Image as PIL_Image

from receipt_dynamo.entities import (
    Image,
    Receipt,
    OCRJob,
    OCRRoutingDecision,
    Line,
    Word,
    Letter,
)
from receipt_dynamo.constants import OCRStatus, ImageType
from receipt_dynamo import DynamoClient
from receipt_upload.utils import (
    upload_png_to_s3,
    upload_jpeg_to_s3,
    calculate_sha256_from_bytes,
    image_ocr_to_receipt_ocr,
)


def process_native(
    raw_bucket: str,
    site_bucket: str,
    dynamo_table_name: str,
    ocr_job_queue_url: str,
    image: PIL_Image.Image,
    lines: List[Line],
    words: List[Word],
    letters: List[Letter],
    ocr_routing_decision: OCRRoutingDecision,
    ocr_job: OCRJob,
) -> None:
    """
    Process a native receipt image.

    Args:
        raw_bucket: S3 bucket for raw images
        site_bucket: S3 bucket for processed images
        dynamo_table_name: DynamoDB table name
        ocr_job_queue_url: SQS queue URL for OCR jobs
        image: PIL Image object
        lines: List of OCR lines
        words: List of OCR words
        letters: List of OCR letters
        ocr_routing_decision: OCR routing decision object
        ocr_job: OCR job object
    """
    dynamo_client = DynamoClient(dynamo_table_name)

    # Convert image OCR to receipt OCR
    receipt_lines, receipt_words, receipt_letters = image_ocr_to_receipt_ocr(
        lines=lines,
        words=words,
        letters=letters,
        receipt_id=1,
    )

    # Generate S3 keys
    raw_image_s3_key = f"raw/{ocr_job.image_id}.png"
    cdn_image_s3_key = f"assets/{ocr_job.image_id}.jpg"

    # Upload images to S3
    upload_png_to_s3(image, raw_bucket, raw_image_s3_key)
    upload_jpeg_to_s3(image, site_bucket, cdn_image_s3_key)

    # Calculate image hash once
    image_hash = calculate_sha256_from_bytes(image.tobytes())
    current_time = datetime.now(timezone.utc)

    # Add the image and OCR data to DynamoDB
    image_record = Image(
        image_id=ocr_job.image_id,
        width=image.width,
        height=image.height,
        timestamp_added=current_time,
        raw_s3_bucket=raw_bucket,
        raw_s3_key=raw_image_s3_key,
        cdn_s3_bucket=site_bucket,
        cdn_s3_key=cdn_image_s3_key,
        sha256=image_hash,
        image_type=ImageType.NATIVE,
    )
    dynamo_client.addImage(image_record)
    dynamo_client.addLines(lines)
    dynamo_client.addWords(words)
    dynamo_client.addLetters(letters)

    # Add the receipt and OCR data to DynamoDB
    receipt = Receipt(
        image_id=ocr_job.image_id,
        receipt_id=1,
        width=image.width,
        height=image.height,
        timestamp_added=current_time,
        raw_s3_bucket=raw_bucket,
        raw_s3_key=raw_image_s3_key,
        cdn_s3_bucket=site_bucket,
        cdn_s3_key=cdn_image_s3_key,
        sha256=image_hash,
    )
    dynamo_client.addReceipt(receipt)
    dynamo_client.addReceiptLines(receipt_lines)
    dynamo_client.addReceiptWords(receipt_words)
    dynamo_client.addReceiptLetters(receipt_letters)

    # Update the OCR routing decision
    ocr_routing_decision.status = OCRStatus.COMPLETED.value
    ocr_routing_decision.receipt_count = 1
    ocr_routing_decision.updated_at = current_time
    dynamo_client.updateOCRRoutingDecision(ocr_routing_decision)
