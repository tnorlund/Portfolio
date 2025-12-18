#!/usr/bin/env python3
"""
Script to batch update records missing TYPE field.
Reads from the JSON file created by find_missing_type_records.py
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def update_record_batch(table, records: List[Dict], dry_run: bool = False):
    """Update a batch of records with TYPE field."""
    success_count = 0
    error_count = 0

    for record in records:
        if dry_run:
            logger.debug(
                f"[DRY RUN] Would add TYPE={record['TYPE']} to {record['PK']}, {record['SK']}"
            )
            success_count += 1
            continue

        try:
            table.update_item(
                Key={"PK": record["PK"], "SK": record["SK"]},
                UpdateExpression="SET #type = :type",
                ExpressionAttributeNames={"#type": "TYPE"},
                ExpressionAttributeValues={":type": record["TYPE"]},
                ConditionExpression="attribute_not_exists(#type)",
            )
            success_count += 1
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Record already has TYPE, skip
                success_count += 1
            else:
                logger.error(f"Failed to update {record['PK']}, {record['SK']}: {e}")
                error_count += 1
        except Exception as e:
            logger.error(f"Failed to update {record['PK']}, {record['SK']}: {e}")
            error_count += 1

    return success_count, error_count


def batch_update_records(
    table_name: str,
    records: List[Dict],
    dry_run: bool = False,
    batch_size: int = 25,
    max_workers: int = 10,
):
    """Update records in parallel batches."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    total_success = 0
    total_errors = 0

    # Split records into batches
    batches = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]

    logger.info(f"Processing {len(records)} records in {len(batches)} batches...")

    # Process batches in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(update_record_batch, table, batch, dry_run): i
            for i, batch in enumerate(batches)
        }

        completed = 0
        for future in as_completed(futures):
            batch_num = futures[future]
            try:
                success, errors = future.result()
                total_success += success
                total_errors += errors
                completed += 1

                if completed % 10 == 0:
                    logger.info(
                        f"Progress: {completed}/{len(batches)} batches completed"
                    )

            except Exception as e:
                logger.error(f"Batch {batch_num} failed: {e}")

    return total_success, total_errors


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Batch update records missing TYPE field"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack to use (dev or prod)",
    )
    parser.add_argument(
        "--input-file",
        help="JSON file with missing records (default: missing_type_records_<table>.json)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually update the records (default is dry run)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="Number of records per batch (default: 25)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Maximum parallel workers (default: 10)",
    )

    args = parser.parse_args()

    # Get configuration from Pulumi
    script_dir = os.path.dirname(os.path.abspath(__file__))
    portfolio_root = os.path.dirname(script_dir)
    parent_dir = os.path.dirname(portfolio_root)

    sys.path.insert(0, parent_dir)
    sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

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

    # Load records from file
    input_file = args.input_file or f"missing_type_records_{dynamo_table_name}.json"

    try:
        with open(input_file, "r") as f:
            records = json.load(f)
        logger.info(f"Loaded {len(records)} records from {input_file}")
    except FileNotFoundError:
        logger.error(f"Input file not found: {input_file}")
        logger.error("Please run find_missing_type_records.py first")
        sys.exit(1)

    # Update records
    logger.info(f"Mode: {'LIVE UPDATE' if args.no_dry_run else 'DRY RUN'}")

    success_count, error_count = batch_update_records(
        dynamo_table_name,
        records,
        dry_run=not args.no_dry_run,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
    )

    # Print summary
    logger.info("\n" + "=" * 50)
    logger.info("BATCH UPDATE SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Total records processed: {len(records)}")
    logger.info(f"Successfully updated: {success_count}")
    logger.info(f"Errors: {error_count}")

    if not args.no_dry_run:
        logger.info("\nThis was a DRY RUN - no changes were made")
        logger.info("Run with --no-dry-run to apply changes")


if __name__ == "__main__":
    main()
