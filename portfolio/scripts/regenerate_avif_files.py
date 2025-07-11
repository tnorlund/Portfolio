#!/usr/bin/env python3
"""
Regenerate AVIF files for PHOTO images to fix Safari display issues.
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

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_upload.utils import generate_image_sizes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def regenerate_avif_for_image(
    image,
    s3_client,
    s3_bucket_name: str,
    dynamo_client: DynamoClient,
):
    """Regenerate AVIF files for a single image."""
    
    logger.info(f"Processing image: {image.image_id}")
    
    # Download original JPEG
    try:
        response = s3_client.get_object(Bucket=s3_bucket_name, Key=image.cdn_s3_key)
        original_data = response['Body'].read()
        original_image = Image.open(io.BytesIO(original_data))
        
        # Generate multiple sizes
        target_sizes = {
            "thumbnail": 300,
            "small": 600,
            "medium": 1200,
        }
        
        sizes = generate_image_sizes(original_image, target_sizes)
        
        # Add full size
        sizes["full"] = original_image
        
        # Process each size
        updated = False
        for size_name, img in sizes.items():
            # Get the AVIF S3 key
            if size_name == "full":
                avif_key = image.cdn_avif_s3_key
            elif size_name == "thumbnail":
                avif_key = image.cdn_thumbnail_avif_s3_key
            elif size_name == "small":
                avif_key = image.cdn_small_avif_s3_key
            elif size_name == "medium":
                avif_key = image.cdn_medium_avif_s3_key
            
            if not avif_key:
                logger.warning(f"  No AVIF key for {size_name} size, skipping")
                continue
            
            # Save as AVIF with high quality
            avif_buffer = io.BytesIO()
            img.save(avif_buffer, format="AVIF", quality=85, speed=4)  # Higher quality, slower encoding
            avif_data = avif_buffer.getvalue()
            
            # Upload to S3
            s3_client.put_object(
                Bucket=s3_bucket_name,
                Key=avif_key,
                Body=avif_data,
                ContentType="image/avif",
            )
            
            logger.info(f"  ✅ Regenerated {size_name} AVIF: {avif_key} ({len(avif_data):,} bytes)")
            updated = True
        
        if updated:
            logger.info(f"  ✅ Successfully regenerated AVIF files for {image.image_id}")
        
        return updated
        
    except Exception as e:
        logger.error(f"  ❌ Error processing {image.image_id}: {str(e)}")
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Regenerate AVIF files for PHOTO images")
    parser.add_argument("--stack", required=True, choices=["dev", "prod"], help="Pulumi stack")
    parser.add_argument("--limit", type=int, default=5, help="Number of images to process")
    parser.add_argument("--image-id", help="Process specific image ID only")
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    stack_name = f"tnorlund/portfolio/{args.stack}"
    work_dir = os.path.join(os.path.dirname(os.path.dirname(script_dir)), "infra")
    
    stack = auto.create_or_select_stack(stack_name=stack_name, work_dir=work_dir)
    outputs = stack.outputs()
    
    dynamo_table_name = outputs["dynamodb_table_name"].value
    s3_bucket_name = outputs["cdn_bucket_name"].value
    
    logger.info(f"Using stack: {stack_name}")
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"S3 bucket: {s3_bucket_name}")
    
    # Initialize clients
    dynamo_client = DynamoClient(dynamo_table_name)
    s3_client = boto3.client('s3')
    
    # Get images to process
    if args.image_id:
        image = dynamo_client.get_image(args.image_id)
        if not image or image.image_type != ImageType.PHOTO:
            logger.error(f"Image {args.image_id} not found or not a PHOTO type")
            return
        images_to_process = [image]
    else:
        images_to_process, _ = dynamo_client.list_images_by_type(
            image_type=ImageType.PHOTO,
            limit=args.limit,
        )
    
    logger.info(f"Found {len(images_to_process)} PHOTO images to process")
    
    # Process each image
    success_count = 0
    for image in images_to_process:
        if regenerate_avif_for_image(image, s3_client, s3_bucket_name, dynamo_client):
            success_count += 1
    
    logger.info(f"\nCompleted: {success_count}/{len(images_to_process)} images regenerated successfully")


if __name__ == "__main__":
    main()