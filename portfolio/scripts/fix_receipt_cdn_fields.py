#!/usr/bin/env python3
"""
Fix PROD Receipt entities to use correct CDN field mappings.
Receipts use different CDN structure than Images.
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


def extract_ids_from_receipt(entity: Dict[str, Any]) -> Tuple[str, str]:
    """Extract image_id and receipt_id from Receipt entity."""
    # PK format: IMAGE#{image_id} (Receipt entities use IMAGE# prefix in PK)
    # SK format: RECEIPT#{receipt_id} where receipt_id is 5 digits like 00001
    pk = entity['PK']
    sk = entity['SK']
    
    # Remove IMAGE# prefix from PK to get the actual image UUID
    image_id = pk.replace('IMAGE#', '')
    receipt_id = sk.replace('RECEIPT#', '')
    
    return image_id, receipt_id


def generate_receipt_cdn_fields(image_id: str, receipt_id: str, s3_client, cdn_bucket: str) -> Tuple[Dict[str, Any], int]:
    """
    Generate CDN field mappings for a Receipt entity.
    Receipts use pattern: assets/{image_id}_RECEIPT_{receipt_id}.{format}
    Receipts only have full-size images, no thumbnails/small/medium.
    """
    cdn_fields = {}
    
    # Initialize all fields to None (receipts don't have size variants)
    size_variant_fields = [
        'cdn_thumbnail_s3_key', 'cdn_thumbnail_webp_s3_key', 'cdn_thumbnail_avif_s3_key',
        'cdn_small_s3_key', 'cdn_small_webp_s3_key', 'cdn_small_avif_s3_key',
        'cdn_medium_s3_key', 'cdn_medium_webp_s3_key', 'cdn_medium_avif_s3_key'
    ]
    
    for field in size_variant_fields:
        cdn_fields[field] = None
    
    # Check for full-size receipt images
    # Possible formats: PNG (older), JPG (newer), WebP, AVIF
    possible_files = {
        'cdn_s3_key': [
            f"assets/{image_id}_RECEIPT_{receipt_id}.png",
            f"assets/{image_id}_RECEIPT_{receipt_id}.jpg"
        ],
        'cdn_webp_s3_key': f"assets/{image_id}_RECEIPT_{receipt_id}.webp",
        'cdn_avif_s3_key': f"assets/{image_id}_RECEIPT_{receipt_id}.avif"
    }
    
    found_files = 0
    
    # Check for PNG/JPG (use first one found)
    for png_jpg_key in possible_files['cdn_s3_key']:
        try:
            s3_client.head_object(Bucket=cdn_bucket, Key=png_jpg_key)
            cdn_fields['cdn_s3_key'] = png_jpg_key
            found_files += 1
            break
        except s3_client.exceptions.ClientError:
            continue
    
    # Check for WebP
    try:
        s3_client.head_object(Bucket=cdn_bucket, Key=possible_files['cdn_webp_s3_key'])
        cdn_fields['cdn_webp_s3_key'] = possible_files['cdn_webp_s3_key']
        found_files += 1
    except s3_client.exceptions.ClientError:
        cdn_fields['cdn_webp_s3_key'] = None
    
    # Check for AVIF
    try:
        s3_client.head_object(Bucket=cdn_bucket, Key=possible_files['cdn_avif_s3_key'])
        cdn_fields['cdn_avif_s3_key'] = possible_files['cdn_avif_s3_key']
        found_files += 1
    except s3_client.exceptions.ClientError:
        cdn_fields['cdn_avif_s3_key'] = None
    
    logger.info(f"  Receipt {image_id}/RECEIPT_{receipt_id}: {found_files} CDN files found")
    if found_files > 0 and cdn_fields.get('cdn_s3_key'):
        logger.info(f"    Primary format: {cdn_fields['cdn_s3_key'].split('.')[-1].upper()}")
    
    return cdn_fields, found_files


def fix_receipt_cdn_fields(table_name: str, cdn_bucket: str, dry_run: bool = True):
    """Fix PROD Receipt entities with correct CDN field values."""
    dynamodb = boto3.resource('dynamodb')
    s3_client = boto3.client('s3')
    table = dynamodb.Table(table_name)
    
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Fixing CDN fields for Receipt entities...")
    logger.info(f"Table: {table_name}")
    logger.info(f"CDN Bucket: {cdn_bucket}")
    
    stats = {
        'receipts_processed': 0,
        'receipts_updated': 0,
        'total_cdn_files_found': 0,
        'receipts_with_no_cdn': 0,
        'update_errors': 0,
        'format_stats': {
            'png_only': 0,
            'jpg_only': 0,
            'with_webp': 0,
            'with_avif': 0,
            'full_set': 0
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
            image_id, receipt_id = extract_ids_from_receipt(entity)
            
            logger.info(f"Processing Receipt: {image_id}/RECEIPT#{receipt_id}")
            
            # Generate correct CDN fields for receipts
            cdn_fields, found_count = generate_receipt_cdn_fields(image_id, receipt_id, s3_client, cdn_bucket)
            stats['total_cdn_files_found'] += found_count
            
            # Update format statistics
            if found_count == 0:
                stats['receipts_with_no_cdn'] += 1
                logger.warning(f"  No CDN files found for receipt")
                continue
            
            # Track format availability
            has_primary = cdn_fields.get('cdn_s3_key') is not None
            has_webp = cdn_fields.get('cdn_webp_s3_key') is not None
            has_avif = cdn_fields.get('cdn_avif_s3_key') is not None
            
            if has_primary and has_webp and has_avif:
                stats['format_stats']['full_set'] += 1
            elif has_webp:
                stats['format_stats']['with_webp'] += 1
            elif has_avif:
                stats['format_stats']['with_avif'] += 1
            elif cdn_fields.get('cdn_s3_key', '').endswith('.png'):
                stats['format_stats']['png_only'] += 1
            elif cdn_fields.get('cdn_s3_key', '').endswith('.jpg'):
                stats['format_stats']['jpg_only'] += 1
            
            # Update the entity with corrected CDN fields
            try:
                if not dry_run:
                    # Build update expression for all CDN fields
                    update_expression = "SET "
                    expression_attribute_values = {}
                    expression_attribute_names = {}
                    
                    # Update all CDN fields (setting size variants to NULL)
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
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}RECEIPT CDN FIELD FIX SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Receipts processed: {stats['receipts_processed']}")
    logger.info(f"Receipts updated: {stats['receipts_updated']}")
    logger.info(f"Receipts with no CDN files: {stats['receipts_with_no_cdn']}")
    logger.info(f"Total CDN files found: {stats['total_cdn_files_found']}")
    logger.info(f"Update errors: {stats['update_errors']}")
    
    logger.info("\nFormat Distribution:")
    logger.info(f"  PNG only: {stats['format_stats']['png_only']}")
    logger.info(f"  JPG only: {stats['format_stats']['jpg_only']}")
    logger.info(f"  With WebP: {stats['format_stats']['with_webp']}")
    logger.info(f"  With AVIF: {stats['format_stats']['with_avif']}")
    logger.info(f"  Full set (all formats): {stats['format_stats']['full_set']}")
    
    if dry_run:
        logger.info("\n🔍 This was a DRY RUN - no entities were actually updated")
        logger.info("Run with --no-dry-run to actually fix CDN fields")
    else:
        if stats['receipts_updated'] > 0:
            logger.info(f"\n✅ Successfully fixed {stats['receipts_updated']} receipt entities with correct CDN fields")
        if stats['update_errors'] > 0:
            logger.info(f"\n⚠️  {stats['update_errors']} receipts had update errors")
    
    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Fix PROD Receipt entities with correct CDN field values"
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
    
    # Fix Receipt CDN fields
    fix_receipt_cdn_fields(prod_table, cdn_bucket, dry_run=not args.no_dry_run)


if __name__ == "__main__":
    main()