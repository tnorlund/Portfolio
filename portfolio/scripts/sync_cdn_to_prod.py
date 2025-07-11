#!/usr/bin/env python3
"""
Sync multi-size images from dev CDN bucket to prod CDN bucket.
Only syncs the new multi-size formats, not the original images.
"""

import argparse
import logging
import os
import sys
import boto3
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def should_sync_key(key: str) -> bool:
    """Determine if a key should be synced based on naming pattern."""
    
    # Patterns for multi-size images
    size_patterns = ['_thumbnail.', '_small.', '_medium.']
    
    # Check if this is a multi-size image
    for pattern in size_patterns:
        if pattern in key:
            return True
    
    # Also sync full-size WebP and AVIF (but not original JPG)
    if key.endswith('.webp') or key.endswith('.avif'):
        # Make sure it's not already a sized variant
        if not any(pattern in key for pattern in size_patterns):
            return True
    
    return False


def copy_object(s3_client, source_bucket: str, dest_bucket: str, key: str, dry_run: bool = False) -> tuple[str, bool, str]:
    """Copy a single object from source to destination bucket."""
    
    try:
        if dry_run:
            logger.info(f"[DRY RUN] Would copy: {key}")
            return key, True, "dry-run"
        
        # Copy the object
        copy_source = {'Bucket': source_bucket, 'Key': key}
        s3_client.copy_object(
            CopySource=copy_source,
            Bucket=dest_bucket,
            Key=key,
            MetadataDirective='COPY'
        )
        
        logger.debug(f"✅ Copied: {key}")
        return key, True, "success"
        
    except Exception as e:
        logger.error(f"❌ Failed to copy {key}: {str(e)}")
        return key, False, str(e)


def sync_buckets(source_bucket: str, dest_bucket: str, prefix: str = "assets/", 
                 dry_run: bool = False, max_workers: int = 10):
    """Sync multi-size images from source to destination bucket."""
    
    s3_client = boto3.client('s3')
    
    # List all objects in source bucket
    logger.info(f"Listing objects in s3://{source_bucket}/{prefix}")
    
    all_keys = []
    continuation_token = None
    
    while True:
        list_params = {
            'Bucket': source_bucket,
            'Prefix': prefix,
            'MaxKeys': 1000
        }
        
        if continuation_token:
            list_params['ContinuationToken'] = continuation_token
        
        response = s3_client.list_objects_v2(**list_params)
        
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                if should_sync_key(key):
                    all_keys.append(key)
        
        if not response.get('IsTruncated', False):
            break
        
        continuation_token = response.get('NextContinuationToken')
    
    logger.info(f"Found {len(all_keys)} multi-size images to sync")
    
    if not all_keys:
        logger.info("No files to sync")
        return
    
    # Group by type for reporting
    stats = {
        'thumbnail': 0,
        'small': 0,
        'medium': 0,
        'webp_full': 0,
        'avif_full': 0,
    }
    
    for key in all_keys:
        if '_thumbnail.' in key:
            stats['thumbnail'] += 1
        elif '_small.' in key:
            stats['small'] += 1
        elif '_medium.' in key:
            stats['medium'] += 1
        elif key.endswith('.webp'):
            stats['webp_full'] += 1
        elif key.endswith('.avif'):
            stats['avif_full'] += 1
    
    logger.info(f"\nBreakdown by type:")
    logger.info(f"  Thumbnails: {stats['thumbnail']}")
    logger.info(f"  Small: {stats['small']}")
    logger.info(f"  Medium: {stats['medium']}")
    logger.info(f"  Full WebP: {stats['webp_full']}")
    logger.info(f"  Full AVIF: {stats['avif_full']}")
    
    if dry_run:
        logger.info(f"\n[DRY RUN] Would copy {len(all_keys)} files")
        return
    
    # Sync files in parallel
    logger.info(f"\nStarting sync with {max_workers} workers...")
    
    success_count = 0
    error_count = 0
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(copy_object, s3_client, source_bucket, dest_bucket, key, dry_run): key
            for key in all_keys
        }
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            key, success, message = future.result()
            
            if success:
                success_count += 1
            else:
                error_count += 1
            
            # Progress update every 100 files
            if completed % 100 == 0:
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (len(all_keys) - completed) / rate if rate > 0 else 0
                
                logger.info(f"Progress: {completed}/{len(all_keys)} files ({completed/len(all_keys)*100:.1f}%), "
                          f"Rate: {rate:.1f} files/sec, ETA: {eta/60:.1f} minutes")
    
    # Summary
    elapsed_total = time.time() - start_time
    logger.info(f"\n{'='*60}")
    logger.info(f"Sync Complete!")
    logger.info(f"{'='*60}")
    logger.info(f"Total files: {len(all_keys)}")
    logger.info(f"Successful: {success_count}")
    logger.info(f"Failed: {error_count}")
    logger.info(f"Total time: {elapsed_total/60:.1f} minutes")
    logger.info(f"Average rate: {len(all_keys)/elapsed_total:.1f} files/sec")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Sync multi-size images from dev to prod CDN")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without copying")
    parser.add_argument("--workers", type=int, default=10, help="Number of parallel workers")
    parser.add_argument("--prefix", default="assets/", help="S3 prefix to sync")
    
    args = parser.parse_args()
    
    # Get bucket names from Pulumi
    from pulumi import automation as auto
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    portfolio_root = os.path.dirname(script_dir)
    work_dir = os.path.join(os.path.dirname(portfolio_root), "infra")
    
    # Get dev bucket
    logger.info("Getting dev CDN bucket...")
    dev_stack = auto.create_or_select_stack(stack_name="tnorlund/portfolio/dev", work_dir=work_dir)
    dev_outputs = dev_stack.outputs()
    dev_bucket = dev_outputs["cdn_bucket_name"].value
    
    # Get prod bucket
    logger.info("Getting prod CDN bucket...")
    prod_stack = auto.create_or_select_stack(stack_name="tnorlund/portfolio/prod", work_dir=work_dir)
    prod_outputs = prod_stack.outputs()
    prod_bucket = prod_outputs["cdn_bucket_name"].value
    
    logger.info(f"Source bucket (dev): {dev_bucket}")
    logger.info(f"Destination bucket (prod): {prod_bucket}")
    
    # Run sync
    sync_buckets(
        source_bucket=dev_bucket,
        dest_bucket=prod_bucket,
        prefix=args.prefix,
        dry_run=args.dry_run,
        max_workers=args.workers
    )


if __name__ == "__main__":
    main()