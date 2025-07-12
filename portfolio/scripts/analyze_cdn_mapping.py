#!/usr/bin/env python3
"""
Analyze CDN mapping requirements for PROD entities.
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


def analyze_s3_structure(bucket: str, limit: int = None) -> Dict[str, Any]:
    """Analyze the S3 bucket structure for CDN files."""
    s3 = boto3.client('s3')
    
    logger.info(f"Analyzing S3 bucket structure in {bucket}...")
    
    # List all objects in assets/ with pagination
    paginator = s3.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=bucket, Prefix='assets/')
    
    flat_files = set()
    dir_files = set()
    image_ids_with_flat = set()
    image_ids_with_dir = set()
    
    object_count = 0
    
    for page in page_iterator:
        if 'Contents' in page:
            for obj in page['Contents']:
                object_count += 1
                key = obj['Key']
                
                if limit and object_count > limit:
                    break
                
                # Skip the assets/ directory itself
                if key == 'assets/':
                    continue
                
                # Parse the key structure
                if key.startswith('assets/'):
                    remainder = key[7:]  # Remove 'assets/' prefix
                    
                    if '/' in remainder:
                        # Directory structure: assets/{image-id}/filename
                        image_id, filename = remainder.split('/', 1)
                        if image_id and filename:  # Valid UUID and filename
                            dir_files.add(key)
                            image_ids_with_dir.add(image_id)
                    else:
                        # Flat structure: assets/{image-id}_size.format or assets/{image-id}.format
                        flat_files.add(key)
                        # Extract image ID from flat structure
                        if '_' in remainder:
                            image_id = remainder.split('_')[0]
                        else:
                            image_id = remainder.split('.')[0]
                        if image_id:
                            image_ids_with_flat.add(image_id)
        
        if limit and object_count > limit:
            break
    
    return {
        'total_objects': object_count,
        'flat_files': len(flat_files),
        'dir_files': len(dir_files),
        'image_ids_with_flat': len(image_ids_with_flat),
        'image_ids_with_dir': len(image_ids_with_dir),
        'flat_sample': list(flat_files)[:10],
        'dir_sample': list(dir_files)[:10],
        'both_patterns': len(image_ids_with_flat.intersection(image_ids_with_dir)),
        'only_flat': len(image_ids_with_flat - image_ids_with_dir),
        'only_dir': len(image_ids_with_dir - image_ids_with_flat),
    }


def check_cdn_pattern_for_image(bucket: str, image_id: str) -> Dict[str, Any]:
    """Check which CDN pattern exists for a specific image."""
    s3 = boto3.client('s3')
    
    result = {
        'image_id': image_id,
        'flat_files': [],
        'dir_files': [],
        'flat_exists': False,
        'dir_exists': False,
        'recommended_pattern': None,
    }
    
    # Define expected file patterns
    sizes = ['thumbnail', 'small', 'medium']
    formats = ['jpg', 'webp', 'avif']
    
    # Check flat pattern
    for size in sizes:
        for fmt in formats:
            flat_key = f"assets/{image_id}_{size}.{fmt}"
            try:
                s3.head_object(Bucket=bucket, Key=flat_key)
                result['flat_files'].append(flat_key)
                result['flat_exists'] = True
            except s3.exceptions.ClientError:
                pass
    
    # Check directory pattern
    for size in sizes:
        for fmt in formats:
            dir_key = f"assets/{image_id}/1_{size}.{fmt}"
            try:
                s3.head_object(Bucket=bucket, Key=dir_key)
                result['dir_files'].append(dir_key)
                result['dir_exists'] = True
            except s3.exceptions.ClientError:
                pass
    
    # Also check for full-size images
    for fmt in formats:
        # Flat pattern - full size
        flat_key = f"assets/{image_id}.{fmt}"
        try:
            s3.head_object(Bucket=bucket, Key=flat_key)
            result['flat_files'].append(flat_key)
            result['flat_exists'] = True
        except s3.exceptions.ClientError:
            pass
        
        # Directory pattern - full size
        dir_key = f"assets/{image_id}/1.{fmt}"
        try:
            s3.head_object(Bucket=bucket, Key=dir_key)
            result['dir_files'].append(dir_key)
            result['dir_exists'] = True
        except s3.exceptions.ClientError:
            pass
    
    # Determine recommended pattern
    if result['flat_exists'] and result['dir_exists']:
        # Both exist - prefer flat for consistency with DEV database
        result['recommended_pattern'] = 'flat'
    elif result['flat_exists']:
        result['recommended_pattern'] = 'flat'
    elif result['dir_exists']:
        result['recommended_pattern'] = 'dir'
    else:
        result['recommended_pattern'] = None
    
    return result


def generate_cdn_mapping(bucket: str, image_id: str, pattern: str = 'auto') -> Dict[str, str]:
    """Generate CDN field mapping for an image."""
    if pattern == 'auto':
        pattern_info = check_cdn_pattern_for_image(bucket, image_id)
        pattern = pattern_info['recommended_pattern']
    
    if pattern is None:
        return {}
    
    mapping = {}
    
    if pattern == 'flat':
        # Flat pattern mapping
        mapping = {
            'cdn_thumbnail_s3_key': f"assets/{image_id}_thumbnail.jpg",
            'cdn_thumbnail_webp_s3_key': f"assets/{image_id}_thumbnail.webp",
            'cdn_thumbnail_avif_s3_key': f"assets/{image_id}_thumbnail.avif",
            'cdn_small_s3_key': f"assets/{image_id}_small.jpg",
            'cdn_small_webp_s3_key': f"assets/{image_id}_small.webp",
            'cdn_small_avif_s3_key': f"assets/{image_id}_small.avif",
            'cdn_medium_s3_key': f"assets/{image_id}_medium.jpg",
            'cdn_medium_webp_s3_key': f"assets/{image_id}_medium.webp",
            'cdn_medium_avif_s3_key': f"assets/{image_id}_medium.avif",
            # Full-size CDN images
            'cdn_s3_key': f"assets/{image_id}.jpg",
            'cdn_webp_s3_key': f"assets/{image_id}.webp",
            'cdn_avif_s3_key': f"assets/{image_id}.avif",
        }
    elif pattern == 'dir':
        # Directory pattern mapping
        mapping = {
            'cdn_thumbnail_s3_key': f"assets/{image_id}/1_thumbnail.jpg",
            'cdn_thumbnail_webp_s3_key': f"assets/{image_id}/1_thumbnail.webp",
            'cdn_thumbnail_avif_s3_key': f"assets/{image_id}/1_thumbnail.avif",
            'cdn_small_s3_key': f"assets/{image_id}/1_small.jpg",
            'cdn_small_webp_s3_key': f"assets/{image_id}/1_small.webp",
            'cdn_small_avif_s3_key': f"assets/{image_id}/1_small.avif",
            'cdn_medium_s3_key': f"assets/{image_id}/1_medium.jpg",
            'cdn_medium_webp_s3_key': f"assets/{image_id}/1_medium.webp",
            'cdn_medium_avif_s3_key': f"assets/{image_id}/1_medium.avif",
            # Full-size CDN images
            'cdn_s3_key': f"assets/{image_id}/1.jpg",
            'cdn_webp_s3_key': f"assets/{image_id}/1.webp",
            'cdn_avif_s3_key': f"assets/{image_id}/1.avif",
        }
    
    return mapping


def analyze_prod_entities(table_name: str, cdn_bucket: str, sample_size: int = 10):
    """Analyze PROD entities and their CDN mapping potential."""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(table_name)
    
    logger.info(f"Analyzing {sample_size} PROD Image entities...")
    
    # Get sample Image entities
    scan_kwargs = {
        'FilterExpression': '#type = :image_type',
        'ExpressionAttributeNames': {'#type': 'TYPE'},
        'ExpressionAttributeValues': {':image_type': 'IMAGE'},
        'Limit': sample_size
    }
    
    response = table.scan(**scan_kwargs)
    image_entities = response.get('Items', [])
    
    results = []
    for entity in image_entities:
        image_id = entity['PK'].replace('IMAGE#', '')
        
        # Check current CDN field state
        cdn_fields = [
            'cdn_thumbnail_s3_key', 'cdn_thumbnail_webp_s3_key', 'cdn_thumbnail_avif_s3_key',
            'cdn_small_s3_key', 'cdn_small_webp_s3_key', 'cdn_small_avif_s3_key',
            'cdn_medium_s3_key', 'cdn_medium_webp_s3_key', 'cdn_medium_avif_s3_key',
            'cdn_s3_key', 'cdn_webp_s3_key', 'cdn_avif_s3_key'
        ]
        
        populated_fields = sum(1 for field in cdn_fields if entity.get(field) is not None)
        
        # Check S3 pattern availability
        pattern_info = check_cdn_pattern_for_image(cdn_bucket, image_id)
        
        # Generate mapping
        mapping = generate_cdn_mapping(cdn_bucket, image_id)
        
        result = {
            'image_id': image_id,
            'current_cdn_fields_populated': populated_fields,
            'pattern_info': pattern_info,
            'recommended_mapping': mapping,
            'can_be_mapped': len(mapping) > 0,
        }
        
        results.append(result)
        
        logger.info(f"Image {image_id}: "
                   f"{populated_fields}/12 fields populated, "
                   f"pattern: {pattern_info['recommended_pattern']}, "
                   f"can map: {result['can_be_mapped']}")
    
    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze CDN mapping requirements for PROD entities"
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
        help="Analyze S3 bucket structure",
    )
    parser.add_argument(
        "--bucket-limit",
        type=int,
        default=1000,
        help="Limit S3 analysis to N objects (default: 1000)",
    )
    
    args = parser.parse_args()
    
    # Get configuration from Pulumi
    from pulumi import automation as auto
    
    work_dir = os.path.join(parent_dir, "infra")
    
    logger.info("Getting PROD configuration...")
    prod_stack = auto.create_or_select_stack(
        stack_name="tnorlund/portfolio/prod",
        work_dir=work_dir,
    )
    prod_outputs = prod_stack.outputs()
    prod_table = prod_outputs["dynamodb_table_name"].value
    cdn_bucket = prod_outputs["cdn_bucket_name"].value
    
    logger.info(f"PROD table: {prod_table}")
    logger.info(f"CDN bucket: {cdn_bucket}")
    
    if args.analyze_bucket:
        logger.info("\n" + "=" * 60)
        logger.info("S3 BUCKET STRUCTURE ANALYSIS")
        logger.info("=" * 60)
        bucket_analysis = analyze_s3_structure(cdn_bucket, args.bucket_limit)
        
        logger.info(f"Total objects analyzed: {bucket_analysis['total_objects']}")
        logger.info(f"Flat files: {bucket_analysis['flat_files']}")
        logger.info(f"Directory files: {bucket_analysis['dir_files']}")
        logger.info(f"Image IDs with flat pattern: {bucket_analysis['image_ids_with_flat']}")
        logger.info(f"Image IDs with directory pattern: {bucket_analysis['image_ids_with_dir']}")
        logger.info(f"Images with both patterns: {bucket_analysis['both_patterns']}")
        logger.info(f"Images with only flat pattern: {bucket_analysis['only_flat']}")
        logger.info(f"Images with only directory pattern: {bucket_analysis['only_dir']}")
        
        logger.info("\nFlat file samples:")
        for sample in bucket_analysis['flat_sample']:
            logger.info(f"  {sample}")
        
        logger.info("\nDirectory file samples:")
        for sample in bucket_analysis['dir_sample']:
            logger.info(f"  {sample}")
    
    logger.info("\n" + "=" * 60)
    logger.info("PROD ENTITY CDN MAPPING ANALYSIS")
    logger.info("=" * 60)
    
    # Analyze PROD entities
    entity_results = analyze_prod_entities(prod_table, cdn_bucket, args.sample_size)
    
    # Summary statistics
    total_entities = len(entity_results)
    can_be_mapped = sum(1 for r in entity_results if r['can_be_mapped'])
    flat_pattern = sum(1 for r in entity_results if r['pattern_info']['recommended_pattern'] == 'flat')
    dir_pattern = sum(1 for r in entity_results if r['pattern_info']['recommended_pattern'] == 'dir')
    no_pattern = sum(1 for r in entity_results if r['pattern_info']['recommended_pattern'] is None)
    
    logger.info(f"\nSUMMARY:")
    logger.info(f"Total entities analyzed: {total_entities}")
    logger.info(f"Can be mapped to CDN: {can_be_mapped} ({can_be_mapped/total_entities*100:.1f}%)")
    logger.info(f"Recommended flat pattern: {flat_pattern}")
    logger.info(f"Recommended directory pattern: {dir_pattern}")
    logger.info(f"No CDN files found: {no_pattern}")
    
    # Show detailed results for first few entities
    logger.info(f"\nDetailed mapping for first 3 entities:")
    for i, result in enumerate(entity_results[:3]):
        logger.info(f"\n--- Entity {i+1}: {result['image_id']} ---")
        logger.info(f"Current CDN fields: {result['current_cdn_fields_populated']}/12")
        logger.info(f"Recommended pattern: {result['pattern_info']['recommended_pattern']}")
        logger.info(f"Flat files found: {len(result['pattern_info']['flat_files'])}")
        logger.info(f"Directory files found: {len(result['pattern_info']['dir_files'])}")
        
        if result['recommended_mapping']:
            logger.info("Recommended CDN field mapping:")
            for field, value in list(result['recommended_mapping'].items())[:5]:  # Show first 5
                logger.info(f"  {field}: {value}")
            if len(result['recommended_mapping']) > 5:
                logger.info(f"  ... and {len(result['recommended_mapping']) - 5} more fields")


if __name__ == "__main__":
    main()