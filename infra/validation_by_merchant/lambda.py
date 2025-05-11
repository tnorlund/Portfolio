import os
from logging import getLogger, StreamHandler, Formatter, INFO
from receipt_label.utils import get_clients
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import (
    ReceiptWord,
    ReceiptWordLabel,
)
from receipt_label.label_validation import (
    LabelValidationResult,
    validate_address,
    validate_currency,
    validate_merchant_name_google,
    validate_merchant_name_pinecone,
    validate_phone_number,
    validate_date,
    validate_time,
    get_unique_merchants_and_data,
    update_labels,
)


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


def list_handler(event, context):
    logger.info("Starting list_handler")
    unique_merchants_and_data = get_unique_merchants_and_data()
    logger.info(f"Found {len(unique_merchants_and_data)} unique merchants")
    return {
        "statusCode": 200,
        "batches": unique_merchants_and_data,
    }


def validate_handler(event, context):
    logger.info("Starting validate_handler")
    receipt_id = int(event["receipt_id"])
    image_id = event["image_id"]
    merchant_name = event["merchant_name"]
    receipt_count = int(event["receipt_count"])
    dynamo_client, _, _ = get_clients()
    receipt, lines, words, letters, tags, labels = dynamo_client.getReceiptDetails(
        image_id=image_id,
        receipt_id=receipt_id,
    )
    receipt_metadata = dynamo_client.getReceiptMetadata(
        image_id=image_id,
        receipt_id=receipt_id,
    )
    logger.info(f"Got receipt details for image {image_id} and receipt {receipt_id}")

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
    logger.info(f"Got {len(labels_and_words)} labels and words to validate")
    label_validation_results: list[tuple[LabelValidationResult, ReceiptWordLabel]] = []
    for label, word in labels_and_words:
        if label.label in [
            "LINE_TOTAL",
            "UNIT_PRICE",
            "TAX",
            "SUBTOTAL",
            "GRAND_TOTAL",
        ]:
            label_validation_results.append((validate_currency(word, label), label))
        elif label.label == "MERCHANT_NAME":
            if receipt_count > 4:
                label_validation_results.append(
                    (
                        validate_merchant_name_pinecone(word, label, merchant_name),
                        label,
                    )
                )
            else:
                label_validation_results.append(
                    (
                        validate_merchant_name_google(word, label, receipt_metadata),
                        label,
                    )
                )
        elif label.label == "PHONE_NUMBER":
            label_validation_results.append((validate_phone_number(word, label), label))
        elif label.label == "DATE":
            label_validation_results.append((validate_date(word, label), label))
        elif label.label == "TIME":
            label_validation_results.append((validate_time(word, label), label))
        elif label.label == "ADDRESS_LINE":
            label_validation_results.append(
                (validate_address(word, label, receipt_metadata), label)
            )
        else:
            raise ValueError(f"Unknown label type: {label.label}")

    if len(label_validation_results) > 0:
        update_labels(label_validation_results)

    logger.info(f"Validated {len(label_validation_results)} labels")
    return {
        "statusCode": 200,
        "message": f"Validated {len(label_validation_results)} labels",
    }
