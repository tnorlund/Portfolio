#!/usr/bin/env python3
"""
Delete ChromaDB Cloud collections.

This script connects to Chroma Cloud and deletes a specified collection.
Useful for resetting embedding data during migrations or testing.

Usage:
    # Dry run (shows what would be deleted)
    python scripts/delete_chroma_cloud_collection.py --env dev --collection lines --dry-run

    # Actually delete
    python scripts/delete_chroma_cloud_collection.py --env dev --collection lines --no-dry-run

    # Delete words collection
    python scripts/delete_chroma_cloud_collection.py --env dev --collection words --no-dry-run
"""

import argparse
import logging
import os
import sys

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_secrets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_chroma_client(api_key: str, tenant: str, database: str):
    """Create a Chroma Cloud client.

    Args:
        api_key: Chroma Cloud API key
        tenant: Chroma Cloud tenant ID
        database: Chroma Cloud database name

    Returns:
        chromadb.CloudClient configured for Chroma Cloud
    """
    try:
        import chromadb
    except ImportError:
        logger.error(
            "chromadb package not installed. "
            "Install with: pip install chromadb"
        )
        sys.exit(1)

    client = chromadb.CloudClient(
        api_key=api_key,
        tenant=tenant or "default",
        database=database or "default",
    )

    return client


def list_collections(client) -> list:
    """List all collections in the Chroma Cloud database.

    Args:
        client: Chroma Cloud client

    Returns:
        List of collection names
    """
    try:
        collections = client.list_collections()
        return [c.name for c in collections]
    except Exception as e:
        logger.error("Error listing collections: %s", e)
        return []


def delete_collection(
    client,
    collection_name: str,
    dry_run: bool = True,
) -> bool:
    """Delete a collection from Chroma Cloud.

    Args:
        client: Chroma Cloud client
        collection_name: Name of the collection to delete
        dry_run: If True, don't actually delete

    Returns:
        True if successful (or would be successful in dry run)
    """
    try:
        # Check if collection exists
        collections = list_collections(client)

        if collection_name not in collections:
            logger.warning(
                "Collection '%s' does not exist. Available: %s",
                collection_name,
                collections,
            )
            return False

        # Get collection info
        try:
            collection = client.get_collection(collection_name)
            count = collection.count()
            logger.info(
                "Collection '%s' contains %d embeddings",
                collection_name,
                count,
            )
        except Exception as e:
            logger.warning("Could not get collection count: %s", e)
            count = "unknown"

        if dry_run:
            logger.info(
                "[DRY RUN] Would delete collection '%s' (%s embeddings)",
                collection_name,
                count,
            )
            return True

        # Delete the collection
        logger.info("Deleting collection '%s'...", collection_name)
        client.delete_collection(collection_name)
        logger.info("Successfully deleted collection '%s'", collection_name)
        return True

    except Exception as e:
        logger.exception("Error deleting collection '%s': %s", collection_name, e)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Delete ChromaDB Cloud collections"
    )
    parser.add_argument(
        "--env",
        type=str,
        required=True,
        choices=["dev", "prod"],
        help="Environment (dev or prod)",
    )
    parser.add_argument(
        "--collection",
        type=str,
        required=True,
        choices=["lines", "words"],
        help="Which collection to delete",
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
        help="Actually delete the collection (disables dry-run)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="Only list collections, don't delete",
    )

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info("Mode: %s", mode)
    logger.info("Environment: %s", args.env.upper())
    logger.info("Collection: %s", args.collection)

    # Load secrets from Pulumi
    logger.info("Loading Pulumi secrets...")
    secrets = load_secrets(env=args.env)

    # Extract Chroma Cloud credentials
    # Keys are prefixed with "portfolio:" in Pulumi config
    api_key = secrets.get("portfolio:CHROMA_CLOUD_API_KEY")
    tenant = secrets.get("portfolio:CHROMA_CLOUD_TENANT")
    database = secrets.get("portfolio:CHROMA_CLOUD_DATABASE")
    enabled = secrets.get("portfolio:CHROMA_CLOUD_ENABLED", "false")

    if not api_key:
        logger.error("CHROMA_CLOUD_API_KEY not found in Pulumi secrets")
        sys.exit(1)
    if not tenant:
        logger.error("CHROMA_CLOUD_TENANT not found in Pulumi secrets")
        sys.exit(1)
    if not database:
        logger.error("CHROMA_CLOUD_DATABASE not found in Pulumi secrets")
        sys.exit(1)

    logger.info("Chroma Cloud tenant: %s", tenant)
    logger.info("Chroma Cloud database: %s", database)
    logger.info("Chroma Cloud enabled: %s", enabled)

    if enabled.lower() != "true":
        logger.warning(
            "CHROMA_CLOUD_ENABLED is not 'true' for this environment. "
            "Proceeding anyway..."
        )

    # Create Chroma Cloud client
    logger.info("Connecting to Chroma Cloud...")
    client = get_chroma_client(api_key, tenant, database)

    # List collections
    collections = list_collections(client)
    logger.info("Available collections: %s", collections)

    if args.list_only:
        for coll_name in collections:
            try:
                coll = client.get_collection(coll_name)
                logger.info("  - %s: %d embeddings", coll_name, coll.count())
            except Exception:
                logger.info("  - %s: (count unavailable)", coll_name)
        return

    # Safety confirmation for live mode
    if not args.dry_run:
        logger.warning(
            "This will PERMANENTLY DELETE the '%s' collection in %s!",
            args.collection,
            args.env.upper(),
        )
        logger.warning("Press Ctrl+C within 5 seconds to abort...")
        import time

        time.sleep(5)

    # Delete the collection
    success = delete_collection(
        client,
        args.collection,
        dry_run=args.dry_run,
    )

    if success:
        if args.dry_run:
            logger.info(
                "[DRY RUN] No changes made. Use --no-dry-run to delete."
            )
        else:
            logger.info("Collection deletion complete.")
    else:
        logger.error("Collection deletion failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
