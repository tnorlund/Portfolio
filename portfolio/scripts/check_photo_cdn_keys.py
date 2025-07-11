#!/usr/bin/env python3
"""
Quick script to check PHOTO image CDN keys in the database.
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


def check_photo_cdn_keys(dynamo_table_name: str, limit: int = 5):
    """Check CDN keys for PHOTO images."""
    
    client = DynamoClient(dynamo_table_name)
    
    # Get PHOTO images
    images, _ = client.list_images_by_type(
        image_type=ImageType.PHOTO,
        limit=limit,
    )
    
    logger.info(f"Found {len(images)} PHOTO images")
    
    for i, image in enumerate(images):
        logger.info(f"\n--- PHOTO Image {i+1}: {image.image_id} ---")
        logger.info(f"Full size keys:")
        logger.info(f"  JPEG: {image.cdn_s3_key}")
        logger.info(f"  WebP: {image.cdn_webp_s3_key}")
        logger.info(f"  AVIF: {image.cdn_avif_s3_key}")
        
        logger.info(f"Medium size keys:")
        logger.info(f"  JPEG: {image.cdn_medium_s3_key}")
        logger.info(f"  WebP: {image.cdn_medium_webp_s3_key}")
        logger.info(f"  AVIF: {image.cdn_medium_avif_s3_key}")
        
        logger.info(f"Thumbnail size keys:")
        logger.info(f"  JPEG: {image.cdn_thumbnail_s3_key}")
        logger.info(f"  WebP: {image.cdn_thumbnail_webp_s3_key}")
        logger.info(f"  AVIF: {image.cdn_thumbnail_avif_s3_key}")
        
        # Check if all medium keys are present
        has_medium_jpeg = bool(image.cdn_medium_s3_key)
        has_medium_webp = bool(image.cdn_medium_webp_s3_key)
        has_medium_avif = bool(image.cdn_medium_avif_s3_key)
        
        if has_medium_jpeg and has_medium_webp and has_medium_avif:
            logger.info("✅ All medium CDN keys present")
        else:
            logger.warning("❌ Missing medium CDN keys:")
            if not has_medium_jpeg:
                logger.warning("  - Missing JPEG medium key")
            if not has_medium_webp:
                logger.warning("  - Missing WebP medium key")
            if not has_medium_avif:
                logger.warning("  - Missing AVIF medium key")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Check PHOTO image CDN keys")
    parser.add_argument("--stack", required=True, choices=["dev", "prod"], help="Pulumi stack")
    parser.add_argument("--limit", type=int, default=5, help="Number of images to check")
    
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
    
    check_photo_cdn_keys(dynamo_table_name, args.limit)


if __name__ == "__main__":
    main()