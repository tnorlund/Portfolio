#!/usr/bin/env python3
"""
Sync OCRJob records and their associated S3 artifacts from dev to prod.

For each REGIONAL_REOCR job in dev:
1. Copies the ocr_results JSON from the dev raw bucket to prod raw bucket
2. Rewrites the OCRJob record's s3_bucket to the prod bucket name
3. Writes the OCRJob record to prod DynamoDB

This preserves the full audit trail (region, reason, timestamps) while making
all S3 references point to prod. Since the raw source images are already
synced by sync_images_dev_to_prod_fast.sh, and the OCR result JSONs are copied
here, prod becomes fully self-contained.

Usage:
    # Dry run - show what would be synced
    python scripts/sync_ocr_jobs_dev_to_prod.py

    # Dry run for specific images only
    python scripts/sync_ocr_jobs_dev_to_prod.py --image-ids abc123 def456

    # Actually sync
    python scripts/sync_ocr_jobs_dev_to_prod.py --no-dry-run

    # Sync all job types (not just REGIONAL_REOCR)
    python scripts/sync_ocr_jobs_dev_to_prod.py --all-job-types --no-dry-run
"""

import argparse
import logging
import os
import sys
from typing import Any

import boto3

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.constants import OCRJobType
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.ocr_job import OCRJob

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_all_ocr_jobs(client: DynamoClient) -> list[OCRJob]:
    """Paginate through all OCRJob records."""
    all_jobs: list[OCRJob] = []
    last_key: dict[str, Any] | None = None
    while True:
        jobs, last_key = client.list_ocr_jobs(
            limit=500, last_evaluated_key=last_key
        )
        all_jobs.extend(jobs)
        if last_key is None:
            break
    return all_jobs


def s3_object_exists(s3_client, bucket: str, key: str) -> bool:
    """Check if an S3 object exists."""
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except s3_client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def copy_s3_object(
    s3_client, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
) -> bool:
    """Copy an S3 object between buckets. Returns True if copied."""
    try:
        s3_client.copy_object(
            Bucket=dst_bucket,
            Key=dst_key,
            CopySource={"Bucket": src_bucket, "Key": src_key},
        )
        return True
    except s3_client.exceptions.ClientError as e:
        logger.error(
            "  Failed to copy s3://%s/%s -> s3://%s/%s: %s",
            src_bucket,
            src_key,
            dst_bucket,
            dst_key,
            e,
        )
        return False


