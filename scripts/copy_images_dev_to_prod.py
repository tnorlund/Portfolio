#!/usr/bin/env python3
"""
Copy images from dev S3 buckets to prod S3 buckets.

This script:
1. Copies raw images from dev raw bucket to prod raw bucket
2. Copies CDN images from dev CDN bucket to prod CDN bucket
3. Preserves the exact S3 key structure
4. Skips files that already exist in prod (idempotent)
5. Provides progress tracking and summary

Usage:
    # Dry run first
    python scripts/copy_images_dev_to_prod.py --dry-run

    # Actually copy
    python scripts/copy_images_dev_to_prod.py
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_bucket_names(stack: str) -> Dict[str, str]:
    """Get S3 bucket names from Pulumi stack."""
    logger.info(f"Getting {stack.upper()} bucket names from Pulumi...")

    env = load_env(env=stack)
    raw_bucket = env.get("raw_bucket_name")
    cdn_bucket = env.get("cdn_bucket_name")

    if not raw_bucket or not cdn_bucket:
        raise ValueError(f"Could not find bucket names in Pulumi {stack} stack outputs")

    logger.info(f"{stack.upper()} buckets: raw={raw_bucket}, cdn={cdn_bucket}")
    return {"raw": raw_bucket, "cdn": cdn_bucket}


def list_all_objects(
    s3_client,
    bucket: str,
    prefix: str = "",
    extensions: Optional[List[str]] = None,
) -> List[str]:
    """
    List all objects in an S3 bucket with optional prefix and extension filter.

    Args:
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        prefix: Optional prefix to filter by
        extensions: Optional list of file extensions to filter by (e.g., ['.png', '.jpg'])

    Returns:
        List of S3 keys
    """
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")

    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                key = obj["Key"]
                if extensions:
                    if any(key.lower().endswith(ext.lower()) for ext in extensions):
                        keys.append(key)
                else:
                    keys.append(key)

    except ClientError as e:
        logger.error(f"Error listing objects in {bucket}/{prefix}: {e}")
        raise

    return keys


def object_exists(s3_client, bucket: str, key: str) -> bool:
    """Check if an object exists in S3."""
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def check_objects_batch(
    s3_client, bucket: str, keys: List[str], max_workers: int = 10
) -> Dict[str, bool]:
    """
    Check existence of multiple objects in parallel.

    Returns:
        Dictionary mapping key -> exists (bool)
    """
    results = {}

    # Use a lock to ensure thread-safe access to results
    from threading import Lock

    lock = Lock()

    def check_one(key: str) -> tuple[str, bool]:
        try:
            return key, object_exists(s3_client, bucket, key)
        except Exception as e:
            logger.debug(f"Error checking {key}: {e}")
            return key, False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {executor.submit(check_one, key): key for key in keys}
        for future in as_completed(future_to_key):
            try:
                key, exists = future.result()
                with lock:
                    results[key] = exists
            except Exception as e:
                key = future_to_key[future]
                logger.error(f"Error checking {key}: {e}")
                with lock:
                    results[key] = False

    return results


def copy_one_object(
    s3_client,
    source_bucket: str,
    dest_bucket: str,
    key: str,
    dry_run: bool,
) -> tuple[str, bool, Optional[str]]:
    """
    Copy a single object. Returns (key, success, error_message).
    """
    try:
        if dry_run:
            return key, True, None

        copy_source = {"Bucket": source_bucket, "Key": key}
        s3_client.copy_object(CopySource=copy_source, Bucket=dest_bucket, Key=key)
        return key, True, None
    except Exception as e:
        return key, False, str(e)


def copy_objects(
    s3_client,
    source_bucket: str,
    dest_bucket: str,
    keys: List[str],
    dry_run: bool = True,
    skip_existing: bool = True,
    max_workers: int = 20,
) -> Dict[str, int]:
    """
    Copy objects from source bucket to dest bucket using parallel processing.

    Returns:
        Dictionary with statistics: copied, skipped, failed, errors
    """
    stats = {
        "total": len(keys),
        "copied": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    logger.info(f"Processing {len(keys)} objects with {max_workers} workers...")

    # Batch check for existing objects if skip_existing is enabled
    keys_to_copy = keys
    if skip_existing:
        logger.info("Checking which objects already exist in destination...")
        existing = check_objects_batch(s3_client, dest_bucket, keys, max_workers=20)
        keys_to_copy = [k for k in keys if not existing.get(k, False)]
        stats["skipped"] = len(keys) - len(keys_to_copy)
        logger.info(
            f"Found {stats['skipped']} existing objects, {len(keys_to_copy)} to copy"
        )

    if not keys_to_copy:
        logger.info("All objects already exist, nothing to copy")
        return stats

    # Copy objects in parallel
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {
            executor.submit(
                copy_one_object,
                s3_client,
                source_bucket,
                dest_bucket,
                key,
                dry_run,
            ): key
            for key in keys_to_copy
        }

        for future in as_completed(future_to_key):
            completed += 1
            key, success, error = future.result()

            if success:
                stats["copied"] += 1
            else:
                stats["failed"] += 1
                error_msg = f"Failed to copy {key}: {error}"
                stats["errors"].append(error_msg)
                logger.error(error_msg)

            # Progress update every 100 items
            if completed % 100 == 0:
                logger.info(
                    f"Progress: {completed}/{len(keys_to_copy)} "
                    f"(copied: {stats['copied']}, failed: {stats['failed']})"
                )

    return stats


def copy_raw_images(
    s3_client,
    dev_raw_bucket: str,
    prod_raw_bucket: str,
    dry_run: bool = True,
    max_workers: int = 20,
) -> Dict[str, int]:
    """Copy raw images and JSON files from dev to prod."""
    logger.info("=" * 60)
    logger.info("COPYING RAW IMAGES")
    logger.info("=" * 60)

    # List all raw images (PNG, JPG, JPEG, WEBP)
    logger.info("Listing raw images from dev...")
    image_keys = list_all_objects(
        s3_client,
        dev_raw_bucket,
        prefix="raw/",
        extensions=[".png", ".jpg", ".jpeg", ".webp"],
    )
    logger.info(f"Found {len(image_keys)} raw images")

    # List all raw JSON files
    logger.info("Listing raw JSON files from dev...")
    json_keys = list_all_objects(
        s3_client, dev_raw_bucket, prefix="raw/", extensions=[".json"]
    )
    logger.info(f"Found {len(json_keys)} raw JSON files")

    # Combine and copy
    all_keys = image_keys + json_keys
    logger.info(f"Total raw files to copy: {len(all_keys)}")

    stats = copy_objects(
        s3_client,
        dev_raw_bucket,
        prod_raw_bucket,
        all_keys,
        dry_run=dry_run,
        max_workers=max_workers,
    )

    return stats


def copy_cdn_images(
    s3_client,
    dev_cdn_bucket: str,
    prod_cdn_bucket: str,
    dry_run: bool = True,
    max_workers: int = 20,
) -> Dict[str, int]:
    """Copy CDN images from dev to prod."""
    logger.info("=" * 60)
    logger.info("COPYING CDN IMAGES")
    logger.info("=" * 60)

    # List all CDN images (PNG, JPG, JPEG, WEBP, AVIF)
    logger.info("Listing CDN images from dev...")
    image_keys = list_all_objects(
        s3_client,
        dev_cdn_bucket,
        extensions=[".png", ".jpg", ".jpeg", ".webp", ".avif"],
    )
    logger.info(f"Found {len(image_keys)} CDN images")

    stats = copy_objects(
        s3_client,
        dev_cdn_bucket,
        prod_cdn_bucket,
        image_keys,
        dry_run=dry_run,
        max_workers=max_workers,
    )

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Copy images from dev S3 buckets to prod S3 buckets"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default: True, use --no-dry-run to actually copy)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually copy files (disables dry-run)",
    )
    parser.add_argument(
        "--skip-raw",
        action="store_true",
        help="Skip copying raw images",
    )
    parser.add_argument(
        "--skip-cdn",
        action="store_true",
        help="Skip copying CDN images",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=20,
        help="Number of parallel workers for copying (default: 20)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY RUN" if args.dry_run else "LIVE COPY"
    logger.info(f"Mode: {mode}")
    if args.dry_run:
        logger.info("No files will be copied. Use --no-dry-run to actually copy.")

    try:
        # Get bucket names
        dev_buckets = get_bucket_names("dev")
        prod_buckets = get_bucket_names("prod")

        # Initialize S3 client with larger connection pool for parallel operations
        from botocore.config import Config

        config = Config(
            max_pool_connections=args.max_workers
            * 2,  # Allow more concurrent connections
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        s3_client = boto3.client("s3", config=config)

        # Statistics
        total_stats = {
            "raw": {"copied": 0, "skipped": 0, "failed": 0, "errors": []},
            "cdn": {"copied": 0, "skipped": 0, "failed": 0, "errors": []},
        }

        # Copy raw images
        if not args.skip_raw:
            raw_stats = copy_raw_images(
                s3_client,
                dev_buckets["raw"],
                prod_buckets["raw"],
                dry_run=args.dry_run,
                max_workers=args.max_workers,
            )
            total_stats["raw"] = raw_stats

        # Copy CDN images
        if not args.skip_cdn:
            cdn_stats = copy_cdn_images(
                s3_client,
                dev_buckets["cdn"],
                prod_buckets["cdn"],
                dry_run=args.dry_run,
                max_workers=args.max_workers,
            )
            total_stats["cdn"] = cdn_stats

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("COPY SUMMARY")
        logger.info("=" * 60)

        if not args.skip_raw:
            logger.info("\nRaw Images:")
            logger.info(f"  Total: {total_stats['raw'].get('total', 0)}")
            logger.info(
                f"  {'Would copy' if args.dry_run else 'Copied'}: {total_stats['raw']['copied']}"
            )
            logger.info(f"  Skipped (already exist): {total_stats['raw']['skipped']}")
            logger.info(f"  Failed: {total_stats['raw']['failed']}")

        if not args.skip_cdn:
            logger.info("\nCDN Images:")
            logger.info(f"  Total: {total_stats['cdn'].get('total', 0)}")
            logger.info(
                f"  {'Would copy' if args.dry_run else 'Copied'}: {total_stats['cdn']['copied']}"
            )
            logger.info(f"  Skipped (already exist): {total_stats['cdn']['skipped']}")
            logger.info(f"  Failed: {total_stats['cdn']['failed']}")

        # Show errors if any
        raw_errors = total_stats["raw"].get("errors", [])
        cdn_errors = total_stats["cdn"].get("errors", [])
        # Ensure errors are lists
        if not isinstance(raw_errors, list):
            raw_errors = []
        if not isinstance(cdn_errors, list):
            cdn_errors = []
        all_errors = raw_errors + cdn_errors
        if all_errors:
            logger.warning(f"\n{len(all_errors)} errors occurred:")
            for error in all_errors[:10]:  # Show first 10
                logger.warning(f"  - {error}")
            if len(all_errors) > 10:
                logger.warning(f"  ... and {len(all_errors) - 10} more errors")

        if args.dry_run:
            logger.info(
                "\n✅ Dry run completed. Use --no-dry-run to actually copy files."
            )
        else:
            total_failed = total_stats["raw"]["failed"] + total_stats["cdn"]["failed"]
            if total_failed > 0:
                logger.error("\n❌ Copy completed with errors")
                sys.exit(1)
            else:
                logger.info("\n✅ Copy completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
