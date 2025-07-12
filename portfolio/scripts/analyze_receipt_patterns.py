#!/usr/bin/env python3
"""
Analyze receipt file patterns in S3 CDN bucket.
"""

import boto3
from collections import defaultdict
import re


def main():
    """Analyze receipt files in the CDN bucket."""
    s3 = boto3.client('s3')
    bucket_name = "sitebucket-778abc9"
    
    print("=== Analyzing Receipt Files in CDN ===\n")
    
    # Statistics
    receipt_files = []
    format_counts = defaultdict(int)
    base_images = {}  # Map base UUID to list of receipt files
    size_stats = {
        'total_size': 0,
        'min_size': float('inf'),
        'max_size': 0,
        'count': 0
    }
    
    # Paginate through objects
    paginator = s3.get_paginator('list_objects_v2')
    pages = paginator.paginate(Bucket=bucket_name, Prefix="assets/")
    
    print("Scanning for receipt files...")
    total_files = 0
    
    for page in pages:
        if 'Contents' not in page:
            continue
            
        for obj in page['Contents']:
            total_files += 1
            key = obj['Key']
            size = obj['Size']
            
            # Check if it's a receipt file
            if '_RECEIPT_' in key:
                receipt_files.append((key, size))
                
                # Extract format
                if key.endswith('.png'):
                    format_counts['PNG'] += 1
                elif key.endswith('.jpg') or key.endswith('.jpeg'):
                    format_counts['JPG'] += 1
                elif key.endswith('.avif'):
                    format_counts['AVIF'] += 1
                elif key.endswith('.webp'):
                    format_counts['WebP'] += 1
                else:
                    format_counts['Other'] += 1
                
                # Extract base UUID
                match = re.match(r'assets/([a-f0-9-]+)_RECEIPT_', key)
                if match:
                    base_uuid = match.group(1)
                    if base_uuid not in base_images:
                        base_images[base_uuid] = []
                    base_images[base_uuid].append(key)
                
                # Update size stats
                size_stats['total_size'] += size
                size_stats['min_size'] = min(size_stats['min_size'], size)
                size_stats['max_size'] = max(size_stats['max_size'], size)
                size_stats['count'] += 1
            
            # Progress
            if total_files % 1000 == 0:
                print(f"  Processed {total_files} files...")
    
    # Print analysis
    print(f"\n=== Receipt File Analysis ===")
    print(f"Total files scanned: {total_files}")
    print(f"Receipt files found: {len(receipt_files)}")
    print(f"Unique base images with receipts: {len(base_images)}")
    
    print(f"\n=== Format Distribution ===")
    for fmt, count in sorted(format_counts.items(), key=lambda x: -x[1]):
        percentage = (count / len(receipt_files) * 100) if receipt_files else 0
        print(f"{fmt}: {count} ({percentage:.1f}%)")
    
    print(f"\n=== Size Statistics ===")
    if size_stats['count'] > 0:
        avg_size = size_stats['total_size'] / size_stats['count']
        print(f"Total size: {size_stats['total_size']:,} bytes ({size_stats['total_size'] / (1024**3):.2f} GB)")
        print(f"Average size: {avg_size:,.0f} bytes ({avg_size / (1024**2):.2f} MB)")
        print(f"Min size: {size_stats['min_size']:,} bytes")
        print(f"Max size: {size_stats['max_size']:,} bytes")
    
    # Analyze multiple receipts per image
    print(f"\n=== Multiple Receipts Per Image ===")
    multi_receipt_count = 0
    receipt_number_counts = defaultdict(int)
    
    for base_uuid, files in base_images.items():
        if len(files) > 1:
            multi_receipt_count += 1
        receipt_number_counts[len(files)] += 1
    
    print(f"Images with multiple receipts: {multi_receipt_count}")
    print(f"\nReceipts per image distribution:")
    for count, num_images in sorted(receipt_number_counts.items()):
        print(f"  {count} receipt(s): {num_images} images")
    
    # Show examples of images with multiple receipts
    print(f"\n=== Examples of Images with Multiple Receipts ===")
    examples_shown = 0
    for base_uuid, files in sorted(base_images.items(), key=lambda x: -len(x[1])):
        if len(files) > 1 and examples_shown < 5:
            print(f"\n{base_uuid}: {len(files)} receipts")
            for f in sorted(files)[:5]:
                print(f"  - {f}")
            examples_shown += 1
    
    # Check for original image files (without _RECEIPT_)
    print(f"\n=== Checking Original Image Status ===")
    original_exists_count = 0
    missing_original_count = 0
    
    # Check first 10 base images
    pages = paginator.paginate(Bucket=bucket_name, Prefix="assets/")
    all_files = set()
    
    for page in pages:
        if 'Contents' not in page:
            continue
        for obj in page['Contents']:
            all_files.add(obj['Key'])
    
    for base_uuid in list(base_images.keys())[:100]:  # Check first 100
        # Look for original files
        has_original = False
        for ext in ['.png', '.jpg', '.jpeg', '.avif', '.webp']:
            if f"assets/{base_uuid}{ext}" in all_files:
                has_original = True
                break
        
        if has_original:
            original_exists_count += 1
        else:
            missing_original_count += 1
    
    print(f"Checked {min(100, len(base_images))} base images:")
    print(f"  - With original image: {original_exists_count}")
    print(f"  - Missing original image: {missing_original_count}")
    
    # Find receipts with non-standard patterns
    print(f"\n=== Non-Standard Receipt Patterns ===")
    non_standard = []
    for key, size in receipt_files[:100]:  # Check first 100
        if not re.match(r'assets/[a-f0-9-]+_RECEIPT_\d{5}\.(png|jpg|jpeg|avif|webp)$', key):
            non_standard.append(key)
    
    if non_standard:
        print(f"Found {len(non_standard)} non-standard patterns:")
        for pattern in non_standard[:10]:
            print(f"  - {pattern}")
    else:
        print("All receipts follow standard pattern: assets/{UUID}_RECEIPT_{NUMBER}.{EXT}")


if __name__ == "__main__":
    main()