#!/usr/bin/env python3
"""
Enhanced export script that includes receipt word labels.
"""

import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from receipt_dynamo import DynamoClient


def export_image_with_labels(
    table_name: str, image_id: str, output_dir: str
) -> None:
    """
    Exports all DynamoDB data related to an image as JSON, including labels.

    Args:
        table_name: The DynamoDB table name
        image_id: UUID of the image to export
        output_dir: Directory where JSON file should be exported
    """
    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get all data from DynamoDB
    details = dynamo_client.get_image_details(image_id)

    # Get receipt word labels
    receipt_word_labels = dynamo_client.list_receipt_word_labels_for_image(
        image_id
    )

    images = details.images
    lines = details.lines
    words = details.words
    letters = details.letters
    receipts = details.receipts
    receipt_lines = details.receipt_lines
    receipt_words = details.receipt_words
    receipt_letters = details.receipt_letters
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
        # Add receipt word labels
        "receipt_word_labels": [
            {
                "image_id": label.image_id,
                "receipt_id": label.receipt_id,
                "line_id": label.line_id,
                "word_id": label.word_id,
                "label_type": label.label,  # Note: label attribute contains the type
                "reasoning": label.reasoning,
                "timestamp_added": (
                    label.timestamp_added.isoformat()
                    if hasattr(label.timestamp_added, "isoformat")
                    else str(label.timestamp_added)
                ),
                "validation_status": label.validation_status,
                "label_proposed_by": label.label_proposed_by,
                "label_consolidated_from": label.label_consolidated_from,
            }
            for label in receipt_word_labels
        ],
    }

    output_file = os.path.join(output_dir, f"{image_id}.json")
    with open(output_file, "w") as f:
        json.dump(results, f, indent=4)

    print(
        f"Exported {image_id} with {len(receipt_word_labels)} labels to {output_file}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export receipt with labels")
    parser.add_argument("table_name", help="DynamoDB table name")
    parser.add_argument("image_id", help="Image ID to export")
    parser.add_argument(
        "--output-dir",
        default="./receipt_data_with_labels",
        help="Output directory",
    )

    args = parser.parse_args()

    export_image_with_labels(args.table_name, args.image_id, args.output_dir)
