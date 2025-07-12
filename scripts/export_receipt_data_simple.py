#!/usr/bin/env python3
"""
Simplified DynamoDB receipt data export tool for testing.

This is a minimal version that exports receipts with their associated data.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo import DynamoClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime and Decimal objects."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def export_receipts(dynamo_client: DynamoClient, output_dir: str, limit: int = 5):
    """Export receipts with their associated data."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    logger.info(f"Listing up to {limit} receipts...")
    
    # Get receipts using the correct method
    receipts_data, last_key = dynamo_client.list_receipts(limit=limit)
    
    logger.info(f"Found {len(receipts_data)} receipts")
    
    export_count = 0
    
    # Check the structure of receipts_data
    if receipts_data and len(receipts_data) > 0:
        logger.debug(f"First item type: {type(receipts_data[0])}")
    
    for receipt in receipts_data:
        # receipts_data contains Receipt objects directly
        if hasattr(receipt, 'image_id') and hasattr(receipt, 'receipt_id'):
            
            logger.info(f"Exporting receipt {receipt.receipt_id} from image {receipt.image_id}")
            
            # Create directory for this receipt
            receipt_dir = output_path / f"image_{receipt.image_id}_receipt_{receipt.receipt_id:05d}"
            receipt_dir.mkdir(exist_ok=True)
            
            # Export receipt entity
            with open(receipt_dir / "receipt.json", "w") as f:
                json.dump(receipt.to_dict(), f, indent=2, cls=DateTimeEncoder)
            
            # Export words
            try:
                words = dynamo_client.list_receipt_words_from_receipt(receipt.image_id, receipt.receipt_id)
                if words:
                    words_data = [word.to_dict() for word in words]
                    with open(receipt_dir / "words.json", "w") as f:
                        json.dump(words_data, f, indent=2, cls=DateTimeEncoder)
                    logger.info(f"  → Exported {len(words)} words")
            except Exception as e:
                logger.warning(f"  → Failed to export words: {e}")
            
            # Export lines
            try:
                lines = dynamo_client.list_receipt_lines_from_receipt(receipt.image_id, receipt.receipt_id)
                if lines:
                    lines_data = [line.to_dict() for line in lines]
                    with open(receipt_dir / "lines.json", "w") as f:
                        json.dump(lines_data, f, indent=2, cls=DateTimeEncoder)
                    logger.info(f"  → Exported {len(lines)} lines")
            except Exception as e:
                logger.warning(f"  → Failed to export lines: {e}")
            
            # Export metadata
            try:
                metadata = dynamo_client.get_receipt_metadata(str(receipt.receipt_id))
                if metadata:
                    with open(receipt_dir / "metadata.json", "w") as f:
                        json.dump(metadata.to_dict(), f, indent=2, cls=DateTimeEncoder)
                    logger.info(f"  → Exported metadata: {metadata.merchant_name}")
            except Exception as e:
                logger.debug(f"  → No metadata: {e}")
            
            # Export labels
            try:
                # Note: There might not be a direct method for this, we'll skip for now
                labels = []  # dynamo_client.list_receipt_word_labels_by_receipt(str(receipt.receipt_id))
                if labels:
                    labels_data = [label.to_dict() for label in labels]
                    with open(receipt_dir / "labels.json", "w") as f:
                        json.dump(labels_data, f, indent=2, cls=DateTimeEncoder)
                    logger.info(f"  → Exported {len(labels)} labels")
            except Exception as e:
                logger.debug(f"  → No labels: {e}")
            
            export_count += 1
    
    # Create index
    index = {
        "export_date": datetime.now().isoformat(),
        "total_receipts": export_count,
        "receipts": []
    }
    
    # Add receipt info to index
    for receipt_dir in output_path.glob("image_*_receipt_*"):
        if receipt_dir.is_dir():
            # Parse image_id and receipt_id from directory name
            parts = receipt_dir.name.split("_")
            if len(parts) >= 4:
                image_id = parts[1]
                receipt_id = parts[3]
                index["receipts"].append({
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "directory": receipt_dir.name
                })
    
    with open(output_path / "index.json", "w") as f:
        json.dump(index, f, indent=2)
    
    logger.info(f"\nExport complete: {export_count} receipts exported to {output_path}")
    

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Simple receipt data export tool"
    )
    
    parser.add_argument(
        "--output-dir",
        default="./receipt_data",
        help="Output directory (default: ./receipt_data)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of receipts to export (default: 5)"
    )
    parser.add_argument(
        "--table-name",
        help="DynamoDB table name (overrides environment variable)"
    )
    
    args = parser.parse_args()
    
    # Get table name
    table_name = args.table_name or os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        logger.error("DYNAMODB_TABLE_NAME environment variable or --table-name is required")
        sys.exit(1)
    
    # Create DynamoDB client
    dynamo_client = DynamoClient(table_name)
    
    # Export receipts
    export_receipts(dynamo_client, args.output_dir, args.limit)


if __name__ == "__main__":
    main()