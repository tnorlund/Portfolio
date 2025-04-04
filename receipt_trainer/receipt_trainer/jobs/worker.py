"""Worker module for processing training jobs from SQS queue."""

import os
import time
import json
import logging
import traceback
from typing import Dict, Any, Optional, List, Callable, Union
import uuid

from receipt_trainer import ReceiptTrainer, TrainingConfig, DataConfig
from receipt_trainer.jobs import (
    Job,
    JobQueue,
    JobQueueConfig,
    JobRetryStrategy,
)
from receipt_trainer.utils.infrastructure import (
    TrainingEnvironment,
    EFSManager,
    SpotInstanceHandler,
    EC2Metadata,
)

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
    job: Job, receipt_handle: str, dynamo_table: str, job_queue: JobQueue
) -> bool:
    """Process a training job from the SQS queue.

    Args:
        job: Job to process
        receipt_handle: SQS receipt handle for the job
        dynamo_table: DynamoDB table name
        job_queue: JobQueue instance for status updates

    Returns:
        True if successful, False otherwise
    """
    job_id = job.job_id
    logger.info(f"Processing training job {job_id}")

    try:
        # Set up training environment
        env = TrainingEnvironment(
            job_id=job_id,
            registry_table=os.environ.get("INSTANCE_REGISTRY_TABLE"),
        )

        # Convert job config to trainer parameters
        trainer_params = job_config_to_trainer_params(job.job_config)

        # Initialize trainer
        trainer = ReceiptTrainer(
            **trainer_params,
            dynamo_table=dynamo_table,
        )

        # Record processing instance information
        instance_id = EC2Metadata.get_instance_id()
        instance_type = EC2Metadata.get_instance_type()

        # Set up spot interruption handler with trainer-specific callback
        def spot_interruption_callback(checkpoint_path):
            logger.warning("Spot interruption callback triggered")
            try:
                # Save checkpoint
                trainer.save_checkpoint(checkpoint_path)

                # Update job status
                trainer.update_job_status(
                    "interrupted",
                    f"Spot instance interrupted, checkpoint saved at {checkpoint_path}",
                )

                # Release SQS message with short visibility timeout
                # This ensures another instance can pick it up soon
                job_queue.extend_visibility_timeout(receipt_handle, 60)
            except Exception as e:
                logger.error(f"Error in spot interruption callback: {e}")

        # Register spot interruption handler
        env.setup_spot_handler(checkpoint_callback=spot_interruption_callback)

        # Process the job using the integrated method
        success = trainer.process_from_job_queue(
            job, receipt_handle, job_queue
        )

        logger.info(
            f"Training job {job_id} {'completed successfully' if success else 'failed'}"
        )
        return success

    except Exception as e:
        logger.error(f"Error processing job {job_id}: {str(e)}")
        logger.error(traceback.format_exc())

        # Update job status if possible
        try:
            from receipt_dynamo.services.job_service import JobService

            job_service = JobService(dynamo_table)
            job_service.add_job_status(
                job_id, "failed", f"Training failed: {str(e)}"
            )
            job_service.add_job_log(
                job_id,
                "ERROR",
                f"Error during training: {str(e)}\n{traceback.format_exc()}",
            )
        except Exception as log_error:
            logger.error(f"Failed to update job status: {log_error}")

        return False


def process_training_jobs(
    queue_url: str,
    dynamo_table: str,
    max_runtime: Optional[int] = None,
    wait_time: int = 20,
    visibility_timeout: int = 3600,
) -> None:
    """Process training jobs from an SQS queue until stopped.

    Args:
        queue_url: SQS queue URL
        dynamo_table: DynamoDB table name
        max_runtime: Maximum runtime in seconds (None for indefinite)
        wait_time: SQS long polling wait time in seconds
        visibility_timeout: Initial visibility timeout in seconds
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

    # Set up training environment if needed
    env = TrainingEnvironment(
        registry_table=os.environ.get("INSTANCE_REGISTRY_TABLE")
    )

    # Start instance heartbeat thread
    heartbeat_thread = env.start_heartbeat_thread(60)

    # Define job handler
    def job_handler(job: Job, receipt_handle: str) -> bool:
        return process_training_job(job, receipt_handle, dynamo_table, queue)

    # Track start time for max_runtime
    start_time = time.time()

    # Process jobs until stopped or max_runtime reached
    try:
        while True:
            # Check max_runtime
            if max_runtime and (time.time() - start_time) > max_runtime:
                logger.info(
                    f"Reached maximum runtime of {max_runtime} seconds"
                )
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

                    # Process the job
                    success = job_handler(job, receipt_handle)

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
                                                "StringValue": str(
                                                    job.retry_count
                                                ),
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
                                    logger.error(
                                        f"Failed to send job to DLQ: {e}"
                                    )

            except Exception as e:
                logger.error(f"Error processing job batch: {e}")
                logger.error(traceback.format_exc())
                time.sleep(5)  # Avoid tight loop on errors

    except KeyboardInterrupt:
        logger.info("Job processor interrupted by user")
    finally:
        logger.info("Job processor shutting down")
