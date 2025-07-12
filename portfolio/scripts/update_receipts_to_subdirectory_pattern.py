#!/usr/bin/env python3
"""
Update PROD Receipt entities to use subdirectory CDN pattern with sized images.
Pattern: assets/{image_id}/{receipt_number}_{size}.{format}
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List, Optional, Tuple
import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def extract_ids_from_receipt(entity: Dict[str, Any]) -> Tuple[str, str, int]:
    """Extract image_id and receipt_id from Receipt entity."""
    # PK format: IMAGE#{image_id}
    # SK format: RECEIPT#{receipt_id} where receipt_id is 5 digits like 00001
    pk = entity['PK']
    sk = entity['SK']
    
    # Remove IMAGE# prefix from PK to get the actual image UUID
    image_id = pk.replace('IMAGE#', '')
    receipt_id = sk.replace('RECEIPT#', '')
    receipt_number = int(receipt_id)  # Convert to int for subdirectory pattern
    
    return image_id, receipt_id, receipt_number


def generate_subdirectory_cdn_fields(image_id: str, receipt_number: int, s3_client, cdn_bucket: str) -> Tuple[Dict[str, Any], int, Dict[str, int]]:
    """
    Generate CDN field mappings for a Receipt entity using subdirectory pattern.
    Pattern: assets/{image_id}/{receipt_number}_{size}.{format}
    """
    cdn_fields = {}
    found_files = 0
    size_stats = {'thumbnail': 0, 'small': 0, 'medium': 0, 'full': 0}
    
    # Define all possible CDN files using subdirectory pattern
    cdn_files = {
        # Thumbnail
        'cdn_thumbnail_s3_key': f"assets/{image_id}/{receipt_number}_thumbnail.jpg",
        'cdn_thumbnail_webp_s3_key': f"assets/{image_id}/{receipt_number}_thumbnail.webp", 
        'cdn_thumbnail_avif_s3_key': f"assets/{image_id}/{receipt_number}_thumbnail.avif",
        # Small
        'cdn_small_s3_key': f"assets/{image_id}/{receipt_number}_small.jpg",
        'cdn_small_webp_s3_key': f"assets/{image_id}/{receipt_number}_small.webp",
        'cdn_small_avif_s3_key': f"assets/{image_id}/{receipt_number}_small.avif",
        # Medium
        'cdn_medium_s3_key': f"assets/{image_id}/{receipt_number}_medium.jpg",
        'cdn_medium_webp_s3_key': f"assets/{image_id}/{receipt_number}_medium.webp",
        'cdn_medium_avif_s3_key': f"assets/{image_id}/{receipt_number}_medium.avif",
        # Full size
        'cdn_s3_key': f"assets/{image_id}/{receipt_number}.jpg",
        'cdn_webp_s3_key': f"assets/{image_id}/{receipt_number}.webp",
        'cdn_avif_s3_key': f"assets/{image_id}/{receipt_number}.avif"
    }
    
    # Check which files actually exist in S3
    for field_name, s3_key in cdn_files.items():
        try:
            s3_client.head_object(Bucket=cdn_bucket, Key=s3_key)
            cdn_fields[field_name] = s3_key
            found_files += 1
            
            # Track size statistics
            if 'thumbnail' in field_name:
                size_stats['thumbnail'] += 1
            elif 'small' in field_name:
                size_stats['small'] += 1
            elif 'medium' in field_name:
                size_stats['medium'] += 1
            else:
                size_stats['full'] += 1
                
        except s3_client.exceptions.ClientError:
            # File doesn't exist, set to None
            cdn_fields[field_name] = None
    
    logger.info(f"  Receipt {image_id}/{receipt_number}: {found_files}/12 CDN files found")
    if found_files > 0:
        size_summary = []
        if size_stats['thumbnail'] > 0:
            size_summary.append(f"{size_stats['thumbnail']} thumbnail")
        if size_stats['small'] > 0:
            size_summary.append(f"{size_stats['small']} small")
        if size_stats['medium'] > 0:
            size_summary.append(f"{size_stats['medium']} medium")
        if size_stats['full'] > 0:
            size_summary.append(f"{size_stats['full']} full")
        logger.info(f"    Sizes: {', '.join(size_summary)}")
    
    return cdn_fields, found_files, size_stats


def update_receipts_to_subdirectory(table_name: str, cdn_bucket: str, dry_run: bool = True):
    """Update PROD Receipt entities to use subdirectory CDN pattern."""
    dynamodb = boto3.resource('dynamodb')
    s3_client = boto3.client('s3')
    table = dynamodb.Table(table_name)
    
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Updating Receipt entities to subdirectory CDN pattern...")
    logger.info(f"Table: {table_name}")
    logger.info(f"CDN Bucket: {cdn_bucket}")
    
    stats = {
        'receipts_processed': 0,
        'receipts_updated': 0,
        'receipts_with_sized_images': 0,
        'total_cdn_files_found': 0,
        'receipts_with_no_cdn': 0,
        'update_errors': 0,
        'size_distribution': {
            'has_thumbnails': 0,
            'has_small': 0,
            'has_medium': 0,
            'has_all_sizes': 0
        }
    }
    
    # Process Receipt entities
    logger.info("\nProcessing RECEIPT entities...")
    receipt_scan_kwargs = {
        'FilterExpression': '#type = :receipt_type',
        'ExpressionAttributeNames': {'#type': 'TYPE'},
        'ExpressionAttributeValues': {':receipt_type': 'RECEIPT'}
    }
    
    done = False
    start_key = None
    
    while not done:
        if start_key:
            receipt_scan_kwargs['ExclusiveStartKey'] = start_key
            
        response = table.scan(**receipt_scan_kwargs)
        items = response.get('Items', [])
        
        for entity in items:
            stats['receipts_processed'] += 1
            image_id, receipt_id, receipt_number = extract_ids_from_receipt(entity)
            
            logger.info(f"Processing Receipt: {image_id}/RECEIPT#{receipt_id}")
            
            # Generate subdirectory CDN fields
            cdn_fields, found_count, size_stats = generate_subdirectory_cdn_fields(
                image_id, receipt_number, s3_client, cdn_bucket
            )
            stats['total_cdn_files_found'] += found_count
            
            if found_count == 0:
                stats['receipts_with_no_cdn'] += 1
                logger.warning(f"  No CDN files found in subdirectory pattern")
                continue
            
            # Track size statistics
            if size_stats['thumbnail'] > 0 or size_stats['small'] > 0 or size_stats['medium'] > 0:
                stats['receipts_with_sized_images'] += 1
                
                if size_stats['thumbnail'] > 0:
                    stats['size_distribution']['has_thumbnails'] += 1
                if size_stats['small'] > 0:
                    stats['size_distribution']['has_small'] += 1
                if size_stats['medium'] > 0:
                    stats['size_distribution']['has_medium'] += 1
                if size_stats['thumbnail'] > 0 and size_stats['small'] > 0 and size_stats['medium'] > 0:
                    stats['size_distribution']['has_all_sizes'] += 1
            
            # Update the entity with subdirectory CDN fields
            try:
                if not dry_run:
                    # Build update expression for all CDN fields
                    update_expression = "SET "
                    expression_attribute_values = {}
                    expression_attribute_names = {}
                    
                    # Update all CDN fields
                    all_cdn_fields = [
                        'cdn_s3_key', 'cdn_webp_s3_key', 'cdn_avif_s3_key',
                        'cdn_thumbnail_s3_key', 'cdn_thumbnail_webp_s3_key', 'cdn_thumbnail_avif_s3_key',
                        'cdn_small_s3_key', 'cdn_small_webp_s3_key', 'cdn_small_avif_s3_key',
                        'cdn_medium_s3_key', 'cdn_medium_webp_s3_key', 'cdn_medium_avif_s3_key'
                    ]
                    
                    for field_name in all_cdn_fields:
                        field_value = cdn_fields.get(field_name)
                        update_expression += f"#{field_name} = :{field_name}, "
                        expression_attribute_names[f"#{field_name}"] = field_name
                        
                        if field_value is None:
                            expression_attribute_values[f":{field_name}"] = None
                        else:
                            expression_attribute_values[f":{field_name}"] = field_value
                    
                    # Remove trailing comma and space
                    update_expression = update_expression.rstrip(", ")
                    
                    table.update_item(
                        Key={'PK': entity['PK'], 'SK': entity['SK']},
                        UpdateExpression=update_expression,
                        ExpressionAttributeNames=expression_attribute_names,
                        ExpressionAttributeValues=expression_attribute_values
                    )
                        
                stats['receipts_updated'] += 1
                logger.info(f"  ✅ {'[DRY RUN] ' if dry_run else ''}Updated receipt with {found_count} CDN files")
                
            except Exception as e:
                stats['update_errors'] += 1
                logger.error(f"  ❌ Failed to update receipt: {e}")
        
        start_key = response.get('LastEvaluatedKey', None)
        done = start_key is None
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}RECEIPT SUBDIRECTORY PATTERN UPDATE SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Receipts processed: {stats['receipts_processed']}")
    logger.info(f"Receipts updated: {stats['receipts_updated']}")
    logger.info(f"Receipts with sized images: {stats['receipts_with_sized_images']}")
    logger.info(f"Receipts with no CDN files: {stats['receipts_with_no_cdn']}")
    logger.info(f"Total CDN files found: {stats['total_cdn_files_found']}")
    logger.info(f"Update errors: {stats['update_errors']}")
    
    logger.info("\nSize Distribution:")
    logger.info(f"  Has thumbnails: {stats['size_distribution']['has_thumbnails']}")
    logger.info(f"  Has small: {stats['size_distribution']['has_small']}")
    logger.info(f"  Has medium: {stats['size_distribution']['has_medium']}")
    logger.info(f"  Has all sizes: {stats['size_distribution']['has_all_sizes']}")
    
    avg_files = stats['total_cdn_files_found'] / stats['receipts_updated'] if stats['receipts_updated'] > 0 else 0
    logger.info(f"\nAverage CDN files per receipt: {avg_files:.1f}")
    
    if dry_run:
        logger.info("\n🔍 This was a DRY RUN - no entities were actually updated")
        logger.info("Run with --no-dry-run to actually update CDN fields")
    else:
        if stats['receipts_updated'] > 0:
            logger.info(f"\n✅ Successfully updated {stats['receipts_updated']} receipts to subdirectory pattern")
            logger.info(f"   {stats['receipts_with_sized_images']} receipts now have sized images!")
        if stats['update_errors'] > 0:
            logger.info(f"\n⚠️  {stats['update_errors']} receipts had update errors")
    
    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Update PROD Receipt entities to use subdirectory CDN pattern"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually update CDN fields (default is dry run)",
    )
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    work_dir = os.path.join(parent_dir, "infra")
    
    logger.info("Getting PROD configuration...")
    prod_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/prod",
        work_dir=work_dir,
    )
    prod_outputs = prod_stack.outputs()
    prod_table = prod_outputs["dynamodb_table_name"].value
    cdn_bucket = prod_outputs["cdn_bucket_name"].value
    
    logger.info(f"PROD table: {prod_table}")
    logger.info(f"CDN bucket: {cdn_bucket}")
    
    # Update receipts to subdirectory pattern
    update_receipts_to_subdirectory(prod_table, cdn_bucket, dry_run=not args.no_dry_run)


if __name__ == "__main__":
    main()