def find_ocr_result_key(s3_client, bucket: str, image_id: str, job_id: str) -> str | None:
    """Find the ocr_results JSON for a given job.

    The Swift worker uploads to: ocr_results/{name}-{job_id}-reocr.json
    where {name} is derived from the original image s3_key basename.
    We search by prefix since the exact filename depends on the source image name.
    """
    # List objects matching the job_id under ocr_results/
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="ocr_results/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # Match by job_id in the filename
            if job_id in key:
                return key
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Sync OCRJob records and S3 artifacts from dev to prod"
    )
    parser.add_argument(
        "--image-ids",
        nargs="+",
        default=None,
        help="Only sync jobs for these image IDs (default: all)",
    )
    parser.add_argument(
        "--all-job-types",
        action="store_true",
        help="Sync all job types, not just REGIONAL_REOCR (default: REGIONAL_REOCR only)",
    )
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
        help="Actually sync records and S3 objects",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    mode = "DRY RUN" if args.dry_run else "LIVE SYNC"
    logger.info("Mode: %s", mode)
    if args.dry_run:
        logger.info("No changes will be made. Use --no-dry-run to actually sync.")

    # Load environment configs
    logger.info("Loading configurations from Pulumi...")
    dev_config = load_env(env="dev")
    prod_config = load_env(env="prod")

    dev_table = dev_config["dynamodb_table_name"]
    prod_table = prod_config["dynamodb_table_name"]
    dev_raw_bucket = dev_config["raw_bucket_name"]
    prod_raw_bucket = prod_config["raw_bucket_name"]

    logger.info("Dev table:      %s", dev_table)
    logger.info("Prod table:     %s", prod_table)
    logger.info("Dev raw bucket: %s", dev_raw_bucket)
    logger.info("Prod raw bucket: %s", prod_raw_bucket)

    dev_client = DynamoClient(dev_table)
    prod_client = DynamoClient(prod_table)
    s3_client = boto3.client("s3")

    # -------------------------------------------------------------------------
    # 1. Fetch all OCRJob records from both environments
    # -------------------------------------------------------------------------
    logger.info("Fetching OCRJob records from dev...")
    dev_jobs = fetch_all_ocr_jobs(dev_client)
    logger.info("  Found %d total OCRJob records in dev", len(dev_jobs))

    logger.info("Fetching OCRJob records from prod...")
    prod_jobs = fetch_all_ocr_jobs(prod_client)
    logger.info("  Found %d total OCRJob records in prod", len(prod_jobs))

    # Index prod jobs by (image_id, job_id) for quick lookup
    prod_job_keys = {(j.image_id, j.job_id) for j in prod_jobs}

    # -------------------------------------------------------------------------
    # 2. Filter dev jobs to sync
    # -------------------------------------------------------------------------
    jobs_to_sync: list[OCRJob] = []
    for job in dev_jobs:
        # Filter by job type
        if not args.all_job_types and job.job_type != OCRJobType.REGIONAL_REOCR.value:
            continue

        # Filter by image IDs if specified
        if args.image_ids and job.image_id not in args.image_ids:
            continue

        # Skip if already in prod
        if (job.image_id, job.job_id) in prod_job_keys:
            logger.debug(
                "  Skipping %s/%s - already in prod", job.image_id[:8], job.job_id[:8]
            )
            continue

        jobs_to_sync.append(job)

    logger.info(
        "Jobs to sync: %d (filtered from %d dev jobs)", len(jobs_to_sync), len(dev_jobs)
    )

    if not jobs_to_sync:
        logger.info("Nothing to sync!")
        return

    # -------------------------------------------------------------------------
    # 3. Group by image_id for readable output
    # -------------------------------------------------------------------------
    by_image: dict[str, list[OCRJob]] = {}
    for job in jobs_to_sync:
        by_image.setdefault(job.image_id, []).append(job)

    # -------------------------------------------------------------------------
    # 4. Sync each job
    # -------------------------------------------------------------------------
    stats = {
        "jobs_synced": 0,
        "jobs_skipped": 0,
        "s3_copied": 0,
        "s3_already_exists": 0,
        "s3_not_found": 0,
        "s3_copy_failed": 0,
        "errors": 0,
    }

    prefix = "[DRY RUN] " if args.dry_run else ""

    for image_id, jobs in sorted(by_image.items()):
        logger.info("")
        logger.info("Image %s (%d job(s)):", image_id[:12], len(jobs))

        for job in jobs:
            logger.info(
                "  Job %s | type=%s reason=%s receipt=%s",
                job.job_id[:8],
                job.job_type,
                job.reocr_reason or "n/a",
                job.receipt_id,
            )

            # 4a. Find and copy the ocr_results JSON
            ocr_key = find_ocr_result_key(
                s3_client, dev_raw_bucket, image_id, job.job_id
            )
            if ocr_key:
                # Check if it already exists in prod
                if s3_object_exists(s3_client, prod_raw_bucket, ocr_key):
                    logger.info("    S3: %s (already in prod)", ocr_key)
                    stats["s3_already_exists"] += 1
                else:
                    logger.info("    %sS3 copy: %s", prefix, ocr_key)
                    if not args.dry_run:
                        if copy_s3_object(
                            s3_client, dev_raw_bucket, ocr_key, prod_raw_bucket, ocr_key
                        ):
                            stats["s3_copied"] += 1
                        else:
                            stats["s3_copy_failed"] += 1
                    else:
                        stats["s3_copied"] += 1
            else:
                logger.warning(
                    "    S3: No ocr_results found for job %s in dev bucket",
                    job.job_id[:8],
                )
                stats["s3_not_found"] += 1

            # 4b. Rewrite s3_bucket and write OCRJob to prod
            # The s3_key stays the same (it points to the raw source image,
            # which is synced by sync_images_dev_to_prod_fast.sh).
            # We only need to rewrite s3_bucket from dev to prod.
            prod_job = OCRJob(
                image_id=job.image_id,
                job_id=job.job_id,
                s3_bucket=prod_raw_bucket,
                s3_key=job.s3_key,
                created_at=job.created_at,
                updated_at=job.updated_at,
                status=job.status,
                job_type=job.job_type,
                receipt_id=job.receipt_id,
                reocr_region=job.reocr_region,
                reocr_reason=job.reocr_reason,
            )

            logger.info(
                "    %sDynamo: OCRJob -> prod (s3_bucket: %s -> %s)",
                prefix,
                job.s3_bucket,
                prod_raw_bucket,
            )

            if not args.dry_run:
                try:
                    prod_client.add_ocr_job(prod_job)
                    stats["jobs_synced"] += 1
                except Exception as e:
                    logger.error("    Error writing OCRJob to prod: %s", e)
                    stats["errors"] += 1
            else:
                stats["jobs_synced"] += 1

    # -------------------------------------------------------------------------
    # 5. Print summary
    # -------------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 60)
    logger.info("SYNC SUMMARY")
    logger.info("=" * 60)
    logger.info("OCRJob records synced:    %d", stats["jobs_synced"])
    logger.info("OCRJob records skipped:   %d (already in prod)", stats["jobs_skipped"])
    logger.info("S3 objects copied:        %d", stats["s3_copied"])
    logger.info("S3 objects already exist: %d", stats["s3_already_exists"])
    logger.info("S3 objects not found:     %d", stats["s3_not_found"])
    logger.info("S3 copy failures:         %d", stats["s3_copy_failed"])
    logger.info("Errors:                   %d", stats["errors"])

    if args.dry_run:
        logger.info("")
        logger.info("[DRY RUN] No changes made. Use --no-dry-run to sync.")
    elif stats["errors"] > 0:
        logger.error("")
        logger.error("Sync completed with %d errors", stats["errors"])
        sys.exit(1)
    else:
        logger.info("")
        logger.info("Sync completed successfully!")


if __name__ == "__main__":
    main()
