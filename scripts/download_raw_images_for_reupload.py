#!/usr/bin/env python3
"""
Download raw images from S3 for re-upload.

This script downloads the raw images for receipts that need proper OCR processing.

Usage:
    python scripts/download_raw_images_for_reupload.py --stack dev --output-dir raw_images_to_reupload
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_table_name(stack: str) -> str:
    """Get the DynamoDB table name from Pulumi stack."""
    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")
    if not table_name:
        raise ValueError(
            f"Could not find dynamodb_table_name in Pulumi {stack} stack outputs"
        )
    return table_name


def download_image_from_s3(
    s3_client, bucket: str, key: str, local_path: Path
) -> bool:
    """Download an image from S3."""
    try:
        logger.info(f"Downloading s3://{bucket}/{key} to {local_path}")
        s3_client.download_file(bucket, key, str(local_path))
        return True
    except Exception as e:
        logger.error(f"Failed to download {bucket}/{key}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Download raw images from S3 for re-upload"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--output-dir",
        default="raw_images_to_reupload",
        help="Output directory for downloaded images",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        # Images that need re-processing
        images_to_download = [
            "abaec508-6730-4d75-9d48-76492a26a168",
            "cb22100f-44c2-4b7d-b29f-46627a64355a",
        ]

        table_name = get_table_name(args.stack)
        client = DynamoClient(table_name)

        # Create output directory
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize S3 client
        s3_client = boto3.client("s3")

        logger.info(f"Downloading {len(images_to_download)} raw images...\n")

        downloaded = []
        failed = []

        for image_id in images_to_download:
            try:
                # Get image details
                image = client.get_image(image_id)
                logger.info(f"Image {image_id}:")
                logger.info(f"  S3 Bucket: {image.raw_s3_bucket}")
                logger.info(f"  S3 Key: {image.raw_s3_key}")
                logger.info(f"  Image Type: {image.image_type}")
                logger.info(f"  Dimensions: {image.width}x{image.height}")

                # Determine file extension from S3 key
                s3_key_lower = image.raw_s3_key.lower()
                if s3_key_lower.endswith(".png"):
                    ext = ".png"
                elif s3_key_lower.endswith(".jpg") or s3_key_lower.endswith(
                    ".jpeg"
                ):
                    ext = ".jpg"
                else:
                    ext = ".png"  # Default

                # Download image
                local_filename = f"{image_id}{ext}"
                local_path = output_dir / local_filename

                if download_image_from_s3(
                    s3_client, image.raw_s3_bucket, image.raw_s3_key, local_path
                ):
                    file_size = local_path.stat().st_size
                    logger.info(
                        f"  ✅ Downloaded to {local_path} ({file_size:,} bytes)"
                    )
                    downloaded.append(
                        {
                            "image_id": image_id,
                            "local_path": str(local_path),
                            "s3_bucket": image.raw_s3_bucket,
                            "s3_key": image.raw_s3_key,
                            "image_type": image.image_type,
                            "width": image.width,
                            "height": image.height,
                        }
                    )
                else:
                    failed.append(image_id)
                    logger.error(f"  ❌ Failed to download {image_id}")

            except Exception as e:
                logger.error(f"Error processing {image_id}: {e}", exc_info=True)
                failed.append(image_id)

            logger.info("")

        # Summary
        logger.info("=" * 80)
        logger.info("DOWNLOAD SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Successfully downloaded: {len(downloaded)}")
        logger.info(f"Failed: {len(failed)}")
        logger.info(f"\nImages saved to: {output_dir}")

        if downloaded:
            logger.info("\n✅ Downloaded images:")
            for img in downloaded:
                logger.info(f"  - {img['local_path']}")
                logger.info(
                    f"    Image ID: {img['image_id']}, Type: {img['image_type']}"
                )

        if failed:
            logger.warning("\n❌ Failed to download:")
            for image_id in failed:
                logger.warning(f"  - {image_id}")

        logger.info(
            f"\n💡 Next steps: Re-upload these images through your upload lambda"
        )
        logger.info(f"   to trigger proper OCR processing for the receipt regions.")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


