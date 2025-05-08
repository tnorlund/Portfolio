"""Utility for submitting training jobs to SQS queue."""

import os
import json
import uuid
import logging
import argparse
from typing import Dict, Any, Optional, List, Union, Tuple
from pathlib import Path

import yaml
import itertools

from receipt_trainer.config import TrainingConfig, DataConfig
from receipt_trainer.jobs.job import Job, JobStatus, JobPriority
from receipt_trainer.jobs.queue import JobQueue, JobQueueConfig

logger = logging.getLogger(__name__)


def load_job_config(config_file: str) -> Dict[str, Any]:
    """Load job configuration from YAML or JSON file.

    Args:
        config_file: Path to configuration file

    Returns:
        Job configuration dictionary
    """
    filepath = Path(config_file)

    if not filepath.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    # Load configuration based on file extension
    if filepath.suffix.lower() in [".yaml", ".yml"]:
        with open(filepath, "r") as f:
            return yaml.safe_load(f)
    elif filepath.suffix.lower() == ".json":
        with open(filepath, "r") as f:
            return json.load(f)
    else:
        raise ValueError(
            f"Unsupported configuration file format: {filepath.suffix}"
        )


def submit_training_job(
    model_name: str,
    training_config: Union[TrainingConfig, Dict[str, Any]],
    data_config: Union[DataConfig, Dict[str, Any]],
    queue_url: str,
    priority: str = "medium",
    job_name: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
    region: Optional[str] = None,
) -> str:
    """Submit a training job to the SQS queue.

    Args:
        model_name: Name or path of the model
        training_config: Training configuration
        data_config: Data configuration
        queue_url: SQS queue URL
        priority: Job priority ("low", "medium", "high", "critical")
        job_name: Job name (optional)
        description: Job description (optional)
        tags: Job tags (optional)
        region: AWS region (optional)

    Returns:
        Job ID
    """
    # Generate a unique job ID
    job_id = str(uuid.uuid4())

    # Create default job name if not provided
    if not job_name:
        model_short_name = model_name.split("/")[-1]
        job_name = f"Train {model_short_name}"

    # Create default description if not provided
    if not description:
        description = f"Training job for {model_name}"

    # Convert configuration objects to dictionaries if needed
    if isinstance(training_config, TrainingConfig):
        training_config_dict = training_config.to_dict()
    else:
        training_config_dict = training_config

    if isinstance(data_config, DataConfig):
        data_config_dict = data_config.to_dict()
    else:
        data_config_dict = data_config

    # Create job configuration
    job_config = {
        "model": model_name,
        "training_config": training_config_dict,
        "data_config": data_config_dict,
    }

    # Create job
    job = Job(
        job_id=job_id,
        name=job_name,
        description=description,
        job_config=job_config,
        priority=priority,
        tags=tags or {},
    )

    # Initialize queue
    config = JobQueueConfig(
        queue_url=queue_url,
        aws_region=region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
    queue = JobQueue(config)

    # Submit job
    try:
        queue.submit_job(job)
        logger.info(f"Submitted job {job_id} to queue {queue_url}")
        return job_id
    except Exception as e:
        logger.error(f"Failed to submit job: {str(e)}")
        raise


def submit_job_from_config_file(
    config_file: str,
    queue_url: str,
    priority: str = "medium",
    region: Optional[str] = None,
) -> str:
    """Submit a training job from a configuration file.

    Args:
        config_file: Path to configuration file (YAML or JSON)
        queue_url: SQS queue URL
        priority: Job priority ("low", "medium", "high", "critical")
        region: AWS region (optional)

    Returns:
        Job ID
    """
    # Load configuration
    config = load_job_config(config_file)

    # Extract job details
    model_name = config.get("model", config.get("model_name"))
    if not model_name:
        raise ValueError("Model name not specified in configuration")

    # Submit job
    return submit_training_job(
        model_name=model_name,
        training_config=config.get("training_config", {}),
        data_config=config.get("data_config", {}),
        queue_url=queue_url,
        priority=priority,
        job_name=config.get("job_name"),
        description=config.get("description"),
        tags=config.get("tags"),
        region=region,
    )


def submit_hyperparameter_sweep(
    model_name: str,
    param_grid: Dict[str, List[Any]],
    base_config: Dict[str, Any] = None,
    queue_url: str = None,
    dynamo_table: str = None,
    parent_job_id: str = None,
    region: Optional[str] = None,
) -> Tuple[List[str], str]:
    """Submit a hyperparameter sweep as multiple training jobs to the SQS queue.

    Args:
        model_name: Name or path of the model
        param_grid: Dictionary mapping parameter names to lists of values
        base_config: Base configuration to use for all jobs
        queue_url: SQS queue URL
        dynamo_table: DynamoDB table name
        parent_job_id: Optional parent job ID for tracking related jobs
        region: AWS region (optional)

    Returns:
        Tuple of (list of submitted job IDs, parent job ID)
    """
    # Generate all hyperparameter configurations
    param_keys = list(param_grid.keys())
    param_values = list(param_grid.values())

    sweep_configs = []
    for combo in itertools.product(*param_values):
        config = {param_keys[i]: combo[i] for i in range(len(param_keys))}
        sweep_configs.append(config)

    logger.info(
        f"Generated {len(sweep_configs)} hyperparameter configurations"
    )

    # Initialize queue
    aws_region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    config = JobQueueConfig(
        queue_url=queue_url,
        aws_region=aws_region,
    )
    queue = JobQueue(config)

    # Ensure base_config exists
    if base_config is None:
        base_config = {}

    # If parent_job_id not provided, create a sweep parent job
    if parent_job_id is None:
        parent_job_id = str(uuid.uuid4())

        # Create a DynamoDB entry for the sweep parent job if dynamo_table is provided
        if dynamo_table:
            try:
                # Create parent job metadata in DynamoDB
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

                # Add to DynamoDB if possible
                # Note that different versions of the system might use different methods
                # We try multiple approaches to be compatible
                try:
                    # Try to use a job service if available
                    if "job_service" in globals():
                        globals()["job_service"].create_job(sweep_job)
                    # Otherwise try to use DynamoDB client directly
                    else:
                        from receipt_dynamo.data.dynamo_client import (
                            DynamoClient,
                        )

                        dynamo_client = DynamoClient(dynamo_table)

                        if hasattr(dynamo_client, "putJob"):
                            dynamo_client.putJob(sweep_job)
                        elif hasattr(dynamo_client, "addJob"):
                            dynamo_client.addJob(sweep_job)

                    logger.info(
                        f"Created parent sweep job with ID: {parent_job_id}"
                    )
                except Exception as inner_e:
                    logger.warning(
                        f"Failed to store parent sweep job: {inner_e}"
                    )
            except Exception as e:
                logger.warning(f"Failed to create parent sweep job: {e}")

    # Submit individual jobs for each hyperparameter configuration
    job_ids = []
    for i, sweep_config in enumerate(sweep_configs):
        # Create job name
        trial_name = f"Sweep Trial {i+1}/{len(sweep_configs)} - {model_name}"

        # Create training configuration with this sweep config
        training_config = base_config.get("training_config", {}).copy()
        training_config.update(sweep_config)

        # Create data configuration
        data_config = base_config.get("data_config", {})

        # Create full job configuration
        job_config = {
            "model": model_name,
            "training_config": training_config,
            "data_config": data_config,
            "sweep_parent_id": parent_job_id,
            "sweep_trial_index": i,
            "requires_gpu": base_config.get("requires_gpu", True),
        }

        # Submit individual training job
        try:
            # Create job
            job = Job(
                job_id=str(uuid.uuid4()),
                name=trial_name,
                type="training",
                config=job_config,
                priority=JobPriority.MEDIUM,
                status=JobStatus.PENDING,
                tags={
                    "type": "sweep_trial",
                    "parent_id": parent_job_id,
                    "trial_index": str(i),
                },
            )

            # Submit job to queue
            job_id = queue.submit_job(job)
            job_ids.append(job_id)
            logger.info(
                f"Submitted sweep trial {i+1}/{len(sweep_configs)}, job ID: {job_id}"
            )
        except Exception as e:
            logger.error(f"Failed to submit sweep trial {i+1}: {e}")

    logger.info(
        f"Submitted {len(job_ids)}/{len(sweep_configs)} hyperparameter training jobs"
    )
    logger.info(f"Parent sweep job ID: {parent_job_id}")

    return job_ids, parent_job_id


def main():
    """Command-line entry point for job submission."""
    parser = argparse.ArgumentParser(
        description="Submit training jobs to SQS queue"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to job configuration file (YAML or JSON)",
    )
    parser.add_argument("--queue-url", required=True, help="SQS queue URL")
    parser.add_argument(
        "--priority",
        choices=["low", "medium", "high", "critical"],
        default="medium",
        help="Job priority",
    )
    parser.add_argument("--region", help="AWS region")
    parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        # Submit job
        job_id = submit_job_from_config_file(
            config_file=args.config,
            queue_url=args.queue_url,
            priority=args.priority,
            region=args.region,
        )

        print(f"Successfully submitted job with ID: {job_id}")
    except Exception as e:
        logger.error(f"Error submitting job: {str(e)}")
        import traceback

        logger.debug(traceback.format_exc())
        print(f"Failed to submit job: {str(e)}")
        exit(1)


if __name__ == "__main__":
    main()
