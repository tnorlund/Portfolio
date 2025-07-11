#!/usr/bin/env python3
"""
Test script to verify the S3 prefix fix for PHOTO images.
This tests a small sample size to ensure Images and Receipts have separate CDN keys.
"""

import argparse
import logging
import os
import sys

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data.dynamo_client import DynamoClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def test_photo_data(dynamo_table_name: str):
    """Test images to understand the data structure."""
    
    client = DynamoClient(dynamo_table_name)
    
    # Get ALL receipts by paginating
    logger.info("Fetching ALL receipts...")
    all_receipts = []
    last_evaluated_key = None
    
    while True:
        receipts, last_evaluated_key = client.list_receipts(limit=100, last_evaluated_key=last_evaluated_key)
        all_receipts.extend(receipts)
        logger.info(f"Fetched {len(receipts)} receipts, total so far: {len(all_receipts)}")
        if not last_evaluated_key:
            break
    
    logger.info(f"Total receipts fetched: {len(all_receipts)}")
    
    # Group receipts by image_id
    receipts_by_image = {}
    for receipt in all_receipts:
        if receipt.image_id not in receipts_by_image:
            receipts_by_image[receipt.image_id] = []
        receipts_by_image[receipt.image_id].append(receipt)
    
    logger.info(f"Found {len(receipts_by_image)} unique images with receipts")
    
    # Get ALL images by paginating
    logger.info("Fetching ALL images...")
    all_images = []
    last_evaluated_key = None
    
    # Get both PHOTO and SCAN images
    for image_type in [ImageType.PHOTO, ImageType.SCAN]:
        logger.info(f"Fetching {image_type} images...")
        last_evaluated_key = None
        
        while True:
            images, last_evaluated_key = client.list_images_by_type(
                image_type=image_type, 
                limit=100, 
                last_evaluated_key=last_evaluated_key
            )
            all_images.extend(images)
            logger.info(f"Fetched {len(images)} {image_type} images, total so far: {len(all_images)}")
            if not last_evaluated_key:
                break
    
    logger.info(f"Total images fetched: {len(all_images)}")
    
    # Create image lookup
    images_by_id = {image.image_id: image for image in all_images}
    
    # Now check for conflicts
    total_conflicts = 0
    images_with_conflicts = 0
    
    logger.info("\n" + "="*50)
    logger.info("ANALYZING CDN KEY CONFLICTS")
    logger.info("="*50)
    
    for image_id, receipts in receipts_by_image.items():
        if image_id not in images_by_id:
            logger.warning(f"Image {image_id} has receipts but image not found!")
            continue
            
        image = images_by_id[image_id]
        conflicts_found = 0
        
        for receipt in receipts:
            # Check for conflicts
            if image.cdn_s3_key == receipt.cdn_s3_key:
                conflicts_found += 1
                total_conflicts += 1
                logger.error(f"❌ CONFLICT: Image {image_id} CDN key matches Receipt {receipt.receipt_id} CDN key!")
                
            if image.cdn_thumbnail_s3_key == receipt.cdn_thumbnail_s3_key:
                conflicts_found += 1
                total_conflicts += 1
                logger.error(f"❌ CONFLICT: Image {image_id} thumbnail matches Receipt {receipt.receipt_id} thumbnail!")
                
        if conflicts_found > 0:
            images_with_conflicts += 1
            logger.error(f"Image {image_id} has {conflicts_found} conflicts with {len(receipts)} receipts")
        else:
            logger.info(f"✅ Image {image_id} has no conflicts with {len(receipts)} receipts")
    
    logger.info("\n" + "="*50)
    logger.info("SUMMARY")
    logger.info("="*50)
    logger.info(f"Total images: {len(all_images)}")
    logger.info(f"Total receipts: {len(all_receipts)}")
    logger.info(f"Images with receipts: {len(receipts_by_image)}")
    logger.info(f"Images with conflicts: {images_with_conflicts}")
    logger.info(f"Total conflicts found: {total_conflicts}")
    
    if total_conflicts > 0:
        logger.error(f"❌ CRITICAL: Found {total_conflicts} CDN key conflicts!")
    else:
        logger.info(f"✅ No CDN key conflicts found")


def main():
    parser = argparse.ArgumentParser(description="Test PHOTO images for CDN key conflicts")
    parser.add_argument("--stack", required=True, choices=["dev", "prod"], help="Pulumi stack")
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    stack_name = f"tnorlund/portfolio/{args.stack}"
    work_dir = os.path.join(os.path.dirname(os.path.dirname(script_dir)), "infra")
    
    stack = auto.create_or_select_stack(stack_name=stack_name, work_dir=work_dir)
    outputs = stack.outputs()
    
    dynamo_table_name = outputs["dynamodb_table_name"].value
    
    logger.info(f"Using stack: {stack_name}")
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    
    test_photo_data(dynamo_table_name)


if __name__ == "__main__":
    main()