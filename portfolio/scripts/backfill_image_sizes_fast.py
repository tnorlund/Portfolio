#!/usr/bin/env python3
"""
Fast version of backfill script with optimizations:
- No sleep delays between batches
- Larger batch size
- Parallel processing within batches
"""

import argparse
import asyncio
import concurrent.futures
import logging
import os
import sys
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Set

import boto3
from boto3.dynamodb.conditions import Attr
from PIL import Image as PIL_Image
from tqdm import tqdm

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Image, Receipt
from receipt_upload.utils import upload_all_cdn_formats

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("backfill_image_sizes_fast.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class FastImageBackfiller:
    """Fast backfilling of image sizes with parallel processing."""

    def __init__(
        self,
        dynamo_table_name: str,
        raw_bucket: str,
        site_bucket: str,
        dry_run: bool = False,
        batch_size: int = 20,  # Increased from 10
        max_workers: int = 4,  # Parallel workers
    ):
        self.dynamo_client = DynamoClient(dynamo_table_name)
        self.raw_bucket = raw_bucket
        self.site_bucket = site_bucket
        self.dry_run = dry_run
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.s3_client = boto3.client("s3")

        # Track statistics
        self.stats = {
            "total_scanned": 0,
            "needs_backfill": 0,
            "successfully_processed": 0,
            "failed": 0,
            "skipped": 0,
        }
        self.failed_items: List[Dict[str, Any]] = []

    def download_image_from_s3(
        self, bucket: str, key: str
    ) -> Optional[PIL_Image.Image]:
        """Download an image from S3 and return as PIL Image."""
        try:
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            image_data = response["Body"].read()
            return PIL_Image.open(BytesIO(image_data))
        except Exception as e:
            logger.error(f"Failed to download image from s3://{bucket}/{key}: {e}")
            return None

    def needs_thumbnail_fields(self, item: Dict[str, Any]) -> bool:
        """Check if an item needs thumbnail fields added."""
        thumbnail_fields = [
            "cdn_thumbnail_s3_key",
            "cdn_thumbnail_webp_s3_key",
            "cdn_thumbnail_avif_s3_key",
            "cdn_small_s3_key",
            "cdn_small_webp_s3_key",
            "cdn_small_avif_s3_key",
            "cdn_medium_s3_key",
            "cdn_medium_webp_s3_key",
            "cdn_medium_avif_s3_key",
        ]

        for field in thumbnail_fields:
            if field not in item:
                return True
            value = item[field]
            if value is None:
                return True
            if isinstance(value, dict) and value.get("NULL") is True:
                return True
        return False

    def scan_images_needing_backfill(self) -> List[Image]:
        """Scan for all images that need thumbnail generation."""
        images_to_process = []

        logger.info("Scanning for images needing thumbnail generation...")

        for image_type in [ImageType.PHOTO, ImageType.SCAN]:
            last_evaluated_key = None

            while True:
                images, last_evaluated_key = self.dynamo_client.list_images_by_type(
                    image_type=image_type,
                    limit=100,
                    last_evaluated_key=last_evaluated_key,
                )

                self.stats["total_scanned"] += len(images)

                for image in images:
                    item = image.to_item()
                    if self.needs_thumbnail_fields(item):
                        self.stats["needs_backfill"] += 1
                        images_to_process.append(image)

                if not last_evaluated_key:
                    break

        logger.info(
            f"Found {len(images_to_process)} images needing backfill out of {self.stats['total_scanned']} total images"
        )
        return images_to_process

    def scan_receipts_needing_backfill(self) -> List[Receipt]:
        """Scan for all receipts that need thumbnail generation."""
        receipts_to_process = []

        logger.info("Scanning for receipts needing thumbnail generation...")

        last_evaluated_key = None

        while True:
            receipts, last_evaluated_key = self.dynamo_client.list_receipts(
                limit=100,
                last_evaluated_key=last_evaluated_key,
            )

            self.stats["total_scanned"] += len(receipts)

            for receipt in receipts:
                item = receipt.to_item()
                if self.needs_thumbnail_fields(item):
                    self.stats["needs_backfill"] += 1
                    receipts_to_process.append(receipt)

            if not last_evaluated_key:
                break

        logger.info(f"Found {len(receipts_to_process)} receipts needing backfill")
        return receipts_to_process

    def process_image(self, image: Image) -> bool:
        """Process a single image to generate thumbnails."""
        try:
            logger.info(f"Processing image {image.image_id}")

            if self.dry_run:
                logger.info(f"[DRY RUN] Would process image {image.image_id}")
                return True

            # Download the original image
            pil_image = self.download_image_from_s3(
                image.raw_s3_bucket, image.raw_s3_key
            )
            if not pil_image:
                raise Exception("Failed to download image")

            # Generate all sizes
            s3_prefix = f"assets/{image.image_id}"
            cdn_keys = upload_all_cdn_formats(
                pil_image,
                self.site_bucket,
                s3_prefix,
                generate_thumbnails=True,
            )

            # Update the image record with new S3 keys
            image.cdn_thumbnail_s3_key = cdn_keys.get("jpeg_thumbnail")
            image.cdn_thumbnail_webp_s3_key = cdn_keys.get("webp_thumbnail")
            image.cdn_thumbnail_avif_s3_key = cdn_keys.get("avif_thumbnail")
            image.cdn_small_s3_key = cdn_keys.get("jpeg_small")
            image.cdn_small_webp_s3_key = cdn_keys.get("webp_small")
            image.cdn_small_avif_s3_key = cdn_keys.get("avif_small")
            image.cdn_medium_s3_key = cdn_keys.get("jpeg_medium")
            image.cdn_medium_webp_s3_key = cdn_keys.get("webp_medium")
            image.cdn_medium_avif_s3_key = cdn_keys.get("avif_medium")

            # Update DynamoDB
            self.dynamo_client.update_image(image)

            logger.info(f"Successfully processed image {image.image_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to process image {image.image_id}: {e}")
            self.failed_items.append(
                {
                    "type": "image",
                    "id": image.image_id,
                    "error": str(e),
                }
            )
            return False

    def process_receipt(self, receipt: Receipt) -> bool:
        """Process a single receipt to generate thumbnails."""
        try:
            logger.info(f"Processing receipt {receipt.image_id}:{receipt.receipt_id}")

            if self.dry_run:
                logger.info(
                    f"[DRY RUN] Would process receipt {receipt.image_id}:{receipt.receipt_id}"
                )
                return True

            # Download the original image
            pil_image = self.download_image_from_s3(
                receipt.raw_s3_bucket, receipt.raw_s3_key
            )
            if not pil_image:
                raise Exception("Failed to download image")

            # For receipts, use a unique S3 prefix to avoid overwriting image CDN keys
            s3_prefix = f"assets/{receipt.image_id}/{receipt.receipt_id}"
            cdn_keys = upload_all_cdn_formats(
                pil_image,
                self.site_bucket,
                s3_prefix,
                generate_thumbnails=True,
            )

            # Update the receipt record with new S3 keys
            receipt.cdn_thumbnail_s3_key = cdn_keys.get("jpeg_thumbnail")
            receipt.cdn_thumbnail_webp_s3_key = cdn_keys.get("webp_thumbnail")
            receipt.cdn_thumbnail_avif_s3_key = cdn_keys.get("avif_thumbnail")
            receipt.cdn_small_s3_key = cdn_keys.get("jpeg_small")
            receipt.cdn_small_webp_s3_key = cdn_keys.get("webp_small")
            receipt.cdn_small_avif_s3_key = cdn_keys.get("avif_small")
            receipt.cdn_medium_s3_key = cdn_keys.get("jpeg_medium")
            receipt.cdn_medium_webp_s3_key = cdn_keys.get("webp_medium")
            receipt.cdn_medium_avif_s3_key = cdn_keys.get("avif_medium")

            # Update DynamoDB
            self.dynamo_client.update_receipt(receipt)

            logger.info(
                f"Successfully processed receipt {receipt.image_id}:{receipt.receipt_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to process receipt {receipt.image_id}:{receipt.receipt_id}: {e}"
            )
            self.failed_items.append(
                {
                    "type": "receipt",
                    "id": f"{receipt.image_id}:{receipt.receipt_id}",
                    "error": str(e),
                }
            )
            return False

    def process_batch_parallel(self, items: List, process_func):
        """Process a batch of items in parallel."""
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        ) as executor:
            futures = {executor.submit(process_func, item): item for item in items}

            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    self.stats["successfully_processed"] += 1
                else:
                    self.stats["failed"] += 1

    def run(self, limit: Optional[int] = None):
        """Run the backfill process with parallel processing."""
        start_time = time.time()

        # Get items needing backfill
        images = self.scan_images_needing_backfill()
        receipts = self.scan_receipts_needing_backfill()

        # Apply limit if specified
        if limit:
            images = images[:limit]
            receipts = receipts[: max(0, limit - len(images))]

        total_items = len(images) + len(receipts)
        if total_items == 0:
            logger.info("No items need backfilling!")
            return

        logger.info(f"Processing {len(images)} images and {len(receipts)} receipts...")

        # Process images in parallel batches
        with tqdm(total=len(images), desc="Processing images") as pbar:
            for i in range(0, len(images), self.batch_size):
                batch = images[i : i + self.batch_size]
                self.process_batch_parallel(batch, self.process_image)
                pbar.update(len(batch))

        # Process receipts in parallel batches
        with tqdm(total=len(receipts), desc="Processing receipts") as pbar:
            for i in range(0, len(receipts), self.batch_size):
                batch = receipts[i : i + self.batch_size]
                self.process_batch_parallel(batch, self.process_receipt)
                pbar.update(len(batch))

        # Print summary
        elapsed_time = time.time() - start_time
        logger.info("\n" + "=" * 50)
        logger.info("BACKFILL SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Total items scanned: {self.stats['total_scanned']}")
        logger.info(f"Items needing backfill: {self.stats['needs_backfill']}")
        logger.info(f"Successfully processed: {self.stats['successfully_processed']}")
        logger.info(f"Failed: {self.stats['failed']}")
        logger.info(f"Time elapsed: {elapsed_time:.2f} seconds")
        logger.info(
            f"Average time per item: {elapsed_time / max(self.stats['successfully_processed'], 1):.2f} seconds"
        )

        if self.failed_items:
            logger.error("\nFailed items:")
            for item in self.failed_items:
                logger.error(f"  - {item['type']} {item['id']}: {item['error']}")

        if self.dry_run:
            logger.info("\nThis was a DRY RUN - no changes were made")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fast backfill of missing image sizes with parallel processing"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack to use (dev or prod)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making any changes",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Number of items to process in each batch (default: 20)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Maximum parallel workers (default: 4)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of items to process",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    from pulumi import automation as auto

    # Set up the stack
    stack_name = f"tnorlund/portfolio/{args.stack}"
    # Get the path to the infra directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    portfolio_root = os.path.dirname(script_dir)
    parent_dir = os.path.dirname(portfolio_root)
    work_dir = os.path.join(parent_dir, "infra")

    # Create a stack reference to get outputs
    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )

    # Get the outputs
    outputs = stack.outputs()

    # Extract configuration
    dynamo_table_name = outputs["dynamodb_table_name"].value
    raw_bucket = outputs["raw_bucket_name"].value
    site_bucket = outputs["cdn_bucket_name"].value

    logger.info(f"Using stack: {stack_name}")
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"Raw bucket: {raw_bucket}")
    logger.info(f"Site bucket: {site_bucket}")

    # Create and run backfiller
    backfiller = FastImageBackfiller(
        dynamo_table_name=dynamo_table_name,
        raw_bucket=raw_bucket,
        site_bucket=site_bucket,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
    )

    backfiller.run(limit=args.limit)


if __name__ == "__main__":
    main()
