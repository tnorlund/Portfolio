#!/usr/bin/env python3
"""
Analyze Receipt CDN file structure and mapping requirements.
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List, Optional, Set
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


def analyze_receipt_s3_structure(bucket: str, limit: int = None) -> Dict[str, Any]:
    """Analyze the S3 bucket structure for receipt CDN files."""
    s3 = boto3.client('s3')
    
    logger.info(f"Analyzing S3 bucket structure for receipts in {bucket}...")
    
    # List all receipt objects
    paginator = s3.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=bucket, Prefix='assets/')
    
    receipt_files = []
    image_ids_with_receipts = set()
    receipt_formats = set()
    receipt_patterns = {}
    
    object_count = 0
    
    for page in page_iterator:
        if 'Contents' in page:
            for obj in page['Contents']:
                key = obj['Key']
                
                # Look for receipt files
                if '_RECEIPT_' in key:
                    object_count += 1
                    receipt_files.append(key)
                    
                    if limit and object_count > limit:
                        break
                    
                    # Parse the receipt file pattern
                    # Expected: assets/{image_id}_RECEIPT_{receipt_id}.{format}
                    if key.startswith('assets/'):
                        filename = key[7:]  # Remove 'assets/' prefix
                        
                        # Extract components
                        if '_RECEIPT_' in filename:
                            parts = filename.split('_RECEIPT_')
                            if len(parts) == 2:
                                image_id = parts[0]
                                receipt_part = parts[1]
                                
                                # Extract receipt ID and format
                                if '.' in receipt_part:
                                    receipt_id, format_ext = receipt_part.rsplit('.', 1)
                                    image_ids_with_receipts.add(image_id)
                                    receipt_formats.add(format_ext.lower())
                                    
                                    # Track patterns
                                    pattern_key = f"{image_id}_RECEIPT_{receipt_id}"
                                    if pattern_key not in receipt_patterns:
                                        receipt_patterns[pattern_key] = []
                                    receipt_patterns[pattern_key].append(format_ext.lower())
        
        if limit and object_count > limit:
            break
    
    # Analyze patterns
    formats_per_receipt = {}
    for pattern, formats in receipt_patterns.items():
        format_count = len(formats)
        if format_count not in formats_per_receipt:
            formats_per_receipt[format_count] = 0
        formats_per_receipt[format_count] += 1
    
    return {
        'total_receipt_files': len(receipt_files),
        'unique_image_ids': len(image_ids_with_receipts),
        'formats_found': sorted(list(receipt_formats)),
        'receipt_file_samples': receipt_files[:10],
        'formats_per_receipt': formats_per_receipt,
        'unique_receipts': len(receipt_patterns),
    }


def check_receipt_cdn_files(bucket: str, image_id: str, receipt_id: int) -> Dict[str, Any]:
    """Check which CDN files exist for a specific receipt."""
    s3 = boto3.client('s3')
    
    result = {
        'image_id': image_id,
        'receipt_id': receipt_id,
        'files_found': [],
        'formats': [],
        'recommended_mapping': {},
    }
    
    # Receipt ID formatted with 5 digits
    receipt_id_str = f"{receipt_id:05d}"
    
    # Check for different formats
    formats_to_check = ['png', 'jpg', 'jpeg', 'webp', 'avif']
    
    for fmt in formats_to_check:
        # Check standard pattern: assets/{image_id}_RECEIPT_{receipt_id}.{format}
        key = f"assets/{image_id}_RECEIPT_{receipt_id_str}.{fmt}"
        try:
            s3.head_object(Bucket=bucket, Key=key)
            result['files_found'].append(key)
            result['formats'].append(fmt)
        except s3.exceptions.ClientError:
            pass
    
    # Generate recommended mapping based on what was found
    if result['files_found']:
        # Find the base format (usually PNG or JPG)
        base_format = None
        if 'png' in result['formats']:
            base_format = 'png'
        elif 'jpg' in result['formats']:
            base_format = 'jpg'
        elif 'jpeg' in result['formats']:
            base_format = 'jpeg'
        
        if base_format:
            result['recommended_mapping']['cdn_s3_key'] = f"assets/{image_id}_RECEIPT_{receipt_id_str}.{base_format}"
        
        if 'webp' in result['formats']:
            result['recommended_mapping']['cdn_webp_s3_key'] = f"assets/{image_id}_RECEIPT_{receipt_id_str}.webp"
        
        if 'avif' in result['formats']:
            result['recommended_mapping']['cdn_avif_s3_key'] = f"assets/{image_id}_RECEIPT_{receipt_id_str}.avif"
    
    return result


def analyze_receipt_entities(table_name: str, cdn_bucket: str, sample_size: int = 10):
    """Analyze Receipt entities and their CDN mapping potential."""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    logger.info(f"Analyzing {sample_size} Receipt entities...")
    
    # Get sample Receipt entities
    scan_kwargs = {
        'FilterExpression': '#type = :receipt_type',
        'ExpressionAttributeNames': {'#type': 'TYPE'},
        'ExpressionAttributeValues': {':receipt_type': 'RECEIPT'},
        'Limit': 100  # Scan more items to find receipts
    }
    
    response = table.scan(**scan_kwargs)
    all_items = response.get('Items', [])
    
    # Keep scanning if we haven't found enough receipts
    receipt_entities = []
    for item in all_items:
        if item.get('TYPE') == 'RECEIPT':
            receipt_entities.append(item)
            if len(receipt_entities) >= sample_size:
                break
    
    # If we still need more, do additional scans
    while len(receipt_entities) < sample_size and 'LastEvaluatedKey' in response:
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
        response = table.scan(**scan_kwargs)
        for item in response.get('Items', []):
            if item.get('TYPE') == 'RECEIPT':
                receipt_entities.append(item)
                if len(receipt_entities) >= sample_size:
                    break
    
    results = []
    for entity in receipt_entities:
        # Parse image ID and receipt ID from keys
        image_id = entity['PK'].replace('IMAGE#', '')
        receipt_id = int(entity['SK'].replace('RECEIPT#', ''))
        
        # Check current CDN field state
        cdn_fields = [
            'cdn_s3_key', 'cdn_webp_s3_key', 'cdn_avif_s3_key',
            'cdn_thumbnail_s3_key', 'cdn_thumbnail_webp_s3_key', 'cdn_thumbnail_avif_s3_key',
            'cdn_small_s3_key', 'cdn_small_webp_s3_key', 'cdn_small_avif_s3_key',
            'cdn_medium_s3_key', 'cdn_medium_webp_s3_key', 'cdn_medium_avif_s3_key'
        ]
        
        populated_fields = {}
        for field in cdn_fields:
            if entity.get(field) and entity[field] != 'NULL':
                populated_fields[field] = entity[field]
        
        # Check S3 for actual files
        s3_check = check_receipt_cdn_files(cdn_bucket, image_id, receipt_id)
        
        result = {
            'image_id': image_id,
            'receipt_id': receipt_id,
            'current_cdn_fields': populated_fields,
            'cdn_fields_count': len(populated_fields),
            's3_check': s3_check,
            'can_be_mapped': len(s3_check['recommended_mapping']) > 0,
        }
        
        results.append(result)
        
        logger.info(f"Receipt IMAGE#{image_id}/RECEIPT#{receipt_id:05d}: "
                   f"{len(populated_fields)} CDN fields populated, "
                   f"{len(s3_check['files_found'])} files in S3, "
                   f"can map: {result['can_be_mapped']}")
    
    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze Receipt CDN mapping requirements"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Number of entities to analyze (default: 10)",
    )
    parser.add_argument(
        "--analyze-bucket",
        action="store_true",
        help="Analyze S3 bucket structure for receipts",
    )
    parser.add_argument(
        "--bucket-limit",
        type=int,
        default=1000,
        help="Limit S3 analysis to N receipt objects (default: 1000)",
    )
    parser.add_argument(
        "--stack",
        choices=["dev", "prod"],
        default="prod",
        help="Which stack to analyze (default: prod)",
    )
    parser.add_argument(
        "--check-specific",
        help="Check specific receipt (format: IMAGE_ID/RECEIPT_ID, e.g., 04b16930-7baf-4539-866f-b77d8e73cff8/1)",
    )
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    work_dir = os.path.join(parent_dir, "infra")
    
    stack_name = f"tnorlund/portfolio/{args.stack}"
    logger.info(f"Getting {args.stack.upper()} configuration...")
    
    stack = auto.create_or_select_stack(
        stack_name=stack_name,
        work_dir=work_dir,
    )
    outputs = stack.outputs()
    table_name = outputs["dynamodb_table_name"].value
    cdn_bucket = outputs["cdn_bucket_name"].value
    
    logger.info(f"{args.stack.upper()} table: {table_name}")
    logger.info(f"CDN bucket: {cdn_bucket}")
    
    if args.check_specific:
        # Check a specific receipt
        parts = args.check_specific.split('/')
        if len(parts) == 2:
            image_id = parts[0]
            receipt_id = int(parts[1])
            logger.info(f"\nChecking specific receipt: IMAGE#{image_id}/RECEIPT#{receipt_id:05d}")
            result = check_receipt_cdn_files(cdn_bucket, image_id, receipt_id)
            
            logger.info(f"Files found: {len(result['files_found'])}")
            for file in result['files_found']:
                logger.info(f"  - {file}")
            
            logger.info(f"\nRecommended mapping:")
            for field, value in result['recommended_mapping'].items():
                logger.info(f"  {field}: {value}")
        else:
            logger.error("Invalid format. Use IMAGE_ID/RECEIPT_ID")
            return
    
    if args.analyze_bucket:
        logger.info("\n" + "=" * 60)
        logger.info("S3 RECEIPT FILE STRUCTURE ANALYSIS")
        logger.info("=" * 60)
        bucket_analysis = analyze_receipt_s3_structure(cdn_bucket, args.bucket_limit)
        
        logger.info(f"Total receipt files found: {bucket_analysis['total_receipt_files']}")
        logger.info(f"Unique image IDs with receipts: {bucket_analysis['unique_image_ids']}")
        logger.info(f"Unique receipts: {bucket_analysis['unique_receipts']}")
        logger.info(f"Formats found: {', '.join(bucket_analysis['formats_found'])}")
        
        logger.info(f"\nFormats per receipt distribution:")
        for count, num_receipts in sorted(bucket_analysis['formats_per_receipt'].items()):
            logger.info(f"  {count} formats: {num_receipts} receipts")
        
        logger.info("\nReceipt file samples:")
        for sample in bucket_analysis['receipt_file_samples']:
            logger.info(f"  {sample}")
    
    if not args.check_specific:
        logger.info("\n" + "=" * 60)
        logger.info("RECEIPT ENTITY CDN MAPPING ANALYSIS")
        logger.info("=" * 60)
        
        # Analyze Receipt entities
        entity_results = analyze_receipt_entities(table_name, cdn_bucket, args.sample_size)
        
        # Summary statistics
        total_entities = len(entity_results)
        can_be_mapped = sum(1 for r in entity_results if r['can_be_mapped'])
        already_mapped = sum(1 for r in entity_results if r['cdn_fields_count'] > 0)
        
        logger.info(f"\nSUMMARY:")
        logger.info(f"Total receipts analyzed: {total_entities}")
        if total_entities > 0:
            logger.info(f"Already have CDN fields: {already_mapped} ({already_mapped/total_entities*100:.1f}%)")
            logger.info(f"Can be mapped to CDN: {can_be_mapped} ({can_be_mapped/total_entities*100:.1f}%)")
        else:
            logger.info("No receipt entities found in the sample.")
        
        # Show detailed results for first few entities
        logger.info(f"\nDetailed mapping for first 3 receipts:")
        for i, result in enumerate(entity_results[:3]):
            logger.info(f"\n--- Receipt {i+1}: IMAGE#{result['image_id']}/RECEIPT#{result['receipt_id']:05d} ---")
            logger.info(f"Current CDN fields populated: {result['cdn_fields_count']}")
            logger.info(f"Files found in S3: {len(result['s3_check']['files_found'])}")
            logger.info(f"Formats available: {', '.join(result['s3_check']['formats'])}")
            
            if result['s3_check']['recommended_mapping']:
                logger.info("Recommended CDN field mapping:")
                for field, value in result['s3_check']['recommended_mapping'].items():
                    logger.info(f"  {field}: {value}")


if __name__ == "__main__":
    main()