#!/usr/bin/env python3
"""
Script to efficiently find all records missing TYPE field in DynamoDB.
Lists them out so we can update them properly.
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict, Any, List

import boto3
from boto3.dynamodb.conditions import Attr

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def find_missing_type_records(table_name: str):
    """Find all records missing TYPE field and save to file."""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    missing_records = []
    total_scanned = 0
    
    logger.info(f"Scanning table {table_name} for records missing TYPE field...")
    
    # Scan with filter for missing TYPE
    scan_kwargs = {
        'FilterExpression': Attr('TYPE').not_exists(),
        'ProjectionExpression': 'PK, SK'  # Only get keys to minimize data transfer
    }
    
    done = False
    start_key = None
    
    while not done:
        if start_key:
            scan_kwargs['ExclusiveStartKey'] = start_key
            
        response = table.scan(**scan_kwargs)
        items = response.get('Items', [])
        total_scanned += response.get('ScannedCount', 0)
        
        for item in items:
            pk = item.get('PK', '')
            sk = item.get('SK', '')
            
            # Determine type from key pattern
            if pk.startswith('IMAGE#') and sk.startswith('IMAGE#'):
                record_type = 'IMAGE'
            elif pk.startswith('IMAGE#') and sk.startswith('RECEIPT#'):
                record_type = 'RECEIPT'
            else:
                record_type = 'UNKNOWN'
            
            missing_records.append({
                'PK': pk,
                'SK': sk,
                'TYPE': record_type
            })
        
        # Progress update
        logger.info(f"Scanned {total_scanned} records, found {len(missing_records)} missing TYPE...")
        
        start_key = response.get('LastEvaluatedKey', None)
        done = start_key is None
    
    # Save results
    output_file = f'missing_type_records_{table_name}.json'
    with open(output_file, 'w') as f:
        json.dump(missing_records, f, indent=2)
    
    # Print summary
    logger.info(f"\nSummary:")
    logger.info(f"Total records scanned: {total_scanned}")
    logger.info(f"Records missing TYPE: {len(missing_records)}")
    
    # Count by type
    type_counts = {}
    for record in missing_records:
        record_type = record['TYPE']
        type_counts[record_type] = type_counts.get(record_type, 0) + 1
    
    for record_type, count in sorted(type_counts.items()):
        logger.info(f"  {record_type}: {count}")
    
    logger.info(f"\nResults saved to: {output_file}")
    
    return missing_records


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Find all records missing TYPE field"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack to use (dev or prod)",
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
    
    # Find missing records
    find_missing_type_records(dynamo_table_name)


if __name__ == "__main__":
    main()