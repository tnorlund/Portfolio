#!/usr/bin/env python3
"""
Export and import DynamoDB receipt data for local testing.

This script allows you to:
1. Export receipt data from DynamoDB to JSON files
2. Import receipt data from JSON files to a local DynamoDB instance
3. Work with receipt data offline for testing
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptLine, ReceiptMetadata, ReceiptWord

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime and Decimal objects."""

    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def decimal_decoder(obj):
    """Decode JSON floats as Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    return obj


def export_receipt_data(
    dynamo_client: DynamoClient, receipt_id: str, output_dir: str
) -> Dict[str, int]:
    """
    Export a single receipt's data to JSON files.

    Args:
        dynamo_client: DynamoDB client
        receipt_id: Receipt ID to export
        output_dir: Directory to save JSON files

    Returns:
        Dictionary with counts of exported items
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    receipt_dir = output_path / f"receipt_{receipt_id}"
    receipt_dir.mkdir(exist_ok=True)

    counts = {}

    # Export receipt words
    logger.info(f"Exporting words for receipt {receipt_id}...")
    words = dynamo_client.list_receipt_words_by_receipt(receipt_id)
    if words:
        words_data = [word.to_dict() for word in words]
        with open(receipt_dir / "words.json", "w") as f:
            json.dump(words_data, f, indent=2, cls=DateTimeEncoder)
        counts["words"] = len(words)
        logger.info(f"  Exported {len(words)} words")

    # Export receipt lines
    logger.info(f"Exporting lines for receipt {receipt_id}...")
    lines = dynamo_client.list_receipt_lines_by_receipt(receipt_id)
    if lines:
        lines_data = [line.to_dict() for line in lines]
        with open(receipt_dir / "lines.json", "w") as f:
            json.dump(lines_data, f, indent=2, cls=DateTimeEncoder)
        counts["lines"] = len(lines)
        logger.info(f"  Exported {len(lines)} lines")

    # Export receipt metadata
    logger.info(f"Exporting metadata for receipt {receipt_id}...")
    try:
        metadata = dynamo_client.get_receipt_metadata(receipt_id)
        if metadata:
            with open(receipt_dir / "metadata.json", "w") as f:
                json.dump(metadata.to_dict(), f, indent=2, cls=DateTimeEncoder)
            counts["metadata"] = 1
            logger.info(f"  Exported metadata")
    except Exception as e:
        logger.warning(f"  No metadata found: {e}")

    # Export word labels if they exist
    logger.info(f"Exporting word labels for receipt {receipt_id}...")
    try:
        labels = dynamo_client.list_receipt_word_labels_by_receipt(receipt_id)
        if labels:
            labels_data = [label.to_dict() for label in labels]
            with open(receipt_dir / "labels.json", "w") as f:
                json.dump(labels_data, f, indent=2, cls=DateTimeEncoder)
            counts["labels"] = len(labels)
            logger.info(f"  Exported {len(labels)} labels")
    except Exception as e:
        logger.warning(f"  No labels found: {e}")

    # Save summary
    summary = {
        "receipt_id": receipt_id,
        "export_date": datetime.utcnow().isoformat(),
        "counts": counts,
    }
    with open(receipt_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return counts


def export_image_receipts(
    dynamo_client: DynamoClient,
    image_id: str,
    output_dir: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """Export all receipts from an image."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    image_dir = output_path / f"image_{image_id}"
    image_dir.mkdir(exist_ok=True)

    # Query receipts for image
    logger.info(f"Finding receipts for image {image_id}...")
    items = dynamo_client.query(
        pk_value=f"IMAGE#{image_id}", sk_prefix="RECEIPT#", limit=limit
    )

    receipt_ids = list(
        set(item["receipt_id"] for item in items if "receipt_id" in item)
    )
    logger.info(f"Found {len(receipt_ids)} receipts")

    all_counts = {}
    for receipt_id in receipt_ids:
        counts = export_receipt_data(
            dynamo_client, str(receipt_id), str(image_dir)
        )
        all_counts[receipt_id] = counts

    # Save image summary
    summary = {
        "image_id": image_id,
        "export_date": datetime.utcnow().isoformat(),
        "receipt_count": len(receipt_ids),
        "receipt_ids": receipt_ids,
        "counts": all_counts,
    }
    with open(image_dir / "image_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return summary


def import_receipt_data(
    dynamo_client: DynamoClient, input_dir: str
) -> Dict[str, int]:
    """
    Import receipt data from JSON files.

    Args:
        dynamo_client: DynamoDB client
        input_dir: Directory containing receipt JSON files

    Returns:
        Dictionary with counts of imported items
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        raise ValueError(f"Input directory does not exist: {input_dir}")

    counts = {}

    # Import words
    words_file = input_path / "words.json"
    if words_file.exists():
        logger.info("Importing words...")
        with open(words_file) as f:
            words_data = json.load(f, parse_float=decimal_decoder)

        for word_dict in words_data:
            word = ReceiptWord.from_dict(word_dict)
            dynamo_client.put_receipt_word(word)

        counts["words"] = len(words_data)
        logger.info(f"  Imported {len(words_data)} words")

    # Import lines
    lines_file = input_path / "lines.json"
    if lines_file.exists():
        logger.info("Importing lines...")
        with open(lines_file) as f:
            lines_data = json.load(f, parse_float=decimal_decoder)

        for line_dict in lines_data:
            line = ReceiptLine.from_dict(line_dict)
            dynamo_client.put_receipt_line(line)

        counts["lines"] = len(lines_data)
        logger.info(f"  Imported {len(lines_data)} lines")

    # Import metadata
    metadata_file = input_path / "metadata.json"
    if metadata_file.exists():
        logger.info("Importing metadata...")
        with open(metadata_file) as f:
            metadata_dict = json.load(f, parse_float=decimal_decoder)

        metadata = ReceiptMetadata.from_dict(metadata_dict)
        dynamo_client.put_receipt_metadata(metadata)

        counts["metadata"] = 1
        logger.info(f"  Imported metadata")

    return counts


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Export and import DynamoDB receipt data"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Export command
    export_parser = subparsers.add_parser(
        "export", help="Export data from DynamoDB"
    )
    export_parser.add_argument("--receipt-id", help="Export specific receipt")
    export_parser.add_argument(
        "--image-id", help="Export all receipts from an image"
    )
    export_parser.add_argument(
        "--output-dir",
        default="./receipt_data",
        help="Output directory (default: ./receipt_data)",
    )
    export_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Limit receipts per image (default: 10)",
    )

    # Import command
    import_parser = subparsers.add_parser(
        "import", help="Import data to DynamoDB"
    )
    import_parser.add_argument(
        "input_dir", help="Directory containing receipt JSON files"
    )
    import_parser.add_argument(
        "--table-name", help="Override DynamoDB table name"
    )

    args = parser.parse_args()

    if not args.command:
        parser.error("Please specify a command: export or import")

    # Get table name
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if args.command == "import" and args.table_name:
        table_name = args.table_name

    if not table_name:
        logger.error("DYNAMODB_TABLE_NAME environment variable is required")
        sys.exit(1)

    # Create DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Execute command
    if args.command == "export":
        if args.receipt_id:
            counts = export_receipt_data(
                dynamo_client, args.receipt_id, args.output_dir
            )
            logger.info(f"\nExport complete: {counts}")
        elif args.image_id:
            summary = export_image_receipts(
                dynamo_client, args.image_id, args.output_dir, args.limit
            )
            logger.info(
                f"\nExport complete: {summary['receipt_count']} receipts"
            )
        else:
            parser.error("Please specify --receipt-id or --image-id")

    elif args.command == "import":
        counts = import_receipt_data(dynamo_client, args.input_dir)
        logger.info(f"\nImport complete: {counts}")


if __name__ == "__main__":
    main()
