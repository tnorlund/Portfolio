"""Worker module for processing training jobs from SQS queue."""

import argparse
import json
import logging
import os
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional, Union

from receipt_dynamo.services.job_service import JobService
from receipt_trainer import DataConfig, ReceiptTrainer, TrainingConfig
from receipt_trainer.jobs import (Job, JobQueue, JobQueueConfig,
                                  JobRetryStrategy)
from receipt_trainer.utils.checkpoint import CheckpointManager
from receipt_trainer.utils.infrastructure import (EC2Metadata, EFSManager,
                                                  SpotInstanceHandler,
                                                  TrainingEnvironment)

logger = logging.getLogger(__name__)


def job_config_to_trainer_params(job_config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert job configuration to ReceiptTrainer parameters.

    Args:
        job_config: Job configuration dictionary

    Returns:
        Dictionary of ReceiptTrainer parameters
    """
    params = {}

    # Extract model name
    params["model_name"] = job_config.get(
        "model",
        job_config.get("model_name", "microsoft/layoutlm-base-uncased"),
    )

    # Extract training config
    train_cfg = job_config.get("training_config", {})
    if train_cfg:
        training_config = TrainingConfig()

        # Set configuration parameters
        for key, value in train_cfg.items():
            if hasattr(training_config, key):
                setattr(training_config, key, value)

        params["training_config"] = training_config

    # Extract data config
    data_cfg = job_config.get("data_config", {})
    if data_cfg:
        data_config = DataConfig()

        # Set configuration parameters
        for key, value in data_cfg.items():
            if hasattr(data_config, key):
                setattr(data_config, key, value)

        params["data_config"] = data_config

    return params


def process_training_job(
    job: Job,
    receipt_handle: str,
    dynamo_table: str,
    job_queue: JobQueue,
    training_env: Optional[TrainingEnvironment] = None,
) -> bool:
    """Process a single training job.

    Args:
        job: Job to process
        receipt_handle: SQS receipt handle
        dynamo_table: DynamoDB table name
        job_queue: JobQueue instance
        training_env: Optional TrainingEnvironment

    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"Processing job {job.job_id}: {job.name}")

        # Parse job configuration
        job_config = job.config or {}
        trainer_params = job_config_to_trainer_params(job_config)

        # Set up training environment if not provided
        if not training_env:
            training_env = TrainingEnvironment(
                job_id=job.job_id,
                registry_table=os.environ.get("INSTANCE_REGISTRY_TABLE"),
                setup_efs=True,
                handle_spot=True,
                enable_coordination=True,  # Enable enhanced coordination
            )

        # Register this job with the cluster if coordination is enabled
        if training_env.cluster_manager:
            register_job_with_cluster(training_env.cluster_manager, job)

        # Initialize checkpoint manager
        checkpoint_manager = None
        if "checkpoint_dir" in job_config:
            checkpoint_dir = job_config["checkpoint_dir"]
        else:
            checkpoint_dir = f"/mnt/checkpoints/{job.job_id}"

        if os.path.exists(os.path.dirname(checkpoint_dir)):
            checkpoint_manager = CheckpointManager(
                checkpoint_dir,
                job_id=job.job_id,
                job_service=JobService(dynamo_table),
            )

            # Add checkpoint manager to trainer params
            trainer_params["checkpoint_manager"] = checkpoint_manager

        # Initialize trainer
        trainer = ReceiptTrainer(**trainer_params)

        # Set up job queue and receipt handle for heartbeats
        trainer.job_queue = job_queue
        trainer.job_receipt_handle = receipt_handle
        trainer.job_id = job.job_id

        # Start heartbeat thread for SQS visibility timeout
        trainer.start_heartbeat_thread()

        # Start training
        result = trainer.train()

        # Update job status (if job service available)
        try:
            job_service = JobService(dynamo_table)
            job_service.updateJobStatus(job.job_id, "completed")
        except Exception as e:
            logger.error(f"Failed to update job status: {e}")

        # Report job completion to cluster manager
        if training_env.cluster_manager:
            report_job_completion(training_env.cluster_manager, job, result)

        # Clean up
        trainer.cleanup()

        logger.info(f"Job {job.job_id} completed successfully")
        return True

    except Exception as e:
        logger.error(f"Error processing job {job.job_id}: {e}")
        logger.error(traceback.format_exc())

        # Update job status (if job service available)
        try:
            job_service = JobService(dynamo_table)
            job_service.updateJobStatus(
                job.job_id,
                "failed",
                {"error": str(e), "traceback": traceback.format_exc()},
            )
        except Exception as update_error:
            logger.error(f"Failed to update job status: {update_error}")

        # Report job failure to cluster manager
        if training_env and training_env.cluster_manager:
            report_job_failure(training_env.cluster_manager, job, str(e))

        return False


