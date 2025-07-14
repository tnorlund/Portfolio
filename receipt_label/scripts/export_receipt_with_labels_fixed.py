#!/usr/bin/env python3
"""
Fixed export script that properly queries receipt word labels.
"""

import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from receipt_dynamo import DynamoClient


def get_receipt_word_labels_for_image(dynamo_client, image_id: str):
    """
    Get receipt word labels for an image using proper query.

    Since the SK contains #LABEL# at the end, we need to query differently.
    """
    labels = []

    # Query all items for this image that contain LABEL in the SK
    query_params = {
        "TableName": dynamo_client.table_name,
        "KeyConditionExpression": "#pk = :pk",
        "ExpressionAttributeNames": {
            "#pk": "PK",
        },
        "ExpressionAttributeValues": {
            ":pk": {"S": f"IMAGE#{image_id}"},
        },
    }

    try:
        response = dynamo_client._client.query(**query_params)

        # Filter for items that have #LABEL# in their SK
        for item in response.get("Items", []):
            sk = item.get("SK", {}).get("S", "")
            if "#LABEL#" in sk:
                # This is a label item - parse it
                try:
                    # Extract fields from SK
                    parts = sk.split("#")
                    receipt_id = int(parts[1])
                    line_id = int(parts[3])
                    word_id = int(parts[5])
                    label_type = parts[7] if len(parts) > 7 else ""

                    label_data = {
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                        "line_id": line_id,
                        "word_id": word_id,
                        "label_type": label_type,
                        "validation_status": item.get(
                            "validation_status", {}
                        ).get("S"),
                        "reasoning": item.get("reasoning", {}).get("S"),
                        "label_proposed_by": item.get(
                            "label_proposed_by", {}
                        ).get("S"),
                        "label_consolidated_from": item.get(
                            "label_consolidated_from", {}
                        ).get("S"),
                        "timestamp_added": item.get("timestamp_added", {}).get(
                            "S"
                        ),
                    }
                    labels.append(label_data)
                except (IndexError, ValueError) as e:
                    print(
                        f"Warning: Could not parse label from SK: {sk} - {e}"
                    )

        # Handle pagination
        while "LastEvaluatedKey" in response:
            query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = dynamo_client._client.query(**query_params)

            for item in response.get("Items", []):
                sk = item.get("SK", {}).get("S", "")
                if "#LABEL#" in sk:
                    try:
                        parts = sk.split("#")
                        receipt_id = int(parts[1])
                        line_id = int(parts[3])
                        word_id = int(parts[5])
                        label_type = parts[7] if len(parts) > 7 else ""

                        label_data = {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "line_id": line_id,
                            "word_id": word_id,
                            "label_type": label_type,
                            "validation_status": item.get(
                                "validation_status", {}
                            ).get("S"),
                            "reasoning": item.get("reasoning", {}).get("S"),
                            "label_proposed_by": item.get(
                                "label_proposed_by", {}
                            ).get("S"),
                            "label_consolidated_from": item.get(
                                "label_consolidated_from", {}
                            ).get("S"),
                            "timestamp_added": item.get(
                                "timestamp_added", {}
                            ).get("S"),
                        }
                        labels.append(label_data)
                    except (IndexError, ValueError) as e:
                        print(
                            f"Warning: Could not parse label from SK: {sk} - {e}"
                        )

    except Exception as e:
        print(f"Error querying labels: {e}")

    return labels


def export_image_with_labels(
    table_name: str, image_id: str, output_dir: str
) -> None:
    """
    Exports all DynamoDB data related to an image as JSON, including labels.
    """
    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get all data from DynamoDB
    details = dynamo_client.get_image_details(image_id)

    # Get receipt word labels using fixed query
    receipt_word_labels = get_receipt_word_labels_for_image(
        dynamo_client, image_id
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
        "receipt_word_labels": receipt_word_labels,
    }

    output_file = os.path.join(output_dir, f"{image_id}.json")
    with open(output_file, "w") as f:
        json.dump(results, f, indent=4)

    return len(receipt_word_labels)


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

    label_count = export_image_with_labels(
        args.table_name, args.image_id, args.output_dir
    )
    print(f"Exported {args.image_id} with {label_count} labels")
