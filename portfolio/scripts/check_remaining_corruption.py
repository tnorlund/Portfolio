#!/usr/bin/env python3
"""
Check for any remaining corrupted entities after the PITR restore fix.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List
import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.entities import item_to_image, item_to_receipt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_remaining_corruption(table_name: str):
    """Check for remaining corrupted entities that can't be parsed."""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    stats = {
        'total_images': 0,
        'total_receipts': 0,
        'corrupted_images': 0,
        'corrupted_receipts': 0,
        'parsing_errors': []
    }
    
    logger.info("Checking all Image and Receipt entities for parsing errors...")
    
    # Check IMAGE entities
    logger.info("Scanning IMAGE entities...")
    image_scan_kwargs = {
        'FilterExpression': '#type = :image_type',
        'ExpressionAttributeNames': {'#type': 'TYPE'},
        'ExpressionAttributeValues': {':image_type': 'IMAGE'}
    }
    
    done = False
    start_key = None
    
    while not done:
        if start_key:
            image_scan_kwargs['ExclusiveStartKey'] = start_key
            
        response = table.scan(**image_scan_kwargs)
        items = response.get('Items', [])
        
        for item in items:
            stats['total_images'] += 1
            
            try:
                # Try to parse the Image entity
                image_entity = item_to_image(item)
                # If we get here, parsing succeeded
                
            except Exception as e:
                stats['corrupted_images'] += 1
                error_info = {
                    'type': 'IMAGE',
                    'PK': item.get('PK', 'unknown'),
                    'SK': item.get('SK', 'unknown'),
                    'error': str(e),
                    'missing_fields': []
                }
                
                # Check what fields are missing
                required_fields = ['width', 'height', 'timestamp_added', 'raw_s3_bucket', 'raw_s3_key', 'image_type']
                for field in required_fields:
                    if field not in item or item[field] is None:
                        error_info['missing_fields'].append(field)
                
                stats['parsing_errors'].append(error_info)
                logger.error(f"Corrupted IMAGE {item.get('PK')}: {e}")
                if error_info['missing_fields']:
                    logger.error(f"  Missing fields: {error_info['missing_fields']}")
        
        start_key = response.get('LastEvaluatedKey', None)
        done = start_key is None
        
        if stats['total_images'] % 100 == 0:
            logger.info(f"Checked {stats['total_images']} IMAGE entities, found {stats['corrupted_images']} corrupted...")
    
    # Check RECEIPT entities
    logger.info("Scanning RECEIPT entities...")
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
        
        for item in items:
            stats['total_receipts'] += 1
            
            try:
                # Try to parse the Receipt entity
                receipt_entity = item_to_receipt(item)
                # If we get here, parsing succeeded
                
            except Exception as e:
                stats['corrupted_receipts'] += 1
                error_info = {
                    'type': 'RECEIPT',
                    'PK': item.get('PK', 'unknown'),
                    'SK': item.get('SK', 'unknown'),
                    'error': str(e),
                    'missing_fields': []
                }
                
                # Check what fields are missing
                required_fields = ['width', 'height', 'timestamp_added', 'raw_s3_bucket', 'raw_s3_key', 
                                  'top_left', 'top_right', 'bottom_left', 'bottom_right']
                for field in required_fields:
                    if field not in item or item[field] is None:
                        error_info['missing_fields'].append(field)
                
                stats['parsing_errors'].append(error_info)
                logger.error(f"Corrupted RECEIPT {item.get('PK')}/{item.get('SK')}: {e}")
                if error_info['missing_fields']:
                    logger.error(f"  Missing fields: {error_info['missing_fields']}")
        
        start_key = response.get('LastEvaluatedKey', None)
        done = start_key is None
        
        if stats['total_receipts'] % 100 == 0:
            logger.info(f"Checked {stats['total_receipts']} RECEIPT entities, found {stats['corrupted_receipts']} corrupted...")
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("CORRUPTION CHECK RESULTS")
    logger.info("=" * 60)
    logger.info(f"Total IMAGE entities: {stats['total_images']}")
    logger.info(f"Corrupted IMAGE entities: {stats['corrupted_images']}")
    logger.info(f"Total RECEIPT entities: {stats['total_receipts']}")
    logger.info(f"Corrupted RECEIPT entities: {stats['corrupted_receipts']}")
    logger.info(f"Total corrupted entities: {stats['corrupted_images'] + stats['corrupted_receipts']}")
    
    if stats['parsing_errors']:
        logger.info(f"\nDETAILED ERRORS:")
        for error in stats['parsing_errors']:
            logger.info(f"  {error['type']} {error['PK']}/{error['SK']}")
            logger.info(f"    Error: {error['error']}")
            if error['missing_fields']:
                logger.info(f"    Missing: {error['missing_fields']}")
    else:
        logger.info("\n✅ No corrupted entities found! All entities parse correctly.")
    
    return stats['parsing_errors']


def delete_corrupted_entities(table_name: str, corrupted_entities: List[Dict], dry_run: bool = True):
    """Delete the corrupted entities that can't be parsed."""
    if not corrupted_entities:
        logger.info("No corrupted entities to delete!")
        return
    
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}Deleting {len(corrupted_entities)} corrupted entities...")
    
    deleted_count = 0
    error_count = 0
    
    for entity in corrupted_entities:
        try:
            pk = entity['PK']
            sk = entity['SK']
            entity_type = entity['type']
            
            logger.info(f"{'[DRY RUN] ' if dry_run else ''}Deleting {entity_type}: {pk}/{sk}")
            
            if not dry_run:
                table.delete_item(Key={'PK': pk, 'SK': sk})
                
            deleted_count += 1
            
        except Exception as e:
            logger.error(f"Failed to delete {entity['PK']}/{entity['SK']}: {e}")
            error_count += 1
    
    logger.info(f"\n{'[DRY RUN] ' if dry_run else ''}Deletion summary:")
    logger.info(f"  Entities deleted: {deleted_count}")
    logger.info(f"  Errors: {error_count}")
    
    if dry_run:
        logger.info("\nThis was a DRY RUN - no entities were actually deleted")
        logger.info("Run with --no-dry-run to actually delete corrupted entities")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check for and optionally delete remaining corrupted entities"
    )
    parser.add_argument(
        "--delete-corrupted",
        action="store_true",
        help="Delete any corrupted entities found",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually delete corrupted entities (default is dry run)",
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
    
    logger.info(f"PROD table: {prod_table}")
    
    # Check for corrupted entities
    corrupted_entities = check_remaining_corruption(prod_table)
    
    # Delete corrupted entities if requested
    if args.delete_corrupted and corrupted_entities:
        delete_corrupted_entities(prod_table, corrupted_entities, dry_run=not args.no_dry_run)


if __name__ == "__main__":
    main()