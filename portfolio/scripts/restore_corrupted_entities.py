#!/usr/bin/env python3
"""
Script to restore corrupted entities using Point-in-Time Recovery.

This script:
1. Creates a temporary table from a PITR restore
2. Extracts the corrupted entities from the restored table
3. Copies them to the current PROD table
4. Cleans up the temporary table

Usage:
    python restore_corrupted_entities.py --restore-time "2025-07-01T12:00:00"
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

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


class PITRRestorer:
    """Handles Point-in-Time Recovery for corrupted entities."""

    def __init__(self, source_table: str, restore_time: str, dry_run: bool = True):
        self.source_table = source_table
        self.restore_time = restore_time
        self.dry_run = dry_run
        
        # Generate unique name for restore table
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.restore_table_name = f"{source_table}-pitr-restore-{timestamp}"
        
        self.dynamodb = boto3.client('dynamodb')
        self.dynamodb_resource = boto3.resource('dynamodb')
        
        self.stats = {
            "corrupted_entities_found": 0,
            "entities_restored": 0,
            "entities_copied": 0,
            "errors": 0,
        }
        
        # List of corrupted entity keys (we'll need to identify these)
        self.corrupted_entity_keys = []

    def parse_restore_time(self) -> datetime:
        """Parse restore time string to datetime object."""
        try:
            # Try ISO format first
            return datetime.fromisoformat(self.restore_time.replace('Z', '+00:00'))
        except ValueError:
            try:
                # Try common format
                return datetime.strptime(self.restore_time, "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                raise ValueError(f"Invalid time format: {self.restore_time}. Use ISO format like '2025-07-01T12:00:00'")

    def identify_corrupted_entities(self):
        """Identify corrupted entities in the current PROD table."""
        logger.info("Identifying corrupted entities in current PROD table...")
        
        source_table = self.dynamodb_resource.Table(self.source_table)
        
        # Scan for entities with TYPE but missing other required fields
        scan_kwargs = {
            'FilterExpression': 'attribute_exists(#type)',
            'ExpressionAttributeNames': {'#type': 'TYPE'}
        }
        
        corrupted_count = 0
        done = False
        start_key = None
        
        while not done:
            if start_key:
                scan_kwargs['ExclusiveStartKey'] = start_key
                
            response = source_table.scan(**scan_kwargs)
            items = response.get('Items', [])
            
            for item in items:
                pk = item.get('PK', '')
                sk = item.get('SK', '')
                entity_type = item.get('TYPE', '')
                
                # Check if this entity is corrupted
                is_corrupted = False
                
                if entity_type == 'IMAGE' and pk.startswith('IMAGE#') and sk.startswith('IMAGE#'):
                    # Check for missing required Image fields
                    required_fields = ['width', 'height', 'timestamp_added', 'raw_s3_bucket', 'raw_s3_key', 'image_type']
                    for field in required_fields:
                        if field not in item:
                            is_corrupted = True
                            break
                            
                elif entity_type == 'RECEIPT' and pk.startswith('IMAGE#') and sk.startswith('RECEIPT#'):
                    # Check for missing required Receipt fields
                    required_fields = ['width', 'height', 'timestamp_added', 'raw_s3_bucket', 'raw_s3_key', 
                                      'top_left', 'top_right', 'bottom_left', 'bottom_right']
                    for field in required_fields:
                        if field not in item:
                            is_corrupted = True
                            break
                
                if is_corrupted:
                    self.corrupted_entity_keys.append({'PK': pk, 'SK': sk, 'TYPE': entity_type})
                    corrupted_count += 1
                    logger.debug(f"Found corrupted {entity_type}: {pk}, {sk}")
            
            start_key = response.get('LastEvaluatedKey', None)
            done = start_key is None
            
            # Progress update
            logger.info(f"Scanned entities, found {corrupted_count} corrupted so far...")
        
        self.stats["corrupted_entities_found"] = corrupted_count
        logger.info(f"Identified {corrupted_count} corrupted entities to restore")
        
        return corrupted_count > 0

    def create_pitr_restore(self) -> bool:
        """Create a Point-in-Time Recovery restore table."""
        restore_datetime = self.parse_restore_time()
        
        logger.info(f"Creating PITR restore of {self.source_table} to time: {restore_datetime}")
        logger.info(f"Restore table name: {self.restore_table_name}")
        
        if self.dry_run:
            logger.info("[DRY RUN] Would create PITR restore table")
            return True
        
        try:
            response = self.dynamodb.restore_table_to_point_in_time(
                SourceTableName=self.source_table,
                TargetTableName=self.restore_table_name,
                RestoreDateTime=restore_datetime,
                UseLatestRestorableTime=False
            )
            
            logger.info("PITR restore initiated. Waiting for table to become active...")
            
            # Wait for table to become active
            waiter = self.dynamodb.get_waiter('table_exists')
            waiter.wait(
                TableName=self.restore_table_name,
                WaiterConfig={'Delay': 30, 'MaxAttempts': 60}  # Wait up to 30 minutes
            )
            
            logger.info("Restore table is now active")
            return True
            
        except ClientError as e:
            logger.error(f"Failed to create PITR restore: {e}")
            return False

    def extract_restored_entities(self) -> List[Dict[str, Any]]:
        """Extract the corrupted entities from the restored table."""
        if self.dry_run:
            logger.info("[DRY RUN] Would extract entities from restore table")
            # For dry run, let's at least check what we would find
            logger.info(f"[DRY RUN] Would check {len(self.corrupted_entity_keys)} entities in restore table")
            self.stats["entities_restored"] = len(self.corrupted_entity_keys)
            return []
        
        logger.info("Extracting corrupted entities from restore table...")
        
        restore_table = self.dynamodb_resource.Table(self.restore_table_name)
        restored_entities = []
        
        for entity_key in self.corrupted_entity_keys:
            try:
                response = restore_table.get_item(Key={
                    'PK': entity_key['PK'], 
                    'SK': entity_key['SK']
                })
                
                if 'Item' in response:
                    restored_item = response['Item']
                    restored_entities.append(restored_item)
                    
                    # Log what we found in the restored entity
                    s3_key = restored_item.get('raw_s3_key', 'NOT_FOUND')
                    s3_bucket = restored_item.get('raw_s3_bucket', 'NOT_FOUND')
                    width = restored_item.get('width', 'NOT_FOUND')
                    height = restored_item.get('height', 'NOT_FOUND')
                    
                    logger.info(f"Restored {entity_key['TYPE']}: {entity_key['PK']}")
                    logger.info(f"  S3: {s3_bucket}/{s3_key}")
                    logger.info(f"  Dimensions: {width}x{height}")
                else:
                    logger.warning(f"Entity not found in restore: {entity_key['PK']}, {entity_key['SK']}")
                    
            except Exception as e:
                logger.error(f"Failed to extract {entity_key['PK']}, {entity_key['SK']}: {e}")
                self.stats["errors"] += 1
        
        self.stats["entities_restored"] = len(restored_entities)
        logger.info(f"Extracted {len(restored_entities)} entities from restore table")
        
        return restored_entities

    def copy_entities_to_prod(self, entities: List[Dict[str, Any]]):
        """Copy the restored entities back to the PROD table."""
        if not entities:
            logger.info("No entities to copy")
            return
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Would copy {len(entities)} entities to PROD")
            self.stats["entities_copied"] = len(entities)
            return
        
        logger.info(f"Copying {len(entities)} entities to PROD table...")
        
        source_table = self.dynamodb_resource.Table(self.source_table)
        
        # Copy entities in batches
        def copy_entity_batch(entity_batch):
            success_count = 0
            for entity in entity_batch:
                try:
                    source_table.put_item(Item=entity)
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to copy entity {entity.get('PK')}, {entity.get('SK')}: {e}")
                    self.stats["errors"] += 1
            return success_count
        
        # Process in parallel batches
        batch_size = 25
        batches = [entities[i:i + batch_size] for i in range(0, len(entities), batch_size)]
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(copy_entity_batch, batch): i 
                for i, batch in enumerate(batches)
            }
            
            total_copied = 0
            for future in as_completed(futures):
                try:
                    copied = future.result()
                    total_copied += copied
                except Exception as e:
                    logger.error(f"Batch copy failed: {e}")
        
        self.stats["entities_copied"] = total_copied
        logger.info(f"Successfully copied {total_copied} entities to PROD")

    def cleanup_restore_table(self):
        """Delete the temporary restore table."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would delete restore table: {self.restore_table_name}")
            return
        
        logger.info(f"Cleaning up restore table: {self.restore_table_name}")
        
        try:
            self.dynamodb.delete_table(TableName=self.restore_table_name)
            logger.info("Restore table cleanup initiated")
        except Exception as e:
            logger.error(f"Failed to cleanup restore table: {e}")

    def run(self):
        """Run the complete restore process."""
        logger.info("Starting Point-in-Time Recovery process...")
        
        # Step 1: Identify corrupted entities
        if not self.identify_corrupted_entities():
            logger.info("No corrupted entities found. Nothing to restore.")
            return
        
        # Step 2: Create PITR restore
        if not self.create_pitr_restore():
            logger.error("Failed to create PITR restore. Aborting.")
            return
        
        try:
            # Step 3: Extract entities from restore
            restored_entities = self.extract_restored_entities()
            
            # Step 4: Copy entities back to PROD
            self.copy_entities_to_prod(restored_entities)
            
        finally:
            # Step 5: Cleanup restore table
            self.cleanup_restore_table()
        
        # Print summary
        self.print_summary()

    def print_summary(self):
        """Print summary of the restore operation."""
        logger.info("\n" + "=" * 50)
        logger.info("PITR RESTORE SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Corrupted entities found: {self.stats['corrupted_entities_found']}")
        logger.info(f"Entities restored from backup: {self.stats['entities_restored']}")
        logger.info(f"Entities copied to PROD: {self.stats['entities_copied']}")
        logger.info(f"Errors: {self.stats['errors']}")
        
        if self.dry_run:
            logger.info("\nThis was a DRY RUN - no changes were made")
            logger.info("Run with --no-dry-run to apply changes")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Restore corrupted entities using Point-in-Time Recovery"
    )
    parser.add_argument(
        "--restore-time",
        required=True,
        help="Time to restore to (ISO format: 2025-07-01T12:00:00)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually perform the restore (default is dry run)",
    )
    
    args = parser.parse_args()
    
    # Get PROD table name from Pulumi
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
    logger.info(f"Restore time: {args.restore_time}")
    logger.info(f"Mode: {'LIVE RESTORE' if args.no_dry_run else 'DRY RUN'}")
    
    # Create and run restorer
    restorer = PITRRestorer(
        source_table=prod_table,
        restore_time=args.restore_time,
        dry_run=not args.no_dry_run,
    )
    
    restorer.run()


if __name__ == "__main__":
    main()