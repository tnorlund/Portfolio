#!/usr/bin/env python3
"""Scan ALL PROD entities to find any subdirectory patterns."""

import boto3
from typing import Dict, List
from collections import defaultdict

def get_table_client(table_name: str):
    """Get DynamoDB table client."""
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    return dynamodb.Table(table_name)

def scan_all_for_patterns(table: any) -> Dict:
    """Scan entire table for CDN patterns."""
    patterns = defaultdict(int)
    subdirectory_examples = []
    total_with_cdn = 0
    total_scanned = 0
    
    try:
        # Scan with projection to reduce data transfer
        response = table.scan(
            ProjectionExpression='PK, SK, #type, cdn_s3_key',
            ExpressionAttributeNames={
                '#type': 'TYPE'
            }
        )
        
        while True:
            items = response.get('Items', [])
            
            for item in items:
                total_scanned += 1
                entity_type = item.get('TYPE')
                
                if entity_type in ['IMAGE', 'RECEIPT']:
                    cdn_key = item.get('cdn_s3_key')
                    if cdn_key:
                        total_with_cdn += 1
                        
                        # Check for subdirectory patterns
                        if '/1_' in cdn_key or '/2_' in cdn_key or '/3_' in cdn_key:
                            patterns['subdirectory'] += 1
                            if len(subdirectory_examples) < 10:
                                subdirectory_examples.append({
                                    'type': entity_type,
                                    'pk': item.get('PK'),
                                    'cdn_key': cdn_key
                                })
                        elif '_thumbnail' in cdn_key or '_RECEIPT_' in cdn_key:
                            patterns['flat'] += 1
                        else:
                            patterns['other'] += 1
            
            # Check if there are more items
            if 'LastEvaluatedKey' in response:
                print(f"  Scanned {total_scanned} items so far...")
                response = table.scan(
                    ProjectionExpression='PK, SK, #type, cdn_s3_key',
                    ExpressionAttributeNames={
                        '#type': 'TYPE'
                    },
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
            else:
                break
                
    except Exception as e:
        print(f"Error scanning table: {e}")
    
    return {
        'total_scanned': total_scanned,
        'total_with_cdn': total_with_cdn,
        'patterns': dict(patterns),
        'subdirectory_examples': subdirectory_examples
    }

def main():
    prod_table_name = 'ReceiptsTable-d7ff76a'
    prod_table = get_table_client(prod_table_name)
    
    print("=" * 80)
    print("COMPREHENSIVE PROD TABLE CDN PATTERN SCAN")
    print("=" * 80)
    print(f"Table: {prod_table_name}")
    print("\nScanning entire table for CDN patterns...")
    
    results = scan_all_for_patterns(prod_table)
    
    print(f"\nScan complete!")
    print(f"  Total items scanned: {results['total_scanned']}")
    print(f"  Items with CDN fields: {results['total_with_cdn']}")
    
    print("\nCDN Pattern Distribution:")
    for pattern, count in results['patterns'].items():
        print(f"  {pattern}: {count}")
    
    if results['subdirectory_examples']:
        print("\nSubdirectory Pattern Examples:")
        for ex in results['subdirectory_examples']:
            print(f"  {ex['type']} {ex['pk']}")
            print(f"    → {ex['cdn_key']}")
    
    print("\n" + "=" * 80)
    print("FINAL VERDICT")
    print("=" * 80)
    
    if results['patterns'].get('subdirectory', 0) > 0:
        print("\n⚠️  CRITICAL: Found subdirectory pattern in PROD!")
        print(f"   {results['patterns']['subdirectory']} entities use subdirectory pattern")
        print("\n   DO NOT copy CDN values from DEV (flat) to PROD (mixed patterns)!")
        print("   This would corrupt the subdirectory entries.")
    else:
        print("\n✓ PROD uses only flat CDN pattern.")
        print("   Safe to consider copying from DEV if needed.")

if __name__ == "__main__":
    main()