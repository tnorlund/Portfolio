#!/usr/bin/env python3
"""
Enhanced DynamoDB receipt data export tool for local testing.

This script enables comprehensive export of receipt data for local development:
1. Export receipts with all related entities (words, lines, metadata, labels)
2. Filter by merchant, validation status, or label presence
3. Download associated images from S3 (optional)
4. Create sample datasets for testing
5. Export merchant patterns for local pattern detection
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import boto3
from botocore.exceptions import ClientError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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


class ReceiptExporter:
    """Enhanced receipt data exporter with filtering and batch capabilities."""
    
    def __init__(self, dynamo_client: DynamoClient, output_dir: str):
        self.dynamo_client = dynamo_client
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.s3_client = None
        self.export_stats = {
            "receipts": 0,
            "words": 0,
            "lines": 0,
            "labels": 0,
            "metadata": 0,
            "images": 0,
            "errors": 0
        }
        
    def init_s3(self):
        """Initialize S3 client for image downloads."""
        if not self.s3_client:
            self.s3_client = boto3.client('s3')
            
    def export_receipt(
        self,
        image_id: str,
        receipt_id: str,
        download_image: bool = False,
        include_patterns: bool = True
    ) -> Dict[str, Any]:
        """
        Export a single receipt with all related data.
        
        Args:
            image_id: Image ID containing the receipt
            receipt_id: Receipt ID to export
            download_image: Whether to download the image from S3
            include_patterns: Whether to include pattern detection results
            
        Returns:
            Export summary dict
        """
        receipt_dir = self.output_dir / f"image_{image_id}_receipt_{receipt_id}"
        receipt_dir.mkdir(exist_ok=True)
        
        export_data = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "export_date": datetime.utcnow().isoformat(),
            "counts": {}
        }
        
        try:
            # Get receipt entity
            logger.info(f"Exporting receipt {receipt_id} from image {image_id}...")
            receipt = self._get_receipt(image_id, receipt_id)
            if receipt:
                with open(receipt_dir / "receipt.json", "w") as f:
                    json.dump(receipt.to_dict(), f, indent=2, cls=DateTimeEncoder)
                export_data["counts"]["receipt"] = 1
                self.export_stats["receipts"] += 1
                
                # Download image if requested
                if download_image and receipt.s3_bucket and receipt.s3_key:
                    self._download_image(receipt, receipt_dir)
            
            # Export words
            words = self._export_words(receipt_id, receipt_dir)
            export_data["counts"]["words"] = words
            
            # Export lines
            lines = self._export_lines(receipt_id, receipt_dir)
            export_data["counts"]["lines"] = lines
            
            # Export metadata
            metadata = self._export_metadata(receipt_id, receipt_dir)
            export_data["counts"]["metadata"] = metadata
            
            # Export labels
            labels = self._export_labels(receipt_id, receipt_dir)
            export_data["counts"]["labels"] = labels
            
            # Export pattern results if requested
            if include_patterns:
                patterns = self._export_patterns(receipt_id, receipt_dir)
                export_data["counts"]["patterns"] = patterns
                
            # Save export summary
            with open(receipt_dir / "export_summary.json", "w") as f:
                json.dump(export_data, f, indent=2)
                
            logger.info(f"✓ Exported image {image_id} receipt {receipt_id}: {export_data['counts']}")
            
        except Exception as e:
            logger.error(f"Error exporting image {image_id} receipt {receipt_id}: {e}")
            self.export_stats["errors"] += 1
            export_data["error"] = str(e)
            
        return export_data
    
    def _get_receipt(self, image_id: str, receipt_id: str) -> Optional[Receipt]:
        """Get receipt entity from DynamoDB."""
        try:
            # Query for receipt entity with composite key
            items = self.dynamo_client.query(
                pk_value=f"IMAGE#{image_id}",
                sk_value=f"RECEIPT#{int(receipt_id):05d}"
            )
            if items and len(items) > 0:
                return Receipt.from_dict(items[0])
        except Exception as e:
            logger.error(f"Error getting receipt {receipt_id} from image {image_id}: {e}")
        return None
    
    def _export_words(self, receipt_id: str, receipt_dir: Path) -> int:
        """Export receipt words."""
        logger.info(f"  Exporting words...")
        words = self.dynamo_client.list_receipt_words_by_receipt(receipt_id)
        if words:
            words_data = [word.to_dict() for word in words]
            with open(receipt_dir / "words.json", "w") as f:
                json.dump(words_data, f, indent=2, cls=DateTimeEncoder)
            self.export_stats["words"] += len(words)
            logger.info(f"    → {len(words)} words")
            return len(words)
        return 0
    
    def _export_lines(self, receipt_id: str, receipt_dir: Path) -> int:
        """Export receipt lines."""
        logger.info(f"  Exporting lines...")
        lines = self.dynamo_client.list_receipt_lines_by_receipt(receipt_id)
        if lines:
            lines_data = [line.to_dict() for line in lines]
            with open(receipt_dir / "lines.json", "w") as f:
                json.dump(lines_data, f, indent=2, cls=DateTimeEncoder)
            self.export_stats["lines"] += len(lines)
            logger.info(f"    → {len(lines)} lines")
            return len(lines)
        return 0
    
    def _export_metadata(self, receipt_id: str, receipt_dir: Path) -> int:
        """Export receipt metadata."""
        logger.info(f"  Exporting metadata...")
        try:
            metadata = self.dynamo_client.get_receipt_metadata(receipt_id)
            if metadata:
                with open(receipt_dir / "metadata.json", "w") as f:
                    json.dump(metadata.to_dict(), f, indent=2, cls=DateTimeEncoder)
                self.export_stats["metadata"] += 1
                logger.info(f"    → Merchant: {metadata.merchant_name}")
                return 1
        except Exception as e:
            logger.debug(f"    → No metadata: {e}")
        return 0
    
    def _export_labels(self, receipt_id: str, receipt_dir: Path) -> int:
        """Export receipt word labels."""
        logger.info(f"  Exporting labels...")
        try:
            labels = self.dynamo_client.list_receipt_word_labels_by_receipt(receipt_id)
            if labels:
                labels_data = [label.to_dict() for label in labels]
                with open(receipt_dir / "labels.json", "w") as f:
                    json.dump(labels_data, f, indent=2, cls=DateTimeEncoder)
                self.export_stats["labels"] += len(labels)
                logger.info(f"    → {len(labels)} labels")
                return len(labels)
        except Exception as e:
            logger.debug(f"    → No labels: {e}")
        return 0
    
    def _export_patterns(self, receipt_id: str, receipt_dir: Path) -> int:
        """Export pattern detection results (placeholder for future implementation)."""
        # TODO: Export pattern detection results when available
        return 0
    
    def _download_image(self, receipt: Receipt, receipt_dir: Path) -> bool:
        """Download receipt image from S3."""
        try:
            self.init_s3()
            logger.info(f"  Downloading image from S3...")
            
            # Determine file extension from S3 key
            ext = Path(receipt.s3_key).suffix or '.jpg'
            image_path = receipt_dir / f"image{ext}"
            
            self.s3_client.download_file(
                receipt.s3_bucket,
                receipt.s3_key,
                str(image_path)
            )
            
            self.export_stats["images"] += 1
            logger.info(f"    → Saved image to {image_path.name}")
            return True
            
        except ClientError as e:
            logger.error(f"    → Failed to download image: {e}")
            return False
    
    def find_receipts_by_image(self, image_id: str, limit: int = 100) -> List[Tuple[str, str]]:
        """
        Find all receipts for a given image.
        
        Args:
            image_id: Image ID to search for
            limit: Maximum number of receipts to return
            
        Returns:
            List of (image_id, receipt_id) tuples
        """
        items = self.dynamo_client.query(
            pk_value=f"IMAGE#{image_id}",
            sk_prefix="RECEIPT#",
            limit=limit
        )
        
        receipts = []
        for item in items:
            if "receipt_id" in item:
                receipts.append((image_id, str(item["receipt_id"])))
        
        return receipts
    
    def export_by_merchant(
        self,
        merchant_name: str,
        limit: int = 10,
        download_images: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Export receipts for a specific merchant.
        
        Args:
            merchant_name: Merchant name to filter by
            limit: Maximum number of receipts to export
            download_images: Whether to download images
            
        Returns:
            List of export summaries
        """
        logger.info(f"Exporting receipts for merchant: {merchant_name}")
        
        # Query GSI2 for merchant
        items = self.dynamo_client.query_gsi(
            index_name="GSI2",
            pk_value=f"MERCHANT#{merchant_name.upper()}",
            sk_prefix="RECEIPT#",
            limit=limit
        )
        
        receipts = []
        for item in items:
            if "image_id" in item and "receipt_id" in item:
                receipts.append((item["image_id"], item["receipt_id"]))
        
        logger.info(f"Found {len(receipts)} receipts for {merchant_name}")
        
        results = []
        for image_id, receipt_id in receipts:
            result = self.export_receipt(image_id, receipt_id, download_images)
            results.append(result)
            
        return results
    
    def export_labeled_receipts(
        self,
        limit: int = 10,
        min_labels: int = 5,
        download_images: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Export receipts that have labels.
        
        Args:
            limit: Maximum number of receipts to export
            min_labels: Minimum number of labels required
            download_images: Whether to download images
            
        Returns:
            List of export summaries
        """
        logger.info(f"Exporting labeled receipts (min {min_labels} labels)...")
        
        # Query for receipts with labels
        # Note: This is a simplified approach - in production you might want
        # to use a GSI specifically for this query pattern
        results = []
        checked = 0
        
        # Get recent receipts and check for labels
        items = self.dynamo_client.query(
            pk_value="TYPE#RECEIPT",
            limit=limit * 3  # Check more since not all will have labels
        )
        
        for item in items:
            if "image_id" in item and "receipt_id" in item:
                image_id = item["image_id"]
                receipt_id = item["receipt_id"]
                
                # Check if receipt has enough labels
                labels = self.dynamo_client.list_receipt_word_labels_by_receipt(receipt_id)
                if labels and len(labels) >= min_labels:
                    result = self.export_receipt(image_id, receipt_id, download_images)
                    results.append(result)
                    
                    if len(results) >= limit:
                        break
                        
                checked += 1
                if checked % 10 == 0:
                    logger.info(f"Checked {checked} receipts, found {len(results)} with labels")
        
        logger.info(f"Exported {len(results)} labeled receipts")
        return results
    
    def export_sample_dataset(
        self,
        sample_size: int = 20,
        download_images: bool = False
    ) -> Dict[str, Any]:
        """
        Export a diverse sample dataset for testing.
        
        Args:
            sample_size: Number of receipts to include
            download_images: Whether to download images
            
        Returns:
            Export summary
        """
        logger.info(f"Creating sample dataset with {sample_size} receipts...")
        
        sample_data = {
            "export_date": datetime.utcnow().isoformat(),
            "sample_size": sample_size,
            "categories": {}
        }
        
        # Try to get diverse samples
        # 1. Some with labels
        labeled = self.export_labeled_receipts(
            limit=sample_size // 3,
            download_images=download_images
        )
        sample_data["categories"]["labeled"] = len(labeled)
        
        # 2. Some from popular merchants
        merchants = ["WALMART", "TARGET", "MCDONALDS", "STARBUCKS", "CVS"]
        merchant_receipts = []
        for merchant in merchants[:sample_size // 5]:
            receipts = self.export_by_merchant(
                merchant,
                limit=2,
                download_images=download_images
            )
            merchant_receipts.extend(receipts)
        sample_data["categories"]["known_merchants"] = len(merchant_receipts)
        
        # 3. Some recent receipts
        recent_items = self.dynamo_client.query(
            pk_value="TYPE#RECEIPT",
            limit=sample_size // 3
        )
        
        recent_receipts = []
        for item in recent_items:
            if "image_id" in item and "receipt_id" in item:
                result = self.export_receipt(item["image_id"], item["receipt_id"], download_images)
                recent_receipts.append(result)
        sample_data["categories"]["recent"] = len(recent_receipts)
        
        # Create index file
        all_receipts = labeled + merchant_receipts + recent_receipts
        sample_data["total_exported"] = len(all_receipts)
        sample_data["receipts"] = [
            {"image_id": r["image_id"], "receipt_id": r["receipt_id"]} 
            for r in all_receipts if "image_id" in r and "receipt_id" in r
        ]
        
        with open(self.output_dir / "sample_index.json", "w") as f:
            json.dump(sample_data, f, indent=2)
            
        # Print summary
        logger.info("\nExport Summary:")
        logger.info(f"  Total receipts: {self.export_stats['receipts']}")
        logger.info(f"  Total words: {self.export_stats['words']}")
        logger.info(f"  Total lines: {self.export_stats['lines']}")
        logger.info(f"  Total labels: {self.export_stats['labels']}")
        logger.info(f"  Total metadata: {self.export_stats['metadata']}")
        if download_images:
            logger.info(f"  Total images: {self.export_stats['images']}")
        logger.info(f"  Errors: {self.export_stats['errors']}")
        
        return sample_data
    
    def create_master_index(self):
        """Create a master index of all exported receipts."""
        index = {
            "export_date": datetime.utcnow().isoformat(),
            "receipts": [],
            "statistics": self.export_stats
        }
        
        # Find all receipt directories
        for receipt_dir in self.output_dir.glob("image_*_receipt_*"):
            if receipt_dir.is_dir():
                summary_file = receipt_dir / "export_summary.json"
                if summary_file.exists():
                    with open(summary_file) as f:
                        summary = json.load(f)
                        index["receipts"].append({
                            "image_id": summary["image_id"],
                            "receipt_id": summary["receipt_id"],
                            "export_date": summary["export_date"],
                            "counts": summary["counts"],
                            "has_image": (receipt_dir / "image.jpg").exists() or 
                                       (receipt_dir / "image.png").exists()
                        })
        
        # Save index
        with open(self.output_dir / "index.json", "w") as f:
            json.dump(index, f, indent=2)
            
        logger.info(f"\nCreated master index with {len(index['receipts'])} receipts")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enhanced DynamoDB receipt data export tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export a single receipt
  %(prog)s single --image-id 550e8400-e29b-41d4-a716-446655440000 --receipt-id 1
  
  # Export all receipts from an image
  %(prog)s image --image-id 550e8400-e29b-41d4-a716-446655440000
  
  # Export receipts for a merchant
  %(prog)s merchant --name "Walmart" --limit 20
  
  # Export labeled receipts for testing
  %(prog)s labeled --limit 10 --min-labels 5
  
  # Create a diverse sample dataset
  %(prog)s sample --size 50 --download-images
  
  # Export with image downloads
  %(prog)s single --image-id 550e8400-e29b-41d4-a716-446655440000 --receipt-id 1 --download-images
        """
    )
    
    # Global options
    parser.add_argument(
        "--output-dir",
        default="./receipt_data",
        help="Output directory (default: ./receipt_data)"
    )
    parser.add_argument(
        "--table-name",
        help="DynamoDB table name (overrides environment variable)"
    )
    
    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Export mode")
    
    # Single receipt export
    single_parser = subparsers.add_parser("single", help="Export a single receipt")
    single_parser.add_argument("--image-id", required=True, help="Image ID containing the receipt")
    single_parser.add_argument("--receipt-id", required=True, help="Receipt ID to export")
    single_parser.add_argument("--download-images", action="store_true", help="Download images from S3")
    
    # Merchant export
    merchant_parser = subparsers.add_parser("merchant", help="Export receipts by merchant")
    merchant_parser.add_argument("--name", required=True, help="Merchant name")
    merchant_parser.add_argument("--limit", type=int, default=10, help="Maximum receipts (default: 10)")
    merchant_parser.add_argument("--download-images", action="store_true", help="Download images from S3")
    
    # Labeled receipts export
    labeled_parser = subparsers.add_parser("labeled", help="Export receipts with labels")
    labeled_parser.add_argument("--limit", type=int, default=10, help="Maximum receipts (default: 10)")
    labeled_parser.add_argument("--min-labels", type=int, default=5, help="Minimum labels required (default: 5)")
    labeled_parser.add_argument("--download-images", action="store_true", help="Download images from S3")
    
    # Sample dataset export
    sample_parser = subparsers.add_parser("sample", help="Create sample dataset")
    sample_parser.add_argument("--size", type=int, default=20, help="Sample size (default: 20)")
    sample_parser.add_argument("--download-images", action="store_true", help="Download images from S3")
    
    # Image export
    image_parser = subparsers.add_parser("image", help="Export all receipts from an image")
    image_parser.add_argument("--image-id", required=True, help="Image ID to export")
    image_parser.add_argument("--download-images", action="store_true", help="Download images from S3")
    image_parser.add_argument("--limit", type=int, default=100, help="Maximum receipts (default: 100)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.error("Please specify a command: single, image, merchant, labeled, or sample")
    
    # Get table name
    table_name = args.table_name or os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        logger.error("DYNAMODB_TABLE_NAME environment variable or --table-name is required")
        sys.exit(1)
    
    # Create exporter
    dynamo_client = DynamoClient(table_name)
    exporter = ReceiptExporter(dynamo_client, args.output_dir)
    
    # Execute command
    try:
        if args.command == "single":
            exporter.export_receipt(args.image_id, args.receipt_id, args.download_images)
            
        elif args.command == "merchant":
            exporter.export_by_merchant(args.name, args.limit, args.download_images)
            
        elif args.command == "labeled":
            exporter.export_labeled_receipts(args.limit, args.min_labels, args.download_images)
            
        elif args.command == "sample":
            exporter.export_sample_dataset(args.size, args.download_images)
            
        elif args.command == "image":
            receipts = exporter.find_receipts_by_image(args.image_id, args.limit)
            logger.info(f"Found {len(receipts)} receipts in image {args.image_id}")
            for image_id, receipt_id in receipts:
                exporter.export_receipt(image_id, receipt_id, args.download_images)
        
        # Create master index
        exporter.create_master_index()
        
    except Exception as e:
        logger.error(f"Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()