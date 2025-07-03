#!/usr/bin/env python
"""Hyperparameter sweep script for receipt training.

This script submits multiple training jobs with different hyperparameter combinations
to the SQS queue, allowing the auto-scaling system to handle the processing.
"""

import argparse
import itertools
import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Dict, List

import boto3

from receipt_trainer.jobs.job import Job, JobPriority, JobStatus
from receipt_trainer.jobs.queue import JobQueue, JobQueueConfig
from receipt_trainer.utils.pulumi import (
    create_auto_scaling_manager,
    get_auto_scaling_config,
)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("hyperparameter_sweep")


def generate_hyperparameter_combinations(
    param_grid: Dict[str, List[Any]],
) -> List[Dict[str, Any]]:
    """Generate all combinations of hyperparameters from the parameter grid.

    Args:
        param_grid: Dictionary mapping parameter names to lists of values

    Returns:
        List of dictionaries containing hyperparameter combinations
    """
    # Get parameter names and values
    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())

    # Generate all combinations
    combinations = []
    for values in itertools.product(*param_values):
        combination = dict(zip(param_names, values))
        combinations.append(combination)

    return combinations


def create_training_job(
    model_name: str,
    hyperparameters: Dict[str, Any],
    data_config: Dict[str, Any],
    job_id: str = None,
    priority: str = "medium",
    name_suffix: str = "",
) -> Job:
    """Create a training job with the specified hyperparameters.

    Args:
        model_name: Name of the model to train
        hyperparameters: Hyperparameters for training
        data_config: Data configuration
        job_id: Optional job ID (will be generated if not provided)
        priority: Job priority
        name_suffix: Suffix to add to the job name

    Returns:
        Job object ready to be submitted
    """
    # Generate job ID if not provided
    if job_id is None:
        job_id = str(uuid.uuid4())

    # Create the formatted job name
    job_name = f"Train {model_name.split('/')[-1]}"
    if name_suffix:
        job_name += f" {name_suffix}"

    # Create job config with hyperparameters
    config = {
        "model": model_name,
        "training_config": hyperparameters,
        "data_config": data_config,
        "requires_gpu": True,  # Signal that we need GPU
    }

    # Map priority string to enum
    priority_map = {
        "low": JobPriority.LOW,
        "medium": JobPriority.MEDIUM,
        "high": JobPriority.HIGH,
        "critical": JobPriority.CRITICAL,
    }
    job_priority = priority_map.get(priority.lower(), JobPriority.MEDIUM)

    # Create the job
    job = Job(
        job_id=job_id,
        name=job_name,
        type="training",
        config=config,
        priority=job_priority,
        status=JobStatus.PENDING,
        tags={
            "sweep": "true",
            "hyperparameters": json.dumps(
                {k: str(v) for k, v in hyperparameters.items()}
            ),
        },
    )

    return job


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Hyperparameter sweep for receipt training."
    )

    # Queue configuration
    parser.add_argument(
        "--stack", default="dev", help="Pulumi stack name (default: dev)"
    )
    parser.add_argument(
        "--region", help="AWS region (optional, defaults to stack region)"
    )

    # Model configuration
    parser.add_argument(
        "--model",
        default="microsoft/layoutlm-base-uncased",
        help="Model name or path (default: microsoft/layoutlm-base-uncased)",
    )

    # Hyperparameter sweep configuration
    parser.add_argument(
        "--learning-rates",
        default="5e-5,3e-5,1e-5",
        help="Comma-separated list of learning rates (default: 5e-5,3e-5,1e-5)",
    )
    parser.add_argument(
        "--batch-sizes",
        default="4,8,16",
        help="Comma-separated list of batch sizes (default: 4,8,16)",
    )
    parser.add_argument(
        "--epochs",
        default="3,5",
        help="Comma-separated list of epoch counts (default: 3,5)",
    )
    parser.add_argument(
        "--warmup-ratios",
        default="0.1,0.2",
        help="Comma-separated list of warmup ratios (default: 0.1,0.2)",
    )

    # Dataset configuration
    parser.add_argument(
        "--max-samples",
        type=int,
        default=1000,
        help="Maximum number of samples to use (default: 1000)",
    )
    parser.add_argument(
        "--no-augment", action="store_true", help="Disable data augmentation"
    )

    # Job submission options
    parser.add_argument(
        "--priority",
        choices=["low", "medium", "high", "critical"],
        default="medium",
        help="Job priority (default: medium)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print jobs without submitting them",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Start auto-scaling monitor after submitting jobs",
    )
    parser.add_argument(
        "--monitor-timeout",
        type=int,
        default=3600,
        help="Maximum time to monitor in seconds (default: 3600)",
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Parse hyperparameter lists
    param_grid = {
        "learning_rate": [float(x) for x in args.learning_rates.split(",")],
        "batch_size": [int(x) for x in args.batch_sizes.split(",")],
        "num_epochs": [int(x) for x in args.epochs.split(",")],
        "warmup_ratio": [float(x) for x in args.warmup_ratios.split(",")],
    }

    # Add fixed hyperparameters
    fixed_params = {
        "weight_decay": 0.01,
        "gradient_accumulation_steps": 1,
        "fp16": True,
        "evaluation_steps": 50,
        "save_steps": 50,
        "logging_steps": 10,
    }

    # Generate all combinations
    combinations = generate_hyperparameter_combinations(param_grid)
    logger.info(f"Generated {len(combinations)} hyperparameter combinations")

    # Create data configuration
    data_config = {
        "use_sroie": True,
        "max_samples": args.max_samples,
        "use_sliding_window": True,
        "augment": not args.no_augment,
    }

    # Get SQS queue information from Pulumi
    config = get_auto_scaling_config(args.stack)
    queue_url = config["queue_url"]
    region = args.region or config.get("region", "us-east-1")

    # Initialize queue
    queue_config = JobQueueConfig(
        queue_url=queue_url,
        aws_region=region,
    )
    queue = JobQueue(queue_config)

    # Create and submit jobs
    submitted_job_ids = []

    try:
        for i, params in enumerate(combinations):
            # Combine with fixed parameters
            full_params = {**fixed_params, **params}

            # Create job with descriptive name
            job = create_training_job(
                model_name=args.model,
                hyperparameters=full_params,
                data_config=data_config,
                priority=args.priority,
                name_suffix=f"Sweep #{i+1}/{len(combinations)}",
            )

            if args.dry_run:
                logger.info(f"[DRY RUN] Would submit job: {job.name}")
                logger.info(f"Hyperparameters: {params}")
            else:
                # Submit job
                logger.info(
                    f"Submitting job {i+1}/{len(combinations)}: {job.name}"
                )
                logger.info(f"Hyperparameters: {params}")
                queue.submit_job(job)
                submitted_job_ids.append(job.job_id)

                # Small delay between submissions to avoid SQS throttling
                time.sleep(0.5)

        logger.info(
            f"Submitted {len(submitted_job_ids)} jobs to queue: {queue_url}"
        )

        # Start monitoring if requested
        if args.monitor and not args.dry_run:
            logger.info("Starting auto-scaling monitoring...")
            manager = create_auto_scaling_manager(
                stack_name=args.stack,
                min_instances=0,
                max_instances=4,  # Allow up to 4 instances for the sweep
            )

            # Start monitoring
            thread = manager.start_monitoring(interval_seconds=30)

            try:
                # Monitor status for the specified timeout
                start_time = time.time()
                while time.time() - start_time < args.monitor_timeout:
                    status = manager.get_instance_status()
                    logger.info(
                        f"Queue depth: {status['queue_depth']}, "
                        + f"Instances: {status.get('instances_by_state', {})}"
                    )
                    time.sleep(30)
            finally:
                # Stop monitoring
                logger.info("Stopping monitoring...")
                manager.stop_monitoring()

    except KeyboardInterrupt:
        logger.info("Operation interrupted by user")
    except Exception as e:
        logger.error(f"Error during hyperparameter sweep: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
