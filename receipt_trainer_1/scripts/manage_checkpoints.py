#!/usr/bin/env python
"""
Checkpoint Management Script

This script provides tools for managing ML model checkpoints stored on EFS:
- List all checkpoints for a job
- Compare checkpoints and their metrics
- Delete old or unnecessary checkpoints
- Mark a checkpoint as the best
- Download checkpoints locally

Usage:
  python manage_checkpoints.py list --job-id JOB_ID [--dynamo-table TABLE]
  python manage_checkpoints.py compare --job-id JOB_ID [--metric METRIC] [--dynamo-table TABLE]
  python manage_checkpoints.py delete --job-id JOB_ID --checkpoint NAME [--dynamo-table TABLE]
  python manage_checkpoints.py delete-older-than --job-id JOB_ID --days DAYS [--dynamo-table TABLE]
  python manage_checkpoints.py mark-best --job-id JOB_ID --checkpoint NAME [--dynamo-table TABLE]
  python manage_checkpoints.py download --job-id JOB_ID [--checkpoint NAME] [--output DIR] [--dynamo-table TABLE]
"""

import os
import sys
import json
import time
import argparse
import datetime
import logging
from typing import List, Dict, Any, Optional

from receipt_trainer.utils.checkpoint import CheckpointManager
from receipt_trainer.utils.infrastructure import EFSManager
from receipt_dynamo.services.job_service import JobService
from tabulate import tabulate

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("checkpoint-manager")


