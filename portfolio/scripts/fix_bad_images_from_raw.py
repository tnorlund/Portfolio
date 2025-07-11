#!/usr/bin/env python3
"""
Fix specific bad images by regenerating ALL formats (JPEG, WebP, AVIF) from raw_image source.
"""

import argparse
import logging
import os
import sys
import boto3
from PIL import Image
import io
import pillow_avif  # Enable AVIF support

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_upload.utils import generate_image_sizes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def regenerate_all_formats_from_raw(image_id: str, dynamo_client: DynamoClient, s3_client, cdn_bucket: str, raw_bucket: str):
    """Regenerate ALL formats (JPEG, WebP, AVIF) from raw_image source."""
    
    try:
        # Get image from DynamoDB
        image = dynamo_client.get_image(image_id)
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {image.image_type} image: {image_id}")
        logger.info(f"Raw S3 key: {image.raw_s3_key}")
        logger.info(f"Raw bucket: {image.raw_s3_bucket}")
        
        # Download raw image
        logger.info(f"Downloading raw image from s3://{image.raw_s3_bucket}/{image.raw_s3_key}")
        response = s3_client.get_object(Bucket=image.raw_s3_bucket, Key=image.raw_s3_key)
        raw_data = response['Body'].read()
        raw_image = Image.open(io.BytesIO(raw_data))
        
        logger.info(f"Raw image size: {raw_image.width}x{raw_image.height}")
        
        # Generate multiple sizes
        target_sizes = {
            "thumbnail": 300,
            "small": 600,
            "medium": 1200,
        }
        
        sizes = generate_image_sizes(raw_image, target_sizes)
        sizes["full"] = raw_image
        
        # Process each size
        for size_name, img in sizes.items():
            logger.info(f"\nProcessing {size_name} size ({img.width}x{img.height}):")
            
            # Get the S3 keys
            if size_name == "full":
                jpeg_key = image.cdn_s3_key
                webp_key = image.cdn_webp_s3_key
                avif_key = image.cdn_avif_s3_key
            elif size_name == "thumbnail":
                jpeg_key = image.cdn_thumbnail_s3_key
                webp_key = image.cdn_thumbnail_webp_s3_key
                avif_key = image.cdn_thumbnail_avif_s3_key
            elif size_name == "small":
                jpeg_key = image.cdn_small_s3_key
                webp_key = image.cdn_small_webp_s3_key
                avif_key = image.cdn_small_avif_s3_key
            elif size_name == "medium":
                jpeg_key = image.cdn_medium_s3_key
                webp_key = image.cdn_medium_webp_s3_key
                avif_key = image.cdn_medium_avif_s3_key
            
            # Save as JPEG
            if jpeg_key:
                jpeg_buffer = io.BytesIO()
                img.save(jpeg_buffer, format="JPEG", quality=85, optimize=True)
                jpeg_data = jpeg_buffer.getvalue()
                
                s3_client.put_object(
                    Bucket=cdn_bucket,
                    Key=jpeg_key,
                    Body=jpeg_data,
                    ContentType="image/jpeg",
                )
                logger.info(f"  ✅ Regenerated JPEG: {jpeg_key} ({len(jpeg_data):,} bytes)")
            
            # Save as WebP
            if webp_key:
                webp_buffer = io.BytesIO()
                img.save(webp_buffer, format="WebP", quality=85, method=6)
                webp_data = webp_buffer.getvalue()
                
                s3_client.put_object(
                    Bucket=cdn_bucket,
                    Key=webp_key,
                    Body=webp_data,
                    ContentType="image/webp",
                )
                logger.info(f"  ✅ Regenerated WebP: {webp_key} ({len(webp_data):,} bytes)")
            
            # Save as AVIF
            if avif_key:
                avif_buffer = io.BytesIO()
                img.save(avif_buffer, format="AVIF", quality=85, speed=4)
                avif_data = avif_buffer.getvalue()
                
                s3_client.put_object(
                    Bucket=cdn_bucket,
                    Key=avif_key,
                    Body=avif_data,
                    ContentType="image/avif",
                )
                logger.info(f"  ✅ Regenerated AVIF: {avif_key} ({len(avif_data):,} bytes)")
        
        logger.info(f"\n✅ Successfully regenerated ALL formats for {image_id}")
        return True
        
    except Exception as e:
        logger.error(f"\n❌ Error processing {image_id}: {str(e)}")
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Fix bad images by regenerating from raw source")
    parser.add_argument("image_ids", nargs="+", help="Image IDs to fix")
    parser.add_argument("--stack", default="dev", choices=["dev", "prod"], help="Pulumi stack")
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    stack_name = f"tnorlund/portfolio/{args.stack}"
    work_dir = os.path.join(os.path.dirname(os.path.dirname(script_dir)), "infra")
    
    stack = auto.create_or_select_stack(stack_name=stack_name, work_dir=work_dir)
    outputs = stack.outputs()
    
    dynamo_table_name = outputs["dynamodb_table_name"].value
    cdn_bucket_name = outputs["cdn_bucket_name"].value
    raw_bucket_name = outputs["raw_bucket_name"].value
    
    logger.info(f"Using stack: {stack_name}")
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"CDN bucket: {cdn_bucket_name}")
    logger.info(f"Raw bucket: {raw_bucket_name}")
    logger.info(f"Images to fix: {args.image_ids}")
    
    # Initialize clients
    dynamo_client = DynamoClient(dynamo_table_name)
    s3_client = boto3.client('s3')
    
    # Process each image
    success_count = 0
    for image_id in args.image_ids:
        if regenerate_all_formats_from_raw(image_id, dynamo_client, s3_client, cdn_bucket_name, raw_bucket_name):
            success_count += 1
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"✅ Fixed {success_count}/{len(args.image_ids)} images")
    if success_count < len(args.image_ids):
        logger.info(f"❌ Failed to fix {len(args.image_ids) - success_count} images")


if __name__ == "__main__":
    main()