def register_job_with_cluster(cluster_manager, job: Job) -> None:
    """Register a job with the cluster manager.

    Args:
        cluster_manager: ClusterManager instance
        job: Job to register
    """
    try:
        logger.info(f"Registering job {job.job_id} with cluster manager")
        # Add to active jobs if we're the leader
        if cluster_manager.is_leader:
            cluster_manager.active_training_jobs[job.job_id] = {
                "job_id": job.job_id,
                "name": job.name,
                "start_time": int(time.time()),
                "status": "running",
                "instance_id": EC2Metadata.get_instance_id(),
            }
    except Exception as e:
        logger.error(f"Error registering job with cluster: {e}")


def report_job_completion(cluster_manager, job: Job, result: Dict[str, Any]) -> None:
    """Report job completion to the cluster manager.

    Args:
        cluster_manager: ClusterManager instance
        job: Completed job
        result: Training result
    """
    try:
        logger.info(f"Reporting job {job.job_id} completion to cluster manager")

        # Update active jobs if we're the leader
        if (
            cluster_manager.is_leader
            and job.job_id in cluster_manager.active_training_jobs
        ):
            cluster_manager.active_training_jobs[job.job_id].update(
                {
                    "status": "completed",
                    "end_time": int(time.time()),
                    "result": result,
                }
            )

        # Submit a notification task for other instances
        cluster_manager.coordinator.submit_task(
            "cluster_state",
            {
                "action": "job_completed",
                "job_id": job.job_id,
                "timestamp": int(time.time()),
                "result_summary": {
                    k: v
                    for k, v in result.items()
                    if k in ["final_loss", "accuracy", "f1_score"]
                },
            },
        )
    except Exception as e:
        logger.error(f"Error reporting job completion to cluster: {e}")


def report_job_failure(cluster_manager, job: Job, error: str) -> None:
    """Report job failure to the cluster manager.

    Args:
        cluster_manager: ClusterManager instance
        job: Failed job
        error: Error message
    """
    try:
        logger.info(f"Reporting job {job.job_id} failure to cluster manager")

        # Update active jobs if we're the leader
        if (
            cluster_manager.is_leader
            and job.job_id in cluster_manager.active_training_jobs
        ):
            cluster_manager.active_training_jobs[job.job_id].update(
                {
                    "status": "failed",
                    "end_time": int(time.time()),
                    "error": error,
                }
            )

        # Submit a notification task for other instances
        cluster_manager.coordinator.submit_task(
            "cluster_state",
            {
                "action": "job_failed",
                "job_id": job.job_id,
                "timestamp": int(time.time()),
                "error": error,
            },
        )
    except Exception as e:
        logger.error(f"Error reporting job failure to cluster: {e}")


def handle_leader_election(training_env: TrainingEnvironment) -> None:
    """Handle becoming the leader instance.

    Args:
        training_env: TrainingEnvironment instance
    """
    logger.info("This instance is now the leader")

    # Perform leader-specific setup
    # For example, start monitoring jobs across the cluster
    try:
        # Check for stalled jobs from previous leaders
        recover_stalled_jobs(training_env)

        # Set up periodic health check for all instances
        schedule_cluster_health_check(training_env)
    except Exception as e:
        logger.error(f"Error in leader setup: {e}")


def recover_stalled_jobs(training_env: TrainingEnvironment) -> None:
    """Recover stalled jobs from previous leaders.

    Args:
        training_env: TrainingEnvironment instance
    """
    logger.info("Checking for stalled jobs to recover")

    try:
        # Get cluster state
        cluster_state = training_env.get_cluster_state()

        # Look for jobs that were running on instances that are no longer active
        active_instances = set(cluster_state.get("instances", {}).keys())

        # Check job status in DynamoDB
        job_service = JobService(os.environ.get("DYNAMODB_TABLE"))
        jobs = job_service.getAllJobs(status="running")

        for job in jobs:
            instance_id = job.get("instance_id")
            job_id = job.get("job_id")

            if instance_id and instance_id not in active_instances and job_id:
                logger.warning(
                    f"Found stalled job {job_id} from inactive instance {instance_id}"
                )

                # Update job status to 'pending' to allow reprocessing
                job_service.updateJobStatus(job_id, "pending")

                # Notify the cluster
                training_env.cluster_manager.coordinator.submit_task(
                    "cluster_state",
                    {
                        "action": "job_recovered",
                        "job_id": job_id,
                        "previous_instance": instance_id,
                        "timestamp": int(time.time()),
                    },
                )
    except Exception as e:
        logger.error(f"Error recovering stalled jobs: {e}")


