#!/usr/bin/env python
"""Example script showing how to use auto-scaling with Pulumi stack outputs.

This example demonstrates:
1. How to fetch configuration from a Pulumi stack
2. How to initialize the auto-scaling manager
3. How to start and monitor auto-scaling
"""

import os
import sys
import time
import argparse
import logging

from receipt_trainer.utils.pulumi import create_auto_scaling_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("auto_scale_example")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Example auto-scaling script using Pulumi outputs"
    )

    parser.add_argument(
        "--stack", default="dev", help="Pulumi stack name (default: dev)"
    )
    parser.add_argument(
        "--min-instances",
        type=int,
        default=1,
        help="Minimum number of instances",
    )
    parser.add_argument(
        "--max-instances",
        type=int,
        default=5,
        help="Maximum number of instances",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Monitoring interval in seconds",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="How long to run monitoring in seconds (default: 5 minutes)",
    )

    # Optional GPU instance configuration
    parser.add_argument(
        "--use-gpu",
        action="store_true",
        help="Configure for GPU instance types",
    )

    return parser.parse_args()


def main():
    """Run the example."""
    args = parse_args()

    logger.info(f"Starting auto-scaling example with Pulumi stack: {args.stack}")

    try:
        # Set up GPU instance types if requested
        gpu_instance_types = None
        if args.use_gpu:
            gpu_instance_types = ["g4dn.xlarge", "g4dn.2xlarge", "p3.2xlarge"]
            logger.info(f"Configured for GPU instances: {gpu_instance_types}")

        # Create auto-scaling manager
        manager = create_auto_scaling_manager(
            stack_name=args.stack,
            min_instances=args.min_instances,
            max_instances=args.max_instances,
            gpu_instance_types=gpu_instance_types,
        )

        # Start monitoring
        logger.info(f"Starting auto-scaling monitoring with {args.interval}s interval")
        manager.start_monitoring(interval_seconds=args.interval)

        # Run for specified duration
        start_time = time.time()
        end_time = start_time + args.duration

        logger.info(f"Auto-scaling will run for {args.duration} seconds")

        # Monitor and report status
        while time.time() < end_time:
            try:
                # Get and log current status
                status = manager.get_instance_status()
                logger.info(
                    f"Queue depth: {status['queue_depth']}, "
                    f"Instances: {status['total_instances']} "
                    f"({', '.join(f'{k}:{v}' for k, v in status.get('instances_by_state', {}).items())})"
                )

                # Sleep for 10 seconds
                time.sleep(min(10, end_time - time.time()))

            except KeyboardInterrupt:
                logger.info("Example interrupted by user")
                break

        # Stop monitoring
        logger.info("Stopping auto-scaling monitoring")
        manager.stop_monitoring()

        # Get final status
        final_status = manager.get_instance_status()
        logger.info(
            f"Final status - Queue depth: {final_status['queue_depth']}, "
            f"Total instances: {final_status['total_instances']}"
        )

        return 0

    except Exception as e:
        logger.error(f"Error running auto-scaling example: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
