#!/usr/bin/env python3
"""
Final verification of CDN status - check both flat and subdirectory patterns.
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


def check_entity_cdn_completeness(entity: Dict[str, Any], s3_client, cdn_bucket: str) -> Dict[str, Any]:
    """Check CDN completeness for an entity."""
    entity_type = entity.get('TYPE')
    result = {
        'entity_id': entity['PK'],
        'entity_type': entity_type,
        'cdn_fields_populated': 0,
        'cdn_files_in_s3': 0,
        'has_flat_pattern': False,
        'has_subdirectory_pattern': False,
        'missing_files': []
    }
    
    if entity_type == 'IMAGE':
        entity_id = entity['PK'].replace('IMAGE#', '')
        
        # Check populated CDN fields
        cdn_fields = [
            'cdn_s3_key', 'cdn_webp_s3_key', 'cdn_avif_s3_key',
            'cdn_thumbnail_s3_key', 'cdn_thumbnail_webp_s3_key', 'cdn_thumbnail_avif_s3_key',
            'cdn_small_s3_key', 'cdn_small_webp_s3_key', 'cdn_small_avif_s3_key',
            'cdn_medium_s3_key', 'cdn_medium_webp_s3_key', 'cdn_medium_avif_s3_key'
        ]
        
        for field in cdn_fields:
            if entity.get(field) and entity[field] != 'None':
                result['cdn_fields_populated'] += 1
                
                # Check if file exists in S3
                try:
                    s3_client.head_object(Bucket=cdn_bucket, Key=entity[field])
                    result['cdn_files_in_s3'] += 1
                    result['has_flat_pattern'] = True
                except s3_client.exceptions.ClientError:
                    result['missing_files'].append(entity[field])
        
        # Check subdirectory pattern
        subdirectory_patterns = [
            f"assets/{entity_id}/1.jpg",
            f"assets/{entity_id}/1_thumbnail.jpg",
            f"assets/{entity_id}/1_small.jpg",
            f"assets/{entity_id}/1_medium.jpg"
        ]
        
        for pattern in subdirectory_patterns:
            try:
                s3_client.head_object(Bucket=cdn_bucket, Key=pattern)
                result['has_subdirectory_pattern'] = True
                break
            except s3_client.exceptions.ClientError:
                pass
    
    elif entity_type == 'RECEIPT':
        image_id = entity['PK'].replace('IMAGE#', '')
        receipt_id = entity['SK'].replace('RECEIPT#', '')
        
        # Check full-size CDN fields for receipts
        cdn_fields = ['cdn_s3_key', 'cdn_webp_s3_key', 'cdn_avif_s3_key']
        
        for field in cdn_fields:
            if entity.get(field) and entity[field] != 'None':
                result['cdn_fields_populated'] += 1
                
                # Check if file exists in S3
                try:
                    s3_client.head_object(Bucket=cdn_bucket, Key=entity[field])
                    result['cdn_files_in_s3'] += 1
                    result['has_flat_pattern'] = True
                except s3_client.exceptions.ClientError:
                    result['missing_files'].append(entity[field])
        
        # Check subdirectory pattern for receipts
        receipt_num = int(receipt_id)
        subdirectory_patterns = [
            f"assets/{image_id}/{receipt_num}.jpg",
            f"assets/{image_id}/{receipt_num}_thumbnail.jpg",
            f"assets/{image_id}/{receipt_num}_small.jpg"
        ]
        
        for pattern in subdirectory_patterns:
            try:
                s3_client.head_object(Bucket=cdn_bucket, Key=pattern)
                result['has_subdirectory_pattern'] = True
                break
            except s3_client.exceptions.ClientError:
                pass
    
    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Final verification of CDN status in PROD"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Number of entities to sample",
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
    
    dynamodb = boto3.resource('dynamodb')
    s3_client = boto3.client('s3')
    table = dynamodb.Table(prod_table)
    
    stats = {
        'images_checked': 0,
        'receipts_checked': 0,
        'images_fully_populated': 0,
        'receipts_fully_populated': 0,
        'entities_with_flat_pattern': 0,
        'entities_with_subdirectory_pattern': 0,
        'entities_with_both_patterns': 0,
        'entities_with_missing_files': 0
    }
    
    # Sample Image entities
    logger.info(f"\nChecking {args.sample_size} IMAGE entities...")
    image_scan_kwargs = {
        'FilterExpression': '#type = :image_type',
        'ExpressionAttributeNames': {'#type': 'TYPE'},
        'ExpressionAttributeValues': {':image_type': 'IMAGE'},
        'Limit': args.sample_size
    }
    
    response = table.scan(**image_scan_kwargs)
    images = response.get('Items', [])
    
    for entity in images:
        stats['images_checked'] += 1
        result = check_entity_cdn_completeness(entity, s3_client, cdn_bucket)
        
        if result['cdn_fields_populated'] > 0 and result['cdn_fields_populated'] == result['cdn_files_in_s3']:
            stats['images_fully_populated'] += 1
        
        if result['has_flat_pattern']:
            stats['entities_with_flat_pattern'] += 1
        
        if result['has_subdirectory_pattern']:
            stats['entities_with_subdirectory_pattern'] += 1
            
        if result['has_flat_pattern'] and result['has_subdirectory_pattern']:
            stats['entities_with_both_patterns'] += 1
            logger.info(f"  Image {result['entity_id']} has BOTH patterns available")
        
        if result['missing_files']:
            stats['entities_with_missing_files'] += 1
            logger.warning(f"  Image {result['entity_id']} missing files: {result['missing_files']}")
    
    # Sample Receipt entities
    logger.info(f"\nChecking {args.sample_size} RECEIPT entities...")
    receipt_scan_kwargs = {
        'FilterExpression': '#type = :receipt_type',
        'ExpressionAttributeNames': {'#type': 'TYPE'},
        'ExpressionAttributeValues': {':receipt_type': 'RECEIPT'},
        'Limit': args.sample_size
    }
    
    response = table.scan(**receipt_scan_kwargs)
    receipts = response.get('Items', [])
    
    for entity in receipts:
        stats['receipts_checked'] += 1
        result = check_entity_cdn_completeness(entity, s3_client, cdn_bucket)
        
        if result['cdn_fields_populated'] > 0 and result['cdn_fields_populated'] == result['cdn_files_in_s3']:
            stats['receipts_fully_populated'] += 1
        
        if result['has_flat_pattern']:
            stats['entities_with_flat_pattern'] += 1
        
        if result['has_subdirectory_pattern']:
            stats['entities_with_subdirectory_pattern'] += 1
            
        if result['has_flat_pattern'] and result['has_subdirectory_pattern']:
            stats['entities_with_both_patterns'] += 1
            logger.info(f"  Receipt {result['entity_id']} has BOTH patterns available")
        
        if result['missing_files']:
            stats['entities_with_missing_files'] += 1
            logger.warning(f"  Receipt {result['entity_id']} missing files: {result['missing_files']}")
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("FINAL CDN STATUS VERIFICATION")
    logger.info("=" * 60)
    logger.info(f"Images checked: {stats['images_checked']}")
    logger.info(f"Images fully populated with valid S3 files: {stats['images_fully_populated']}")
    logger.info(f"Receipts checked: {stats['receipts_checked']}")
    logger.info(f"Receipts fully populated with valid S3 files: {stats['receipts_fully_populated']}")
    logger.info(f"\nPattern Analysis:")
    logger.info(f"Entities using flat pattern: {stats['entities_with_flat_pattern']}")
    logger.info(f"Entities with subdirectory pattern available: {stats['entities_with_subdirectory_pattern']}")
    logger.info(f"Entities with BOTH patterns: {stats['entities_with_both_patterns']}")
    logger.info(f"Entities with missing S3 files: {stats['entities_with_missing_files']}")
    
    if stats['entities_with_both_patterns'] > 0:
        logger.info("\n⚠️  Some entities have CDN files in BOTH patterns!")
        logger.info("The database is currently using the flat pattern.")
        logger.info("Consider migrating to subdirectory pattern if that's preferred.")
    
    if stats['entities_with_missing_files'] > 0:
        logger.info("\n❌ Some entities reference S3 files that don't exist!")
        logger.info("These CDN fields need to be corrected.")
    
    total_entities = stats['images_checked'] + stats['receipts_checked']
    total_populated = stats['images_fully_populated'] + stats['receipts_fully_populated']
    
    if total_populated == total_entities:
        logger.info("\n✅ All sampled entities have valid CDN configurations!")
    else:
        logger.info(f"\n⚠️  {total_entities - total_populated} entities have CDN issues")


if __name__ == "__main__":
    main()