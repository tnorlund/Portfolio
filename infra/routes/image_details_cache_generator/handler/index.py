"""Lambda handler for generating image details cache.

Pre-generates a pool of image details for SCAN and PHOTO types,
enabling fast client-side random selection without real-time DynamoDB queries.
"""

import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ImageType

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_CACHE_BUCKET = os.environ["S3_CACHE_BUCKET"]

# Configuration
POOL_SIZE = 20  # Number of images to cache per type
QUERY_LIMIT = 100  # Max images to query before random selection

# Initialize clients
s3_client = boto3.client("s3")


def image_to_dict(image):
    """Convert Image entity to dict for JSON serialization."""
    return {
        "image_id": image.image_id,
        "width": image.width,
        "height": image.height,
        "timestamp_added": str(image.timestamp_added),
        "raw_s3_bucket": image.raw_s3_bucket,
        "raw_s3_key": image.raw_s3_key,
        "sha256": image.sha256,
        "cdn_s3_bucket": getattr(image, "cdn_s3_bucket", None),
        "cdn_s3_key": getattr(image, "cdn_s3_key", None),
        "cdn_webp_s3_key": getattr(image, "cdn_webp_s3_key", None),
        "cdn_avif_s3_key": getattr(image, "cdn_avif_s3_key", None),
        "cdn_thumbnail_s3_key": getattr(image, "cdn_thumbnail_s3_key", None),
        "cdn_thumbnail_webp_s3_key": getattr(
            image, "cdn_thumbnail_webp_s3_key", None
        ),
        "cdn_thumbnail_avif_s3_key": getattr(
            image, "cdn_thumbnail_avif_s3_key", None
        ),
        "cdn_small_s3_key": getattr(image, "cdn_small_s3_key", None),
        "cdn_small_webp_s3_key": getattr(image, "cdn_small_webp_s3_key", None),
        "cdn_small_avif_s3_key": getattr(image, "cdn_small_avif_s3_key", None),
        "cdn_medium_s3_key": getattr(image, "cdn_medium_s3_key", None),
        "cdn_medium_webp_s3_key": getattr(
            image, "cdn_medium_webp_s3_key", None
        ),
        "cdn_medium_avif_s3_key": getattr(
            image, "cdn_medium_avif_s3_key", None
        ),
        "image_type": getattr(image, "image_type", None),
    }


def line_to_dict(line):
    """Convert ReceiptLine entity to dict for JSON serialization."""
    return {
        "image_id": line.image_id,
        "line_id": line.line_id,
        "text": line.text,
        "bounding_box": line.bounding_box,
        "top_left": line.top_left,
        "top_right": line.top_right,
        "bottom_left": line.bottom_left,
        "bottom_right": line.bottom_right,
        "angle_degrees": getattr(line, "angle_degrees", 0),
        "angle_radians": getattr(line, "angle_radians", 0),
        "confidence": getattr(line, "confidence", 1.0),
    }


def receipt_to_dict(receipt):
    """Convert Receipt entity to dict for JSON serialization."""
    return {
        "image_id": receipt.image_id,
        "receipt_id": receipt.receipt_id,
        "width": receipt.width,
        "height": receipt.height,
        "timestamp_added": str(receipt.timestamp_added),
        "raw_s3_bucket": receipt.raw_s3_bucket,
        "raw_s3_key": receipt.raw_s3_key,
        "top_left": receipt.top_left,
        "top_right": receipt.top_right,
        "bottom_left": receipt.bottom_left,
        "bottom_right": receipt.bottom_right,
        "sha256": receipt.sha256,
        "cdn_s3_bucket": getattr(receipt, "cdn_s3_bucket", None),
        "cdn_s3_key": getattr(receipt, "cdn_s3_key", None),
        "cdn_webp_s3_key": getattr(receipt, "cdn_webp_s3_key", None),
        "cdn_avif_s3_key": getattr(receipt, "cdn_avif_s3_key", None),
        "cdn_thumbnail_s3_key": getattr(receipt, "cdn_thumbnail_s3_key", None),
        "cdn_thumbnail_webp_s3_key": getattr(
            receipt, "cdn_thumbnail_webp_s3_key", None
        ),
        "cdn_thumbnail_avif_s3_key": getattr(
            receipt, "cdn_thumbnail_avif_s3_key", None
        ),
        "cdn_small_s3_key": getattr(receipt, "cdn_small_s3_key", None),
        "cdn_small_webp_s3_key": getattr(
            receipt, "cdn_small_webp_s3_key", None
        ),
        "cdn_small_avif_s3_key": getattr(
            receipt, "cdn_small_avif_s3_key", None
        ),
        "cdn_medium_s3_key": getattr(receipt, "cdn_medium_s3_key", None),
        "cdn_medium_webp_s3_key": getattr(
            receipt, "cdn_medium_webp_s3_key", None
        ),
        "cdn_medium_avif_s3_key": getattr(
            receipt, "cdn_medium_avif_s3_key", None
        ),
    }


