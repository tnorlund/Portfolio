"""
Example script demonstrating the standardized job processing system.

This script shows how to:
1. Create and configure a job processor
2. Submit jobs to the processor
3. Process jobs with different modes (normal, debug, test)
4. Handle job success and failure

Usage:
    python -m receipt_trainer.jobs.examples.standardized_job_example --mode=debug
"""

import argparse
import logging
import os
import random
import time
import uuid
from typing import Any, Dict

import boto3

from receipt_dynamo.data._pulumi import load_env
from receipt_trainer.jobs import (Job, JobPriority, JobStatus, ProcessingMode,
                                  create_job_processor)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def dynamodb_table(env: str) -> str:
    """
    Function that retrieves the DynamoDB table name from Pulumi dev environment.

    Returns:
        str: The name of the DynamoDB table
    """
    env_vars = load_env(env)
    if not env_vars or "dynamodb_table_name" not in env_vars:
        pytest.skip("DynamoDB table name not found in Pulumi stack outputs")
    return env_vars["dynamodb_table_name"]


def queue_url(env: str) -> str:
    """
    Function that retrieves the SQS queue URL from Pulumi dev environment.

    Returns:
        str: The URL of the SQS queue
    """
    env_vars = load_env(env)
    if not env_vars or "job_queue_url" not in env_vars:
        raise ValueError(f"Job queue URL not found in Pulumi {env} stack outputs")
    return env_vars["job_queue_url"]


def setup_test_resources():
    """
    Create test resources (SQS queue and DynamoDB table).

    Returns:
        Tuple of (queue_url, dynamo_table_name)
    """
    # Create a unique identifier for test resources
    test_id = str(uuid.uuid4())[:8]

    # Create SQS queue
    sqs = boto3.client("sqs")
    queue_name = f"test-job-queue-{test_id}"

    response = sqs.create_queue(
        QueueName=queue_name,
        Attributes={
            "VisibilityTimeout": "60",
            "MessageRetentionPeriod": "3600",
        },
    )
    queue_url = response["QueueUrl"]
    logger.info(f"Created test queue: {queue_url}")

    # Create DynamoDB table
    dynamodb = boto3.client("dynamodb")
    table_name = f"test-job-table-{test_id}"

    response = dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {
                    "ReadCapacityUnits": 5,
                    "WriteCapacityUnits": 5,
                },
            },
        ],
        ProvisionedThroughput={
            "ReadCapacityUnits": 5,
            "WriteCapacityUnits": 5,
        },
    )
    logger.info(f"Created test table: {table_name}")

    # Wait for the table to be created
    dynamodb.get_waiter("table_exists").wait(TableName=table_name)

    return queue_url, table_name


def cleanup_test_resources(queue_url, table_name):
    """
    Clean up test resources.

    Args:
        queue_url: SQS queue URL
        table_name: DynamoDB table name
    """
    # Delete SQS queue
    sqs = boto3.client("sqs")
    sqs.delete_queue(QueueUrl=queue_url)
    logger.info(f"Deleted test queue: {queue_url}")

    # Delete DynamoDB table
    dynamodb = boto3.client("dynamodb")
    dynamodb.delete_table(TableName=table_name)
    logger.info(f"Deleted test table: {table_name}")


def create_sample_job(name, job_type="training", success_probability=0.8):
    """
    Create a sample job for testing.

    Args:
        name: Job name
        job_type: Job type
        success_probability: Probability of job success (for the handler)

    Returns:
        Job instance
    """
    return Job(
        name=name,
        type=job_type,
        config={
            "model_name": "sample_model",
            "training_params": {
                "learning_rate": 0.001,
                "batch_size": 32,
                "epochs": 5,
            },
            "success_probability": success_probability,
        },
        priority=JobPriority.MEDIUM,
        max_attempts=3,
    )


