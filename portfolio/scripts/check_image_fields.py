#!/usr/bin/env python3
"""Quick script to check what fields exist on images in DynamoDB."""

import os
import sys

# Add parent directories to path
script_dir = os.path.dirname(os.path.abspath(__file__))
portfolio_root = os.path.dirname(script_dir)
parent_dir = os.path.dirname(portfolio_root)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data.dynamo_client import DynamoClient

# Get configuration from environment or hardcode for dev
DYNAMO_TABLE_NAME = "ReceiptsTable-dc5be22"

# Create client
client = DynamoClient(DYNAMO_TABLE_NAME)

# Get a few images
print("Fetching a few images to check their fields...")
images, _ = client.list_images_by_type(
    image_type=ImageType.PHOTO,
    limit=3,
)

for i, image in enumerate(images):
    print(f"\n--- Image {i+1} ---")
    print(f"Image ID: {image.image_id}")
    print(f"Width: {image.width}, Height: {image.height}")
    
    # Check thumbnail fields
    item = image.to_item()
    thumbnail_fields = {
        "cdn_thumbnail_s3_key": item.get("cdn_thumbnail_s3_key"),
        "cdn_thumbnail_webp_s3_key": item.get("cdn_thumbnail_webp_s3_key"),
        "cdn_thumbnail_avif_s3_key": item.get("cdn_thumbnail_avif_s3_key"),
        "cdn_small_s3_key": item.get("cdn_small_s3_key"),
        "cdn_small_webp_s3_key": item.get("cdn_small_webp_s3_key"),
        "cdn_small_avif_s3_key": item.get("cdn_small_avif_s3_key"),
        "cdn_medium_s3_key": item.get("cdn_medium_s3_key"),
        "cdn_medium_webp_s3_key": item.get("cdn_medium_webp_s3_key"),
        "cdn_medium_avif_s3_key": item.get("cdn_medium_avif_s3_key"),
    }
    
    print("\nThumbnail fields:")
    for field, value in thumbnail_fields.items():
        if value:
            print(f"  ✓ {field}: {value}")
        else:
            print(f"  ✗ {field}: None or missing")