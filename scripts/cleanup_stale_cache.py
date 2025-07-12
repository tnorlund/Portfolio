#!/usr/bin/env python3
"""Cleanup script to remove stale label count cache entries from DynamoDB."""

import os
import time
import boto3
from boto3.dynamodb.types import TypeDeserializer


def main():
    """Remove stale cache entries."""
    # Get table name from environment or use default
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    print(f"Cleaning up stale cache entries in table: {table_name}")
    
    # Create DynamoDB client
    client = boto3.client('dynamodb')
    deserializer = TypeDeserializer()
    
    # Query for LABEL_CACHE entries
    print("Querying for all LABEL_COUNT_CACHE entries...")
    response = client.query(
        TableName=table_name,
        IndexName='GSITYPE',
        KeyConditionExpression='#t = :type_val',
        ExpressionAttributeNames={
            '#t': 'TYPE'
        },
        ExpressionAttributeValues={
            ':type_val': {'S': 'LABEL_COUNT_CACHE'}
        }
    )
    
    items = response.get('Items', [])
    print(f"Found {len(items)} cache entries")
    
    # Get user confirmation (check for --force flag)
    import sys
    if items:
        print("\nThis will delete ALL existing cache entries.")
        print("The cache updater Lambda will repopulate them with correct TTL values.")
        
        if '--force' not in sys.argv:
            print("\nTo proceed, run with --force flag:")
            print(f"  python {sys.argv[0]} --force")
            return
    
    # Delete all cache entries
    deleted_count = 0
    for item in items:
        try:
            # Extract PK and SK
            pk = item['PK']['S']
            sk = item['SK']['S']
            
            # Delete the item
            client.delete_item(
                TableName=table_name,
                Key={
                    'PK': {'S': pk},
                    'SK': {'S': sk}
                }
            )
            
            deleted_count += 1
            print(f"Deleted: {sk}")
            
        except Exception as e:
            print(f"Error deleting item {sk}: {e}")
    
    print(f"\nSuccessfully deleted {deleted_count} stale cache entries.")
    print("The cache updater Lambda will repopulate the cache within 5 minutes.")
    print("\nNext steps:")
    print("1. Wait ~5 minutes for the cache updater Lambda to run")
    print("2. Run debug_label_cache_simple.py again to verify new entries use 'TimeToLive'")
    print("3. Test the API to confirm it's using cached values (should be fast)")


if __name__ == "__main__":
    main()