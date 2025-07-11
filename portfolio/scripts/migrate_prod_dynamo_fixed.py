#!/usr/bin/env python3
"""
Migrate production DynamoDB records to add multi-size image fields.
This script updates existing Image and Receipt entities with new CDN fields.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, Optional
import boto3
from decimal import Decimal

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.image import Image
from receipt_dynamo.entities.receipt import Receipt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def generate_cdn_keys(base_key: str, entity_type: str = "image") -> Dict[str, str]:
    """Generate all CDN keys based on the base key pattern."""
    
    # Extract base name without extension
    if base_key.endswith('.jpg'):
        base_name = base_key[:-4]
    else:
        logger.warning(f"Unexpected key format: {base_key}")
        return {}
    
    keys = {
        # Thumbnail sizes
        'cdn_thumbnail_s3_key': f"{base_name}_thumbnail.jpg",
        'cdn_thumbnail_webp_s3_key': f"{base_name}_thumbnail.webp",
        'cdn_thumbnail_avif_s3_key': f"{base_name}_thumbnail.avif",
        
        # Small sizes
        'cdn_small_s3_key': f"{base_name}_small.jpg",
        'cdn_small_webp_s3_key': f"{base_name}_small.webp",
        'cdn_small_avif_s3_key': f"{base_name}_small.avif",
        
        # Medium sizes
        'cdn_medium_s3_key': f"{base_name}_medium.jpg",
        'cdn_medium_webp_s3_key': f"{base_name}_medium.webp",
        'cdn_medium_avif_s3_key': f"{base_name}_medium.avif",
        
        # Full size alternate formats
        'cdn_webp_s3_key': f"{base_name}.webp",
        'cdn_avif_s3_key': f"{base_name}.avif",
    }
    
    return keys


def check_s3_keys_exist(s3_client, bucket: str, keys: Dict[str, str]) -> Dict[str, bool]:
    """Check which S3 keys actually exist."""
    exists = {}
    
    for field, key in keys.items():
        try:
            s3_client.head_object(Bucket=bucket, Key=key)
            exists[field] = True
        except s3_client.exceptions.ClientError:
            exists[field] = False
    
    return exists


def migrate_image(image: Image, s3_client, cdn_bucket: str, dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """Generate update expression for an image if files exist in S3."""
    
    # Skip if already has multi-size fields
    if image.cdn_thumbnail_s3_key:
        logger.debug(f"Image {image.image_id} already migrated, skipping")
        return None
    
    # Generate expected keys
    new_keys = generate_cdn_keys(image.cdn_s3_key, "image")
    
    # Check which files actually exist
    exists = check_s3_keys_exist(s3_client, cdn_bucket, new_keys)
    
    # Only include fields for files that exist
    update_fields = {k: v for k, v in new_keys.items() if exists.get(k, False)}
    
    if not update_fields:
        logger.warning(f"No multi-size files found for image {image.image_id}")
        return None
    
    if dry_run:
        logger.info(f"[DRY RUN] Would update image {image.image_id} with {len(update_fields)} fields")
        return None
    
    # Build DynamoDB update
    update_expr_parts = []
    expr_attr_values = {}
    
    for i, (field, value) in enumerate(update_fields.items()):
        placeholder = f":val{i}"
        update_expr_parts.append(f"{field} = {placeholder}")
        expr_attr_values[placeholder] = value
    
    update_item = {
        'Key': {
            'PK': f"IMAGE#{image.image_id}",
            'SK': f"IMAGE#{image.image_id}"
        },
        'UpdateExpression': f"SET {', '.join(update_expr_parts)}",
        'ExpressionAttributeValues': expr_attr_values
    }
    
    logger.info(f"Updating image {image.image_id} with {len(update_fields)} fields")
    return update_item


def migrate_receipt(receipt: Receipt, s3_client, cdn_bucket: str, dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """Generate update expression for a receipt if files exist in S3."""
    
    # Skip if already has multi-size fields
    if receipt.cdn_thumbnail_s3_key:
        logger.debug(f"Receipt {receipt.receipt_id} already migrated, skipping")
        return None
    
    # Generate expected keys
    new_keys = generate_cdn_keys(receipt.cdn_s3_key, "receipt")
    
    # Check which files actually exist
    exists = check_s3_keys_exist(s3_client, cdn_bucket, new_keys)
    
    # Only include fields for files that exist
    update_fields = {k: v for k, v in new_keys.items() if exists.get(k, False)}
    
    if not update_fields:
        logger.warning(f"No multi-size files found for receipt {receipt.receipt_id}")
        return None
    
    if dry_run:
        logger.info(f"[DRY RUN] Would update receipt {receipt.receipt_id} with {len(update_fields)} fields")
        return None
    
    # Build DynamoDB update
    update_expr_parts = []
    expr_attr_values = {}
    
    for i, (field, value) in enumerate(update_fields.items()):
        placeholder = f":val{i}"
        update_expr_parts.append(f"{field} = {placeholder}")
        expr_attr_values[placeholder] = value
    
    update_item = {
        'Key': {
            'PK': f"IMAGE#{receipt.image_id}",
            'SK': f"RECEIPT#{receipt.receipt_id}"
        },
        'UpdateExpression': f"SET {', '.join(update_expr_parts)}",
        'ExpressionAttributeValues': expr_attr_values
    }
    
    logger.info(f"Updating receipt {receipt.receipt_id} with {len(update_fields)} fields")
    return update_item


def process_batch_updates(table, updates):
    """Process a batch of updates."""
    for update in updates:
        table.update_item(**update)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Migrate production DynamoDB records")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")
    parser.add_argument("--limit", type=int, help="Limit number of records to process")
    parser.add_argument("--entity-type", choices=["image", "receipt", "both"], default="both", help="Entity type to migrate")
    
    args = parser.parse_args()
    
    # Get production configuration from Pulumi
    from pulumi import automation as auto
    
    stack_name = "tnorlund/portfolio/prod"
    work_dir = os.path.join(os.path.dirname(os.path.dirname(script_dir)), "infra")
    
    logger.info(f"Loading Pulumi stack: {stack_name}")
    stack = auto.create_or_select_stack(stack_name=stack_name, work_dir=work_dir)
    outputs = stack.outputs()
    
    dynamo_table_name = outputs["dynamodb_table_name"].value
    cdn_bucket_name = outputs["cdn_bucket_name"].value
    
    logger.info(f"DynamoDB table: {dynamo_table_name}")
    logger.info(f"CDN bucket: {cdn_bucket_name}")
    logger.info(f"Dry run: {args.dry_run}")
    
    # Initialize clients
    dynamo_client = DynamoClient(dynamo_table_name)
    s3_client = boto3.client('s3')
    dynamo_resource = boto3.resource('dynamodb')
    table = dynamo_resource.Table(dynamo_table_name)
    
    updates_pending = []
    processed_count = 0
    
    # Process Images
    if args.entity_type in ["image", "both"]:
        logger.info("\n📸 Processing Images...")
        last_evaluated_key = None
        
        while True:
            images, last_key = dynamo_client.list_images(
                limit=100,
                last_evaluated_key=last_evaluated_key,
            )
            
            for image in images:
                if args.limit and processed_count >= args.limit:
                    break
                
                update = migrate_image(image, s3_client, cdn_bucket_name, args.dry_run)
                if update:
                    updates_pending.append(update)
                    processed_count += 1
                
                # Process updates every 25 items
                if len(updates_pending) >= 25 and not args.dry_run:
                    logger.info(f"Writing batch of {len(updates_pending)} updates...")
                    process_batch_updates(table, updates_pending)
                    updates_pending = []
            
            if not last_key or (args.limit and processed_count >= args.limit):
                break
            
            last_evaluated_key = last_key
    
    # Process Receipts
    if args.entity_type in ["receipt", "both"]:
        logger.info("\n🧾 Processing Receipts...")
        last_evaluated_key = None
        
        while True:
            receipts, last_key = dynamo_client.list_receipts(
                limit=100,
                last_evaluated_key=last_evaluated_key,
            )
            
            for receipt in receipts:
                if args.limit and processed_count >= args.limit:
                    break
                
                update = migrate_receipt(receipt, s3_client, cdn_bucket_name, args.dry_run)
                if update:
                    updates_pending.append(update)
                    processed_count += 1
                
                # Process updates every 25 items
                if len(updates_pending) >= 25 and not args.dry_run:
                    logger.info(f"Writing batch of {len(updates_pending)} updates...")
                    process_batch_updates(table, updates_pending)
                    updates_pending = []
            
            if not last_key or (args.limit and processed_count >= args.limit):
                break
            
            last_evaluated_key = last_key
    
    # Write any remaining updates
    if updates_pending and not args.dry_run:
        logger.info(f"Writing final batch of {len(updates_pending)} updates...")
        process_batch_updates(table, updates_pending)
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"Migration {'(DRY RUN) ' if args.dry_run else ''}Complete!")
    logger.info(f"Processed {processed_count} entities")
    
    if args.dry_run:
        logger.info("\nRun without --dry-run to apply changes")


if __name__ == "__main__":
    main()