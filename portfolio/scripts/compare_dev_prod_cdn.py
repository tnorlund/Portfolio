#!/usr/bin/env python3
"""Compare CDN fields between DEV and PROD DynamoDB tables."""

import boto3
from typing import Dict, List, Optional, Set
import json
from collections import defaultdict

def get_table_client(table_name: str):
    """Get DynamoDB table client."""
    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    return dynamodb.Table(table_name)

def get_entity_by_id(table: any, entity_type: str, entity_id: str) -> Optional[Dict]:
    """Get a specific entity by ID."""
    try:
        pk = f"{entity_type}#{entity_id}"
        sk = entity_id
        
        response = table.get_item(
            Key={
                'PK': pk,
                'SK': sk
            }
        )
        return response.get('Item')
    except Exception as e:
        print(f"Error getting {entity_type} {entity_id}: {e}")
        return None

def query_entities(table: any, entity_type: str, limit: int = 100) -> List[Dict]:
    """Query entities of a specific type using GSITYPE."""
    try:
        response = table.query(
            IndexName='GSITYPE',
            KeyConditionExpression='#type = :type',
            ExpressionAttributeNames={
                '#type': 'TYPE'
            },
            ExpressionAttributeValues={
                ':type': entity_type
            },
            Limit=limit
        )
        return response.get('Items', [])
    except Exception as e:
        print(f"Error querying {entity_type}: {e}")
        return []

def analyze_cdn_fields(entity: Dict) -> Dict:
    """Analyze CDN fields in an entity."""
    entity_id = entity.get('PK', '').replace('IMAGE#', '').replace('RECEIPT#', '')
    
    cdn_info = {
        'id': entity_id,
        'type': entity.get('TYPE'),
        'cdn_s3_key': entity.get('cdn_s3_key'),
        'cdn_thumbnail_s3_key': entity.get('cdn_thumbnail_s3_key'),
        'cdn_url': entity.get('cdn_url'),
        'cdn_thumbnail_url': entity.get('cdn_thumbnail_url'),
        'thumbnail_s3_key': entity.get('thumbnail_s3_key'),
        's3_key': entity.get('s3_key'),
        'bucket': entity.get('bucket')
    }
    
    # Check pattern
    if cdn_info['cdn_s3_key']:
        if f"assets/{entity_id}/" in cdn_info['cdn_s3_key']:
            cdn_info['pattern'] = 'subdirectory'
        else:
            cdn_info['pattern'] = 'flat'
    else:
        cdn_info['pattern'] = 'none'
    
    return cdn_info

