#!/usr/bin/env python3
"""
Export all images from DynamoDB, compute hashes, and find duplicates.

This script:
1. Lists all images from DynamoDB
2. Downloads each image from S3 to local storage
3. Computes SHA256 hashes (or uses stored sha256 if available)
4. Groups images by hash to identify duplicates
5. Tracks receipt word labels for each duplicate image
6. Generates a comprehensive report

Usage:
    # Analyze dev environment
    python scripts/find_duplicate_images.py --stack dev --output-dir duplicate_analysis

    # Analyze prod environment
    python scripts/find_duplicate_images.py --stack prod --output-dir duplicate_analysis
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import boto3
from tqdm import tqdm

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


@dataclass
class ImageInfo:
    """Information about an image and its associated data."""

    image_id: str
    sha256: Optional[str]
    computed_sha256: Optional[str]
    raw_s3_bucket: str
    raw_s3_key: str
    width: int
    height: int
    timestamp_added: str
    image_type: str
    receipt_ids: List[int]
    receipt_word_label_count: int
    local_path: Optional[str] = None
    download_error: Optional[str] = None
    hash_error: Optional[str] = None


@dataclass
class DuplicateGroup:
    """A group of duplicate images with the same hash."""

    sha256: str
    image_ids: List[str]
    images: List[ImageInfo]
    total_receipt_word_labels: int
    unique_receipt_ids: Set[int]


def get_table_name(stack: str) -> str:
    """Get the DynamoDB table name from Pulumi stack."""
    logger.info(f"Getting {stack.upper()} configuration from Pulumi...")

    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(
            f"Could not find dynamodb_table_name in Pulumi {stack} stack outputs"
        )

    logger.info(f"{stack.upper()} table: {table_name}")
    return table_name


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def download_image_from_s3(
    s3_client, bucket: str, key: str, local_path: Path
) -> bool:
    """Download an image from S3 to local path."""
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        s3_client.download_file(bucket, key, str(local_path))
        return True
    except Exception as e:
        logger.error(f"Failed to download {bucket}/{key}: {e}")
        return False


def get_image_info(
    dynamo_client: DynamoClient,
    image,
    images_dir: Path,
    s3_client,
    download_images: bool = True,
) -> ImageInfo:
    """Get comprehensive information about an image."""
    image_id = image.image_id

    # Get image details to find receipt word labels
    try:
        details = dynamo_client.get_image_details(image_id)
        receipt_ids = [r.receipt_id for r in details.receipts]
        receipt_word_label_count = len(details.receipt_word_labels)
    except Exception as e:
        logger.warning(f"Failed to get details for {image_id}: {e}")
        receipt_ids = []
        receipt_word_label_count = 0

    # Download image if requested
    local_path = None
    download_error = None
    computed_sha256 = None
    hash_error = None

    if download_images:
        local_path = images_dir / f"{image_id}.png"
        if not local_path.exists():
            if not download_image_from_s3(
                s3_client, image.raw_s3_bucket, image.raw_s3_key, local_path
            ):
                download_error = "Download failed"
        else:
            logger.debug(f"Image {image_id} already downloaded")

        # Compute hash from downloaded file
        if local_path and local_path.exists():
            try:
                computed_sha256 = compute_sha256(local_path)
            except Exception as e:
                hash_error = f"Hash computation failed: {e}"

    return ImageInfo(
        image_id=image_id,
        sha256=image.sha256,
        computed_sha256=computed_sha256,
        raw_s3_bucket=image.raw_s3_bucket,
        raw_s3_key=image.raw_s3_key,
        width=image.width,
        height=image.height,
        timestamp_added=str(image.timestamp_added),
        image_type=str(image.image_type),
        receipt_ids=receipt_ids,
        receipt_word_label_count=receipt_word_label_count,
        local_path=str(local_path) if local_path else None,
        download_error=download_error,
        hash_error=hash_error,
    )


def analyze_duplicates(
    table_name: str,
    output_dir: str,
    download_images: bool = True,
    limit: Optional[int] = None,
) -> Dict:
    """
    Analyze images for duplicates.

    Args:
        table_name: DynamoDB table name
        output_dir: Directory to save results
        download_images: Whether to download images from S3
        limit: Optional limit on number of images to process

    Returns:
        Dictionary with analysis results
    """
    client = DynamoClient(table_name)
    s3_client = boto3.client("s3")

    # Create output directories
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    images_dir = output_path / "images"
    images_dir.mkdir(exist_ok=True)

    logger.info(f"Output directory: {output_path.absolute()}")

    # Statistics
    stats = {
        "total_images": 0,
        "processed": 0,
        "failed": 0,
        "with_stored_hash": 0,
        "with_computed_hash": 0,
        "download_errors": 0,
        "hash_errors": 0,
    }

    # Store image info by hash
    images_by_hash: Dict[str, List[ImageInfo]] = defaultdict(list)
    all_image_info: Dict[str, ImageInfo] = {}

    # List all images
    logger.info("Listing all images from DynamoDB...")
    last_evaluated_key = None

    with tqdm(desc="Fetching images", unit="pages") as pbar:
        while True:
            try:
                images, last_evaluated_key = client.list_images(
                    limit=500,  # Process in batches
                    last_evaluated_key=last_evaluated_key,
                )

                if not images:
                    break

                stats["total_images"] += len(images)
                pbar.update(1)
                pbar.set_postfix({"total": stats["total_images"]})

                for image in images:
                    if limit and stats["processed"] >= limit:
                        break

                    try:
                        image_info = get_image_info(
                            client, image, images_dir, s3_client, download_images
                        )
                        all_image_info[image_info.image_id] = image_info
                        stats["processed"] += 1

                        # Track hash
                        hash_to_use = None
                        if image_info.sha256:
                            hash_to_use = image_info.sha256
                            stats["with_stored_hash"] += 1
                        elif image_info.computed_sha256:
                            hash_to_use = image_info.computed_sha256
                            stats["with_computed_hash"] += 1

                        if hash_to_use:
                            images_by_hash[hash_to_use].append(image_info)
                        else:
                            logger.warning(
                                f"No hash available for {image_info.image_id}"
                            )

                        if image_info.download_error:
                            stats["download_errors"] += 1
                        if image_info.hash_error:
                            stats["hash_errors"] += 1

                        if stats["processed"] % 10 == 0:
                            logger.info(
                                f"Processed {stats['processed']} images "
                                f"(failed: {stats['failed']})"
                            )

                    except Exception as e:
                        stats["failed"] += 1
                        error_msg = f"Failed to process {image.image_id}: {e}"
                        logger.error(error_msg, exc_info=True)

                if limit and stats["processed"] >= limit:
                    break

                if not last_evaluated_key:
                    break

            except Exception as e:
                logger.error(f"Error listing images: {e}", exc_info=True)
                break

    # Find duplicates (groups with more than one image)
    duplicate_groups: List[DuplicateGroup] = []
    for sha256, images_list in images_by_hash.items():
        if len(images_list) > 1:
            all_receipt_ids = set()
            total_labels = 0
            for img_info in images_list:
                all_receipt_ids.update(img_info.receipt_ids)
                total_labels += img_info.receipt_word_label_count

            duplicate_groups.append(
                DuplicateGroup(
                    sha256=sha256,
                    image_ids=[img.image_id for img in images_list],
                    images=images_list,
                    total_receipt_word_labels=total_labels,
                    unique_receipt_ids=all_receipt_ids,
                )
            )

    # Sort duplicate groups by number of duplicates (descending)
    duplicate_groups.sort(key=lambda g: len(g.image_ids), reverse=True)

    # Generate report
    report = {
        "statistics": stats,
        "total_unique_hashes": len(images_by_hash),
        "total_duplicate_groups": len(duplicate_groups),
        "total_duplicate_images": sum(len(g.image_ids) for g in duplicate_groups),
        "duplicate_groups": [
            {
                "sha256": g.sha256,
                "duplicate_count": len(g.image_ids),
                "image_ids": g.image_ids,
                "total_receipt_word_labels": g.total_receipt_word_labels,
                "unique_receipt_ids": sorted(list(g.unique_receipt_ids)),
                "images": [
                    {
                        "image_id": img.image_id,
                        "receipt_ids": img.receipt_ids,
                        "receipt_word_label_count": img.receipt_word_label_count,
                        "timestamp_added": img.timestamp_added,
                        "image_type": img.image_type,
                        "width": img.width,
                        "height": img.height,
                        "raw_s3_key": img.raw_s3_key,
                        "download_error": img.download_error,
                        "hash_error": img.hash_error,
                    }
                    for img in g.images
                ],
            }
            for g in duplicate_groups
        ],
    }

    # Save report
    report_path = output_path / "duplicate_analysis_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"Report saved to {report_path}")

    # Save summary
    summary_path = output_path / "duplicate_summary.txt"
    with open(summary_path, "w") as f:
        f.write("=" * 80 + "\n")
        f.write("DUPLICATE IMAGE ANALYSIS SUMMARY\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total images processed: {stats['processed']}\n")
        f.write(f"Total unique hashes: {len(images_by_hash)}\n")
        f.write(f"Total duplicate groups: {len(duplicate_groups)}\n")
        f.write(
            f"Total duplicate images: {sum(len(g.image_ids) for g in duplicate_groups)}\n"
        )
        f.write(f"\nImages with stored hash: {stats['with_stored_hash']}\n")
        f.write(f"Images with computed hash: {stats['with_computed_hash']}\n")
        f.write(f"Download errors: {stats['download_errors']}\n")
        f.write(f"Hash errors: {stats['hash_errors']}\n")
        f.write("\n" + "=" * 80 + "\n")
        f.write("DUPLICATE GROUPS (sorted by count)\n")
        f.write("=" * 80 + "\n\n")

        for i, group in enumerate(duplicate_groups, 1):
            f.write(f"\nGroup {i}: {len(group.image_ids)} duplicates\n")
            f.write(f"  SHA256: {group.sha256}\n")
            f.write(f"  Total receipt word labels: {group.total_receipt_word_labels}\n")
            f.write(f"  Unique receipt IDs: {len(group.unique_receipt_ids)}\n")
            f.write(f"  Image IDs:\n")
            for img in group.images:
                f.write(f"    - {img.image_id}\n")
                f.write(f"      Receipt IDs: {img.receipt_ids}\n")
                f.write(f"      Labels: {img.receipt_word_label_count}\n")
                f.write(f"      Added: {img.timestamp_added}\n")
                if img.download_error:
                    f.write(f"      ⚠️  Download error: {img.download_error}\n")
                if img.hash_error:
                    f.write(f"      ⚠️  Hash error: {img.hash_error}\n")

    logger.info(f"Summary saved to {summary_path}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Find duplicate images in DynamoDB by comparing SHA256 hashes"
    )
    parser.add_argument(
        "--stack",
        required=True,
        choices=["dev", "prod"],
        help="Pulumi stack name (dev or prod)",
    )
    parser.add_argument(
        "--output-dir",
        default="duplicate_analysis",
        help="Output directory for results (default: duplicate_analysis)",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Skip downloading images (use stored hashes only)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of images to process (for testing)",
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
        # Get table name from Pulumi
        table_name = get_table_name(args.stack)

        # Analyze duplicates
        logger.info(f"Starting duplicate analysis for {args.stack.upper()} stack...")
        report = analyze_duplicates(
            table_name=table_name,
            output_dir=args.output_dir,
            download_images=not args.no_download,
            limit=args.limit,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("ANALYSIS SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total images processed: {report['statistics']['processed']}")
        logger.info(f"Total unique hashes: {report['total_unique_hashes']}")
        logger.info(f"Total duplicate groups: {report['total_duplicate_groups']}")
        logger.info(f"Total duplicate images: {report['total_duplicate_images']}")
        logger.info(f"Output directory: {Path(args.output_dir).absolute()}")

        if report["duplicate_groups"]:
            logger.info("\nTop 10 duplicate groups:")
            for i, group in enumerate(report["duplicate_groups"][:10], 1):
                logger.info(
                    f"  {i}. {group['duplicate_count']} duplicates "
                    f"({group['total_receipt_word_labels']} labels, "
                    f"{len(group['unique_receipt_ids'])} receipts)"
                )
        else:
            logger.info("\n✅ No duplicates found!")

        logger.info("\n✅ Analysis completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


