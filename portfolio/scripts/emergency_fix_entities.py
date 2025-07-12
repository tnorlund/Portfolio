#!/usr/bin/env python3
"""
Emergency script to fix entities that may have been corrupted.
This script directly queries DynamoDB and reconstructs entities properly.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key, Attr

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Receipt, Image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class EmergencyFixer:
    """Emergency fixes for potentially corrupted entities."""

    def __init__(self, dynamo_table_name: str, dry_run: bool = True):
        self.table_name = dynamo_table_name
        self.dry_run = dry_run
        self.dynamo_resource = boto3.resource('dynamodb')
        self.table = self.dynamo_resource.Table(dynamo_table_name)
        self.dynamo_client = DynamoClient(dynamo_table_name)
        
        self.stats = {
            "images_scanned": 0,
            "receipts_scanned": 0,
            "images_fixed": 0,
            "receipts_fixed": 0,
            "errors": 0,
        }

    def scan_and_fix_all(self):
        """Scan and fix all entities."""
        logger.info("Starting emergency entity fix...")
        
        # First, ensure all Images have TYPE field
        self.ensure_image_type_fields()
        
        # Then, ensure all Receipts have TYPE field
        self.ensure_receipt_type_fields()
        
        # Print summary
        self.print_summary()

    def ensure_image_type_fields(self):
        """Ensure all Image entities have TYPE field."""
        logger.info("Checking Image entities for TYPE field...")
        
        # Scan for all Image entities
        scan_kwargs = {
            'FilterExpression': Key('PK').begins_with('IMAGE#') & Key('SK').begins_with('IMAGE#')
        }
        
        done = False
        start_key = None
        
        while not done:
            if start_key:
                scan_kwargs['ExclusiveStartKey'] = start_key
                
            response = self.table.scan(**scan_kwargs)
            items = response.get('Items', [])
            
            for item in items:
                self.stats["images_scanned"] += 1
                
                # Check if TYPE field exists
                if 'TYPE' not in item:
                    logger.warning(f"Image missing TYPE: {item['PK']}")
                    
                    if not self.dry_run:
                        try:
                            # Add TYPE field
                            self.table.update_item(
                                Key={
                                    'PK': item['PK'],
                                    'SK': item['SK']
                                },
                                UpdateExpression='SET #type = :type',
                                ExpressionAttributeNames={'#type': 'TYPE'},
                                ExpressionAttributeValues={':type': 'IMAGE'}
                            )
                            self.stats["images_fixed"] += 1
                            logger.info(f"Added TYPE to Image: {item['PK']}")
                        except Exception as e:
                            self.stats["errors"] += 1
                            logger.error(f"Failed to fix Image {item['PK']}: {e}")
                    else:
                        self.stats["images_fixed"] += 1
            
            start_key = response.get('LastEvaluatedKey', None)
            done = start_key is None

    def ensure_receipt_type_fields(self):
        """Ensure all Receipt entities have TYPE field."""
        logger.info("Checking Receipt entities for TYPE field...")
        
        # Scan for all Receipt entities
        scan_kwargs = {
            'FilterExpression': Key('PK').begins_with('IMAGE#') & Key('SK').begins_with('RECEIPT#')
        }
        
        done = False
        start_key = None
        
        while not done:
            if start_key:
                scan_kwargs['ExclusiveStartKey'] = start_key
                
            response = self.table.scan(**scan_kwargs)
            items = response.get('Items', [])
            
            for item in items:
                self.stats["receipts_scanned"] += 1
                
                # Check if TYPE field exists
                if 'TYPE' not in item:
                    logger.warning(f"Receipt missing TYPE: {item['PK']}, {item['SK']}")
                    
                    if not self.dry_run:
                        try:
                            # Add TYPE field
                            self.table.update_item(
                                Key={
                                    'PK': item['PK'],
                                    'SK': item['SK']
                                },
                                UpdateExpression='SET #type = :type',
                                ExpressionAttributeNames={'#type': 'TYPE'},
                                ExpressionAttributeValues={':type': 'RECEIPT'}
                            )
                            self.stats["receipts_fixed"] += 1
                            logger.info(f"Added TYPE to Receipt: {item['PK']}, {item['SK']}")
                        except Exception as e:
                            self.stats["errors"] += 1
                            logger.error(f"Failed to fix Receipt {item['PK']}, {item['SK']}: {e}")
                    else:
                        self.stats["receipts_fixed"] += 1
            
            start_key = response.get('LastEvaluatedKey', None)
            done = start_key is None

    def print_summary(self):
        """Print summary of fixes."""
        logger.info("\n" + "=" * 50)
        logger.info("EMERGENCY FIX SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Images scanned: {self.stats['images_scanned']}")
        logger.info(f"Receipts scanned: {self.stats['receipts_scanned']}")
        
        if not self.dry_run:
            logger.info(f"Images fixed: {self.stats['images_fixed']}")
            logger.info(f"Receipts fixed: {self.stats['receipts_fixed']}")
            logger.info(f"Errors: {self.stats['errors']}")
        else:
            logger.info(f"Images needing TYPE: {self.stats['images_fixed']}")
            logger.info(f"Receipts needing TYPE: {self.stats['receipts_fixed']}")
            logger.info("\nThis was a DRY RUN - no changes were made")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Emergency fix for entities missing TYPE field"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack to use (dev or prod)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually fix the entities (default is dry run)",
    )
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    # Set up the stack
    stack_name = f"tnorlund/portfolio/{args.stack}"
    work_dir = os.path.join(parent_dir, "infra")
    
    logger.info(f"Using stack: {stack_name}")
    
    # Create a stack reference to get outputs
    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )
    
    # Get the outputs
    outputs = stack.outputs()
    
    # Extract configuration
    dynamo_table_name = outputs["dynamodb_table_name"].value
    
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"Mode: {'LIVE UPDATE' if args.no_dry_run else 'DRY RUN'}")
    
    # Create and run fixer
    fixer = EmergencyFixer(
        dynamo_table_name=dynamo_table_name,
        dry_run=not args.no_dry_run,
    )
    
    fixer.scan_and_fix_all()


if __name__ == "__main__":
    main()