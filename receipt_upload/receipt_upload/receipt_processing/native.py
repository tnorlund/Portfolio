from datetime import datetime, timezone
from typing import List

from PIL import Image as PIL_Image

from receipt_dynamo.constants import ImageType, OCRStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (
    Image,
    Letter,
    Line,
    OCRJob,
    OCRRoutingDecision,
    Receipt,
    Word,
)
from receipt_upload.utils import (
    calculate_sha256_from_bytes,
    image_ocr_to_receipt_ocr,
    upload_all_cdn_formats,
    upload_jpeg_to_s3,
    upload_png_to_s3,
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

    # Upload images to S3
    upload_png_to_s3(image, raw_bucket, raw_image_s3_key)
    cdn_keys = upload_all_cdn_formats(
        image, site_bucket, f"assets/{ocr_job.image_id}", generate_thumbnails=True
    )

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
        cdn_s3_key=cdn_keys["jpeg"],
        cdn_webp_s3_key=cdn_keys["webp"],
        cdn_avif_s3_key=cdn_keys["avif"],
        # Add thumbnail versions
        cdn_thumbnail_s3_key=cdn_keys.get("jpeg_thumbnail"),
        cdn_thumbnail_webp_s3_key=cdn_keys.get("webp_thumbnail"),
        cdn_thumbnail_avif_s3_key=cdn_keys.get("avif_thumbnail"),
        # Add small versions
        cdn_small_s3_key=cdn_keys.get("jpeg_small"),
        cdn_small_webp_s3_key=cdn_keys.get("webp_small"),
        cdn_small_avif_s3_key=cdn_keys.get("avif_small"),
        # Add medium versions
        cdn_medium_s3_key=cdn_keys.get("jpeg_medium"),
        cdn_medium_webp_s3_key=cdn_keys.get("webp_medium"),
        cdn_medium_avif_s3_key=cdn_keys.get("avif_medium"),
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
        cdn_s3_key=cdn_keys["jpeg"],
        cdn_webp_s3_key=cdn_keys["webp"],
        cdn_avif_s3_key=cdn_keys["avif"],
        # Add thumbnail versions
        cdn_thumbnail_s3_key=cdn_keys.get("jpeg_thumbnail"),
        cdn_thumbnail_webp_s3_key=cdn_keys.get("webp_thumbnail"),
        cdn_thumbnail_avif_s3_key=cdn_keys.get("avif_thumbnail"),
        # Add small versions
        cdn_small_s3_key=cdn_keys.get("jpeg_small"),
        cdn_small_webp_s3_key=cdn_keys.get("webp_small"),
        cdn_small_avif_s3_key=cdn_keys.get("avif_small"),
        # Add medium versions
        cdn_medium_s3_key=cdn_keys.get("jpeg_medium"),
        cdn_medium_webp_s3_key=cdn_keys.get("webp_medium"),
        cdn_medium_avif_s3_key=cdn_keys.get("avif_medium"),
        sha256=image_hash,
        top_left={"x": 0.0, "y": 1.0},
        top_right={"x": 1.0, "y": 1.0},
        bottom_left={"x": 0.0, "y": 0.0},
        bottom_right={"x": 1.0, "y": 0.0},
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