def setup_parser():
    """Set up command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Manage ML model checkpoints stored on EFS"
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Common arguments for all commands
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--job-id",
        type=str,
        required=True,
        help="Job ID to manage checkpoints for",
    )
    common_parser.add_argument(
        "--dynamo-table", type=str, help="DynamoDB table for job tracking"
    )
    common_parser.add_argument(
        "--efs-mount-point",
        type=str,
        default="/mnt/checkpoints",
        help="EFS mount point",
    )

    # List command
    list_parser = subparsers.add_parser(
        "list", parents=[common_parser], help="List all checkpoints for a job"
    )
    list_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )

    # Compare command
    compare_parser = subparsers.add_parser(
        "compare",
        parents=[common_parser],
        help="Compare checkpoints and their metrics",
    )
    compare_parser.add_argument(
        "--metric", type=str, help="Metric to compare (e.g., loss, accuracy)"
    )
    compare_parser.add_argument(
        "--sort-by",
        type=str,
        default="created_at",
        help="Field to sort by (e.g., created_at, step, metric)",
    )
    compare_parser.add_argument(
        "--reverse", action="store_true", help="Reverse sort order"
    )

    # Delete command
    delete_parser = subparsers.add_parser(
        "delete", parents=[common_parser], help="Delete a specific checkpoint"
    )
    delete_parser.add_argument(
        "--checkpoint",
        type=str,
        required=True,
        help="Checkpoint name to delete",
    )

    # Delete older than command
    delete_older_parser = subparsers.add_parser(
        "delete-older-than",
        parents=[common_parser],
        help="Delete checkpoints older than specified days",
    )
    delete_older_parser.add_argument(
        "--days",
        type=int,
        required=True,
        help="Delete checkpoints older than this many days",
    )
    delete_older_parser.add_argument(
        "--keep-best",
        action="store_true",
        help="Keep the best checkpoint even if it's older",
    )

    # Mark best command
    mark_best_parser = subparsers.add_parser(
        "mark-best",
        parents=[common_parser],
        help="Mark a checkpoint as the best",
    )
    mark_best_parser.add_argument(
        "--checkpoint",
        type=str,
        required=True,
        help="Checkpoint name to mark as best",
    )

    # Download command
    download_parser = subparsers.add_parser(
        "download", parents=[common_parser], help="Download a checkpoint"
    )
    download_parser.add_argument(
        "--checkpoint",
        type=str,
        help="Checkpoint name to download (defaults to best)",
    )
    download_parser.add_argument(
        "--best", action="store_true", help="Download the best checkpoint"
    )
    download_parser.add_argument(
        "--latest", action="store_true", help="Download the latest checkpoint"
    )
    download_parser.add_argument(
        "--output", type=str, default="./checkpoint", help="Output directory"
    )

    # Sync command
    sync_parser = subparsers.add_parser(
        "sync",
        parents=[common_parser],
        help="Sync checkpoint metadata from DynamoDB",
    )

    return parser


def create_checkpoint_manager(args):
    """Create and initialize checkpoint manager."""
    # First check if EFS is mounted
    if not EFSManager.is_efs_mounted(args.efs_mount_point):
        logger.error(f"EFS not mounted at {args.efs_mount_point}")
        logger.info("Attempting to mount EFS...")

        # Try to mount EFS
        efs_dns_name = os.environ.get("EFS_DNS_NAME")
        checkpoints_ap_id = os.environ.get("CHECKPOINTS_ACCESS_POINT_ID")

        if not efs_dns_name or not checkpoints_ap_id:
            logger.error(
                "EFS environment variables not set. Please set EFS_DNS_NAME and CHECKPOINTS_ACCESS_POINT_ID"
            )
            sys.exit(1)

        mount_success = EFSManager.mount_efs(
            efs_dns_name, args.efs_mount_point, checkpoints_ap_id
        )

        if not mount_success:
            logger.error("Failed to mount EFS. Please check your configuration.")
            sys.exit(1)
        else:
            logger.info(f"Successfully mounted EFS at {args.efs_mount_point}")
    else:
        logger.info(f"EFS is already mounted at {args.efs_mount_point}")

    # Create checkpoint manager
    try:
        checkpoint_manager = CheckpointManager(
            job_id=args.job_id,
            efs_mount_point=args.efs_mount_point,
            dynamo_table=args.dynamo_table,
        )

        # Sync with DynamoDB if table is provided
        if args.dynamo_table:
            logger.info("Syncing with DynamoDB...")
            checkpoint_manager.sync_from_dynamo()

        return checkpoint_manager
    except Exception as e:
        logger.error(f"Failed to create checkpoint manager: {e}")
        sys.exit(1)


def format_metrics(metrics: Dict[str, Any]) -> str:
    """Format metrics dictionary as a string."""
    if not metrics:
        return "None"

    return ", ".join(
        f"{k}={v:.4f}" if isinstance(v, (float)) else f"{k}={v}"
        for k, v in metrics.items()
    )


def list_checkpoints(args):
    """List all checkpoints for a job."""
    cm = create_checkpoint_manager(args)
    checkpoints = cm.list_checkpoints()

    if not checkpoints:
        logger.info(f"No checkpoints found for job {args.job_id}")
        return

    # Sort by created_at date (newest first)
    checkpoints = sorted(
        checkpoints, key=lambda x: x.get("created_at", ""), reverse=True
    )

    if args.format == "json":
        print(json.dumps(checkpoints, indent=2))
    else:
        # Create table
        headers = ["Name", "Created", "Step", "Epoch", "Metrics", "Best"]
        rows = []

        for cp in checkpoints:
            created_at = cp.get("created_at", "")
            if created_at:
                # Convert ISO format to readable date
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                    created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass

            rows.append(
                [
                    cp.get("name", ""),
                    created_at,
                    cp.get("step", ""),
                    cp.get("epoch", ""),
                    format_metrics(cp.get("metrics", {})),
                    "✓" if cp.get("is_best", False) else "",
                ]
            )

        print(tabulate(rows, headers=headers, tablefmt="grid"))
        print(f"Total checkpoints: {len(checkpoints)}")


def compare_checkpoints(args):
    """Compare checkpoints based on metrics."""
    cm = create_checkpoint_manager(args)
    checkpoints = cm.list_checkpoints()

    if not checkpoints:
        logger.info(f"No checkpoints found for job {args.job_id}")
        return

    # Define sort key function
    if args.sort_by == "created_at":
        sort_key = lambda x: x.get("created_at", "")
    elif args.sort_by == "step":
        sort_key = lambda x: x.get("step", 0)
    elif args.sort_by == "metric" and args.metric:
        sort_key = lambda x: x.get("metrics", {}).get(args.metric, 0)
    else:
        sort_key = lambda x: x.get("created_at", "")

    # Sort checkpoints
    checkpoints = sorted(checkpoints, key=sort_key, reverse=args.reverse)

    # Filter metrics if specific metric requested
    if args.metric:
        logger.info(f"Comparing checkpoints by metric: {args.metric}")

        # Create table focused on the specific metric
        headers = ["Name", "Created", "Step", f"{args.metric}", "Best"]
        rows = []

        for cp in checkpoints:
            created_at = cp.get("created_at", "")
            if created_at:
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                    created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass

            metric_value = cp.get("metrics", {}).get(args.metric, "N/A")
            if isinstance(metric_value, float):
                metric_value = f"{metric_value:.6f}"

            rows.append(
                [
                    cp.get("name", ""),
                    created_at,
                    cp.get("step", ""),
                    metric_value,
                    "✓" if cp.get("is_best", False) else "",
                ]
            )

        print(tabulate(rows, headers=headers, tablefmt="grid"))
    else:
        # Show all metrics
        headers = ["Name", "Created", "Step", "Epoch", "Metrics", "Best"]
        rows = []

        for cp in checkpoints:
            created_at = cp.get("created_at", "")
            if created_at:
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                    created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass

            rows.append(
                [
                    cp.get("name", ""),
                    created_at,
                    cp.get("step", ""),
                    cp.get("epoch", ""),
                    format_metrics(cp.get("metrics", {})),
                    "✓" if cp.get("is_best", False) else "",
                ]
            )

        print(tabulate(rows, headers=headers, tablefmt="grid"))


def delete_checkpoint(args):
    """Delete a specific checkpoint."""
    cm = create_checkpoint_manager(args)

    # Check if checkpoint exists
    checkpoints = cm.list_checkpoints()
    checkpoint_names = [cp.get("name", "") for cp in checkpoints]

    if args.checkpoint not in checkpoint_names:
        logger.error(f"Checkpoint '{args.checkpoint}' not found")
        return

    # Check if it's the best checkpoint
    is_best = False
    for cp in checkpoints:
        if cp.get("name") == args.checkpoint and cp.get("is_best", False):
            is_best = True
            break

    if is_best:
        logger.warning(
            f"Checkpoint '{args.checkpoint}' is marked as the best checkpoint"
        )
        confirm = input("Are you sure you want to delete the best checkpoint? (y/N): ")
        if confirm.lower() != "y":
            logger.info("Deletion cancelled")
            return

    # Delete the checkpoint
    result = cm.delete_checkpoint(args.checkpoint)

    if result:
        logger.info(f"Successfully deleted checkpoint '{args.checkpoint}'")
    else:
        logger.error(f"Failed to delete checkpoint '{args.checkpoint}'")


def delete_older_than(args):
    """Delete checkpoints older than specified days."""
    cm = create_checkpoint_manager(args)
    checkpoints = cm.list_checkpoints()

    if not checkpoints:
        logger.info(f"No checkpoints found for job {args.job_id}")
        return

    # Calculate cutoff date
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=args.days)
    cutoff_iso = cutoff_date.isoformat()

    checkpoints_to_delete = []
    for cp in checkpoints:
        created_at = cp.get("created_at", "")
        is_best = cp.get("is_best", False)

        # Skip if no creation date
        if not created_at:
            continue

        # Skip best checkpoint if keep-best flag is set
        if is_best and args.keep_best:
            continue

        # Check if older than cutoff
        if created_at < cutoff_iso:
            checkpoints_to_delete.append(cp.get("name"))

    if not checkpoints_to_delete:
        logger.info(f"No checkpoints older than {args.days} days found")
        return

    logger.info(
        f"Found {len(checkpoints_to_delete)} checkpoints older than {args.days} days"
    )
    for name in checkpoints_to_delete:
        logger.info(f"  - {name}")

    confirm = input(
        f"Are you sure you want to delete these {len(checkpoints_to_delete)} checkpoints? (y/N): "
    )
    if confirm.lower() != "y":
        logger.info("Deletion cancelled")
        return

    # Delete checkpoints
    deleted_count = 0
    for name in checkpoints_to_delete:
        result = cm.delete_checkpoint(name)
        if result:
            deleted_count += 1
            logger.info(f"Deleted checkpoint '{name}'")
        else:
            logger.error(f"Failed to delete checkpoint '{name}'")

    logger.info(f"Deleted {deleted_count} of {len(checkpoints_to_delete)} checkpoints")


def mark_best_checkpoint(args):
    """Mark a checkpoint as the best."""
    cm = create_checkpoint_manager(args)

    # Check if checkpoint exists
    checkpoints = cm.list_checkpoints()
    checkpoint_names = [cp.get("name", "") for cp in checkpoints]

    if args.checkpoint not in checkpoint_names:
        logger.error(f"Checkpoint '{args.checkpoint}' not found")
        return

    # Mark as best
    result = cm.mark_as_best(args.checkpoint)

    if result:
        logger.info(f"Successfully marked checkpoint '{args.checkpoint}' as best")

        # Update DynamoDB if table is provided
        if args.dynamo_table:
            logger.info("Updated best checkpoint in DynamoDB")
    else:
        logger.error(f"Failed to mark checkpoint '{args.checkpoint}' as best")


def download_checkpoint(args):
    """Download a checkpoint to a local directory."""
    cm = create_checkpoint_manager(args)

    # Determine which checkpoint to download
    checkpoint_path = None

    if args.checkpoint:
        # First check if the named checkpoint exists
        checkpoints = cm.list_checkpoints()
        checkpoint_dir_path = os.path.join(cm.job_checkpoint_dir, args.checkpoint)
        if os.path.exists(checkpoint_dir_path):
            checkpoint_path = checkpoint_dir_path
        else:
            logger.error(f"Checkpoint '{args.checkpoint}' not found")
            return
    elif args.best:
        # Get best checkpoint
        checkpoint_path = cm.get_best_checkpoint()
        if not checkpoint_path:
            logger.error("No best checkpoint found")
            return
    elif args.latest:
        # Get latest checkpoint
        checkpoint_path = cm.get_latest_checkpoint()
        if not checkpoint_path:
            logger.error("No checkpoints found")
            return
    else:
        # Default to best checkpoint
        checkpoint_path = cm.get_best_checkpoint()
        if not checkpoint_path:
            # Try latest if no best
            checkpoint_path = cm.get_latest_checkpoint()
            if not checkpoint_path:
                logger.error("No checkpoints found")
                return
            else:
                logger.info("No best checkpoint found, using latest checkpoint")
        else:
            logger.info("Using best checkpoint")

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Download checkpoint
    logger.info(f"Downloading checkpoint from {checkpoint_path} to {args.output}")
    result = cm.load_checkpoint(dest_dir=args.output, checkpoint_path=checkpoint_path)

    if result:
        logger.info(f"Successfully downloaded checkpoint to {args.output}")
    else:
        logger.error("Failed to download checkpoint")


def sync_checkpoints(args):
    """Sync checkpoint metadata from DynamoDB."""
    if not args.dynamo_table:
        logger.error("DynamoDB table name is required for sync")
        return

    cm = create_checkpoint_manager(args)
    result = cm.sync_from_dynamo()

    if result:
        logger.info("Successfully synced checkpoint metadata from DynamoDB")
    else:
        logger.error("Failed to sync checkpoint metadata from DynamoDB")


def main():
    """Main entry point."""
    parser = setup_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Execute command
    if args.command == "list":
        list_checkpoints(args)
    elif args.command == "compare":
        compare_checkpoints(args)
    elif args.command == "delete":
        delete_checkpoint(args)
    elif args.command == "delete-older-than":
        delete_older_than(args)
    elif args.command == "mark-best":
        mark_best_checkpoint(args)
    elif args.command == "download":
        download_checkpoint(args)
    elif args.command == "sync":
        sync_checkpoints(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