def schedule_cluster_health_check(training_env: TrainingEnvironment) -> None:
    """Schedule periodic cluster health checks.

    Args:
        training_env: TrainingEnvironment instance
    """
    logger.info("Setting up periodic cluster health checks")

    def health_check_task():
        # This function will be called by the coordinator's task system
        return {
            "action": "health_check",
            "timestamp": int(time.time()),
            "message": "Please report your status",
        }

    # Register the task handler
    training_env.register_task_handler(
        "health_check",
        lambda params: {
            "status": "healthy",
            "metrics": (
                training_env.instance_coordinator.health_metrics
                if training_env.instance_coordinator
                else {}
            ),
            "timestamp": int(time.time()),
        },
    )


def setup_coordination_handlers(training_env: TrainingEnvironment) -> None:
    """Set up handlers for coordinated tasks.

    Args:
        training_env: TrainingEnvironment instance
    """
    logger.info("Setting up coordination handlers")

    # Register leader election handler
    training_env.register_leader_handler(lambda: handle_leader_election(training_env))

    # Register coordination task handlers
    training_env.register_task_handler(
        "preprocess_dataset", handle_dataset_preprocessing
    )

    training_env.register_task_handler("training_job", handle_training_task)

    training_env.register_task_handler("evaluation", handle_evaluation_task)


