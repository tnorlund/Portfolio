#!/usr/bin/env python3
"""
Quick PITR sample - restore just a few entities to see if they have S3 keys.
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Dict, Any, List
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


def quick_pitr_sample():
    """Quick test of PITR restore with a small sample."""
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
    
    dynamodb = boto3.client('dynamodb')
    dynamodb_resource = boto3.resource('dynamodb')
    
    # Generate unique name for restore table
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    restore_table_name = f"{prod_table}-pitr-test-{timestamp}"
    
    logger.info(f"PROD table: {prod_table}")
    logger.info(f"Restore table: {restore_table_name}")
    
    # Get a few sample corrupted entities first
    logger.info("Getting sample corrupted entities...")
    table = dynamodb_resource.Table(prod_table)
    
    scan_kwargs = {
        'FilterExpression': '#type = :image_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)',
        'ExpressionAttributeNames': {'#type': 'TYPE'},
        'ExpressionAttributeValues': {
            ':image_type': 'IMAGE',
            ':pk_prefix': 'IMAGE#',
            ':sk_prefix': 'IMAGE#'
        },
        'Limit': 5  # Just get 5 for quick test
    }
    
    response = table.scan(**scan_kwargs)
    sample_entities = []
    
    for item in response.get('Items', []):
        # Check if Image entity is corrupted
        required_fields = ['width', 'height', 'timestamp_added', 'raw_s3_bucket', 'raw_s3_key', 'image_type']
        missing_fields = [field for field in required_fields if field not in item]
        
        if missing_fields:
            sample_entities.append({
                'PK': item['PK'],
                'SK': item['SK'],
                'TYPE': 'IMAGE',
                'missing_fields': missing_fields
            })
    
    if not sample_entities:
        logger.info("No corrupted entities found in sample!")
        return
    
    logger.info(f"Found {len(sample_entities)} corrupted entities to test with")
    for entity in sample_entities:
        logger.info(f"  {entity['PK']}: missing {entity['missing_fields']}")
    
    # Create PITR restore
    restore_datetime = datetime.fromisoformat("2025-07-04T12:00:00")
    
    logger.info(f"Creating PITR restore to time: {restore_datetime}")
    
    try:
        response = dynamodb.restore_table_to_point_in_time(
            SourceTableName=prod_table,
            TargetTableName=restore_table_name,
            RestoreDateTime=restore_datetime,
            UseLatestRestorableTime=False
        )
        
        logger.info("PITR restore initiated. Waiting for table to become active...")
        
        # Wait for table to become active
        waiter = dynamodb.get_waiter('table_exists')
        waiter.wait(
            TableName=restore_table_name,
            WaiterConfig={'Delay': 20, 'MaxAttempts': 30}  # Wait up to 10 minutes
        )
        
        logger.info("Restore table is now active!")
        
        # Check the restored entities
        restore_table = dynamodb_resource.Table(restore_table_name)
        
        logger.info("Checking restored entities...")
        for entity in sample_entities:
            try:
                restore_response = restore_table.get_item(Key={
                    'PK': entity['PK'], 
                    'SK': entity['SK']
                })
                
                if 'Item' in restore_response:
                    restored_item = restore_response['Item']
                    
                    # Check what we have in the restored entity
                    s3_key = restored_item.get('raw_s3_key', 'NOT_FOUND')
                    s3_bucket = restored_item.get('raw_s3_bucket', 'NOT_FOUND')
                    width = restored_item.get('width', 'NOT_FOUND')
                    height = restored_item.get('height', 'NOT_FOUND')
                    image_type = restored_item.get('image_type', 'NOT_FOUND')
                    timestamp_added = restored_item.get('timestamp_added', 'NOT_FOUND')
                    
                    logger.info(f"\n✅ RESTORED: {entity['PK']}")
                    logger.info(f"   S3 Bucket: {s3_bucket}")
                    logger.info(f"   S3 Key: {s3_key}")
                    logger.info(f"   Dimensions: {width}x{height}")
                    logger.info(f"   Type: {image_type}")
                    logger.info(f"   Added: {timestamp_added}")
                    
                    # Check if the fields that were missing are now present
                    missing_in_current = entity['missing_fields']
                    still_missing = []
                    now_present = []
                    
                    for field in missing_in_current:
                        if field in restored_item and restored_item[field] is not None:
                            now_present.append(field)
                        else:
                            still_missing.append(field)
                    
                    if now_present:
                        logger.info(f"   ✅ Now has: {now_present}")
                    if still_missing:
                        logger.info(f"   ❌ Still missing: {still_missing}")
                else:
                    logger.warning(f"❌ Entity not found in restore: {entity['PK']}")
                    
            except Exception as e:
                logger.error(f"Error checking restored entity {entity['PK']}: {e}")
        
        # Cleanup
        logger.info(f"\nCleaning up restore table: {restore_table_name}")
        dynamodb.delete_table(TableName=restore_table_name)
        logger.info("Cleanup initiated")
        
    except Exception as e:
        logger.error(f"PITR restore failed: {e}")
        return


if __name__ == "__main__":
    quick_pitr_sample()