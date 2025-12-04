#!/usr/bin/env python3
"""Detailed comparison of CDN fields between DEV and PROD."""

import boto3
from typing import Dict, List, Optional
import json

def get_table_client(table_name: str):
    """Get DynamoDB table client."""
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    return dynamodb.Table(table_name)

def scan_table_sample(table: any, limit: int = 20) -> List[Dict]:
    """Scan table for a sample of items."""
    try:
        response = table.scan(Limit=limit)
        return response.get('Items', [])
    except Exception as e:
        print(f"Error scanning table: {e}")
        return []

def check_subdirectory_pattern(table: any) -> Dict:
    """Check if any entities use subdirectory pattern."""
    try:
        # Scan for items with subdirectory pattern
        response = table.scan(
            FilterExpression='contains(cdn_s3_key, :pattern)',
            ExpressionAttributeValues={
                ':pattern': '/1_'
            },
            Limit=10
        )
        items = response.get('Items', [])
        
        result = {
            'found': len(items) > 0,
            'count': len(items),
            'examples': []
        }
        
        for item in items[:3]:  # First 3 examples
            result['examples'].append({
                'id': item.get('PK', '').replace('IMAGE#', '').replace('RECEIPT#', ''),
                'type': item.get('TYPE'),
                'cdn_s3_key': item.get('cdn_s3_key'),
                'cdn_thumbnail_s3_key': item.get('cdn_thumbnail_s3_key')
            })
        
        return result
    except Exception as e:
        print(f"Error checking subdirectory pattern: {e}")
        return {'found': False, 'count': 0, 'examples': []}

def analyze_missing_cdn_fields(table: any, entity_type: str) -> Dict:
    """Find entities missing CDN fields."""
    try:
        # Query for entities without cdn_s3_key
        response = table.query(
            IndexName='GSITYPE',
            KeyConditionExpression='#type = :type',
            FilterExpression='attribute_not_exists(cdn_s3_key)',
            ExpressionAttributeNames={
                '#type': 'TYPE'
            },
            ExpressionAttributeValues={
                ':type': entity_type
            },
            Limit=10
        )
        
        items = response.get('Items', [])
        return {
            'count': len(items),
            'examples': [item.get('PK', '').replace(f'{entity_type}#', '') for item in items[:5]]
        }
    except Exception as e:
        print(f"Error finding missing CDN fields: {e}")
        return {'count': 0, 'examples': []}

def main():
    # Table names
    dev_table_name = 'ReceiptsTable-dc5be22'
    prod_table_name = 'ReceiptsTable-d7ff76a'
    
    dev_table = get_table_client(dev_table_name)
    prod_table = get_table_client(prod_table_name)
    
    print("=" * 80)
    print("DETAILED CDN FIELD ANALYSIS")
    print("=" * 80)
    print(f"DEV Table:  {dev_table_name}")
    print(f"PROD Table: {prod_table_name}\n")
    
    # Check for subdirectory pattern
    print("CHECKING FOR SUBDIRECTORY PATTERN (assets/{id}/1_size.ext)")
    print("-" * 80)
    
    print("\nDEV Table:")
    dev_subdir = check_subdirectory_pattern(dev_table)
    if dev_subdir['found']:
        print(f"  ✓ Found {dev_subdir['count']} entities with subdirectory pattern")
        for ex in dev_subdir['examples']:
            print(f"    - {ex['type']} {ex['id']}: {ex['cdn_s3_key']}")
    else:
        print("  ✗ No entities found with subdirectory pattern")
    
    print("\nPROD Table:")
    prod_subdir = check_subdirectory_pattern(prod_table)
    if prod_subdir['found']:
        print(f"  ✓ Found {prod_subdir['count']} entities with subdirectory pattern")
        for ex in prod_subdir['examples']:
            print(f"    - {ex['type']} {ex['id']}: {ex['cdn_s3_key']}")
    else:
        print("  ✗ No entities found with subdirectory pattern")
    
    # Check for missing CDN fields
    print("\n\nCHECKING FOR MISSING CDN FIELDS")
    print("-" * 80)
    
    print("\nDEV Table - Missing CDN fields:")
    dev_missing_images = analyze_missing_cdn_fields(dev_table, 'IMAGE')
    dev_missing_receipts = analyze_missing_cdn_fields(dev_table, 'RECEIPT')
    print(f"  Images without cdn_s3_key: {dev_missing_images['count']}")
    if dev_missing_images['examples']:
        print(f"    Examples: {', '.join(dev_missing_images['examples'][:3])}")
    print(f"  Receipts without cdn_s3_key: {dev_missing_receipts['count']}")
    if dev_missing_receipts['examples']:
        print(f"    Examples: {', '.join(dev_missing_receipts['examples'][:3])}")
    
    print("\nPROD Table - Missing CDN fields:")
    prod_missing_images = analyze_missing_cdn_fields(prod_table, 'IMAGE')
    prod_missing_receipts = analyze_missing_cdn_fields(prod_table, 'RECEIPT')
    print(f"  Images without cdn_s3_key: {prod_missing_images['count']}")
    if prod_missing_images['examples']:
        print(f"    Examples: {', '.join(prod_missing_images['examples'][:3])}")
    print(f"  Receipts without cdn_s3_key: {prod_missing_receipts['count']}")
    if prod_missing_receipts['examples']:
        print(f"    Examples: {', '.join(prod_missing_receipts['examples'][:3])}")
    
    # Sample comparison
    print("\n\nSAMPLE COMPARISON")
    print("-" * 80)
    
    print("\nSampling 5 items from each table...")
    dev_sample = scan_table_sample(dev_table, 5)
    prod_sample = scan_table_sample(prod_table, 5)
    
    print("\nDEV Sample:")
    for item in dev_sample:
        if item.get('TYPE') in ['IMAGE', 'RECEIPT']:
            entity_id = item.get('PK', '').replace('IMAGE#', '').replace('RECEIPT#', '')
            print(f"  {item.get('TYPE')} {entity_id[:8]}...")
            print(f"    cdn_s3_key: {item.get('cdn_s3_key', 'NOT SET')}")
            print(f"    s3_key: {item.get('s3_key', 'NOT SET')}")
    
    print("\nPROD Sample:")
    for item in prod_sample:
        if item.get('TYPE') in ['IMAGE', 'RECEIPT']:
            entity_id = item.get('PK', '').replace('IMAGE#', '').replace('RECEIPT#', '')
            print(f"  {item.get('TYPE')} {entity_id[:8]}...")
            print(f"    cdn_s3_key: {item.get('cdn_s3_key', 'NOT SET')}")
            print(f"    s3_key: {item.get('s3_key', 'NOT SET')}")
    
    # Final recommendations
    print("\n\nFINAL ANALYSIS")
    print("=" * 80)
    
    if prod_subdir['found'] and not dev_subdir['found']:
        print("⚠️  PROD uses subdirectory pattern but DEV uses flat pattern!")
        print("   CDN values CANNOT be directly copied from DEV to PROD.")
    elif prod_missing_images['count'] > 0 or prod_missing_receipts['count'] > 0:
        print("✓ PROD has entities missing CDN fields that might benefit from DEV data.")
        print("   However, verify that the entity IDs match between environments.")
    else:
        print("ℹ️  Both DEV and PROD appear to have CDN fields populated.")
        print("   Check if specific entities need updating by comparing individual IDs.")

if __name__ == "__main__":
    main()