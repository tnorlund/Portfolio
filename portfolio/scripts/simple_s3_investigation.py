#!/usr/bin/env python3
"""
Simple S3 investigation script to find receipt files.
"""

import boto3
from collections import defaultdict
import re


def main():
    """Investigate S3 bucket for receipt files."""
    s3 = boto3.client('s3')
    bucket_name = "sitebucket-778abc9"  # The actual CDN bucket from CloudFront
    
    print(f"=== Investigating S3 bucket: {bucket_name} ===\n")
    
    # Collect statistics
    total_objects = 0
    receipt_keywords = ['receipt', 'RECEIPT', 'rcpt', 'RCPT', 'invoice', 'INVOICE', 'bill', 'BILL']
    pattern_matches = defaultdict(list)
    all_prefixes = set()
    
    # Paginate through all objects
    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket_name, Prefix="assets/")
    
    # First, let's get a sample of files to understand the naming pattern
    print("=== Sample of first 50 files ===")
    sample_count = 0
    
    for page in pages:
        if 'Contents' not in page:
            continue
            
        for obj in page['Contents']:
            key = obj['Key']
            size = obj['Size']
            
            if sample_count < 50:
                print(f"  {key} ({size:,} bytes)")
                sample_count += 1
            
            total_objects += 1
            
            # Extract prefix
            parts = key.split('/')
            if len(parts) > 1:
                all_prefixes.add(parts[1])
            
            # Check for receipt patterns
            key_lower = key.lower()
            for keyword in receipt_keywords:
                if keyword.lower() in key_lower:
                    pattern_matches[keyword].append((key, size))
                    break
            
            # Check for specific patterns mentioned by user
            if 'image' in key_lower or 'IMAGE' in key:
                pattern_matches['image_pattern'].append((key, size))
            
            # Show progress
            if total_objects % 1000 == 0:
                print(f"\n  Processed {total_objects} objects...\n")
    
    print(f"\n=== Summary ===")
    print(f"Total objects scanned: {total_objects}")
    
    print(f"\nUnique prefixes found:")
    for prefix in sorted(all_prefixes):
        print(f"  - {prefix}")
    
    print(f"\n=== Pattern Matches ===")
    
    # Show receipt keyword matches
    total_receipt_matches = 0
    for keyword, matches in pattern_matches.items():
        if keyword != 'image_pattern' and matches:
            print(f"\n'{keyword}' pattern ({len(matches)} matches):")
            for key, size in matches[:10]:
                print(f"  - {key} ({size:,} bytes)")
            if len(matches) > 10:
                print(f"  ... and {len(matches) - 10} more")
            total_receipt_matches += len(matches)
    
    if total_receipt_matches == 0:
        print("\nNo files matching receipt keywords found!")
    
    # Show image pattern matches
    if pattern_matches['image_pattern']:
        print(f"\n'image' pattern files ({len(pattern_matches['image_pattern'])} matches):")
        # Sort by size to find larger images that might be receipts
        sorted_images = sorted(pattern_matches['image_pattern'], key=lambda x: -x[1])
        for key, size in sorted_images[:20]:
            print(f"  - {key} ({size:,} bytes)")
        if len(sorted_images) > 20:
            print(f"  ... and {len(sorted_images) - 20} more")
    
    # Let's also check for specific file patterns that might be receipts
    print(f"\n=== Checking for files with specific patterns ===")
    
    # Reset and check for more specific patterns
    pages = paginator.paginate(Bucket=bucket_name, Prefix="assets/")
    specific_patterns = {
        'UUID pattern (IMAGE_)': r'IMAGE_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
        'UUID pattern (RECEIPT_)': r'RECEIPT_[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
        'Date pattern': r'\d{4}-\d{2}-\d{2}',
        'Numeric ID pattern': r'[0-9]{5,}',
    }
    
    specific_matches = defaultdict(list)
    
    for page in pages:
        if 'Contents' not in page:
            continue
            
        for obj in page['Contents']:
            key = obj['Key']
            size = obj['Size']
            
            for pattern_name, pattern in specific_patterns.items():
                if re.search(pattern, key):
                    specific_matches[pattern_name].append((key, size))
    
    for pattern_name, matches in specific_matches.items():
        if matches:
            print(f"\n{pattern_name} ({len(matches)} matches):")
            for key, size in matches[:10]:
                print(f"  - {key} ({size:,} bytes)")
            if len(matches) > 10:
                print(f"  ... and {len(matches) - 10} more")
    
    # Based on user comment about "image images", let's specifically look at IMAGE_ prefixed files
    print(f"\n=== Specifically checking IMAGE_ prefixed files ===")
    pages = paginator.paginate(Bucket=bucket_name, Prefix="assets/IMAGE_")
    
    image_prefix_files = []
    for page in pages:
        if 'Contents' not in page:
            continue
            
        for obj in page['Contents']:
            image_prefix_files.append((obj['Key'], obj['Size']))
    
    if image_prefix_files:
        print(f"Found {len(image_prefix_files)} files with IMAGE_ prefix")
        print("First 20 examples:")
        for key, size in sorted(image_prefix_files, key=lambda x: -x[1])[:20]:
            print(f"  - {key} ({size:,} bytes)")
    else:
        print("No files found with IMAGE_ prefix")
    
    # Check for RECEIPT_ prefix as well
    print(f"\n=== Checking RECEIPT_ prefixed files ===")
    pages = paginator.paginate(Bucket=bucket_name, Prefix="assets/RECEIPT_")
    
    receipt_prefix_files = []
    for page in pages:
        if 'Contents' not in page:
            continue
            
        for obj in page['Contents']:
            receipt_prefix_files.append((obj['Key'], obj['Size']))
    
    if receipt_prefix_files:
        print(f"Found {len(receipt_prefix_files)} files with RECEIPT_ prefix")
        print("Examples:")
        for key, size in receipt_prefix_files[:10]:
            print(f"  - {key} ({size:,} bytes)")
    else:
        print("No files found with RECEIPT_ prefix")


if __name__ == "__main__":
    main()