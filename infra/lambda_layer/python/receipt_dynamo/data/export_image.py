# infra/lambda_layer/python/dynamo/data/export_image.py
import os
import json
from receipt_dynamo.data.dynamo_client import DynamoClient


def export_image(table_name: str, image_id: str, output_dir: str) -> None:
    """
    Exports all DynamoDB data related to an image as JSON.

    Args:
        table_name (str): The DynamoDB table name where receipt data is stored
        image_id (str): UUID of the image to export
        output_dir (str): Directory where JSON file should be exported

    Raises:
        ValueError: If table_name is not provided and the environment variable DYNAMO_DB_TABLE is not set
        Exception: If there are errors accessing DynamoDB

    Example:
        >>> export_image("ReceiptsTable", "550e8400-e29b-41d4-a716-446655440000", "./export")
    """

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get all data from DynamoDB
    (
        images,
        lines,
        words,
        word_tags,
        letters,
        receipts,
        receipt_windows,
        receipt_lines,
        receipt_words,
        receipt_word_tags,
        receipt_letters,
        gpt_initial_taggings,
        gpt_validations,
    ) = dynamo_client.getImageDetails(image_id)

    if not images:
        raise ValueError(f"No image found for image_id {image_id}")

    # Export DynamoDB data as JSON
    results = {
        "images": [dict(image) for image in images],
        "lines": [dict(line) for line in lines],
        "words": [dict(word) for word in words],
        "word_tags": [dict(word_tag) for word_tag in word_tags],
        "letters": [dict(letter) for letter in letters],
        "receipts": [dict(receipt) for receipt in receipts],
        "receipt_windows": [dict(window) for window in receipt_windows],
        "receipt_lines": [dict(line) for line in receipt_lines],
        "receipt_words": [dict(word) for word in receipt_words],
        "receipt_word_tags": [dict(word_tag) for word_tag in receipt_word_tags],
        "receipt_letters": [dict(letter) for letter in receipt_letters],
        "gpt_initial_taggings": [dict(gpt_query) for gpt_query in gpt_initial_taggings],
        "gpt_validations": [dict(gpt_validation) for gpt_validation in gpt_validations],
    }

    with open(os.path.join(output_dir, f"{image_id}.json"), "w") as f:
        json.dump(results, f, indent=4)
