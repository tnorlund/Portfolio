#!/usr/bin/env python3
"""
Export receipt data from DynamoDB for local development and testing.

This script exports DynamoDB receipt data to JSON files for local development,
allowing developers to work without making external API calls or incurring costs.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from receipt_dynamo.data.dynamo_client import DynamoClient


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""
    
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class ReceiptDataExporter:
    """Export receipt data from DynamoDB to local JSON files."""
    
    def __init__(self, table_name: Optional[str] = None, output_dir: str = "./receipt_data"):
        """
        Initialize the exporter.
        
        Args:
            table_name: DynamoDB table name (defaults to environment variable)
            output_dir: Directory to export data to
        """
        self.table_name = table_name or os.getenv("DYNAMODB_TABLE_NAME")
        if not self.table_name:
            raise ValueError("Table name must be provided or set in DYNAMODB_TABLE_NAME")
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        # Initialize DynamoDB client
        try:
            self.dynamo_client = DynamoClient(table_name=self.table_name)
            logging.info(f"Connected to DynamoDB table: {self.table_name}")
        except Exception as e:
            logging.error(f"Failed to connect to DynamoDB: {e}")
            raise
    
    def export_sample_data(self, size: int = 20, merchant_filter: Optional[str] = None) -> int:
        """
        Export a sample of receipt data for development.
        
        Args:
            size: Number of receipts to export
            merchant_filter: Optional merchant name filter
            
        Returns:
            Number of receipts exported
        """
        logging.info(f"Exporting sample data (size={size}, merchant_filter={merchant_filter})")
        
        try:
            # Get list of receipts
            receipts = self._get_sample_receipts(size, merchant_filter)
            
            if not receipts:
                logging.warning("No receipts found to export")
                return 0
            
            # Export each receipt
            exported_count = 0
            for receipt in receipts:
                try:
                    self._export_receipt(receipt.image_id, str(receipt.receipt_id))
                    exported_count += 1
                    if exported_count % 5 == 0:
                        logging.info(f"Exported {exported_count}/{len(receipts)} receipts")
                except Exception as e:
                    logging.error(f"Failed to export receipt {receipt.image_id}/{receipt.receipt_id}: {e}")
            
            # Create sample index
            self._create_sample_index(receipts[:exported_count])
            
            logging.info(f"Successfully exported {exported_count} receipts to {self.output_dir}")
            return exported_count
            
        except Exception as e:
            logging.error(f"Error during sample export: {e}")
            raise
    
    def export_specific_receipt(self, image_id: str, receipt_id: str) -> bool:
        """
        Export a specific receipt by ID.
        
        Args:
            image_id: Image ID
            receipt_id: Receipt ID
            
        Returns:
            True if successful
        """
        logging.info(f"Exporting specific receipt: {image_id}/{receipt_id}")
        
        try:
            self._export_receipt(image_id, receipt_id)
            logging.info(f"Successfully exported receipt {image_id}/{receipt_id}")
            return True
        except Exception as e:
            logging.error(f"Failed to export receipt {image_id}/{receipt_id}: {e}")
            return False
    
    def export_by_merchant(self, merchant_name: str, limit: int = 10) -> int:
        """
        Export receipts for a specific merchant.
        
        Args:
            merchant_name: Merchant name to filter by
            limit: Maximum number of receipts to export
            
        Returns:
            Number of receipts exported
        """
        logging.info(f"Exporting receipts for merchant: {merchant_name} (limit={limit})")
        return self.export_sample_data(size=limit, merchant_filter=merchant_name)
    
    def _get_sample_receipts(self, size: int, merchant_filter: Optional[str] = None):
        """Get a sample of receipts from DynamoDB."""
        try:
            receipts = []
            
            # Use GSITYPE to find receipts efficiently
            import boto3
            dynamodb = boto3.client('dynamodb')
            
            # Query the GSITYPE index for RECEIPT type
            response = dynamodb.query(
                TableName=self.table_name,
                IndexName='GSITYPE',
                KeyConditionExpression='#type = :receipt_type',
                ExpressionAttributeNames={
                    '#type': 'TYPE'
                },
                ExpressionAttributeValues={
                    ':receipt_type': {'S': 'RECEIPT'}
                },
                Limit=size * 2  # Get more than needed for filtering
            )
            
            for item in response.get('Items', []):
                if len(receipts) >= size:
                    break
                
                # Extract image_id and receipt_id from the item
                pk = item.get('PK', {}).get('S', '')
                sk = item.get('SK', {}).get('S', '')
                
                if pk.startswith('IMAGE#') and sk.startswith('RECEIPT#'):
                    image_id = pk.replace('IMAGE#', '')
                    receipt_id_str = sk.replace('RECEIPT#', '').lstrip('0')
                    
                    try:
                        receipt_id = int(receipt_id_str)
                    except ValueError:
                        continue
                    
                    # Apply merchant filter if specified
                    if merchant_filter:
                        merchant_name = item.get('merchant_name', {}).get('S', '')
                        if merchant_filter.lower() not in merchant_name.lower():
                            continue
                    
                    # Create a simple receipt object
                    class SimpleReceipt:
                        def __init__(self, image_id, receipt_id):
                            self.image_id = image_id
                            self.receipt_id = receipt_id
                    
                    receipts.append(SimpleReceipt(image_id, receipt_id))
            
            logging.info(f"Found {len(receipts)} receipts in DynamoDB")
            return receipts[:size]
            
        except Exception as e:
            logging.error(f"Error getting sample receipts: {e}")
            return []
    
    def _export_receipt(self, image_id: str, receipt_id: str):
        """Export a single receipt and all its associated data."""
        receipt_dir = self.output_dir / f"image_{image_id}_receipt_{receipt_id}"
        receipt_dir.mkdir(exist_ok=True)
        
        try:
            # Load receipt
            receipt = self.dynamo_client.get_receipt(image_id, int(receipt_id))
            if receipt:
                self._save_json(receipt_dir / "receipt.json", receipt.__dict__)
                logging.info(f"✓ Exported receipt for {image_id}/{receipt_id}")
            else:
                logging.warning(f"No receipt found for {image_id}/{receipt_id}")
            
            # Load words (skip for now due to data structure issues)
            logging.debug(f"Attempting to load words for {image_id}/{receipt_id}")
            try:
                words = self.dynamo_client.list_receipt_words_from_receipt(image_id, int(receipt_id))
                if words:
                    words_data = [word.__dict__ for word in words]
                    self._save_json(receipt_dir / "words.json", words_data)
                    logging.info(f"✓ Exported {len(words)} words for {image_id}/{receipt_id}")
                else:
                    logging.warning(f"No words found for {image_id}/{receipt_id}")
            except Exception as e:
                logging.error(f"Error loading words for {image_id}/{receipt_id}: {e}")
                logging.warning(f"Skipping words export for {image_id}/{receipt_id} due to data structure issues")
                # Create empty words file so pattern detection doesn't fail
                self._save_json(receipt_dir / "words.json", [])
            
            # Load lines
            logging.debug(f"Attempting to load lines for {image_id}/{receipt_id}")
            try:
                lines = self.dynamo_client.list_receipt_lines_from_receipt(image_id, int(receipt_id))
                if lines:
                    lines_data = [line.__dict__ for line in lines]
                    self._save_json(receipt_dir / "lines.json", lines_data)
                    logging.info(f"✓ Exported {len(lines)} lines for {image_id}/{receipt_id}")
                else:
                    logging.warning(f"No lines found for {image_id}/{receipt_id}")
            except Exception as e:
                logging.error(f"Error loading lines for {image_id}/{receipt_id}: {e}")
                logging.warning(f"Skipping lines export for {image_id}/{receipt_id} due to data structure issues")
                # Create empty lines file so pattern detection doesn't fail
                self._save_json(receipt_dir / "lines.json", [])
            
            # Load labels (if any)
            try:
                labels = self.dynamo_client.list_receipt_word_labels_for_image(image_id)
                if labels:
                    labels_data = [label.__dict__ for label in labels]
                    self._save_json(receipt_dir / "labels.json", labels_data)
            except Exception as e:
                logging.debug(f"No labels found for receipt {image_id}/{receipt_id}: {e}")
            
            # Load metadata (if any)
            try:
                metadata = self.dynamo_client.get_receipt_metadata(image_id, int(receipt_id))
                if metadata:
                    self._save_json(receipt_dir / "metadata.json", metadata.__dict__)
            except Exception as e:
                logging.debug(f"No metadata found for receipt {image_id}/{receipt_id}: {e}")
                
        except Exception as e:
            logging.error(f"Error exporting receipt {image_id}/{receipt_id}: {e}")
            raise
    
    def _save_json(self, file_path: Path, data):
        """Save data as JSON with proper encoding."""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, cls=DecimalEncoder, ensure_ascii=False)
    
    def _create_sample_index(self, receipts):
        """Create an index of exported sample data."""
        index_data = {
            "metadata": {
                "exported_at": "2024-01-01T00:00:00Z",  # You might want to use actual timestamp
                "total_receipts": len(receipts),
                "table_name": self.table_name
            },
            "receipts": [
                {
                    "image_id": receipt.image_id,
                    "receipt_id": receipt.receipt_id,
                    "directory": f"image_{receipt.image_id}_receipt_{receipt.receipt_id}"
                }
                for receipt in receipts
            ],
            "categories": {
                "sample": len(receipts)
            }
        }
        
        self._save_json(self.output_dir / "sample_index.json", index_data)


def main():
    """Main entry point for the export script."""
    parser = argparse.ArgumentParser(description="Export receipt data for local development")
    parser.add_argument("command", choices=["sample", "single", "image", "merchant", "labeled"],
                       help="Export command to run")
    
    # Common arguments
    parser.add_argument("--table-name", help="DynamoDB table name")
    parser.add_argument("--output-dir", default="./receipt_data", help="Output directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    
    # Sample command arguments
    parser.add_argument("--size", type=int, default=20, help="Number of receipts to export (sample)")
    parser.add_argument("--merchant-filter", help="Filter by merchant name (sample)")
    
    # Single receipt arguments
    parser.add_argument("--image-id", help="Image ID (single)")
    parser.add_argument("--receipt-id", help="Receipt ID (single)")
    
    # Merchant command arguments
    parser.add_argument("--name", help="Merchant name (merchant)")
    parser.add_argument("--limit", type=int, default=10, help="Limit receipts (merchant)")
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    try:
        # Create exporter
        exporter = ReceiptDataExporter(
            table_name=args.table_name,
            output_dir=args.output_dir
        )
        
        # Execute command
        if args.command == "sample":
            count = exporter.export_sample_data(
                size=args.size,
                merchant_filter=args.merchant_filter
            )
            print(f"Exported {count} receipts to {args.output_dir}")
            
        elif args.command == "single":
            if not args.image_id or not args.receipt_id:
                print("Error: --image-id and --receipt-id are required for single command")
                sys.exit(1)
            
            success = exporter.export_specific_receipt(args.image_id, args.receipt_id)
            if success:
                print(f"Exported receipt {args.image_id}/{args.receipt_id}")
            else:
                print("Export failed")
                sys.exit(1)
                
        elif args.command == "merchant":
            if not args.name:
                print("Error: --name is required for merchant command")
                sys.exit(1)
            
            count = exporter.export_by_merchant(args.name, args.limit)
            print(f"Exported {count} receipts for merchant '{args.name}'")
            
        else:
            print(f"Command '{args.command}' not yet implemented")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\nExport cancelled by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()