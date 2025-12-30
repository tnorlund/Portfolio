#!/usr/bin/env python3
"""
Backup all data from a DynamoDB table to local JSON files.

This script exports all images and their related entities to a local directory,
organized by image_id. Use this to create local backups before migrations.

Usage:
    # Backup dev table
    python scripts/backup_dynamo_table.py --env dev --output-dir backups/dev

    # Backup prod table
    python scripts/backup_dynamo_table.py --env prod --output-dir backups/prod

    # Backup specific images only
    python scripts/backup_dynamo_table.py --env dev --output-dir backups/dev --image-ids id1,id2,id3
"""

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.export_image import export_image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_table_name(env: str) -> str:
    """Get DynamoDB table name from Pulumi stack."""
    logger.info(f"Getting {env.upper()} configuration from Pulumi...")

    pulumi_env = load_env(env=env)
    table_name = pulumi_env.get("dynamodb_table_name")

    if not table_name:
        raise ValueError(f"Could not find table name in Pulumi {env} stack outputs")

    logger.info(f"{env.upper()}: table={table_name}")
    return table_name


def list_all_image_ids(client: DynamoClient) -> List[str]:
    """List all image IDs in the table."""
    logger.info("Listing all images in table...")

    image_ids = []
    last_key = None

    while True:
        images, last_key = client.list_images(limit=100, last_evaluated_key=last_key)
        image_ids.extend([img.image_id for img in images])

        if last_key is None:
            break

    logger.info(f"Found {len(image_ids)} images")
    return image_ids


def export_single_image(
    table_name: str,
    image_id: str,
    output_dir: str,
) -> Dict[str, Any]:
    """Export a single image and return stats."""
    try:
        export_image(table_name, image_id, output_dir)

        # Get file size
        file_path = os.path.join(output_dir, f"{image_id}.json")
        file_size = os.path.getsize(file_path)

        return {
            "image_id": image_id,
            "success": True,
            "file_size": file_size,
        }
    except Exception as e:
        return {
            "image_id": image_id,
            "success": False,
            "error": str(e),
        }


def backup_table(
    env: str,
    output_dir: str,
    image_ids: Optional[List[str]] = None,
    max_workers: int = 5,
) -> Dict[str, Any]:
    """
    Backup all images from a DynamoDB table to local JSON files.

    Args:
        env: Environment name ('dev' or 'prod')
        output_dir: Directory to write JSON files
        image_ids: Optional list of specific image IDs to backup
        max_workers: Number of parallel workers

    Returns:
        Statistics about the backup
    """
    table_name = get_table_name(env)
    client = DynamoClient(table_name)

    # Get list of images to backup
    if image_ids:
        all_image_ids = image_ids
        logger.info(f"Backing up {len(all_image_ids)} specified images")
    else:
        all_image_ids = list_all_image_ids(client)

    if not all_image_ids:
        logger.warning("No images found to backup")
        return {"total": 0, "success": 0, "failed": 0}

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Write metadata file
    metadata = {
        "env": env,
        "table_name": table_name,
        "backup_started": datetime.now().isoformat(),
        "total_images": len(all_image_ids),
    }

    stats = {
        "total": len(all_image_ids),
        "success": 0,
        "failed": 0,
        "total_size": 0,
        "errors": [],
    }

    # Export images in parallel
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(export_single_image, table_name, img_id, output_dir): img_id
            for img_id in all_image_ids
        }

        for future in as_completed(futures):
            completed += 1
            result = future.result()

            if result["success"]:
                stats["success"] += 1
                stats["total_size"] += result["file_size"]
            else:
                stats["failed"] += 1
                stats["errors"].append({
                    "image_id": result["image_id"],
                    "error": result["error"],
                })

            if completed % 10 == 0 or completed == len(all_image_ids):
                logger.info(
                    f"Progress: {completed}/{len(all_image_ids)} "
                    f"(success: {stats['success']}, failed: {stats['failed']})"
                )

    # Update and save metadata
    metadata["backup_completed"] = datetime.now().isoformat()
    metadata["stats"] = stats

    metadata_path = os.path.join(output_dir, "_backup_metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, default=str)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backup DynamoDB table to local JSON files"
    )
    parser.add_argument(
        "--env",
        type=str,
        required=True,
        choices=["dev", "prod"],
        help="Environment to backup (dev or prod)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help="Directory to write backup files",
    )
    parser.add_argument(
        "--image-ids",
        type=str,
        help="Comma-separated list of specific image IDs to backup",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Number of parallel workers (default: 5)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Parse image IDs if provided
    image_ids = None
    if args.image_ids:
        image_ids = [id.strip() for id in args.image_ids.split(",")]

    logger.info(f"Starting backup of {args.env.upper()} table")
    logger.info(f"Output directory: {args.output_dir}")

    try:
        stats = backup_table(
            env=args.env,
            output_dir=args.output_dir,
            image_ids=image_ids,
            max_workers=args.max_workers,
        )

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("BACKUP SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Environment: {args.env.upper()}")
        logger.info(f"Output directory: {args.output_dir}")
        logger.info(f"Total images: {stats['total']}")
        logger.info(f"Successful: {stats['success']}")
        logger.info(f"Failed: {stats['failed']}")
        logger.info(f"Total size: {stats['total_size'] / 1024 / 1024:.2f} MB")

        if stats["errors"]:
            logger.warning(f"\n{len(stats['errors'])} errors occurred:")
            for err in stats["errors"][:10]:
                logger.warning(f"  - {err['image_id']}: {err['error']}")
            if len(stats["errors"]) > 10:
                logger.warning(f"  ... and {len(stats['errors']) - 10} more")

        if stats["failed"] > 0:
            logger.error("\nBackup completed with errors")
            sys.exit(1)
        else:
            logger.info("\nBackup completed successfully")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
