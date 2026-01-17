#!/usr/bin/env python3
"""One-time sync of S3 ChromaDB snapshots to Chroma Cloud.

This script downloads existing ChromaDB snapshots from S3 and uploads them
to Chroma Cloud for dual-write setup. Run once per environment before
enabling dual-write in the compaction Lambda.

Usage:
    # Dev environment
    CHROMA_CLOUD_API_KEY=xxx python scripts/sync_to_chroma_cloud.py \
        --env dev --collections lines,words

    # Prod environment
    CHROMA_CLOUD_API_KEY=xxx python scripts/sync_to_chroma_cloud.py \
        --env prod --collections lines,words

    # Dry run (no writes)
    CHROMA_CLOUD_API_KEY=xxx python scripts/sync_to_chroma_cloud.py \
        --env dev --collections lines --dry-run

Environment Variables:
    CHROMA_CLOUD_API_KEY: Required. API key from Chroma Cloud dashboard.
    CHROMA_CLOUD_TENANT: Optional. Tenant ID (defaults to "default").
    CHROMA_CLOUD_DATABASE: Optional. Database name (defaults to "default").
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile
from typing import Iterator, List, Optional

import boto3
from botocore.exceptions import ClientError

# Add project root to path for imports
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from receipt_chroma import ChromaClient  # noqa: E402
from receipt_chroma.s3 import download_snapshot_atomic  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Environment-specific bucket names
ENV_BUCKETS = {
    "dev": "portfolio-dev-chromadb",
    "prod": "portfolio-prod-chromadb",
}


def chunked(data: dict, chunk_size: int) -> Iterator[dict]:
    """Split data dict into chunks for batch processing.

    Args:
        data: Dictionary with ids, embeddings, metadatas, documents lists
        chunk_size: Maximum items per chunk

    Yields:
        Dictionary chunks with same structure
    """
    ids = data.get("ids", [])
    embeddings = data.get("embeddings")
    metadatas = data.get("metadatas")
    documents = data.get("documents")

    for i in range(0, len(ids), chunk_size):
        chunk = {
            "ids": ids[i : i + chunk_size],
        }
        if embeddings:
            chunk["embeddings"] = embeddings[i : i + chunk_size]
        if metadatas:
            chunk["metadatas"] = metadatas[i : i + chunk_size]
        if documents:
            chunk["documents"] = documents[i : i + chunk_size]
        yield chunk


def sync_collection(
    bucket: str,
    collection: str,
    cloud_client: ChromaClient,
    dry_run: bool = False,
) -> dict:
    """Sync a single collection from S3 snapshot to Chroma Cloud.

    Args:
        bucket: S3 bucket containing ChromaDB snapshots
        collection: Collection name (e.g., "lines" or "words")
        cloud_client: ChromaClient configured for Chroma Cloud
        dry_run: If True, only report what would be done

    Returns:
        Dictionary with sync status and statistics
    """
    logger.info(
        "Syncing collection '%s' from bucket '%s'", collection, bucket
    )

    temp_dir = tempfile.mkdtemp(prefix=f"chroma-sync-{collection}-")

    try:
        # Download S3 snapshot
        logger.info("Downloading snapshot from S3...")
        download_result = download_snapshot_atomic(
            bucket=bucket,
            collection=collection,
            local_path=temp_dir,
            verify_integrity=True,
        )

        if download_result.get("status") == "error":
            error_msg = download_result.get("error", "Unknown error")
            logger.error("Failed to download snapshot: %s", error_msg)
            return {
                "status": "error",
                "error": error_msg,
                "collection": collection,
            }

        logger.info(
            "Snapshot downloaded (version=%s)",
            download_result.get("version_id"),
        )

        # Open local snapshot in read mode
        with ChromaClient(
            persist_directory=temp_dir, mode="read"
        ) as local_client:
            # Verify collection exists
            if not local_client.collection_exists(collection):
                logger.error(
                    "Collection '%s' not found in snapshot", collection
                )
                return {
                    "status": "error",
                    "error": f"Collection '{collection}' not found in snapshot",
                    "collection": collection,
                }

            local_coll = local_client.get_collection(collection)
            total_count = local_coll.count()

            logger.info(
                "Local snapshot has %d items in collection '%s'",
                total_count,
                collection,
            )

            if dry_run:
                logger.info(
                    "[DRY RUN] Would upload %d items to Chroma Cloud",
                    total_count,
                )
                return {
                    "status": "dry_run",
                    "collection": collection,
                    "total_items": total_count,
                    "would_upload": total_count,
                }

            # Get all data from local collection
            # ChromaDB limits: use include to get embeddings
            logger.info("Reading all data from local snapshot...")
            all_data = local_coll.get(
                include=["embeddings", "metadatas", "documents"]
            )

            if not all_data.get("ids"):
                logger.warning(
                    "No items found in collection '%s'", collection
                )
                return {
                    "status": "success",
                    "collection": collection,
                    "total_items": 0,
                    "uploaded": 0,
                }

            # Get or create cloud collection
            cloud_coll = cloud_client.get_collection(
                collection,
                create_if_missing=True,
                metadata={"synced_from": "s3_snapshot"},
            )

            # Upload to cloud in batches of 5000 (Chroma Cloud limit)
            batch_size = 5000
            uploaded = 0

            for batch in chunked(all_data, batch_size):
                batch_count = len(batch["ids"])
                logger.info(
                    "Uploading batch of %d items (total: %d/%d)",
                    batch_count,
                    uploaded + batch_count,
                    total_count,
                )

                cloud_coll.upsert(
                    ids=batch["ids"],
                    embeddings=batch.get("embeddings"),
                    metadatas=batch.get("metadatas"),
                    documents=batch.get("documents"),
                )
                uploaded += batch_count

            logger.info(
                "Successfully uploaded %d items to Chroma Cloud", uploaded
            )

            # Verify cloud count
            cloud_count = cloud_coll.count()
            logger.info(
                "Cloud collection '%s' now has %d items",
                collection,
                cloud_count,
            )

            return {
                "status": "success",
                "collection": collection,
                "total_items": total_count,
                "uploaded": uploaded,
                "cloud_count": cloud_count,
            }

    finally:
        # Cleanup temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    """Main entry point for the sync script."""
    parser = argparse.ArgumentParser(
        description="Sync S3 ChromaDB snapshots to Chroma Cloud"
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["dev", "prod"],
        help="Environment to sync (dev or prod)",
    )
    parser.add_argument(
        "--collections",
        required=True,
        help="Comma-separated list of collections to sync (e.g., lines,words)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would be done, don't write to cloud",
    )
    parser.add_argument(
        "--tenant",
        default=os.environ.get("CHROMA_CLOUD_TENANT", "default"),
        help="Chroma Cloud tenant ID (default: from env or 'default')",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("CHROMA_CLOUD_DATABASE", "default"),
        help="Chroma Cloud database name (default: from env or 'default')",
    )

    args = parser.parse_args()

    # Validate API key
    api_key = os.environ.get("CHROMA_CLOUD_API_KEY", "").strip()
    if not api_key:
        logger.error(
            "CHROMA_CLOUD_API_KEY environment variable is required"
        )
        sys.exit(1)

    # Get bucket for environment
    bucket = ENV_BUCKETS.get(args.env)
    if not bucket:
        logger.error("Unknown environment: %s", args.env)
        sys.exit(1)

    # Parse collections
    collections = [c.strip() for c in args.collections.split(",") if c.strip()]
    if not collections:
        logger.error("No collections specified")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Chroma Cloud Sync")
    logger.info("=" * 60)
    logger.info("Environment: %s", args.env)
    logger.info("S3 Bucket: %s", bucket)
    logger.info("Collections: %s", ", ".join(collections))
    logger.info("Tenant: %s", args.tenant)
    logger.info("Database: %s", args.database)
    logger.info("Dry Run: %s", args.dry_run)
    logger.info("=" * 60)

    # Verify S3 bucket access
    s3_client = boto3.client("s3")
    try:
        s3_client.head_bucket(Bucket=bucket)
        logger.info("S3 bucket '%s' is accessible", bucket)
    except ClientError as e:
        logger.error("Cannot access S3 bucket '%s': %s", bucket, e)
        sys.exit(1)

    # Create Chroma Cloud client
    cloud_client = ChromaClient(
        cloud_api_key=api_key,
        cloud_tenant=args.tenant,
        cloud_database=args.database,
        mode="write",
        metadata_only=True,
    )

    # Sync each collection
    results = []
    with cloud_client:
        for collection in collections:
            logger.info("-" * 40)
            result = sync_collection(
                bucket=bucket,
                collection=collection,
                cloud_client=cloud_client,
                dry_run=args.dry_run,
            )
            results.append(result)

    # Summary
    logger.info("=" * 60)
    logger.info("Sync Summary")
    logger.info("=" * 60)

    success_count = 0
    for result in results:
        status = result.get("status", "unknown")
        collection = result.get("collection", "unknown")

        if status == "success":
            success_count += 1
            logger.info(
                "[SUCCESS] %s: %d items uploaded (cloud count: %d)",
                collection,
                result.get("uploaded", 0),
                result.get("cloud_count", 0),
            )
        elif status == "dry_run":
            success_count += 1
            logger.info(
                "[DRY RUN] %s: %d items would be uploaded",
                collection,
                result.get("would_upload", 0),
            )
        else:
            logger.error(
                "[FAILED] %s: %s",
                collection,
                result.get("error", "Unknown error"),
            )

    logger.info("-" * 40)
    logger.info(
        "Completed: %d/%d collections synced successfully",
        success_count,
        len(collections),
    )

    if success_count < len(collections):
        sys.exit(1)


if __name__ == "__main__":
    main()
