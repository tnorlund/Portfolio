#!/usr/bin/env python3
"""
Regenerate AVIF files for ALL images to fix Safari display issues.
Processes all image types: PHOTO, SCAN, SCREENSHOT
"""

import argparse
import logging
import os
import sys
import boto3
from PIL import Image
import io
import pillow_avif  # Enable AVIF support
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

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
):
    """Regenerate AVIF files for a single image."""
    
    logger.info(f"Processing {image.image_type} image: {image.image_id}")
    
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
        
        return image.image_id, True, None
        
    except Exception as e:
        logger.error(f"  ❌ Error processing {image.image_id}: {str(e)}")
        return image.image_id, False, str(e)


def process_images_batch(images, s3_client, s3_bucket_name, max_workers=4):
    """Process a batch of images in parallel."""
    results = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_image = {
            executor.submit(regenerate_avif_for_image, image, s3_client, s3_bucket_name): image
            for image in images
        }
        
        for future in as_completed(future_to_image):
            image_id, success, error = future.result()
            results.append((image_id, success, error))
    
    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Regenerate AVIF files for ALL images")
    parser.add_argument("--stack", required=True, choices=["dev", "prod"], help="Pulumi stack")
    parser.add_argument("--batch-size", type=int, default=10, help="Batch size for processing")
    parser.add_argument("--max-workers", type=int, default=4, help="Max parallel workers")
    parser.add_argument("--image-type", choices=["PHOTO", "SCAN", "NATIVE"], help="Process only specific image type")
    
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
    
    # Get all images
    all_images = []
    image_types = [args.image_type] if args.image_type else ["PHOTO", "SCAN", "NATIVE"]
    
    for image_type in image_types:
        logger.info(f"\nFetching {image_type} images...")
        last_evaluated_key = None
        
        while True:
            images, last_key = dynamo_client.list_images_by_type(
                image_type=getattr(ImageType, image_type),
                limit=100,
                last_evaluated_key=last_evaluated_key,
            )
            
            all_images.extend(images)
            
            if not last_key:
                break
            
            last_evaluated_key = last_key
        
        logger.info(f"Found {len([img for img in all_images if img.image_type == getattr(ImageType, image_type)])} {image_type} images")
    
    logger.info(f"\nTotal images to process: {len(all_images)}")
    
    # Process in batches
    success_count = 0
    error_count = 0
    start_time = time.time()
    
    for i in range(0, len(all_images), args.batch_size):
        batch = all_images[i:i+args.batch_size]
        logger.info(f"\nProcessing batch {i//args.batch_size + 1}/{(len(all_images) + args.batch_size - 1)//args.batch_size}")
        
        results = process_images_batch(batch, s3_client, s3_bucket_name, args.max_workers)
        
        for image_id, success, error in results:
            if success:
                success_count += 1
            else:
                error_count += 1
        
        # Progress update
        processed = i + len(batch)
        elapsed = time.time() - start_time
        rate = processed / elapsed if elapsed > 0 else 0
        eta = (len(all_images) - processed) / rate if rate > 0 else 0
        
        logger.info(f"Progress: {processed}/{len(all_images)} images ({processed/len(all_images)*100:.1f}%)")
        logger.info(f"Rate: {rate:.1f} images/sec, ETA: {eta/60:.1f} minutes")
    
    # Final summary
    elapsed_total = time.time() - start_time
    logger.info(f"\n{'='*60}")
    logger.info(f"AVIF Regeneration Complete!")
    logger.info(f"{'='*60}")
    logger.info(f"Total images processed: {len(all_images)}")
    logger.info(f"Successful: {success_count}")
    logger.info(f"Failed: {error_count}")
    logger.info(f"Total time: {elapsed_total/60:.1f} minutes")
    logger.info(f"Average rate: {len(all_images)/elapsed_total:.1f} images/sec")


if __name__ == "__main__":
    main()