def handle_dataset_preprocessing(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle dataset preprocessing task.

    Args:
        params: Task parameters

    Returns:
        Task result
    """
    logger.info(f"Handling dataset preprocessing task: {params.get('dataset_name')}")

    # Implementation for dataset preprocessing
    # This would typically download data, apply transformations, and save the processed data

    return {
        "success": True,
        "dataset_name": params.get("dataset_name"),
        "processed_records": 1000,  # Placeholder
        "processing_time": 120,  # Placeholder
    }


def handle_training_task(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle training task.

    Args:
        params: Task parameters

    Returns:
        Task result
    """
    logger.info(f"Handling training task: {params.get('job_id')}")

    # Implementation for training task
    # This would typically set up a trainer and start training

    return {
        "success": True,
        "job_id": params.get("job_id"),
        "model_name": params.get("model_name"),
        "metrics": {
            "loss": 0.1,  # Placeholder
            "accuracy": 0.95,  # Placeholder
        },
    }


def handle_evaluation_task(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle evaluation task.

    Args:
        params: Task parameters

    Returns:
        Task result
    """
    logger.info(f"Handling evaluation task for model at {params.get('model_path')}")

    # Implementation for evaluation task
    # This would typically load a model and evaluate it on a dataset

    return {
        "success": True,
        "model_path": params.get("model_path"),
        "dataset_name": params.get("dataset_name"),
        "metrics": {
            "precision": 0.92,  # Placeholder
            "recall": 0.89,  # Placeholder
            "f1": 0.90,  # Placeholder
        },
    }


def process_training_jobs(
    queue_url: str,
    dynamo_table: str,
    max_runtime: Optional[int] = None,
    wait_time: int = 20,
    visibility_timeout: int = 3600,
    enable_coordination: bool = True,
) -> None:
    """Process training jobs from an SQS queue until stopped.

    Args:
        queue_url: SQS queue URL
        dynamo_table: DynamoDB table name
        max_runtime: Maximum runtime in seconds (None for indefinite)
        wait_time: SQS long polling wait time in seconds
        visibility_timeout: Initial visibility timeout in seconds
        enable_coordination: Whether to enable enhanced coordination
    """
    logger.info(f"Starting job processor for queue {queue_url}")

    # Initialize JobQueue
    config = JobQueueConfig(
        queue_url=queue_url,
        aws_region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        max_retries=3,
        retry_strategy=JobRetryStrategy.EXPONENTIAL_BACKOFF,
        base_retry_seconds=30,
        visibility_timeout_seconds=visibility_timeout,
        wait_time_seconds=wait_time,
    )
    queue = JobQueue(config)

    # Set up training environment
    env = TrainingEnvironment(
        registry_table=os.environ.get("INSTANCE_REGISTRY_TABLE"),
        enable_coordination=enable_coordination,
    )

    # Set up coordination handlers if enabled
    if enable_coordination and env.instance_coordinator:
        setup_coordination_handlers(env)

    # Start instance heartbeat thread
    heartbeat_thread = env.start_heartbeat_thread(60)

    # Track start time for max_runtime
    start_time = time.time()

    # Process jobs until stopped or max_runtime reached
    try:
        while True:
            # Check max_runtime
            if max_runtime and (time.time() - start_time) > max_runtime:
                logger.info(f"Reached maximum runtime of {max_runtime} seconds")
                break

            # Get next job
            try:
                jobs = queue.receive_jobs(1)

                if not jobs:
                    logger.debug("No jobs available, waiting...")
                    time.sleep(5)
                    continue

                for job, receipt_handle in jobs:
                    logger.info(f"Received job {job.job_id}")

                    # Check if this instance is suitable for the job
                    is_suitable = True

                    # If cluster management is enabled, check instance suitability
                    if env.cluster_manager and env.cluster_manager.is_leader:
                        # Check if there's a better instance for this job
                        requirements = job.config.get("requirements", {})
                        better_instance = (
                            env.cluster_manager._find_best_instance_for_job(
                                requirements
                            )
                        )

                        current_instance = EC2Metadata.get_instance_id()
                        if better_instance and better_instance != current_instance:
                            # There's a better instance for this job
                            logger.info(
                                f"Instance {better_instance} is better suited for job {job.job_id}"
                            )

                            # If we're the leader, we can submit it as a task
                            task_id = env.cluster_manager.submit_training_job(
                                job.job_id,
                                job.config.get("model_name", "layoutlm"),
                                job.config,
                                requirements,
                            )

                            if task_id:
                                logger.info(
                                    f"Submitted job {job.job_id} as task {task_id} to instance {better_instance}"
                                )

                                # Delete the job from our queue since it's now a task
                                queue.delete_job(receipt_handle)
                                continue

                    # Process the job
                    if is_suitable:
                        success = process_training_job(
                            job, receipt_handle, dynamo_table, queue, env
                        )

                        # Delete the message if successful, otherwise let it retry
                        if success:
                            queue.delete_job(receipt_handle)
                            logger.info(
                                f"Deleted completed job {job.job_id} from queue"
                            )
                        else:
                            # Job failed, apply retry mechanism
                            if job.retry_count < config.max_retries:
                                # Delete and re-add with backoff
                                queue.delete_job(receipt_handle)
                                job.retry_count += 1

                                # Resubmit with backoff
                                queue.submit_job(job)
                                logger.info(
                                    f"Resubmitted job {job.job_id} for retry #{job.retry_count}"
                                )
                            else:
                                # Max retries reached, handle failure
                                queue.delete_job(receipt_handle)
                                logger.warning(
                                    f"Job {job.job_id} failed after {job.retry_count} retries"
                                )

                                # Send to DLQ if configured
                                if queue.config.dlq_url:
                                    # Send to dead-letter queue
                                    try:
                                        import boto3

                                        sqs = boto3.client(
                                            "sqs",
                                            region_name=queue.config.aws_region,
                                        )
                                        sqs.send_message(
                                            QueueUrl=queue.config.dlq_url,
                                            MessageBody=job.to_json(),
                                            MessageAttributes={
                                                "RetryCount": {
                                                    "DataType": "Number",
                                                    "StringValue": str(job.retry_count),
                                                },
                                                "FailureReason": {
                                                    "DataType": "String",
                                                    "StringValue": "Exceeded max retries",
                                                },
                                            },
                                        )
                                        logger.info(
                                            f"Sent failed job {job.job_id} to DLQ"
                                        )
                                    except Exception as e:
                                        logger.error(f"Failed to send job to DLQ: {e}")

            except Exception as e:
                logger.error(f"Error processing job batch: {e}")
                logger.error(traceback.format_exc())
                time.sleep(5)  # Avoid tight loop on errors

    except KeyboardInterrupt:
        logger.info("Job processor interrupted by user")
    finally:
        logger.info("Job processor shutting down")

        # Clean up coordination
        if env:
            env.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process training jobs from SQS")
    parser.add_argument(
        "--queue-url",
        type=str,
        required=True,
        help="SQS queue URL for training jobs",
    )
    parser.add_argument(
        "--dynamo-table",
        type=str,
        required=True,
        help="DynamoDB table name for job tracking",
    )
    parser.add_argument(
        "--max-runtime",
        type=int,
        default=None,
        help="Maximum runtime in seconds (default: indefinite)",
    )
    parser.add_argument(
        "--wait-time",
        type=int,
        default=20,
        help="SQS long polling wait time in seconds (default: 20)",
    )
    parser.add_argument(
        "--visibility-timeout",
        type=int,
        default=3600,
        help="Initial SQS visibility timeout in seconds (default: 3600)",
    )
    parser.add_argument(
        "--disable-coordination",
        action="store_true",
        help="Disable enhanced coordination features",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Process jobs
    process_training_jobs(
        queue_url=args.queue_url,
        dynamo_table=args.dynamo_table,
        max_runtime=args.max_runtime,
        wait_time=args.wait_time,
        visibility_timeout=args.visibility_timeout,
        enable_coordination=not args.disable_coordination,
    )
