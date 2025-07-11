#!/usr/bin/env python3
"""
Force re-backfill script to fix CDN key conflicts.

This script specifically targets the data corruption issue where receipt processing
overwrote image CDN keys. It forces regeneration of CDN keys for all images that
have receipts, regardless of whether thumbnail fields exist.
"""

import argparse
import concurrent.futures
import logging
import os
import sys
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Set

import boto3
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
        logging.FileHandler("force_rebackfill_corrupted_keys.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class ForceRebackfiller:
    """Force re-backfill for images with CDN key conflicts."""

    def __init__(
        self,
        dynamo_table_name: str,
        raw_bucket: str,
        site_bucket: str,
        dry_run: bool = False,
        batch_size: int = 20,
        max_workers: int = 6,
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
            "total_images": 0,
            "total_receipts": 0,
            "images_with_receipts": 0,
            "conflicts_found": 0,
            "images_processed": 0,
            "receipts_processed": 0,
            "failed": 0,
        }
        self.failed_items: List[Dict[str, Any]] = []

    def download_image_from_s3(self, bucket: str, key: str) -> Optional[PIL_Image.Image]:
        """Download an image from S3 and return as PIL Image."""
        try:
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            image_data = response["Body"].read()
            return PIL_Image.open(BytesIO(image_data))
        except Exception as e:
            logger.error(f"Failed to download image from s3://{bucket}/{key}: {e}")
            return None

    def get_all_images_and_receipts(self) -> tuple[Dict[str, Image], Dict[str, List[Receipt]]]:
        """Get all images and group receipts by image_id."""
        
        # Get all images
        logger.info("Fetching all images...")
        all_images = []
        for image_type in [ImageType.PHOTO, ImageType.SCAN]:
            last_evaluated_key = None
            while True:
                images, last_evaluated_key = self.dynamo_client.list_images_by_type(
                    image_type=image_type,
                    limit=100,
                    last_evaluated_key=last_evaluated_key,
                )
                all_images.extend(images)
                if not last_evaluated_key:
                    break
        
        images_by_id = {image.image_id: image for image in all_images}
        self.stats["total_images"] = len(all_images)
        logger.info(f"Found {len(all_images)} total images")
        
        # Get all receipts
        logger.info("Fetching all receipts...")
        all_receipts = []
        last_evaluated_key = None
        while True:
            receipts, last_evaluated_key = self.dynamo_client.list_receipts(
                limit=100,
                last_evaluated_key=last_evaluated_key,
            )
            all_receipts.extend(receipts)
            if not last_evaluated_key:
                break
        
        # Group receipts by image_id
        receipts_by_image = {}
        for receipt in all_receipts:
            if receipt.image_id not in receipts_by_image:
                receipts_by_image[receipt.image_id] = []
            receipts_by_image[receipt.image_id].append(receipt)
        
        self.stats["total_receipts"] = len(all_receipts)
        self.stats["images_with_receipts"] = len(receipts_by_image)
        logger.info(f"Found {len(all_receipts)} total receipts")
        logger.info(f"Found {len(receipts_by_image)} images with receipts")
        
        return images_by_id, receipts_by_image

    def analyze_conflicts(self, images_by_id: Dict[str, Image], receipts_by_image: Dict[str, List[Receipt]]) -> List[str]:
        """Analyze CDN key conflicts and return list of image_ids with conflicts."""
        
        conflicted_image_ids = []
        
        logger.info("Analyzing CDN key conflicts...")
        
        for image_id, receipts in receipts_by_image.items():
            if image_id not in images_by_id:
                logger.warning(f"Image {image_id} has receipts but image not found!")
                continue
                
            image = images_by_id[image_id]
            has_conflicts = False
            
            for receipt in receipts:
                # Check for conflicts in any CDN key
                if (image.cdn_thumbnail_s3_key == receipt.cdn_thumbnail_s3_key or
                    image.cdn_small_s3_key == receipt.cdn_small_s3_key or
                    image.cdn_medium_s3_key == receipt.cdn_medium_s3_key or
                    image.cdn_s3_key == receipt.cdn_s3_key):
                    has_conflicts = True
                    self.stats["conflicts_found"] += 1
                    break
            
            if has_conflicts:
                conflicted_image_ids.append(image_id)
        
        logger.info(f"Found {len(conflicted_image_ids)} images with CDN key conflicts")
        return conflicted_image_ids

    def process_image(self, image: Image) -> bool:
        """Force re-process an image to generate new CDN keys."""
        try:
            logger.info(f"Force processing image {image.image_id}")
            
            if self.dry_run:
                logger.info(f"[DRY RUN] Would force process image {image.image_id}")
                return True
            
            # Download the original image
            pil_image = self.download_image_from_s3(image.raw_s3_bucket, image.raw_s3_key)
            if not pil_image:
                raise Exception("Failed to download image")
            
            # Generate all sizes with the correct S3 prefix
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
            
            logger.info(f"Successfully force processed image {image.image_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to force process image {image.image_id}: {e}")
            self.failed_items.append({
                "type": "image",
                "id": image.image_id,
                "error": str(e),
            })
            return False

    def process_receipt(self, receipt: Receipt) -> bool:
        """Force re-process a receipt to generate new CDN keys."""
        try:
            logger.info(f"Force processing receipt {receipt.image_id}:{receipt.receipt_id}")
            
            if self.dry_run:
                logger.info(f"[DRY RUN] Would force process receipt {receipt.image_id}:{receipt.receipt_id}")
                return True
            
            # Download the original image
            pil_image = self.download_image_from_s3(receipt.raw_s3_bucket, receipt.raw_s3_key)
            if not pil_image:
                raise Exception("Failed to download image")
            
            # For receipts, use the CORRECTED S3 prefix to avoid conflicts
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
            
            logger.info(f"Successfully force processed receipt {receipt.image_id}:{receipt.receipt_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to force process receipt {receipt.image_id}:{receipt.receipt_id}: {e}")
            self.failed_items.append({
                "type": "receipt",
                "id": f"{receipt.image_id}:{receipt.receipt_id}",
                "error": str(e),
            })
            return False

    def process_batch_parallel(self, items: List, process_func):
        """Process a batch of items in parallel."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(process_func, item): item for item in items}
            
            for future in concurrent.futures.as_completed(futures):
                if future.result():
                    if process_func.__name__ == "process_image":
                        self.stats["images_processed"] += 1
                    else:
                        self.stats["receipts_processed"] += 1
                else:
                    self.stats["failed"] += 1

    def run(self, limit: Optional[int] = None):
        """Run the force re-backfill process."""
        start_time = time.time()
        
        # Get all data
        images_by_id, receipts_by_image = self.get_all_images_and_receipts()
        
        # Analyze conflicts
        conflicted_image_ids = self.analyze_conflicts(images_by_id, receipts_by_image)
        
        if not conflicted_image_ids:
            logger.info("No CDN key conflicts found!")
            return
        
        # Apply limit if specified
        if limit:
            conflicted_image_ids = conflicted_image_ids[:limit]
        
        logger.info(f"Force processing {len(conflicted_image_ids)} images with conflicts...")
        
        # Process conflicted images
        conflicted_images = [images_by_id[image_id] for image_id in conflicted_image_ids]
        with tqdm(total=len(conflicted_images), desc="Force processing images") as pbar:
            for i in range(0, len(conflicted_images), self.batch_size):
                batch = conflicted_images[i:i + self.batch_size]
                self.process_batch_parallel(batch, self.process_image)
                pbar.update(len(batch))
        
        # Process all receipts for these images
        all_receipts_to_process = []
        for image_id in conflicted_image_ids:
            if image_id in receipts_by_image:
                all_receipts_to_process.extend(receipts_by_image[image_id])
        
        logger.info(f"Force processing {len(all_receipts_to_process)} receipts...")
        with tqdm(total=len(all_receipts_to_process), desc="Force processing receipts") as pbar:
            for i in range(0, len(all_receipts_to_process), self.batch_size):
                batch = all_receipts_to_process[i:i + self.batch_size]
                self.process_batch_parallel(batch, self.process_receipt)
                pbar.update(len(batch))
        
        # Print summary
        elapsed_time = time.time() - start_time
        logger.info("\n" + "=" * 50)
        logger.info("FORCE RE-BACKFILL SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Total images: {self.stats['total_images']}")
        logger.info(f"Total receipts: {self.stats['total_receipts']}")
        logger.info(f"Images with receipts: {self.stats['images_with_receipts']}")
        logger.info(f"Conflicts found: {self.stats['conflicts_found']}")
        logger.info(f"Images processed: {self.stats['images_processed']}")
        logger.info(f"Receipts processed: {self.stats['receipts_processed']}")
        logger.info(f"Failed: {self.stats['failed']}")
        logger.info(f"Time elapsed: {elapsed_time:.2f} seconds")
        
        if self.failed_items:
            logger.error("\nFailed items:")
            for item in self.failed_items:
                logger.error(f"  - {item['type']} {item['id']}: {item['error']}")
        
        if self.dry_run:
            logger.info("\nThis was a DRY RUN - no changes were made")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Force re-backfill to fix CDN key conflicts"
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
        default=6,
        help="Maximum parallel workers (default: 6)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of conflicted images to process",
    )
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    # Set up the stack
    stack_name = f"tnorlund/portfolio/{args.stack}"
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
    
    # Create and run force re-backfiller
    backfiller = ForceRebackfiller(
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