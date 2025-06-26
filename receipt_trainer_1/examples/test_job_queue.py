#!/usr/bin/env python
"""Test script for SQS job queue integration with ReceiptTrainer.

This script demonstrates the end-to-end flow:
1. Creates a job queue
2. Submits a training job
3. Processes the job
"""

import argparse
import logging
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional

import boto3

from receipt_trainer import DataConfig, ReceiptTrainer, TrainingConfig
from receipt_trainer.jobs import (Job, JobPriority, JobQueue, JobQueueConfig,
                                  JobStatus)
from receipt_trainer.jobs.worker import process_training_jobs
from receipt_trainer.utils.infrastructure import EC2Metadata

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("test_job_queue")


def create_training_job(
    model_name: str,
    dataset_id: str,
    hyperparameters: Dict[str, Any],
    priority: JobPriority = JobPriority.MEDIUM,
    training_time_minutes: int = 10,
) -> Job:
    """Create a training job.

    Args:
        model_name: Name of the model to train
        dataset_id: Dataset ID to use for training
        hyperparameters: Hyperparameters for training
        priority: Job priority
        training_time_minutes: Expected training time in minutes

    Returns:
        Job: The created job
    """
    job_id = str(uuid.uuid4())

    # Create training config
    training_config = {
        "batch_size": hyperparameters.get("batch_size", 16),
        "learning_rate": hyperparameters.get("learning_rate", 3e-4),
        "num_epochs": hyperparameters.get("epochs", 5),
        "gradient_accumulation_steps": hyperparameters.get(
            "gradient_accumulation_steps", 1
        ),
        "warmup_ratio": hyperparameters.get("warmup_ratio", 0.1),
        "weight_decay": hyperparameters.get("weight_decay", 0.01),
        "evaluation_steps": hyperparameters.get("evaluation_steps", 100),
        "save_steps": hyperparameters.get("save_steps", 100),
        "logging_steps": hyperparameters.get("logging_steps", 10),
    }

    # Create data config
    data_config = {
        "dataset_id": dataset_id,
        "use_augmentation": hyperparameters.get("use_augmentation", True),
        "balance_ratio": hyperparameters.get("balance_ratio", 0.7),
    }

    # Create job config
    job_config = {
        "model_name": model_name,
        "training_config": training_config,
        "data_config": data_config,
        "expected_duration": training_time_minutes * 60,  # Convert to seconds
    }

    # Create job
    job = Job(
        job_id=job_id,
        name=f"Train {model_name} on {dataset_id}",
        job_type="train",
        priority=priority,
        status=JobStatus.PENDING,
        job_config=job_config,
        retry_count=0,
    )

    return job


def setup_job_queue(queue_name: Optional[str] = None) -> JobQueue:
    """Set up the job queue.

    Args:
        queue_name: Optional name for the queue

    Returns:
        JobQueue: The configured job queue
    """
    # Determine AWS region
    region = EC2Metadata.get_instance_region() or os.environ.get(
        "AWS_DEFAULT_REGION", "us-east-1"
    )

    # Use existing queue or create a new one for testing
    if queue_name:
        # Get queue URL
        sqs = boto3.client("sqs", region_name=region)
        try:
            response = sqs.get_queue_url(QueueName=queue_name)
            queue_url = response["QueueUrl"]
        except Exception as e:
            logger.error(f"Error getting queue URL: {e}")
            sys.exit(1)
    else:
        # For testing, create a unique queue
        test_queue_name = f"receipt-trainer-test-{str(uuid.uuid4())[:8]}"

        # Create queue
        sqs = boto3.client("sqs", region_name=region)
        try:
            response = sqs.create_queue(
                QueueName=test_queue_name,
                Attributes={
                    "VisibilityTimeout": "3600",  # 1 hour
                    "MessageRetentionPeriod": "86400",  # 1 day
                },
            )
            queue_url = response["QueueUrl"]
            logger.info(f"Created test queue: {test_queue_name}")
            logger.info(f"Queue URL: {queue_url}")
        except Exception as e:
            logger.error(f"Error creating test queue: {e}")
            sys.exit(1)

    # Create job queue configuration
    config = JobQueueConfig(
        queue_url=queue_url,
        aws_region=region,
        max_retries=3,
        visibility_timeout_seconds=3600,
        wait_time_seconds=20,
    )

    # Create job queue
    job_queue = JobQueue(config)

    return job_queue


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Test SQS job queue integration")

    # Queue options
    parser.add_argument("--queue", help="Queue name (optional)")
    parser.add_argument("--dynamo-table", required=True, help="DynamoDB table name")

    # Action options
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--submit", action="store_true", help="Submit a test job")
    group.add_argument(
        "--process", action="store_true", help="Process jobs from the queue"
    )
    group.add_argument("--cleanup", action="store_true", help="Clean up the test queue")

    # Job options
    parser.add_argument(
        "--model", default="microsoft/layoutlm-base-uncased", help="Model name"
    )
    parser.add_argument("--dataset", default="receipts-v1", help="Dataset ID")
    parser.add_argument("--epochs", type=int, default=2, help="Number of epochs")
    parser.add_argument("--batch-size", type=int, default=8, help="Batch size")

    # Processing options
    parser.add_argument("--max-runtime", type=int, help="Maximum runtime in seconds")

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Set up job queue
    job_queue = setup_job_queue(args.queue)

    if args.submit:
        # Create and submit a test job
        job = create_training_job(
            model_name=args.model,
            dataset_id=args.dataset,
            hyperparameters={
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "learning_rate": 3e-4,
            },
            priority=JobPriority.HIGH,
        )

        # Submit the job
        job_id = job_queue.submit_job(job)
        logger.info(f"Submitted job: {job_id}")

    elif args.process:
        # Process jobs from the queue
        logger.info("Starting job processor...")

        process_training_jobs(
            queue_url=job_queue.config.queue_url,
            dynamo_table=args.dynamo_table,
            max_runtime=args.max_runtime,
        )

    elif args.cleanup:
        # Clean up the test queue
        try:
            sqs = boto3.client("sqs")
            sqs.delete_queue(QueueUrl=job_queue.config.queue_url)
            logger.info(f"Deleted queue: {job_queue.config.queue_url}")
        except Exception as e:
            logger.error(f"Error deleting queue: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
