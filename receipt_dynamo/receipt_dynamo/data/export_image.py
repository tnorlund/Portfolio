# infra/lambda_layer/python/dynamo/data/export_image.py
import json
import os

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
    details = dynamo_client.get_image_details(image_id)

    images = details.images
    lines = details.lines
    words = details.words
    letters = details.letters
    receipts = details.receipts
    receipt_lines = details.receipt_lines
    receipt_words = details.receipt_words
    receipt_letters = details.receipt_letters
    receipt_word_labels = details.receipt_word_labels
    receipt_metadatas = details.receipt_metadatas
    ocr_jobs = details.ocr_jobs
    ocr_routing_decisions = details.ocr_routing_decisions

    if not images:
        raise ValueError(f"No image found for image_id {image_id}")

    # Export DynamoDB data as JSON
    results = {
        "images": [dict(image) for image in images],
        "lines": [dict(line) for line in lines],
        "words": [dict(word) for word in words],
        "letters": [dict(letter) for letter in letters],
        "receipts": [dict(receipt) for receipt in receipts],
        "receipt_lines": [dict(line) for line in receipt_lines],
        "receipt_words": [dict(word) for word in receipt_words],
        "receipt_letters": [dict(letter) for letter in receipt_letters],
        "receipt_word_labels": [dict(label) for label in receipt_word_labels],
        "receipt_metadatas": [
            dict(metadata) for metadata in receipt_metadatas
        ],
        "ocr_jobs": [dict(job) for job in ocr_jobs],
        "ocr_routing_decisions": [
            {
                "image_id": decision.image_id,
                "job_id": decision.job_id,
                "s3_bucket": decision.s3_bucket,
                "s3_key": decision.s3_key,
                "created_at": decision.created_at.isoformat(),
                "updated_at": (
                    decision.updated_at.isoformat()
                    if decision.updated_at
                    else None
                ),
                "receipt_count": decision.receipt_count,
                "status": decision.status,
            }
            for decision in ocr_routing_decisions
        ],
    }

    with open(os.path.join(output_dir, f"{image_id}.json"), "w") as f:
        json.dump(results, f, indent=4)