def generate_cache_for_type(dynamo_client, image_type: ImageType) -> dict:
    """Generate cache for a single image type.

    Args:
        dynamo_client: DynamoClient instance
        image_type: ImageType.SCAN or ImageType.PHOTO

    Returns:
        dict with images array and metadata
    """
    # Determine target receipt count based on type
    target_receipt_count = 2 if image_type == ImageType.SCAN else 1

    logger.info(
        "Querying %s images with %d receipts",
        image_type.value,
        target_receipt_count,
    )

    # Query for images (limit to QUERY_LIMIT)
    images, _ = dynamo_client.list_images_by_type(
        image_type=image_type,
        receipt_count=target_receipt_count,
        limit=QUERY_LIMIT,
    )

    if not images:
        logger.warning(
            "No %s images found with %d receipts",
            image_type.value,
            target_receipt_count,
        )
        return {
            "images": [],
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

    logger.info(
        "Found %d %s images, selecting %d for cache",
        len(images),
        image_type.value,
        min(POOL_SIZE, len(images)),
    )

    # Randomly select POOL_SIZE images
    selected_images = random.sample(images, min(POOL_SIZE, len(images)))

    # Fetch full details for each selected image in parallel
    def fetch_details(image):
        try:
            details = dynamo_client.get_image_details(image.image_id)
            image_dict = (
                image_to_dict(details.images[0]) if details.images else None
            )
            lines_dict = [line_to_dict(line) for line in details.lines]
            receipts_dict = [
                receipt_to_dict(receipt) for receipt in details.receipts
            ]

            return {
                "image": image_dict,
                "lines": lines_dict,
                "receipts": receipts_dict,
            }
        except Exception as e:
            logger.warning(
                "Error fetching details for %s: %s", image.image_id, e
            )
            return None

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_details, img): img for img in selected_images
        }
        for future in as_completed(futures):
            result = future.result()
            if result and result["image"]:
                results.append(result)

    logger.info(
        "Successfully cached %d %s images", len(results), image_type.value
    )

    return {
        "images": results,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }


def handler(_event, _context):
    """Handle EventBridge scheduled event to generate image details cache."""
    logger.info("Starting image details cache generation")

    try:
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Generate cache for both SCAN and PHOTO types
        for image_type in [ImageType.SCAN, ImageType.PHOTO]:
            cache_data = generate_cache_for_type(dynamo_client, image_type)

            # Upload to S3
            cache_key = f"image-details-cache/{image_type.value}.json"
            logger.info(
                "Uploading %s cache to S3: %s/%s (%d images)",
                image_type.value,
                S3_CACHE_BUCKET,
                cache_key,
                len(cache_data["images"]),
            )

            s3_client.put_object(
                Bucket=S3_CACHE_BUCKET,
                Key=cache_key,
                Body=json.dumps(cache_data, default=str),
                ContentType="application/json",
            )

        logger.info("Cache generation complete")

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cache generated successfully",
                }
            ),
        }

    except Exception as e:
        logger.error("Error generating cache: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
