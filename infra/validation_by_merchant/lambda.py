"""Lambda handlers for validation by merchant."""

import os
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import (
    ReceiptWord,
    ReceiptWordLabel,
)
from receipt_label.label_validation import (
    LabelValidationResult,
    get_unique_merchants_and_data,
    update_labels,
    validate_address,
    validate_currency,
    validate_date,
    validate_merchant_name_google,
    validate_merchant_name_pinecone,
    validate_phone_number,
    validate_time,
)
from receipt_label.utils import get_clients

logger = getLogger()
logger.setLevel(INFO)

label_types = [
    "LINE_TOTAL",
    "UNIT_PRICE",
    "TAX",
    "SUBTOTAL",
    "GRAND_TOTAL",
    "MERCHANT_NAME",
    "PHONE_NUMBER",
    "DATE",
    "TIME",
    "ADDRESS_LINE",
]

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

bucket = os.environ["S3_BUCKET"]


def _get_label_and_word(
    label: str, words: list[ReceiptWord]
) -> tuple[str, ReceiptWord]:
    label_and_word = next(
        (
            (label, word)
            for word in words
            if label.word_id == word.word_id and label.line_id == word.line_id
        ),
        None,
    )
    if label_and_word is None:
        raise ValueError(f"Label not found for label {label}")
    return label_and_word


def list_handler(_event, _context):
    """
    Lambda handler for listing unique merchants and their receipts.

    Returns a list of batches, where each batch contains:
    - merchant_name: The canonical merchant name
    - receipts: List of (image_id, receipt_id) tuples
    - receipt_count: Number of receipts for this merchant

    Args:
        _event: Lambda event (unused)
        _context: Lambda context (unused)

    Returns:
        Dict with statusCode and batches list
    """
    logger.info("Starting list_handler")
    unique_merchants_and_data = get_unique_merchants_and_data()
    logger.info("Found %s unique merchants", len(unique_merchants_and_data))
    return {
        "statusCode": 200,
        "batches": unique_merchants_and_data,
    }


def validate_handler(event, _context):
    """
    Lambda handler for validating labels for a specific receipt.

    Validates different label types using appropriate validation methods:
    - Currency labels: validate_currency
    - Merchant name: validate_merchant_name_pinecone or _google
    - Phone number: validate_phone_number
    - Date/Time: validate_date/validate_time
    - Address: validate_address

    Args:
        event: Lambda event containing:
            - receipt_id: The receipt ID to validate
            - image_id: The image ID
            - merchant_name: The canonical merchant name
            - receipt_count: Number of receipts for this merchant
        _context: Lambda context (unused)

    Returns:
        Dict with statusCode and validation message
    """
    logger.info("Starting validate_handler")
    receipt_id = int(event["receipt_id"])
    image_id = event["image_id"]
    merchant_name = event["merchant_name"]
    receipt_count = int(event["receipt_count"])
    dynamo_client, _, _ = get_clients()
    _, _, words, _, _, labels = dynamo_client.getReceiptDetails(
        image_id=image_id,
        receipt_id=receipt_id,
    )
    receipt_metadata = dynamo_client.getReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
    )
    logger.info(
        "Got receipt details for image %s and receipt %s", image_id, receipt_id
    )

    labels_and_words: list[tuple[ReceiptWordLabel, ReceiptWord]] = []
    labels_and_words.extend(
        [
            _get_label_and_word(label, words)
            for label in labels
            if label.label in label_types
            and label.validation_status
            not in [
                ValidationStatus.NONE.value,
                ValidationStatus.INVALID.value,
            ]
        ]
    )
    logger.info("Got %s labels and words to validate", len(labels_and_words))
    label_validation_results: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ] = []
    for label, word in labels_and_words:
        if label.label in [
            "LINE_TOTAL",
            "UNIT_PRICE",
            "TAX",
            "SUBTOTAL",
            "GRAND_TOTAL",
        ]:
            label_validation_results.append(
                (validate_currency(word, label), label)
            )
        elif label.label == "MERCHANT_NAME":
            if receipt_count > 4:
                label_validation_results.append(
                    (
                        validate_merchant_name_pinecone(
                            word, label, merchant_name
                        ),
                        label,
                    )
                )
            else:
                label_validation_results.append(
                    (
                        validate_merchant_name_google(
                            word, label, receipt_metadata
                        ),
                        label,
                    )
                )
        elif label.label == "PHONE_NUMBER":
            label_validation_results.append(
                (validate_phone_number(word, label), label)
            )
        elif label.label == "DATE":
            label_validation_results.append(
                (validate_date(word, label), label)
            )
        elif label.label == "TIME":
            label_validation_results.append(
                (validate_time(word, label), label)
            )
        elif label.label == "ADDRESS_LINE":
            label_validation_results.append(
                (validate_address(word, label, receipt_metadata), label)
            )
        else:
            raise ValueError(f"Unknown label type: {label.label}")

    if len(label_validation_results) > 0:
        update_labels(label_validation_results)

    logger.info("Validated %s labels", len(label_validation_results))
    return {
        "statusCode": 200,
        "message": f"Validated {len(label_validation_results)} labels",
    }
