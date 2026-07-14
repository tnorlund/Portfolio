import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PIL_Image

from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (
    OCRJob,
    OCRRoutingDecision,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
)

from receipt_upload.ocr import process_ocr_dict_as_receipt
from receipt_upload.receipt_processing.rows import persist_receipt_rows
from receipt_upload.typeface_fingerprint import persist_receipt_fingerprint
from receipt_upload.utils import download_file_from_s3


def refine_receipt(
    dynamo_table_name: str,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    receipt_letters: list[ReceiptLetter],
    ocr_routing_decision: OCRRoutingDecision,
    receipt_image: PIL_Image.Image | None = None,
):
    dynamo_client = DynamoClient(dynamo_table_name)

    # Add the receipt OCR data to the DynamoD
    dynamo_client.add_receipt_lines(receipt_lines)
    dynamo_client.add_receipt_words(receipt_words)
    persist_receipt_rows(dynamo_client, receipt_lines, receipt_words)
    dynamo_client.add_receipt_letters(receipt_letters)
    persist_receipt_fingerprint(dynamo_client, receipt_image, receipt_letters)

    # Update the OCR routing decision
    ocr_routing_decision.status = OCRStatus.COMPLETED.value
    ocr_routing_decision.receipt_count = 1
    ocr_routing_decision.updated_at = datetime.now(timezone.utc)
    dynamo_client.update_ocr_routing_decision(ocr_routing_decision)
