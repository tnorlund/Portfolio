#!/usr/bin/env python3
"""
Copy LayoutLM model from dev to prod training bucket.

This script:
1. Gets the LayoutLM training bucket names from Pulumi (dev and prod)
2. Finds the latest model in dev (runs/{run_id}/best/ or latest checkpoint)
3. Copies the model files to prod
4. Verifies the copy was successful

Usage:
    python scripts/copy_layoutlm_model_dev_to_prod.py
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config as BotoCoreConfig
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


def get_training_bucket_name(stack: str, s3_client=None) -> str:
    """Get LayoutLM training bucket name from Pulumi stack or AWS."""
    logger.info(f"Getting {stack.upper()} LayoutLM training bucket...")
    env = load_env(env=stack)
    bucket_name = env.get("layoutlm_training_bucket")

    if bucket_name:
        logger.info(f"{stack.upper()} training bucket (from Pulumi): {bucket_name}")
        return bucket_name

    # If not in Pulumi outputs, try to find it in AWS
    logger.warning(
        f"Could not find layoutlm_training_bucket in Pulumi {stack} stack outputs"
    )
    logger.info(f"Searching for LayoutLM training bucket in AWS...")

    if s3_client:
        try:
            # List buckets and look for layoutlm-models pattern
            response = s3_client.list_buckets()
            candidates = [
                b
                for b in response.get("Buckets", [])
                if "layoutlm" in b["Name"].lower()
                and "model" in b["Name"].lower()
                and "cache" not in b["Name"].lower()
                and "artifact" not in b["Name"].lower()
            ]

            if candidates:
                # Sort by creation date (newest first) to find the prod bucket
                # Prod bucket should be newer than dev bucket
                candidates_sorted = sorted(
                    candidates,
                    key=lambda x: x.get("CreationDate", datetime.min),
                    reverse=True,
                )

                # If we have multiple buckets, the newest one is likely prod
                # (assuming prod was deployed after dev)
                if len(candidates_sorted) > 1:
                    # For prod, prefer the newest bucket
                    # For dev, prefer the older bucket
                    if stack == "prod":
                        bucket_name = candidates_sorted[0]["Name"]
                        logger.info(
                            f"Found {stack.upper()} training bucket in AWS (newest): {bucket_name}"
                        )
                    else:
                        bucket_name = candidates_sorted[-1]["Name"]
                        logger.info(
                            f"Found {stack.upper()} training bucket in AWS (oldest): {bucket_name}"
                        )
                    return bucket_name
                else:
                    bucket_name = candidates_sorted[0]["Name"]
                    logger.warning(
                        f"Found LayoutLM bucket but may not be for {stack}: {bucket_name}"
                    )
                    return bucket_name
        except Exception as e:
            logger.debug(f"Error searching for bucket: {e}")

    raise ValueError(
        f"Could not find layoutlm_training_bucket for {stack}. "
        f"Please either:\n"
        f"  1. Deploy the LayoutLM training infrastructure to prod via Pulumi, or\n"
        f"  2. Set the bucket name in Pulumi config: pulumi config set ml-training:training-bucket-name <bucket-name> --stack prod"
    )


def find_latest_model(s3_client, bucket: str) -> Optional[str]:
    """
    Find the latest model in the training bucket.

    Returns the S3 prefix (e.g., "runs/{run_id}/best/") or None if not found.
    """
    logger.info(f"Finding latest model in {bucket}...")

    try:
        # List all run prefixes
        resp = s3_client.list_objects_v2(Bucket=bucket, Prefix="runs/", Delimiter="/")
        prefixes = [cp["Prefix"] for cp in resp.get("CommonPrefixes", [])]

        if not prefixes:
            logger.warning("No runs found in bucket")
            return None

        logger.info(f"Found {len(prefixes)} run(s)")

        # Find the latest run by checking last modified time
        latest_prefix, latest_ts = None, None
        for p in prefixes:
            page = s3_client.list_objects_v2(Bucket=bucket, Prefix=p)
            contents = page.get("Contents", [])
            if not contents:
                continue
            ts = max(obj["LastModified"] for obj in contents)
            if latest_ts is None or ts > latest_ts:
                latest_ts, latest_prefix = ts, p

        if not latest_prefix:
            logger.warning("No valid runs found")
            return None

        logger.info(f"Latest run: {latest_prefix}")

        # Check if there's a 'best' directory
        try:
            s3_client.head_object(
                Bucket=bucket, Key=f"{latest_prefix}best/model.safetensors"
            )
            best_prefix = f"{latest_prefix}best/"
            logger.info(f"Found 'best' directory: {best_prefix}")
            return best_prefix
        except ClientError:
            # Fallback: find latest checkpoint with model.safetensors
            logger.info("No 'best' directory, looking for latest checkpoint...")
            page = s3_client.list_objects_v2(Bucket=bucket, Prefix=latest_prefix)
            candidates = [
                o
                for o in page.get("Contents", [])
                if o["Key"].endswith("model.safetensors") and "checkpoint-" in o["Key"]
            ]

            if not candidates:
                logger.warning("No checkpoint with model.safetensors found")
                return None

            latest = max(candidates, key=lambda o: o["LastModified"])
            checkpoint_prefix = latest["Key"].rsplit("/", 1)[0] + "/"
            logger.info(f"Found latest checkpoint: {checkpoint_prefix}")
            return checkpoint_prefix

    except Exception as e:
        logger.error(f"Error finding latest model: {e}", exc_info=True)
        return None


def list_model_files(s3_client, bucket: str, prefix: str) -> List[str]:
    """List all files under the model prefix."""
    files = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            files.append(obj["Key"])
    return files


def copy_one_file(
    s3_client,
    source_bucket: str,
    dest_bucket: str,
    key: str,
    dry_run: bool,
) -> tuple[str, bool, Optional[str]]:
    """Copy a single file. Returns (key, success, error_message)."""
    try:
        if dry_run:
            return key, True, None

        copy_source = {"Bucket": source_bucket, "Key": key}
        s3_client.copy_object(CopySource=copy_source, Bucket=dest_bucket, Key=key)
        return key, True, None
    except Exception as e:
        return key, False, str(e)


def copy_model_files(
    s3_client,
    source_bucket: str,
    dest_bucket: str,
    model_prefix: str,
    dry_run: bool = True,
    max_workers: int = 10,
) -> Dict[str, Any]:
    """
    Copy all model files from source to dest bucket.

    Returns statistics about the copy operation.
    """
    logger.info(f"Listing model files in {source_bucket}/{model_prefix}...")
    files = list_model_files(s3_client, source_bucket, model_prefix)
    logger.info(f"Found {len(files)} file(s) to copy")

    stats: Dict[str, Any] = {
        "total": len(files),
        "copied": 0,
        "failed": 0,
        "errors": [],
    }

    if not files:
        return stats

    # Copy files in parallel
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {
            executor.submit(
                copy_one_file,
                s3_client,
                source_bucket,
                dest_bucket,
                key,
                dry_run,
            ): key
            for key in files
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

            if completed % 10 == 0:
                logger.info(
                    f"Progress: {completed}/{len(files)} "
                    f"(copied: {stats['copied']}, failed: {stats['failed']})"
                )

    return stats


def verify_model_files(s3_client, bucket: str, prefix: str) -> bool:
    """Verify that essential model files exist."""
    required_files = [
        "model.safetensors",
        "config.json",
    ]

    # Tokenizer files (at least one should exist)
    tokenizer_files = [
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    ]

    logger.info(f"Verifying model files in {bucket}/{prefix}...")

    missing_required = []
    for filename in required_files:
        key = f"{prefix}{filename}"
        try:
            s3_client.head_object(Bucket=bucket, Key=key)
            logger.info(f"✓ Found {filename}")
        except ClientError:
            missing_required.append(filename)
            logger.error(f"✗ Missing {filename}")

    if missing_required:
        logger.error(f"Missing required files: {missing_required}")
        return False

    # Check for at least one tokenizer file
    found_tokenizer = False
    for filename in tokenizer_files:
        key = f"{prefix}{filename}"
        try:
            s3_client.head_object(Bucket=bucket, Key=key)
            logger.info(f"✓ Found {filename}")
            found_tokenizer = True
        except ClientError:
            pass

    if not found_tokenizer:
        logger.warning("No tokenizer files found (may still work if using default)")

    logger.info("Model verification complete")
    return True


def main():
    parser = argparse.ArgumentParser(description="Copy LayoutLM model from dev to prod")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually copy the model (disables dry-run)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Number of parallel workers for copying (default: 10)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY RUN" if args.dry_run else "LIVE COPY"
    logger.info(f"Mode: {mode}")
    if args.dry_run:
        logger.info("No files will be copied. Use --no-dry-run to actually copy.")

    try:
        # Initialize S3 client first (needed for bucket discovery)
        config = BotoCoreConfig(
            max_pool_connections=args.max_workers * 2,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        s3_client = boto3.client("s3", config=config)

        # Get bucket names (s3_client passed for fallback discovery)
        dev_bucket = get_training_bucket_name("dev", s3_client)
        prod_bucket = get_training_bucket_name("prod", s3_client)

        # Check if source and destination are the same
        if dev_bucket == prod_bucket:
            logger.info("=" * 60)
            logger.info("BUCKET CHECK")
            logger.info("=" * 60)
            logger.info(f"Dev and prod are using the same bucket: {dev_bucket}")
            logger.info("Model files are already available in prod bucket.")
            logger.info("Skipping copy operation.")

            # Just verify the model exists
            model_prefix = find_latest_model(s3_client, dev_bucket)
            if not model_prefix:
                logger.error("Could not find latest model in bucket")
                sys.exit(1)

            logger.info(f"Model prefix: {model_prefix}")
            logger.info("Verifying model files...")
            if not verify_model_files(s3_client, dev_bucket, model_prefix):
                logger.error("Model verification failed")
                sys.exit(1)

            logger.info("\n✅ Model is already available in prod bucket")
            logger.info(
                f"\nThe inference Lambda will automatically use this model "
                f"from: s3://{prod_bucket}/{model_prefix}"
            )
            logger.info(
                "\n⚠️  Note: Make sure the prod stack has the LayoutLM inference "
                "infrastructure deployed (Lambda functions, EventBridge, etc.)"
            )
            sys.exit(0)

        # Find latest model in dev
        model_prefix = find_latest_model(s3_client, dev_bucket)
        if not model_prefix:
            logger.error("Could not find latest model in dev bucket")
            sys.exit(1)

        logger.info(f"Model prefix: {model_prefix}")

        # Verify model exists in dev
        logger.info("Verifying model in dev...")
        if not verify_model_files(s3_client, dev_bucket, model_prefix):
            logger.error("Model verification failed in dev")
            sys.exit(1)

        # Copy model files
        logger.info("=" * 60)
        logger.info("COPYING MODEL FILES")
        logger.info("=" * 60)
        stats = copy_model_files(
            s3_client,
            dev_bucket,
            prod_bucket,
            model_prefix,
            dry_run=args.dry_run,
            max_workers=args.max_workers,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("COPY SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Model prefix: {model_prefix}")
        logger.info(f"Total files: {stats['total']}")
        logger.info(f"{'Would copy' if args.dry_run else 'Copied'}: {stats['copied']}")
        logger.info(f"Failed: {stats['failed']}")

        if stats["errors"]:
            logger.warning(f"\n{len(stats['errors'])} errors occurred:")
            for error in stats["errors"][:10]:
                logger.warning(f"  - {error}")
            if len(stats["errors"]) > 10:
                logger.warning(f"  ... and {len(stats['errors']) - 10} more errors")

        # Verify model in prod (if not dry run)
        if not args.dry_run and stats["failed"] == 0:
            logger.info("\nVerifying model in prod...")
            if verify_model_files(s3_client, prod_bucket, model_prefix):
                logger.info("✅ Model successfully copied and verified in prod")
            else:
                logger.error("❌ Model verification failed in prod")
                sys.exit(1)

        if args.dry_run:
            logger.info("\n✅ Dry run completed. Use --no-dry-run to actually copy.")
        else:
            if stats["failed"] > 0:
                logger.error("\n❌ Copy completed with errors")
                sys.exit(1)
            else:
                logger.info("\n✅ Model copy completed successfully")
                logger.info(
                    f"\nThe inference Lambda will automatically use this model "
                    f"from: s3://{prod_bucket}/{model_prefix}"
                )

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
