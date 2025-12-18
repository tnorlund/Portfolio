#!/usr/bin/env python3
"""
Update PROD entities with correct CDN field values based on existing S3 structure.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List, Optional
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


def get_entity_id(entity: Dict[str, Any]) -> str:
    """Extract entity ID from PK."""
    pk = entity["PK"]
    if pk.startswith("IMAGE#"):
        return pk.replace("IMAGE#", "")
    elif pk.startswith("RECEIPT#"):
        return pk.replace("RECEIPT#", "")
    else:
        raise ValueError(f"Unknown entity type for PK: {pk}")


def generate_cdn_fields(entity_id: str, s3_client, cdn_bucket: str) -> Dict[str, str]:
    """Generate CDN field mappings for an entity, verifying files exist in S3."""
    cdn_fields = {}

    # Define all possible CDN files using flat pattern (consistent with DEV)
    cdn_files = {
        "cdn_thumbnail_s3_key": f"assets/{entity_id}_thumbnail.jpg",
        "cdn_thumbnail_webp_s3_key": f"assets/{entity_id}_thumbnail.webp",
        "cdn_thumbnail_avif_s3_key": f"assets/{entity_id}_thumbnail.avif",
        "cdn_small_s3_key": f"assets/{entity_id}_small.jpg",
        "cdn_small_webp_s3_key": f"assets/{entity_id}_small.webp",
        "cdn_small_avif_s3_key": f"assets/{entity_id}_small.avif",
        "cdn_medium_s3_key": f"assets/{entity_id}_medium.jpg",
        "cdn_medium_webp_s3_key": f"assets/{entity_id}_medium.webp",
        "cdn_medium_avif_s3_key": f"assets/{entity_id}_medium.avif",
    }

    # Check which files actually exist in S3
    found_files = 0
    for field_name, s3_key in cdn_files.items():
        try:
            s3_client.head_object(Bucket=cdn_bucket, Key=s3_key)
            cdn_fields[field_name] = s3_key
            found_files += 1
        except s3_client.exceptions.ClientError:
            # File doesn't exist, leave field empty/null
            cdn_fields[field_name] = None

    logger.info(f"  Entity {entity_id}: {found_files}/9 CDN files found")
    return cdn_fields, found_files


def update_prod_cdn_fields(table_name: str, cdn_bucket: str, dry_run: bool = True):
    """Update PROD entities with correct CDN field values."""
    dynamodb = boto3.resource("dynamodb")
    s3_client = boto3.client("s3")
    table = dynamodb.Table(table_name)

    logger.info(
        f"{'[DRY RUN] ' if dry_run else ''}Updating CDN fields for PROD entities..."
    )
    logger.info(f"Table: {table_name}")
    logger.info(f"CDN Bucket: {cdn_bucket}")

    stats = {
        "entities_processed": 0,
        "entities_updated": 0,
        "total_cdn_files_found": 0,
        "entities_with_no_cdn": 0,
        "update_errors": 0,
    }

    # Process Image entities
    logger.info("\nProcessing IMAGE entities...")
    image_scan_kwargs = {
        "FilterExpression": "#type = :image_type",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {":image_type": "IMAGE"},
    }

    done = False
    start_key = None

    while not done:
        if start_key:
            image_scan_kwargs["ExclusiveStartKey"] = start_key

        response = table.scan(**image_scan_kwargs)
        items = response.get("Items", [])

        for entity in items:
            stats["entities_processed"] += 1
            entity_id = get_entity_id(entity)

            logger.info(f"Processing Image: {entity_id}")

            # Generate CDN fields based on existing S3 files
            cdn_fields, found_count = generate_cdn_fields(
                entity_id, s3_client, cdn_bucket
            )
            stats["total_cdn_files_found"] += found_count

            if found_count == 0:
                stats["entities_with_no_cdn"] += 1
                logger.warning(f"  No CDN files found for {entity_id}")
                continue

            # Update the entity with CDN fields
            try:
                if not dry_run:
                    # Build update expression
                    update_expression = "SET "
                    expression_attribute_values = {}
                    expression_attribute_names = {}

                    for field_name, field_value in cdn_fields.items():
                        if field_value is not None:
                            update_expression += f"#{field_name} = :{field_name}, "
                            expression_attribute_names[f"#{field_name}"] = field_name
                            expression_attribute_values[f":{field_name}"] = field_value

                    # Remove trailing comma and space
                    update_expression = update_expression.rstrip(", ")

                    if expression_attribute_values:  # Only update if we have values
                        table.update_item(
                            Key={"PK": entity["PK"], "SK": entity["SK"]},
                            UpdateExpression=update_expression,
                            ExpressionAttributeNames=expression_attribute_names,
                            ExpressionAttributeValues=expression_attribute_values,
                        )

                stats["entities_updated"] += 1
                logger.info(
                    f"  ✅ {'[DRY RUN] ' if dry_run else ''}Updated {entity_id} with {found_count} CDN fields"
                )

            except Exception as e:
                stats["update_errors"] += 1
                logger.error(f"  ❌ Failed to update {entity_id}: {e}")

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

    # Process Receipt entities
    logger.info("\nProcessing RECEIPT entities...")
    receipt_scan_kwargs = {
        "FilterExpression": "#type = :receipt_type",
        "ExpressionAttributeNames": {"#type": "TYPE"},
        "ExpressionAttributeValues": {":receipt_type": "RECEIPT"},
    }

    done = False
    start_key = None

    while not done:
        if start_key:
            receipt_scan_kwargs["ExclusiveStartKey"] = start_key

        response = table.scan(**receipt_scan_kwargs)
        items = response.get("Items", [])

        for entity in items:
            stats["entities_processed"] += 1
            entity_id = get_entity_id(entity)

            logger.info(f"Processing Receipt: {entity_id}")

            # Generate CDN fields based on existing S3 files
            cdn_fields, found_count = generate_cdn_fields(
                entity_id, s3_client, cdn_bucket
            )
            stats["total_cdn_files_found"] += found_count

            if found_count == 0:
                stats["entities_with_no_cdn"] += 1
                logger.warning(f"  No CDN files found for {entity_id}")
                continue

            # Update the entity with CDN fields
            try:
                if not dry_run:
                    # Build update expression
                    update_expression = "SET "
                    expression_attribute_values = {}
                    expression_attribute_names = {}

                    for field_name, field_value in cdn_fields.items():
                        if field_value is not None:
                            update_expression += f"#{field_name} = :{field_name}, "
                            expression_attribute_names[f"#{field_name}"] = field_name
                            expression_attribute_values[f":{field_name}"] = field_value

                    # Remove trailing comma and space
                    update_expression = update_expression.rstrip(", ")

                    if expression_attribute_values:  # Only update if we have values
                        table.update_item(
                            Key={"PK": entity["PK"], "SK": entity["SK"]},
                            UpdateExpression=update_expression,
                            ExpressionAttributeNames=expression_attribute_names,
                            ExpressionAttributeValues=expression_attribute_values,
                        )

                stats["entities_updated"] += 1
                logger.info(
                    f"  ✅ {'[DRY RUN] ' if dry_run else ''}Updated {entity_id} with {found_count} CDN fields"
                )

            except Exception as e:
                stats["update_errors"] += 1
                logger.error(f"  ❌ Failed to update {entity_id}: {e}")

        start_key = response.get("LastEvaluatedKey", None)
        done = start_key is None

    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info(f"{'[DRY RUN] ' if dry_run else ''}CDN FIELD UPDATE SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Entities processed: {stats['entities_processed']}")
    logger.info(f"Entities updated: {stats['entities_updated']}")
    logger.info(f"Entities with no CDN files: {stats['entities_with_no_cdn']}")
    logger.info(f"Total CDN files found: {stats['total_cdn_files_found']}")
    logger.info(f"Update errors: {stats['update_errors']}")

    if dry_run:
        logger.info("\n🔍 This was a DRY RUN - no entities were actually updated")
        logger.info("Run with --no-dry-run to actually update CDN fields")
    else:
        if stats["entities_updated"] > 0:
            logger.info(
                f"\n✅ Successfully updated {stats['entities_updated']} entities with CDN fields"
            )
        if stats["update_errors"] > 0:
            logger.info(f"\n⚠️  {stats['update_errors']} entities had update errors")

    return stats


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Update PROD entities with correct CDN field values"
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

    # Update CDN fields
    update_prod_cdn_fields(prod_table, cdn_bucket, dry_run=not args.no_dry_run)


if __name__ == "__main__":
    main()
