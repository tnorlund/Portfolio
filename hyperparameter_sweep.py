#!/usr/bin/env python3
"""
Hyperparameter sweep script for Receipt Trainer.

This script submits multiple training jobs with different hyperparameter configurations.
"""

import os
import uuid
import itertools
import argparse
import logging
from typing import Dict, Any, List, Tuple

from receipt_trainer.jobs.job import Job, JobStatus, JobPriority
from receipt_trainer.jobs.queue import JobQueue, JobQueueConfig
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_trainer.utils.pulumi import get_auto_scaling_config

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def generate_sweep_configs(
    param_grid: Dict[str, List[Any]],
) -> List[Dict[str, Any]]:
    """Generate all hyperparameter configurations from a parameter grid.

    Args:
        param_grid: Dictionary mapping parameter names to lists of values

    Returns:
        List of hyperparameter configurations
    """
    param_keys = list(param_grid.keys())
    param_values = list(param_grid.values())

    configs = []
    for combo in itertools.product(*param_values):
        config = {param_keys[i]: combo[i] for i in range(len(param_keys))}
        configs.append(config)

    logger.info(f"Generated {len(configs)} hyperparameter configurations")
    return configs


def submit_hyperparameter_sweep(
    model_name: str,
    param_grid: Dict[str, List[Any]],
    base_config: Dict[str, Any],
    queue_url: str,
    dynamo_table: str,
    parent_job_id: str = None,
    aws_region: str = None,
) -> Tuple[List[str], str]:
    """Submit hyperparameter sweep training jobs.

    Args:
        model_name: Name of the model to train
        param_grid: Dictionary mapping parameter names to lists of values
        base_config: Base configuration shared across all jobs
        queue_url: SQS queue URL
        dynamo_table: DynamoDB table name
        parent_job_id: Optional parent job ID for linking sweep jobs
        aws_region: AWS region (default: use environment variable)

    Returns:
        Tuple of (list of submitted job IDs, parent job ID)
    """
    # Generate all hyperparameter configurations
    sweep_configs = generate_sweep_configs(param_grid)
    logger.info(
        f"Submitting {len(sweep_configs)} hyperparameter training jobs"
    )

    # Initialize the queue
    region = aws_region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    queue_config = JobQueueConfig(
        queue_url=queue_url,
        aws_region=region,
    )
    queue = JobQueue(queue_config)

    # If parent_job_id not provided, create a sweep parent job
    if parent_job_id is None:
        parent_job_id = str(uuid.uuid4())

        # Create a DynamoDB entry for the sweep parent job if dynamo_table is provided
        if dynamo_table:
            try:
                dynamo_client = DynamoClient(dynamo_table)
                # Check if DynamoClient has addJob method
                if hasattr(dynamo_client, "addJob"):
                    sweep_job = Job(
                        job_id=parent_job_id,
                        name=f"Hyperparameter Sweep - {model_name}",
                        type="hyperparameter_sweep",
                        config={
                            "model": model_name,
                            "param_grid": param_grid,
                            "base_config": base_config,
                            "num_trials": len(sweep_configs),
                        },
                        priority=JobPriority.HIGH,
                        status=JobStatus.PENDING,
                        tags={"type": "sweep_parent"},
                    )
                    dynamo_client.addJob(sweep_job)
                    logger.info(
                        f"Created parent sweep job with ID: {parent_job_id}"
                    )
                else:
                    logger.warning(
                        "DynamoClient.addJob method not found - skipping parent job creation"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to create parent sweep job in DynamoDB: {e}"
                )

    # Submit individual jobs for each hyperparameter configuration
    job_ids = []
    for i, sweep_config in enumerate(sweep_configs):
        # Merge the base configuration with this sweep configuration
        training_config = {**base_config}

        # Update with the specific hyperparameters for this sweep
        if "training_config" not in training_config:
            training_config["training_config"] = {}

        training_config["training_config"].update(sweep_config)

        # Create job
        trial_id = str(uuid.uuid4())
        job = Job(
            job_id=trial_id,
            name=f"Sweep Trial {i+1}/{len(sweep_configs)} - {model_name}",
            type="training",
            config={
                "model": model_name,
                **training_config,
                "sweep_parent_id": parent_job_id,  # Link to parent job
                "sweep_trial_index": i,
            },
            priority=JobPriority.MEDIUM,  # Use enum value
            status=JobStatus.PENDING,
            tags={"type": "sweep_trial", "parent_id": parent_job_id},
        )

        # Submit to queue
        try:
            queue.submit_job(job)
            job_ids.append(trial_id)
            logger.info(
                f"Submitted sweep trial {i+1}/{len(sweep_configs)}, job ID: {trial_id}"
            )
        except Exception as e:
            logger.error(f"Failed to submit sweep trial {i+1}: {e}")

    logger.info(
        f"Submitted {len(job_ids)}/{len(sweep_configs)} hyperparameter training jobs"
    )
    logger.info(f"Parent sweep job ID: {parent_job_id}")

    return job_ids, parent_job_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Submit hyperparameter sweep jobs"
    )
    parser.add_argument(
        "--model", default="microsoft/layoutlm-base-uncased", help="Model name"
    )
    parser.add_argument("--queue-url", help="SQS queue URL")
    parser.add_argument("--dynamo-table", help="DynamoDB table name")
    parser.add_argument(
        "--stack", default="dev", help="Pulumi stack name (default: dev)"
    )
    parser.add_argument("--region", help="AWS region")

    args = parser.parse_args()

    # If queue_url or dynamo_table not provided, try to get from Pulumi stack
    if not args.queue_url or not args.dynamo_table:
        try:
            config = get_auto_scaling_config(args.stack)
            if not args.queue_url:
                args.queue_url = config["queue_url"]
            if not args.dynamo_table:
                args.dynamo_table = config["dynamo_table"]
        except Exception as e:
            logger.error(f"Failed to get configuration from Pulumi stack: {e}")
            if not args.queue_url:
                raise ValueError("queue_url is required")
            if not args.dynamo_table:
                raise ValueError("dynamo_table is required")

    # Define parameter grid
    param_grid = {
        "learning_rate": [1e-5, 3e-5, 5e-5],
        "batch_size": [8, 16, 32],
        "weight_decay": [0.01, 0.1],
    }

    # Define base configuration
    base_config = {
        "requires_gpu": True,
        "training_config": {
            "num_epochs": 3,
            "logging_steps": 100,
            "evaluation_steps": 500,
            "save_steps": 500,
            "fp16": True,
        },
        "data_config": {
            "use_sroie": True,
            "max_samples": 500,  # Limit samples for faster training
            "use_sliding_window": True,
        },
    }

    # Submit sweep
    job_ids, parent_job_id = submit_hyperparameter_sweep(
        model_name=args.model,
        param_grid=param_grid,
        base_config=base_config,
        queue_url=args.queue_url,
        dynamo_table=args.dynamo_table,
        aws_region=args.region,
    )

    print(f"Submitted {len(job_ids)} hyperparameter sweep jobs")
    print(f"Parent sweep job ID: {parent_job_id}")