def job_handler(job: Job) -> bool:
    """
    Handle job processing.

    This is a sample handler that simulates job processing with
    configurable success probability.

    Args:
        job: Job to process

    Returns:
        True if successful, False otherwise
    """
    logger.info(f"Processing job {job.job_id}: {job.name} (type={job.type})")

    # Get success probability from job config
    success_probability = job.config.get("success_probability", 0.8)

    # Simulate processing time
    processing_time = random.uniform(1, 3)
    logger.info(f"Job {job.job_id} will take {processing_time:.2f} seconds to process")
    time.sleep(processing_time)

    # Determine if the job succeeds or fails
    success = random.random() < success_probability

    if success:
        logger.info(f"Job {job.job_id} completed successfully")
        return True
    else:
        logger.error(f"Job {job.job_id} failed")
        return False


def run_example(env: str, mode_str: str, use_test_queue=False, num_jobs=5):
    """
    Run the job processing example.

    Args:
        env: Environment to use
        mode_str: Processing mode string ("normal", "debug", or "test")
        use_test_queue: Whether to create and use a test queue
        num_jobs: Number of jobs to submit
    """
    # Convert mode string to enum
    mode_map = {
        "normal": ProcessingMode.NORMAL,
        "debug": ProcessingMode.DEBUG,
        "test": ProcessingMode.TEST,
    }
    mode = mode_map.get(mode_str.lower(), ProcessingMode.NORMAL)

    # Set up queue and table
    if use_test_queue:
        queue_url_val, table_name = setup_test_resources()
    else:
        # Use existing resources from Pulumi
        try:
            queue_url_val = queue_url(env)
            table_name = dynamodb_table(env)
            logger.info(f"Using queue URL: {queue_url_val}")
            logger.info(f"Using DynamoDB table: {table_name}")
        except Exception as e:
            raise ValueError(
                f"Failed to load resources from Pulumi {env} stack: {e}. "
                "Use --use-test-queue to create test resources."
            )

    try:
        # Create job processor
        processor = create_job_processor(
            processor_type="sqs",
            queue_url=queue_url_val,
            dynamo_table=table_name,
            handler=job_handler,
            mode=mode,
            test_prefix="test" if mode == ProcessingMode.TEST else None,
        )

        # Submit jobs
        job_ids = []
        for i in range(num_jobs):
            # Create job with varying success probability
            success_prob = 0.8 if i % 2 == 0 else 0.2
            job = create_sample_job(
                name=f"test-job-{i+1}",
                job_type="training",
                success_probability=success_prob,
            )

            # Submit job
            job_id = processor.submit_job(job)
            job_ids.append(job_id)
            logger.info(f"Submitted job {job_id}")

        # Start job processor
        processor.start()

        # Wait for jobs to be processed
        logger.info("Waiting for jobs to be processed...")
        max_wait_time = 60  # seconds
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            # Check job statuses
            completed_count = 0
            for job_id in job_ids:
                status = processor.get_job_status(job_id)
                if status in (
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                ):
                    completed_count += 1

            # If all jobs are complete, break
            if completed_count == len(job_ids):
                logger.info("All jobs have completed")
                break

            # Sleep and try again
            time.sleep(5)

        # Stop processor
        processor.stop()

        # Print final job statuses
        logger.info("Final job statuses:")
        for job_id in job_ids:
            status = processor.get_job_status(job_id)
            logger.info(f"Job {job_id}: {status.value if status else 'unknown'}")

        logger.info(
            f"Job processing example completed in {env} environment with {mode.value} mode"
        )

    finally:
        # Clean up test resources if we created them
        if use_test_queue:
            cleanup_test_resources(queue_url_val, table_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job processor example")
    parser.add_argument(
        "--mode",
        choices=["normal", "debug", "test"],
        default="normal",
        help="Processing mode",
    )
    parser.add_argument(
        "--use-test-queue",
        action="store_true",
        help="Create and use test queue and table",
    )
    parser.add_argument(
        "--num-jobs",
        type=int,
        default=5,
        help="Number of jobs to submit",
    )

    parser.add_argument(
        "--env",
        type=str,
        default="dev",
        help="Environment to use",
    )

    args = parser.parse_args()

    run_example(args.env, args.mode, args.use_test_queue, args.num_jobs)
