import os
import json
from typing import Dict, Any
from dynamo.data.dynamo_client import DynamoClient
from dynamo.entities import (
    Image, Line, Word, WordTag, Letter,
    Receipt, ReceiptLine, ReceiptWord, ReceiptWordTag, ReceiptLetter,
    InitialGPTQuery, GPTValidation
)


def import_data(table_name: str, json_path: str) -> None:
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
        >>> import_data("ReceiptsTable", "./export/image-id_RESULTS.json")
    """
    if not table_name:
        # Check the environment variable
        table_name = os.getenv("DYNAMO_DB_TABLE")
        if not table_name:
            raise ValueError("The table_name parameter is required")

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Read the JSON file
    with open(json_path, 'r') as f:
        data = json.load(f)

    # Convert dictionaries back to entity objects
    entities = {
        "images": [Image(**item) for item in data["images"]],
        "lines": [Line(**item) for item in data["lines"]],
        "words": [Word(**item) for item in data["words"]],
        "word_tags": [WordTag(**item) for item in data["word_tags"]],
        "letters": [Letter(**item) for item in data["letters"]],
        "receipts": [Receipt(**item) for item in data["receipts"]],
        "receipt_lines": [ReceiptLine(**item) for item in data["receipt_lines"]],
        "receipt_words": [ReceiptWord(**item) for item in data["receipt_words"]],
        "receipt_word_tags": [ReceiptWordTag(**item) for item in data["receipt_word_tags"]],
        "receipt_letters": [ReceiptLetter(**item) for item in data["receipt_letters"]],
        "initial_gpt_taggings": [InitialGPTQuery(**item) for item in data["initial_gpt_taggings"]],
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
    
    if entities["receipt_lines"]:
        dynamo_client.addReceiptLines(entities["receipt_lines"])
    
    if entities["receipt_words"]:
        dynamo_client.addReceiptWords(entities["receipt_words"])
    
    if entities["receipt_word_tags"]:
        dynamo_client.addReceiptWordTags(entities["receipt_word_tags"])
    
    if entities["receipt_letters"]:
        dynamo_client.addReceiptLetters(entities["receipt_letters"])
    
    if entities["initial_gpt_taggings"]:
        dynamo_client.addGPTInitialTaggings(entities["initial_gpt_taggings"])
    
    if entities["gpt_validations"]:
        dynamo_client.addGPTValidations(entities["gpt_validations"]) 