#!/usr/bin/env python3
"""
Test script to verify AVIF and WebP files from S3 and check for issues.
"""

import argparse
import logging
import os
import sys
import boto3
from PIL import Image
import io
import hashlib

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


def test_image_formats(dynamo_table_name: str, s3_bucket_name: str, image_id: str = None):
    """Test AVIF and WebP files for a specific image."""
    
    client = DynamoClient(dynamo_table_name)
    s3_client = boto3.client('s3')
    
    # Get PHOTO image
    if image_id:
        image = client.get_image(image_id)
        if not image or image.image_type != ImageType.PHOTO:
            logger.error(f"Image {image_id} not found or not a PHOTO type")
            return
        images = [image]
    else:
        images, _ = client.list_images_by_type(
            image_type=ImageType.PHOTO,
            limit=1,
        )
    
    if not images:
        logger.error("No PHOTO images found")
        return
    
    image = images[0]
    logger.info(f"Testing image: {image.image_id}")
    
    # Test medium size files (what the component uses)
    formats_to_test = [
        ("JPEG", image.cdn_medium_s3_key),
        ("WebP", image.cdn_medium_webp_s3_key),
        ("AVIF", image.cdn_medium_avif_s3_key),
    ]
    
    for format_name, s3_key in formats_to_test:
        if not s3_key:
            logger.warning(f"  {format_name}: No S3 key")
            continue
            
        try:
            # Download from S3
            response = s3_client.get_object(Bucket=s3_bucket_name, Key=s3_key)
            file_data = response['Body'].read()
            
            # Calculate hash
            file_hash = hashlib.md5(file_data).hexdigest()
            
            # Try to open with PIL
            img = Image.open(io.BytesIO(file_data))
            
            logger.info(f"  {format_name}: ✅ Valid")
            logger.info(f"    - S3 key: {s3_key}")
            logger.info(f"    - Size: {len(file_data):,} bytes")
            logger.info(f"    - Dimensions: {img.width}x{img.height}")
            logger.info(f"    - Mode: {img.mode}")
            logger.info(f"    - Format: {img.format}")
            logger.info(f"    - MD5: {file_hash}")
            
            # Save locally for manual inspection
            output_path = f"/tmp/test_{image.image_id}_medium.{format_name.lower()}"
            with open(output_path, 'wb') as f:
                f.write(file_data)
            logger.info(f"    - Saved to: {output_path}")
            
        except Exception as e:
            logger.error(f"  {format_name}: ❌ Error - {str(e)}")
    
    # Also check if the files have the same visual content
    logger.info("\nChecking visual similarity...")
    try:
        # Load JPEG as reference
        jpeg_response = s3_client.get_object(Bucket=s3_bucket_name, Key=image.cdn_medium_s3_key)
        jpeg_img = Image.open(io.BytesIO(jpeg_response['Body'].read())).convert('RGB')
        
        # Compare with WebP
        if image.cdn_medium_webp_s3_key:
            webp_response = s3_client.get_object(Bucket=s3_bucket_name, Key=image.cdn_medium_webp_s3_key)
            webp_img = Image.open(io.BytesIO(webp_response['Body'].read())).convert('RGB')
            
            # Simple pixel comparison at center
            jpeg_center = jpeg_img.getpixel((jpeg_img.width // 2, jpeg_img.height // 2))
            webp_center = webp_img.getpixel((webp_img.width // 2, webp_img.height // 2))
            logger.info(f"  JPEG center pixel: {jpeg_center}")
            logger.info(f"  WebP center pixel: {webp_center}")
            
        # Compare with AVIF
        if image.cdn_medium_avif_s3_key:
            avif_response = s3_client.get_object(Bucket=s3_bucket_name, Key=image.cdn_medium_avif_s3_key)
            avif_img = Image.open(io.BytesIO(avif_response['Body'].read())).convert('RGB')
            
            avif_center = avif_img.getpixel((avif_img.width // 2, avif_img.height // 2))
            logger.info(f"  AVIF center pixel: {avif_center}")
            
    except Exception as e:
        logger.error(f"Visual comparison error: {str(e)}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Test AVIF vs WebP files")
    parser.add_argument("--stack", required=True, choices=["dev", "prod"], help="Pulumi stack")
    parser.add_argument("--image-id", help="Specific image ID to test")
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    stack_name = f"tnorlund/portfolio/{args.stack}"
    work_dir = os.path.join(os.path.dirname(os.path.dirname(script_dir)), "infra")
    
    stack = auto.create_or_select_stack(stack_name=stack_name, work_dir=work_dir)
    outputs = stack.outputs()
    
    dynamo_table_name = outputs["dynamodb_table_name"].value
    s3_bucket_name = outputs["s3_bucket_name"].value
    
    logger.info(f"Using stack: {stack_name}")
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"S3 bucket: {s3_bucket_name}")
    
    test_image_formats(dynamo_table_name, s3_bucket_name, args.image_id)


if __name__ == "__main__":
    main()