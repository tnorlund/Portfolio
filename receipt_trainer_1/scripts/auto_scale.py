#!/usr/bin/env python
"""Command-line tool to manage auto-scaling of EC2 instances for training.

This script allows you to:
1. Start and stop auto-scaling
2. View current auto-scaling status
3. Manually adjust the number of instances
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import boto3
from receipt_trainer.utils.auto_scaling import (
    AutoScalingManager,
    generate_training_worker_user_data,
)
from receipt_trainer.utils.pulumi import create_auto_scaling_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("auto_scale")


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Manage auto-scaling of EC2 instances for training"
    )

    # Configuration source options
    config_group = parser.add_mutually_exclusive_group(required=True)
    config_group.add_argument(
        "--stack", help="Pulumi stack name (dev, prod, etc.)"
    )
    config_group.add_argument(
        "--manual-config",
        action="store_true",
        help="Provide manual configuration instead of using Pulumi stack",
    )

    # Manual AWS resource configurations (only needed with --manual-config)
    manual_group = parser.add_argument_group("Manual configuration options")
    manual_group.add_argument("--queue", help="SQS queue URL")
    manual_group.add_argument(
        "--dynamo-table", help="DynamoDB table for receipt data"
    )
    manual_group.add_argument("--ami", help="AMI ID for training instances")
    manual_group.add_argument(
        "--instance-profile", help="IAM instance profile name"
    )
    manual_group.add_argument("--subnet", help="Subnet ID for instances")
    manual_group.add_argument("--security-group", help="Security group ID")

    # Optional resource configurations
    parser.add_argument(
        "--registry-table", help="DynamoDB table for instance registry"
    )
    parser.add_argument("--key-name", help="SSH key name")
    parser.add_argument(
        "--efs-id", help="EFS file system ID for shared storage"
    )
    parser.add_argument("--s3-bucket", help="S3 bucket for artifacts")

    # Scaling parameters
    parser.add_argument(
        "--min-instances",
        type=int,
        default=1,
        help="Minimum number of instances",
    )
    parser.add_argument(
        "--max-instances",
        type=int,
        default=10,
        help="Maximum number of instances",
    )

    # Instance type options
    parser.add_argument(
        "--cpu-instances", nargs="+", help="CPU instance types to use"
    )
    parser.add_argument(
        "--gpu-instances", nargs="+", help="GPU instance types to use"
    )

    # Actions
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--start", action="store_true", help="Start auto-scaling"
    )
    group.add_argument("--stop", action="store_true", help="Stop auto-scaling")
    group.add_argument(
        "--status", action="store_true", help="Show current status"
    )
    group.add_argument(
        "--scale-up", type=int, metavar="N", help="Manually add N instances"
    )
    group.add_argument(
        "--scale-down",
        type=int,
        metavar="N",
        help="Manually remove N instances",
    )

    # Monitoring options
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Monitoring interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--daemonize",
        action="store_true",
        help="Run as daemon (background process)",
    )

    return parser.parse_args()


def create_auto_scaling_manager_from_args(args) -> AutoScalingManager:
    """Create an auto-scaling manager from the parsed arguments.

    Args:
        args: Parsed command-line arguments

    Returns:
        AutoScalingManager instance
    """
    if args.stack:
        # Use Pulumi stack outputs
        return create_auto_scaling_manager(
            stack_name=args.stack,
            min_instances=args.min_instances,
            max_instances=args.max_instances,
            cpu_instance_types=args.cpu_instances,
            gpu_instance_types=args.gpu_instances,
        )
    else:
        # Use manual configuration
        # Generate user data script
        user_data = generate_training_worker_user_data(
            queue_url=args.queue,
            dynamo_table=args.dynamo_table,
            instance_registry_table=args.registry_table,
            s3_bucket=args.s3_bucket,
            efs_id=args.efs_id,
        )

        # Create auto-scaling manager
        manager = AutoScalingManager(
            queue_url=args.queue,
            instance_ami=args.ami,
            instance_profile=args.instance_profile,
            subnet_id=args.subnet,
            security_group_id=args.security_group,
            instance_registry_table=args.registry_table,
            key_name=args.key_name,
            cpu_instance_types=args.cpu_instances,
            gpu_instance_types=args.gpu_instances,
            max_instances=args.max_instances,
            min_instances=args.min_instances,
            user_data=user_data,
            tags={
                "Name": "ReceiptTrainer-Worker",
                "Purpose": "ML Training",
                "CreatedBy": "AutoScalingManager",
            },
        )

        return manager


def start_auto_scaling(args):
    """Start auto-scaling.

    Args:
        args: Parsed command-line arguments
    """
    logger.info("Starting auto-scaling...")

    # Create auto-scaling manager
    manager = create_auto_scaling_manager_from_args(args)

    # Start monitoring
    manager.start_monitoring(interval_seconds=args.interval)

    if not args.daemonize:
        # Keep running in the foreground
        try:
            logger.info("Auto-scaling is running. Press Ctrl+C to stop.")
            while True:
                time.sleep(10)
                status = manager.get_instance_status()
                logger.info(
                    f"Queue depth: {status['queue_depth']}, "
                    f"Instances: {status['total_instances']}"
                )
        except KeyboardInterrupt:
            logger.info("Stopping auto-scaling...")
            manager.stop_monitoring()
            logger.info("Auto-scaling stopped.")
    else:
        # Just return, the thread will keep running in the background
        logger.info("Auto-scaling is running in the background.")


def stop_auto_scaling(args):
    """Stop auto-scaling.

    Args:
        args: Parsed command-line arguments
    """
    logger.info("Stopping auto-scaling...")

    # Create auto-scaling manager (needed to access the same instances)
    manager = create_auto_scaling_manager_from_args(args)

    # Stop monitoring
    manager.stop_monitoring()

    logger.info("Auto-scaling stopped.")


def show_status(args):
    """Show current auto-scaling status.

    Args:
        args: Parsed command-line arguments
    """
    # Create auto-scaling manager
    manager = create_auto_scaling_manager_from_args(args)

    # Get status
    status = manager.get_instance_status()

    # Print status
    print("\n=== Auto-Scaling Status ===")
    print(f"Queue Depth: {status['queue_depth']} messages")
    print(f"Total Instances: {status['total_instances']}")

    print("\nInstances by State:")
    for state, count in status.get("instances_by_state", {}).items():
        print(f"  {state}: {count}")

    print("\nInstances by Type:")
    for instance_type, count in status.get("instances_by_type", {}).items():
        print(f"  {instance_type}: {count}")

    print(f"\nMonitoring Active: {status['monitoring_active']}")
    print("\n")


def scale_up(args):
    """Manually scale up.

    Args:
        args: Parsed command-line arguments
    """
    count = args.scale_up
    logger.info(f"Manually scaling up by {count} instances...")

    # Create auto-scaling manager
    manager = create_auto_scaling_manager_from_args(args)

    # Launch instances
    # Use GPU instances if explicitly requested by type
    need_gpu = bool(args.gpu_instances)
    manager._launch_instances(count, need_gpu)

    logger.info(f"Scaling up initiated for {count} instances.")


def scale_down(args):
    """Manually scale down.

    Args:
        args: Parsed command-line arguments
    """
    count = args.scale_down
    logger.info(f"Manually scaling down by {count} instances...")

    # Create auto-scaling manager
    manager = create_auto_scaling_manager_from_args(args)

    # Terminate instances
    manager._terminate_instances(count)

    logger.info(f"Scaling down initiated for {count} instances.")


def main():
    """Main entry point."""
    args = parse_args()

    try:
        if args.manual_config:
            # Verify required arguments for manual configuration
            required_args = [
                "queue",
                "dynamo_table",
                "ami",
                "instance_profile",
                "subnet",
                "security_group",
            ]
            missing_args = [
                arg for arg in required_args if not getattr(args, arg)
            ]

            if missing_args:
                logger.error(
                    f"Missing required arguments for manual configuration: {', '.join(missing_args)}"
                )
                return 1

        if args.start:
            start_auto_scaling(args)
        elif args.stop:
            stop_auto_scaling(args)
        elif args.status:
            show_status(args)
        elif args.scale_up:
            scale_up(args)
        elif args.scale_down:
            scale_down(args)
        else:
            logger.error("No valid action specified")
            return 1

        return 0

    except Exception as e:
        logger.error(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
