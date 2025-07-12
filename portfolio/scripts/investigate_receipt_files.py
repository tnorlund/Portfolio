#!/usr/bin/env python3
"""
Investigate S3 bucket to find receipt files and understand their naming patterns.
"""

import boto3
import os
import sys
from typing import Dict, List, Set, Tuple
from collections import defaultdict
import re

# Add the parent directory to sys.path to import from receipt packages
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'receipt_dynamo')))

try:
    from receipt_dynamo import Entity, get_dynamo_client, ResilientDynamoClient
except ImportError:
    # If that doesn't work, try direct import
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'packages')))
    from receipt_dynamo import Entity, get_dynamo_client, ResilientDynamoClient


def get_s3_client():
    """Get S3 client for the session."""
    return boto3.client('s3')


def investigate_cdn_bucket(bucket_name: str, prefix: str = "assets/"):
    """Investigate the CDN bucket to find receipt-related files."""
    s3 = get_s3_client()
    
    print(f"\n=== Investigating S3 bucket: {bucket_name} ===")
    print(f"Prefix: {prefix}")
    
    # Collect statistics
    total_objects = 0
    receipt_patterns = defaultdict(list)
    image_patterns = defaultdict(list)
    all_prefixes = set()
    size_distribution = defaultdict(int)
    
    # Common receipt-related keywords
    receipt_keywords = ['receipt', 'RECEIPT', 'rcpt', 'RCPT', 'invoice', 'INVOICE', 'bill', 'BILL']
    
    try:
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        
        for page in pages:
            if 'Contents' not in page:
                continue
                
            for obj in page['Contents']:
                total_objects += 1
                key = obj['Key']
                size = obj['Size']
                
                # Extract prefix pattern (first part after assets/)
                parts = key.split('/')
                if len(parts) > 1:
                    all_prefixes.add(parts[1])
                
                # Size distribution
                if size < 100_000:  # < 100KB
                    size_distribution['<100KB'] += 1
                elif size < 500_000:  # < 500KB
                    size_distribution['100KB-500KB'] += 1
                elif size < 1_000_000:  # < 1MB
                    size_distribution['500KB-1MB'] += 1
                else:
                    size_distribution['>1MB'] += 1
                
                # Check for receipt patterns
                key_lower = key.lower()
                for keyword in receipt_keywords:
                    if keyword.lower() in key_lower:
                        receipt_patterns[keyword].append(key)
                        break
                
                # Check for image patterns that might be receipts
                if any(pattern in key for pattern in ['IMG_', 'IMAGE_', 'PHOTO_', 'PIC_']):
                    # Check if it might be a receipt based on size or other patterns
                    if size > 500_000:  # Receipts are often larger images
                        image_patterns['large_images'].append((key, size))
                
                # Show progress
                if total_objects % 1000 == 0:
                    print(f"  Processed {total_objects} objects...")
        
        # Print findings
        print(f"\n=== Summary ===")
        print(f"Total objects: {total_objects}")
        print(f"\nUnique prefixes found:")
        for prefix in sorted(all_prefixes):
            print(f"  - {prefix}")
        
        print(f"\nSize distribution:")
        for size_range, count in sorted(size_distribution.items()):
            print(f"  {size_range}: {count}")
        
        print(f"\n=== Receipt Pattern Matches ===")
        total_receipt_matches = 0
        for keyword, matches in receipt_patterns.items():
            if matches:
                print(f"\n'{keyword}' pattern ({len(matches)} matches):")
                for match in matches[:5]:  # Show first 5
                    print(f"  - {match}")
                if len(matches) > 5:
                    print(f"  ... and {len(matches) - 5} more")
                total_receipt_matches += len(matches)
        
        if total_receipt_matches == 0:
            print("No files matching receipt keywords found!")
        
        print(f"\n=== Large Images (potential receipts) ===")
        if image_patterns['large_images']:
            print(f"Found {len(image_patterns['large_images'])} large images (>500KB):")
            for key, size in sorted(image_patterns['large_images'], key=lambda x: -x[1])[:10]:
                print(f"  - {key} ({size:,} bytes)")
        
        # Let's also check specific patterns
        print(f"\n=== Checking Specific Patterns ===")
        
        # Pattern 1: Files with receipt-like IDs in the name
        pattern_checks = [
            (r'receipt[-_]\d+', "Receipt with ID pattern"),
            (r'RECEIPT[-_]\d+', "RECEIPT with ID pattern"),
            (r'\d{4}-\d{2}-\d{2}.*receipt', "Date + receipt pattern"),
            (r'receipt.*\d{4}-\d{2}-\d{2}', "Receipt + date pattern"),
            (r'scan[-_]\d+', "Scan pattern (might be receipts)"),
            (r'document[-_]\d+', "Document pattern (might be receipts)"),
        ]
        
        # Re-scan with specific patterns
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
        pattern_matches = defaultdict(list)
        
        for page in pages:
            if 'Contents' not in page:
                continue
                
            for obj in page['Contents']:
                key = obj['Key']
                for pattern, description in pattern_checks:
                    if re.search(pattern, key, re.IGNORECASE):
                        pattern_matches[description].append(key)
        
        for description, matches in pattern_matches.items():
            if matches:
                print(f"\n{description} ({len(matches)} matches):")
                for match in matches[:5]:
                    print(f"  - {match}")
                if len(matches) > 5:
                    print(f"  ... and {len(matches) - 5} more")
        
        # Sample some files to check their patterns
        print(f"\n=== Sample of All Files ===")
        print("First 20 files in the bucket:")
        pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix, MaxKeys=20)
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    print(f"  - {obj['Key']} ({obj['Size']:,} bytes)")
        
    except Exception as e:
        print(f"Error accessing S3 bucket: {e}")
        return


