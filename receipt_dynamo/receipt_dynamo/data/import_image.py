# infra/lambda_layer/python/dynamo/data/import_image.py
import json
import os
from typing import Any, Dict, List

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (
    Image,
    Letter,
    Line,
    OCRJob,
    OCRRoutingDecision,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
    Word,
)


def import_image(table_name: str, json_path: str) -> None:
    """
    Imports data from a JSON file into DynamoDB.
    The JSON file should be in the format produced by the export() function.

    Args:
        table_name (str): The DynamoDB table name where data should be imported
        json_path (str): Path to the JSON file containing the data

    Raises:
        ValueError: If table_name is not provided and the environment variable DYNAMO_DB_TABLE is not set
        FileNotFoundError: If the JSON file doesn't exist
        Exception: If there are errors accessing DynamoDB

    Example:
        >>> import_image("ReceiptsTable", "./export/image-id.json")
    """

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Read the JSON file
    with open(json_path, "r") as f:
        data = json.load(f)

    # Convert dictionaries back to entity objects
    entities: Dict[str, List[Any]] = {
        "images": [Image(**item) for item in data["images"]],
        "lines": [Line(**item) for item in data["lines"]],
        "words": [Word(**item) for item in data["words"]],
        "letters": [Letter(**item) for item in data["letters"]],
        "receipts": [Receipt(**item) for item in data["receipts"]],
        "receipt_lines": [
            ReceiptLine(**item) for item in data["receipt_lines"]
        ],
        "receipt_words": [
            ReceiptWord(**item) for item in data["receipt_words"]
        ],
        "receipt_letters": [
            ReceiptLetter(**item) for item in data["receipt_letters"]
        ],
        "receipt_word_labels": [
            ReceiptWordLabel(**item)
            for item in data.get("receipt_word_labels", [])
        ],
        "receipt_metadatas": [
            ReceiptMetadata(**item)
            for item in data.get("receipt_metadatas", [])
        ],
        "ocr_jobs": [OCRJob(**item) for item in data.get("ocr_jobs", [])],
        "ocr_routing_decisions": [
            OCRRoutingDecision(**item)
            for item in data.get("ocr_routing_decisions", [])
        ],
    }

    # Import data in batches using existing DynamoClient methods
    if entities["images"]:
        dynamo_client.add_images(entities["images"])  # type: ignore[arg-type]

    if entities["lines"]:
        dynamo_client.add_lines(entities["lines"])  # type: ignore[arg-type]

    if entities["words"]:
        dynamo_client.add_words(entities["words"])  # type: ignore[arg-type]

    if entities["letters"]:
        dynamo_client.add_letters(entities["letters"])  # type: ignore[arg-type]

    if entities["receipts"]:
        dynamo_client.add_receipts(entities["receipts"])  # type: ignore[arg-type]

    if entities["receipt_lines"]:
        dynamo_client.add_receipt_lines(entities["receipt_lines"])  # type: ignore[arg-type]

    if entities["receipt_words"]:
        dynamo_client.add_receipt_words(entities["receipt_words"])  # type: ignore[arg-type]

    if entities["receipt_letters"]:
        dynamo_client.add_receipt_letters(entities["receipt_letters"])  # type: ignore[arg-type]

    if entities["receipt_word_labels"]:
        dynamo_client.add_receipt_word_labels(entities["receipt_word_labels"])  # type: ignore[arg-type]

    if entities["receipt_metadatas"]:
        dynamo_client.add_receipt_metadatas(entities["receipt_metadatas"])  # type: ignore[arg-type]

    if entities["ocr_jobs"]:
        dynamo_client.add_ocr_jobs(entities["ocr_jobs"])  # type: ignore[arg-type]

    if entities["ocr_routing_decisions"]:
        dynamo_client.add_ocr_routing_decisions(entities["ocr_routing_decisions"])  # type: ignore[arg-type]
