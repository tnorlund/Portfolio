#!/usr/bin/env python3
"""
Script to reconstruct corrupted entities from S3 objects.

This script:
1. Finds corrupted entities (those with TYPE but missing required fields)
2. Checks if corresponding S3 objects exist
3. Reconstructs the entity metadata from the S3 object
4. Updates the DynamoDB entity with correct data
"""

import argparse
import logging
import os
import sys
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import boto3
from PIL import Image
import io

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


class EntityReconstructor:
    """Reconstructs corrupted entities from S3 objects."""

    def __init__(
        self, table_name: str, raw_bucket: str, cdn_bucket: str, dry_run: bool = True
    ):
        self.table_name = table_name
        self.raw_bucket = raw_bucket
        self.cdn_bucket = cdn_bucket
        self.dry_run = dry_run

        self.dynamodb = boto3.resource("dynamodb")
        self.s3 = boto3.client("s3")
        self.table = self.dynamodb.Table(table_name)

        self.stats = {
            "corrupted_found": 0,
            "s3_objects_found": 0,
            "entities_reconstructed": 0,
            "entities_updated": 0,
            "errors": 0,
        }

    def find_corrupted_image_entities(self) -> List[Dict[str, Any]]:
        """Find corrupted IMAGE entities (faster than scanning all)."""
        logger.info("Finding corrupted IMAGE entities...")

        corrupted_images = []

        # Scan for IMAGE entities with TYPE but missing required fields
        scan_kwargs = {
            "FilterExpression": "#type = :image_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)",
            "ExpressionAttributeNames": {"#type": "TYPE"},
            "ExpressionAttributeValues": {
                ":image_type": "IMAGE",
                ":pk_prefix": "IMAGE#",
                ":sk_prefix": "IMAGE#",
            },
        }

        done = False
        start_key = None

        while not done:
            if start_key:
                scan_kwargs["ExclusiveStartKey"] = start_key

            response = self.table.scan(**scan_kwargs)
            items = response.get("Items", [])

            for item in items:
                # Check if Image entity is corrupted (missing required fields)
                required_fields = [
                    "width",
                    "height",
                    "timestamp_added",
                    "raw_s3_bucket",
                    "raw_s3_key",
                    "image_type",
                ]
                missing_fields = [
                    field for field in required_fields if field not in item
                ]

                if missing_fields:
                    corrupted_images.append(
                        {
                            "item": item,
                            "missing_fields": missing_fields,
                            "image_id": item["PK"].replace("IMAGE#", ""),
                        }
                    )
                    self.stats["corrupted_found"] += 1

            start_key = response.get("LastEvaluatedKey", None)
            done = start_key is None

            if len(corrupted_images) % 25 == 0 and len(corrupted_images) > 0:
                logger.info(
                    f"Found {len(corrupted_images)} corrupted IMAGE entities so far..."
                )

        logger.info(f"Found {len(corrupted_images)} corrupted IMAGE entities total")
        return corrupted_images

    def find_s3_object_for_image(self, image_id: str) -> Optional[Dict[str, Any]]:
        """Find the S3 object for a given image ID."""
        possible_extensions = ["jpg", "jpeg", "png", "tiff", "tif", "webp"]

        for ext in possible_extensions:
            s3_key = f"{image_id}.{ext}"
            try:
                response = self.s3.head_object(Bucket=self.raw_bucket, Key=s3_key)
                return {
                    "bucket": self.raw_bucket,
                    "key": s3_key,
                    "size": response["ContentLength"],
                    "last_modified": response["LastModified"],
                    "content_type": response.get("ContentType", f"image/{ext}"),
                }
            except self.s3.exceptions.ClientError as e:
                if e.response["Error"]["Code"] != "404":
                    logger.warning(f"Error checking S3 object {s3_key}: {e}")
                continue

        return None

    def get_image_dimensions(
        self, bucket: str, key: str
    ) -> Tuple[Optional[int], Optional[int]]:
        """Get image dimensions from S3 object."""
        try:
            # Download just enough bytes to get image header
            response = self.s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-8192")
            image_data = response["Body"].read()

            # Use PIL to get dimensions
            with Image.open(io.BytesIO(image_data)) as img:
                return img.width, img.height

        except Exception as e:
            logger.warning(f"Could not get dimensions for {key}: {e}")
            return None, None

    def reconstruct_image_entity(
        self, corrupted_image: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Reconstruct a single IMAGE entity from S3 data."""
        image_id = corrupted_image["image_id"]
        current_item = corrupted_image["item"]

        # Find S3 object
        s3_info = self.find_s3_object_for_image(image_id)
        if not s3_info:
            logger.warning(f"No S3 object found for image: {image_id}")
            return None

        self.stats["s3_objects_found"] += 1

        # Get image dimensions
        width, height = self.get_image_dimensions(s3_info["bucket"], s3_info["key"])
        if not width or not height:
            logger.warning(f"Could not determine dimensions for: {image_id}")
            return None

        # Determine image type from extension
        extension = s3_info["key"].split(".")[-1].lower()
        image_type_map = {
            "jpg": "JPEG",
            "jpeg": "JPEG",
            "png": "PNG",
            "tiff": "TIFF",
            "tif": "TIFF",
            "webp": "WEBP",
        }
        image_type = image_type_map.get(extension, extension.upper())

        # Build reconstructed entity
        reconstructed = dict(current_item)  # Start with current data

        # Add missing required fields
        reconstructed.update(
            {
                "width": width,
                "height": height,
                "timestamp_added": s3_info["last_modified"].isoformat(),
                "raw_s3_bucket": s3_info["bucket"],
                "raw_s3_key": s3_info["key"],
                "image_type": image_type,
            }
        )

        # Add other standard fields if missing
        if "site_s3_bucket" not in reconstructed:
            reconstructed["site_s3_bucket"] = self.cdn_bucket

        if "site_s3_key" not in reconstructed:
            reconstructed["site_s3_key"] = s3_info["key"]

        # Add CDN fields (initially null)
        cdn_fields = [
            "cdn_thumbnail_s3_key",
            "cdn_thumbnail_webp_s3_key",
            "cdn_thumbnail_avif_s3_key",
            "cdn_small_s3_key",
            "cdn_small_webp_s3_key",
            "cdn_small_avif_s3_key",
            "cdn_medium_s3_key",
            "cdn_medium_webp_s3_key",
            "cdn_medium_avif_s3_key",
        ]
        for field in cdn_fields:
            if field not in reconstructed:
                reconstructed[field] = None

        logger.info(f"Reconstructed IMAGE {image_id}: {width}x{height} {image_type}")
        self.stats["entities_reconstructed"] += 1

        return reconstructed

    def update_entity(self, entity: Dict[str, Any]) -> bool:
        """Update a single entity in DynamoDB."""
        if self.dry_run:
            logger.debug(f"[DRY RUN] Would update {entity['PK']}, {entity['SK']}")
            self.stats["entities_updated"] += 1
            return True

        try:
            self.table.put_item(Item=entity)
            self.stats["entities_updated"] += 1
            return True
        except Exception as e:
            logger.error(f"Failed to update {entity['PK']}, {entity['SK']}: {e}")
            self.stats["errors"] += 1
            return False

    def run(self):
        """Run the complete reconstruction process."""
        logger.info("Starting entity reconstruction...")

        # Find corrupted IMAGE entities
        corrupted_images = self.find_corrupted_image_entities()

        if not corrupted_images:
            logger.info("No corrupted IMAGE entities found!")
            return

        # Process each corrupted image
        logger.info(
            f"Attempting to reconstruct {len(corrupted_images)} IMAGE entities..."
        )

        successful_reconstructions = 0

        for i, corrupted_image in enumerate(corrupted_images):
            if i % 10 == 0:
                logger.info(f"Progress: {i}/{len(corrupted_images)} entities processed")

            try:
                reconstructed = self.reconstruct_image_entity(corrupted_image)
                if reconstructed:
                    if self.update_entity(reconstructed):
                        successful_reconstructions += 1

            except Exception as e:
                logger.error(f"Error processing {corrupted_image['image_id']}: {e}")
                self.stats["errors"] += 1

        logger.info(
            f"Successfully reconstructed {successful_reconstructions} IMAGE entities"
        )

        # Now handle RECEIPT entities (they depend on IMAGE entities being fixed)
        self.reconstruct_receipt_entities()

        # Print summary
        self.print_summary()

    def reconstruct_receipt_entities(self):
        """Reconstruct RECEIPT entities based on their parent IMAGE entities."""
        logger.info("Reconstructing RECEIPT entities...")

        # Find corrupted RECEIPT entities
        scan_kwargs = {
            "FilterExpression": "#type = :receipt_type AND begins_with(PK, :pk_prefix) AND begins_with(SK, :sk_prefix)",
            "ExpressionAttributeNames": {"#type": "TYPE"},
            "ExpressionAttributeValues": {
                ":receipt_type": "RECEIPT",
                ":pk_prefix": "IMAGE#",
                ":sk_prefix": "RECEIPT#",
            },
        }

        receipt_count = 0
        done = False
        start_key = None

        while not done:
            if start_key:
                scan_kwargs["ExclusiveStartKey"] = start_key

            response = self.table.scan(**scan_kwargs)
            items = response.get("Items", [])

            for item in items:
                # Check if Receipt entity is corrupted
                required_fields = [
                    "width",
                    "height",
                    "timestamp_added",
                    "raw_s3_bucket",
                    "raw_s3_key",
                    "top_left",
                    "top_right",
                    "bottom_left",
                    "bottom_right",
                ]
                missing_fields = [
                    field for field in required_fields if field not in item
                ]

                if missing_fields:
                    # Try to get parent IMAGE entity data
                    image_pk = item["PK"]  # Same as IMAGE PK
                    image_sk = image_pk  # IMAGE SK is same as PK

                    try:
                        image_response = self.table.get_item(
                            Key={"PK": image_pk, "SK": image_sk}
                        )
                        if "Item" in image_response:
                            image_item = image_response["Item"]

                            # Build reconstructed receipt
                            reconstructed_receipt = dict(item)

                            # Copy dimensions and S3 info from parent image
                            for field in [
                                "width",
                                "height",
                                "timestamp_added",
                                "raw_s3_bucket",
                                "raw_s3_key",
                            ]:
                                if (
                                    field in image_item
                                    and field not in reconstructed_receipt
                                ):
                                    reconstructed_receipt[field] = image_item[field]

                            # Add default bounding box if missing (full image)
                            if "top_left" not in reconstructed_receipt:
                                reconstructed_receipt.update(
                                    {
                                        "top_left": [0, 0],
                                        "top_right": [image_item.get("width", 100), 0],
                                        "bottom_left": [
                                            0,
                                            image_item.get("height", 100),
                                        ],
                                        "bottom_right": [
                                            image_item.get("width", 100),
                                            image_item.get("height", 100),
                                        ],
                                    }
                                )

                            if self.update_entity(reconstructed_receipt):
                                receipt_count += 1
                                logger.debug(
                                    f"Reconstructed RECEIPT: {item['PK']}, {item['SK']}"
                                )

                    except Exception as e:
                        logger.error(
                            f"Error reconstructing receipt {item['PK']}, {item['SK']}: {e}"
                        )
                        self.stats["errors"] += 1

            start_key = response.get("LastEvaluatedKey", None)
            done = start_key is None

        logger.info(f"Reconstructed {receipt_count} RECEIPT entities")

    def print_summary(self):
        """Print summary of the reconstruction operation."""
        logger.info("\n" + "=" * 50)
        logger.info("ENTITY RECONSTRUCTION SUMMARY")
        logger.info("=" * 50)
        logger.info(f"Corrupted entities found: {self.stats['corrupted_found']}")
        logger.info(f"S3 objects found: {self.stats['s3_objects_found']}")
        logger.info(f"Entities reconstructed: {self.stats['entities_reconstructed']}")
        logger.info(f"Entities updated: {self.stats['entities_updated']}")
        logger.info(f"Errors: {self.stats['errors']}")

        if self.dry_run:
            logger.info("\nThis was a DRY RUN - no changes were made")
            logger.info("Run with --no-dry-run to apply changes")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Reconstruct corrupted entities from S3 objects"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually update the entities (default is dry run)",
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
    raw_bucket = prod_outputs["raw_bucket_name"].value
    cdn_bucket = prod_outputs["cdn_bucket_name"].value

    logger.info(f"PROD table: {prod_table}")
    logger.info(f"Raw bucket: {raw_bucket}")
    logger.info(f"CDN bucket: {cdn_bucket}")
    logger.info(f"Mode: {'LIVE UPDATE' if args.no_dry_run else 'DRY RUN'}")

    # Create and run reconstructor
    reconstructor = EntityReconstructor(
        table_name=prod_table,
        raw_bucket=raw_bucket,
        cdn_bucket=cdn_bucket,
        dry_run=not args.no_dry_run,
    )

    reconstructor.run()


if __name__ == "__main__":
    main()