def main():
    # Table names
    dev_table_name = 'ReceiptsTable-dc5be22'
    prod_table_name = 'ReceiptsTable-d7ff76a'
    
    dev_table = get_table_client(dev_table_name)
    prod_table = get_table_client(prod_table_name)
    
    print("=" * 80)
    print("COMPARING DEV AND PROD CDN FIELDS")
    print("=" * 80)
    print(f"DEV Table:  {dev_table_name}")
    print(f"PROD Table: {prod_table_name}\n")
    
    # Get entities from both tables
    print("Fetching entities from both tables...")
    dev_images = query_entities(dev_table, 'IMAGE', limit=50)
    prod_images = query_entities(prod_table, 'IMAGE', limit=50)
    
    dev_receipts = query_entities(dev_table, 'RECEIPT', limit=50)
    prod_receipts = query_entities(prod_table, 'RECEIPT', limit=50)
    
    # Build ID sets
    dev_image_ids = {img.get('PK', '').replace('IMAGE#', '') for img in dev_images}
    prod_image_ids = {img.get('PK', '').replace('IMAGE#', '') for img in prod_images}
    
    dev_receipt_ids = {r.get('PK', '').replace('RECEIPT#', '') for r in dev_receipts}
    prod_receipt_ids = {r.get('PK', '').replace('RECEIPT#', '') for r in prod_receipts}
    
    # Find common IDs
    common_image_ids = dev_image_ids & prod_image_ids
    common_receipt_ids = dev_receipt_ids & prod_receipt_ids
    
    print(f"\nFound {len(common_image_ids)} common Image IDs")
    print(f"Found {len(common_receipt_ids)} common Receipt IDs")
    
    # Compare CDN patterns
    print("\n" + "=" * 80)
    print("CDN PATTERN ANALYSIS")
    print("=" * 80)
    
    # Analyze DEV patterns
    print("\nDEV Table Patterns:")
    dev_image_patterns = defaultdict(int)
    for img in dev_images:
        cdn_info = analyze_cdn_fields(img)
        dev_image_patterns[cdn_info['pattern']] += 1
    
    print(f"  Images: {dict(dev_image_patterns)}")
    
    dev_receipt_patterns = defaultdict(int)
    for receipt in dev_receipts:
        cdn_info = analyze_cdn_fields(receipt)
        dev_receipt_patterns[cdn_info['pattern']] += 1
    
    print(f"  Receipts: {dict(dev_receipt_patterns)}")
    
    # Analyze PROD patterns
    print("\nPROD Table Patterns:")
    prod_image_patterns = defaultdict(int)
    prod_images_with_cdn = 0
    for img in prod_images:
        cdn_info = analyze_cdn_fields(img)
        prod_image_patterns[cdn_info['pattern']] += 1
        if cdn_info['cdn_s3_key']:
            prod_images_with_cdn += 1
    
    print(f"  Images: {dict(prod_image_patterns)}")
    print(f"  Images with CDN fields: {prod_images_with_cdn}/{len(prod_images)}")
    
    prod_receipt_patterns = defaultdict(int)
    prod_receipts_with_cdn = 0
    for receipt in prod_receipts:
        cdn_info = analyze_cdn_fields(receipt)
        prod_receipt_patterns[cdn_info['pattern']] += 1
        if cdn_info['cdn_s3_key']:
            prod_receipts_with_cdn += 1
    
    print(f"  Receipts: {dict(prod_receipt_patterns)}")
    print(f"  Receipts with CDN fields: {prod_receipts_with_cdn}/{len(prod_receipts)}")
    
    # Compare specific common entities
    if common_image_ids:
        print("\n" + "=" * 80)
        print("COMPARING COMMON IMAGE ENTITIES (first 5)")
        print("=" * 80)
        
        matching_cdn = 0
        different_cdn = 0
        
        for i, img_id in enumerate(sorted(list(common_image_ids))[:5]):
            dev_img = get_entity_by_id(dev_table, 'IMAGE', img_id)
            prod_img = get_entity_by_id(prod_table, 'IMAGE', img_id)
            
            if dev_img and prod_img:
                dev_cdn = analyze_cdn_fields(dev_img)
                prod_cdn = analyze_cdn_fields(prod_img)
                
                print(f"\nImage ID: {img_id}")
                print(f"  DEV CDN:  {dev_cdn['cdn_s3_key']} (pattern: {dev_cdn['pattern']})")
                print(f"  PROD CDN: {prod_cdn['cdn_s3_key']} (pattern: {prod_cdn['pattern']})")
                
                if dev_cdn['cdn_s3_key'] and not prod_cdn['cdn_s3_key']:
                    print(f"  ✓ Can copy from DEV to PROD")
                elif dev_cdn['cdn_s3_key'] == prod_cdn['cdn_s3_key']:
                    print(f"  = Already match")
                    matching_cdn += 1
                else:
                    print(f"  ! Different values")
                    different_cdn += 1
        
        print(f"\nTotal checked: {min(5, len(common_image_ids))}")
        print(f"Matching CDN values: {matching_cdn}")
        print(f"Different CDN values: {different_cdn}")
    
    # Summary and recommendations
    print("\n" + "=" * 80)
    print("SUMMARY AND RECOMMENDATIONS")
    print("=" * 80)
    
    print("\n1. CDN Field Population:")
    print(f"   - DEV has {len([i for i in dev_images if analyze_cdn_fields(i)['cdn_s3_key']])} images with CDN fields")
    print(f"   - PROD has {prod_images_with_cdn} images with CDN fields")
    print(f"   - DEV has {len([r for r in dev_receipts if analyze_cdn_fields(r)['cdn_s3_key']])} receipts with CDN fields")
    print(f"   - PROD has {prod_receipts_with_cdn} receipts with CDN fields")
    
    print("\n2. Pattern Consistency:")
    print(f"   - DEV uses '{list(dev_image_patterns.keys())}' patterns")
    print(f"   - PROD uses '{list(prod_image_patterns.keys())}' patterns")
    
    print("\n3. Entity ID Alignment:")
    print(f"   - {len(common_image_ids)} common Image IDs between DEV and PROD")
    print(f"   - {len(common_receipt_ids)} common Receipt IDs between DEV and PROD")
    
    if 'flat' in dev_image_patterns and 'subdirectory' in prod_image_patterns:
        print("\n⚠️  WARNING: DEV uses flat pattern while PROD uses subdirectory pattern!")
        print("   Copying CDN values directly may not be appropriate.")
    elif common_image_ids and prod_images_with_cdn < len(prod_images):
        print("\n✓ RECOMMENDATION: You can potentially copy CDN values from DEV to PROD")
        print("   for entities with matching IDs that don't have CDN fields in PROD.")
    else:
        print("\n⚠️  No clear benefit to copying CDN values from DEV to PROD")

if __name__ == "__main__":
    main()