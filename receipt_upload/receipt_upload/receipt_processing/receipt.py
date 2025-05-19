from datetime import datetime, timezone
import json
from pathlib import Path

from receipt_dynamo.entities import (
    OCRJob,
    OCRRoutingDecision,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
)
from receipt_dynamo.constants import OCRStatus
from receipt_dynamo import DynamoClient
from receipt_upload.utils import download_file_from_s3
from receipt_upload.ocr import process_ocr_dict_as_receipt


def refine_receipt(
    dynamo_table_name: str,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    receipt_letters: list[ReceiptLetter],
    ocr_routing_decision: OCRRoutingDecision,
):
    dynamo_client = DynamoClient(dynamo_table_name)

    # Add the receipt OCR data to the DynamoDB
    dynamo_client.addReceiptLines(receipt_lines)
    dynamo_client.addReceiptWords(receipt_words)
    dynamo_client.addReceiptLetters(receipt_letters)

    # Update the OCR routing decision
    ocr_routing_decision.status = OCRStatus.COMPLETED.value
    ocr_routing_decision.receipt_count = 1
    ocr_routing_decision.updated_at = datetime.now(timezone.utc)
    dynamo_client.updateOCRRoutingDecision(ocr_routing_decision)
