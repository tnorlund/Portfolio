#!/usr/bin/env python3
"""Verify that thumbnails were created correctly."""

import os
import sys

# Add parent directories to path
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient

# Get configuration
DYNAMO_TABLE_NAME = "ReceiptsTable-dc5be22"

# Create client
client = DynamoClient(DYNAMO_TABLE_NAME)

# Get specific images we just processed
image_ids = ["0a345fdd-ecc3-4577-b3d3-869140451f43", "53500759-d95a-4225-911e-18f5cc45d09a"]

for image_id in image_ids:
    print(f"\n--- Verifying Image {image_id} ---")
    image = client.get_image(image_id)
    
    # Check thumbnail fields
    print(f"Original: {image.cdn_s3_key}")
    print(f"\nThumbnail sizes:")
    print(f"  Thumbnail JPEG: {image.cdn_thumbnail_s3_key}")
    print(f"  Thumbnail WebP: {image.cdn_thumbnail_webp_s3_key}")
    print(f"  Thumbnail AVIF: {image.cdn_thumbnail_avif_s3_key}")
    print(f"\nSmall sizes:")
    print(f"  Small JPEG: {image.cdn_small_s3_key}")
    print(f"  Small WebP: {image.cdn_small_webp_s3_key}")
    print(f"  Small AVIF: {image.cdn_small_avif_s3_key}")
    print(f"\nMedium sizes:")
    print(f"  Medium JPEG: {image.cdn_medium_s3_key}")
    print(f"  Medium WebP: {image.cdn_medium_webp_s3_key}")
    print(f"  Medium AVIF: {image.cdn_medium_avif_s3_key}")