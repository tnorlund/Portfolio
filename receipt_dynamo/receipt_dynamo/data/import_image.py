# infra/lambda_layer/python/dynamo/data/import_image.py
import os
import json
from typing import Dict, Any
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (
    Image,
    Line,
    Word,
    WordTag,
    Letter,
    Receipt,
    ReceiptWindow,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    ReceiptLetter,
    GPTInitialTagging,
    GPTValidation,
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
    entities = {
        "images": [Image(**item) for item in data["images"]],
        "lines": [Line(**item) for item in data["lines"]],
        "words": [Word(**item) for item in data["words"]],
        "word_tags": [WordTag(**item) for item in data["word_tags"]],
        "letters": [Letter(**item) for item in data["letters"]],
        "receipts": [Receipt(**item) for item in data["receipts"]],
        "receipt_windows": [ReceiptWindow(**item) for item in data["receipt_windows"]],
        "receipt_lines": [ReceiptLine(**item) for item in data["receipt_lines"]],
        "receipt_words": [ReceiptWord(**item) for item in data["receipt_words"]],
        "receipt_word_tags": [
            ReceiptWordTag(**item) for item in data["receipt_word_tags"]
        ],
        "receipt_letters": [ReceiptLetter(**item) for item in data["receipt_letters"]],
        "gpt_initial_taggings": [
            GPTInitialTagging(**item) for item in data["gpt_initial_taggings"]
        ],
        "gpt_validations": [GPTValidation(**item) for item in data["gpt_validations"]],
    }

    # Import data in batches using existing DynamoClient methods
    if entities["images"]:
        dynamo_client.addImages(entities["images"])

    if entities["lines"]:
        dynamo_client.addLines(entities["lines"])

    if entities["words"]:
        dynamo_client.addWords(entities["words"])

    if entities["word_tags"]:
        dynamo_client.addWordTags(entities["word_tags"])

    if entities["letters"]:
        dynamo_client.addLetters(entities["letters"])

    if entities["receipts"]:
        dynamo_client.addReceipts(entities["receipts"])

    if entities["receipt_windows"]:
        dynamo_client.addReceiptWindows(entities["receipt_windows"])

    if entities["receipt_lines"]:
        dynamo_client.addReceiptLines(entities["receipt_lines"])

    if entities["receipt_words"]:
        dynamo_client.addReceiptWords(entities["receipt_words"])

    if entities["receipt_word_tags"]:
        dynamo_client.addReceiptWordTags(entities["receipt_word_tags"])

    if entities["receipt_letters"]:
        dynamo_client.addReceiptLetters(entities["receipt_letters"])

    if entities["gpt_initial_taggings"]:
        dynamo_client.addGPTInitialTaggings(entities["gpt_initial_taggings"])

    if entities["gpt_validations"]:
        dynamo_client.addGPTValidations(entities["gpt_validations"])