def check_dynamo_receipt_entities():
    """Check DynamoDB for receipt entities to understand their CDN field patterns."""
    print(f"\n=== Checking DynamoDB Receipt Entities ===")
    
    dynamo_client = ResilientDynamoClient(
        table_name="portfolioTable",
        region_name="us-east-1"
    )
    
    # Query for receipt entities
    receipts = dynamo_client.query_all_by_gsi_type("RECEIPT")
    
    print(f"Found {len(receipts)} receipt entities")
    
    # Analyze CDN fields
    cdn_patterns = defaultdict(int)
    sample_cdn_values = []
    
    for receipt in receipts[:100]:  # Check first 100
        receipt_dict = receipt.to_item()
        
        # Check various CDN-related fields
        cdn_fields = ['cdn', 'cdnThumb', 'cdnFull', 'cdnOriginal', 'image', 'imageUrl', 'url']
        
        for field in cdn_fields:
            if field in receipt_dict and receipt_dict[field]:
                value = receipt_dict[field]
                cdn_patterns[field] += 1
                
                if len(sample_cdn_values) < 10:
                    sample_cdn_values.append({
                        'entity_id': receipt_dict.get('entity', 'unknown'),
                        'field': field,
                        'value': value
                    })
    
    print(f"\nCDN field usage:")
    for field, count in sorted(cdn_patterns.items(), key=lambda x: -x[1]):
        print(f"  {field}: {count} receipts")
    
    print(f"\nSample CDN values:")
    for sample in sample_cdn_values:
        print(f"  Entity: {sample['entity_id']}")
        print(f"    Field: {sample['field']}")
        print(f"    Value: {sample['value']}")
        print()


def main():
    """Main function to investigate receipt files."""
    # Check production CDN bucket
    prod_bucket = "tnorcom-assets-bucket"
    investigate_cdn_bucket(prod_bucket)
    
    # Check DynamoDB entities
    check_dynamo_receipt_entities()
    
    # Based on user's comment about "image images", let's specifically look for image patterns
    print(f"\n=== Investigating 'Image' Pattern Files ===")
    print("Based on the comment 'receipt images were saved as the image images'")
    print("Let's specifically look for files with 'image' in their path...")
    
    s3 = get_s3_client()
    paginator = s3.get_paginator('list_objects_v2')
    
    image_pattern_files = []
    pages = paginator.paginate(Bucket=prod_bucket, Prefix="assets/")
    
    for page in pages:
        if 'Contents' not in page:
            continue
            
        for obj in page['Contents']:
            key = obj['Key']
            if 'image' in key.lower() or 'IMAGE' in key:
                image_pattern_files.append((key, obj['Size']))
    
    print(f"\nFound {len(image_pattern_files)} files with 'image' pattern")
    if image_pattern_files:
        print("First 20 examples:")
        for key, size in image_pattern_files[:20]:
            print(f"  - {key} ({size:,} bytes)")


if __name__ == "__main__":
    main()