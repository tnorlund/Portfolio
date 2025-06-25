#!/usr/bin/env python
"""
Example script demonstrating how to use the job queue system for ML training tasks.
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from typing import Dict, Any, Optional

import boto3

# Add the parent directory to the path so we can import the jobs package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from receipt_trainer.jobs.job import Job, JobStatus, JobPriority
from receipt_trainer.jobs.queue import JobQueue, JobQueueConfig, JobRetryStrategy
from receipt_trainer.jobs.aws import create_queue_with_dlq, get_queue_url


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


def create_training_job(
    model_name: str,
    dataset_id: str,
    hyperparameters: Dict[str, Any],
    priority: JobPriority = JobPriority.MEDIUM,
    training_time_minutes: int = 30,
) -> Job:
    """
    Create a training job configuration.

    Args:
        model_name: Name of the model to train
        dataset_id: ID of the dataset to use for training
        hyperparameters: Model hyperparameters
        priority: Job priority
        training_time_minutes: Expected training time in minutes

    Returns:
        The created job
    """
    # Create a unique job ID
    job_id = str(uuid.uuid4())

    # Create the job configuration
    config = {
        "model_name": model_name,
        "dataset_id": dataset_id,
        "hyperparameters": hyperparameters,
        "expected_training_time": training_time_minutes * 60,  # Convert to seconds
        "output_bucket": "my-ml-models",
        "output_prefix": f"models/{model_name}/{job_id}",
    }

    # Create the job
    job = Job(
        name=f"Train {model_name} on {dataset_id}",
        type="model_training",
        config=config,
        job_id=job_id,
        priority=priority,
        tags={
            "model_type": model_name,
            "dataset": dataset_id,
            "environment": "development",
        },
        timeout_seconds=training_time_minutes
        * 60
        * 2,  # Double the expected training time
    )

    return job


def simulate_training(
    job: Job,
    success_rate: float = 0.8,
    delay_seconds: int = 10,
) -> bool:
    """
    Simulate a model training job.

    Args:
        job: The job to process
        success_rate: Probability of successful training
        delay_seconds: Simulated training time in seconds

    Returns:
        True if the job was successful, False otherwise
    """
    logger.info(f"Starting training job: {job.name}")
    logger.info(f"Job details: {json.dumps(job.to_dict(), indent=2)}")

    # Simulate training time
    logger.info(f"Training will take approximately {delay_seconds} seconds")
    time.sleep(delay_seconds)

    # Simulate training outcome
    import random

    success = random.random() < success_rate

    if success:
        logger.info(f"Training job {job.job_id} completed successfully")
        logger.info(
            f"Model saved to s3://{job.config['output_bucket']}/{job.config['output_prefix']}"
        )
    else:
        logger.error(f"Training job {job.job_id} failed")

    return success


def main():
    """Main entry point for the example script."""
    parser = argparse.ArgumentParser(
        description="Example of using the job queue for ML training"
    )
    parser.add_argument(
        "--queue-name", default="ml-training-jobs", help="Name of the SQS queue"
    )
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument(
        "--create-queue",
        action="store_true",
        help="Create the queue if it doesn't exist",
    )
    parser.add_argument(
        "--submit", action="store_true", help="Submit example jobs to the queue"
    )
    parser.add_argument(
        "--process", action="store_true", help="Process jobs from the queue"
    )
    parser.add_argument(
        "--num-jobs", type=int, default=5, help="Number of jobs to submit"
    )
    parser.add_argument(
        "--process-time", type=int, default=60, help="Time to process jobs in seconds"
    )
    args = parser.parse_args()

    queue_url = None

    # Get or create the queue
    if args.create_queue:
        logger.info(f"Creating queue: {args.queue_name}")
        queue_url, dlq_url = create_queue_with_dlq(
            queue_name=args.queue_name,
            region_name=args.region,
        )
        if queue_url:
            logger.info(f"Created queue: {queue_url}")
            logger.info(f"Created DLQ: {dlq_url}")
        else:
            logger.error("Failed to create queue")
            return
    else:
        logger.info(f"Getting URL for existing queue: {args.queue_name}")
        queue_url = get_queue_url(args.queue_name, args.region)
        if not queue_url:
            logger.error(f"Queue {args.queue_name} does not exist")
            logger.info("Use --create-queue to create it")
            return

    # Create the queue config
    queue_config = JobQueueConfig(
        queue_url=queue_url,
        aws_region=args.region,
        max_retries=3,
        retry_strategy=JobRetryStrategy.EXPONENTIAL_BACKOFF,
        base_retry_seconds=10,
    )

    # Create the job queue
    queue = JobQueue(queue_config)

    # Submit example jobs
    if args.submit:
        logger.info(f"Submitting {args.num_jobs} example jobs")

        # Define some example model configurations
        models = [
            {
                "model_name": "resnet50",
                "dataset_id": "receipts-v1",
                "hyperparameters": {
                    "learning_rate": 0.001,
                    "batch_size": 32,
                    "epochs": 10,
                },
                "priority": JobPriority.HIGH,
                "training_time_minutes": 5,
            },
            {
                "model_name": "efficientnet",
                "dataset_id": "receipts-v2",
                "hyperparameters": {
                    "learning_rate": 0.0005,
                    "batch_size": 16,
                    "epochs": 20,
                },
                "priority": JobPriority.MEDIUM,
                "training_time_minutes": 10,
            },
            {
                "model_name": "vit",
                "dataset_id": "receipts-v3",
                "hyperparameters": {
                    "learning_rate": 0.0001,
                    "batch_size": 8,
                    "epochs": 30,
                },
                "priority": JobPriority.LOW,
                "training_time_minutes": 15,
            },
        ]

        # Submit jobs
        submitted_job_ids = []
        for i in range(args.num_jobs):
            # Choose a random model configuration
            import random

            model_config = random.choice(models)

            # Create the job
            job = create_training_job(**model_config)

            # Submit the job
            job_id = queue.submit_job(job)

            if job_id:
                logger.info(f"Submitted job: {job_id}")
                submitted_job_ids.append(job_id)
            else:
                logger.error(f"Failed to submit job: {job.name}")

        logger.info(f"Submitted {len(submitted_job_ids)} jobs")

    # Process jobs
    if args.process:
        logger.info(f"Processing jobs for {args.process_time} seconds")

        # Set up a handler for processing jobs
        def job_handler(job: Job) -> bool:
            return simulate_training(
                job,
                success_rate=0.8,
                delay_seconds=5,  # Reduced for demonstration
            )

        # Start processing jobs in a background thread
        processing_thread = queue.start_processing(job_handler)

        # Wait for the specified time
        try:
            time.sleep(args.process_time)
        except KeyboardInterrupt:
            logger.info("Processing interrupted by user")

        # Stop processing
        queue.stop_processing()

        # Wait for the thread to finish
        processing_thread.join()

        logger.info("Job processing completed")


if __name__ == "__main__":
    main()
