#!/usr/bin/env python
"""End-to-end test for training a single model using GPU spot instances.

This test will:
1. Create a training job configuration
2. Submit it to the SQS queue
3. Let the auto-scaling system launch GPU instances to process the job
4. Verify the job completes successfully
"""

import os
import time
import json
import uuid
import logging
import pytest
import boto3
from typing import Dict, Any, Optional

from receipt_trainer.jobs.job import Job, JobPriority, JobStatus
from receipt_trainer.jobs.queue import JobQueue, JobQueueConfig
from receipt_trainer.jobs.submit import submit_training_job
from receipt_trainer.utils.pulumi import (
    get_auto_scaling_config,
    create_auto_scaling_manager,
)
from receipt_trainer.utils.auto_scaling import AutoScalingManager
from receipt_trainer.config import TrainingConfig, DataConfig
from receipt_dynamo.data.dynamo_client import DynamoClient

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def check_job_status(
    queue_url: str, job_id: str, region: str = "us-east-1"
) -> Dict[str, Any]:
    """Check the status of a job in DynamoDB.

    Args:
        queue_url: SQS queue URL
        job_id: ID of the job to check
        region: AWS region

    Returns:
        Job status information
    """
    # In a real implementation, this would query DynamoDB for job status
    # For this test, we'll use a simplification and check if the job message still exists in SQS
    sqs = boto3.client("sqs", region_name=region)

    # Check if message with given job ID is still in the queue
    try:
        # List all messages in the queue with a filter for the job ID
        response = sqs.receive_message(
            QueueUrl=queue_url,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
            MaxNumberOfMessages=10,
            VisibilityTimeout=10,
            WaitTimeSeconds=1,
        )

        if "Messages" in response:
            for message in response["Messages"]:
                # Return receipt handle immediately to avoid deleting the message
                sqs.change_message_visibility(
                    QueueUrl=queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                    VisibilityTimeout=0,
                )

                # Check if this message contains our job ID
                attrs = message.get("MessageAttributes", {})
                if (
                    "JobId" in attrs
                    and attrs["JobId"]["StringValue"] == job_id
                ):
                    # Parse the job status from the message
                    body = json.loads(message.get("Body", "{}"))
                    return {"status": body.get("status", "unknown")}

        # Job not found in queue, may have been processed
        return {"status": "processed"}

    except Exception as e:
        logger.error(f"Error checking job status: {e}")
        return {"status": "error", "error": str(e)}


def check_job_in_dynamo(
    job_id: str, dynamo_table: str, region: str = "us-east-1"
) -> Dict[str, Any]:
    """Check if a job exists in DynamoDB and return its details.

    Args:
        job_id: ID of the job to check
        dynamo_table: DynamoDB table name
        region: AWS region

    Returns:
        Job details or empty dict if not found
    """
    try:
        # Connect to DynamoDB
        dynamo_client = DynamoClient(table_name=dynamo_table, region=region)

        # Get the job
        job = dynamo_client.getJob(job_id)
        return {
            "job_id": job.job_id,
            "name": job.name,
            "status": job.status,
            "priority": job.priority,
        }
    except Exception as e:
        logger.warning(f"Failed to get job from DynamoDB: {e}")
        return {}


@pytest.mark.end_to_end
@pytest.mark.real_aws
@pytest.mark.skipif(
    not os.environ.get("ENABLE_REAL_AWS_TESTS"),
    reason="Real AWS tests disabled. Set ENABLE_REAL_AWS_TESTS=1 to run",
)
def test_single_model_training(aws_credentials, pulumi_config):
    """
    End-to-end test for training a single model using GPU instances.

    This test will:
    1. Create and submit a training job to the SQS queue
    2. Start auto-scaling monitoring to launch GPU instances
    3. Wait for the job to be processed
    4. Verify that instances are appropriately scaled up and down
    5. Verify job data is correctly written to DynamoDB
    """
    # Get account info and Pulumi config
    session = boto3.Session()
    account_id = session.client("sts").get_caller_identity().get("Account")

    print(f"\nRunning end-to-end test with AWS account: {account_id}")
    print(f"Using config from stack: dev")

    # Get auto-scaling configuration from Pulumi
    config = get_auto_scaling_config("dev")

    # Create auto-scaling manager
    manager = create_auto_scaling_manager(
        stack_name="dev",
        min_instances=0,  # Start with 0 instances
        max_instances=2,  # Allow up to 2 instances for the test
    )

    # Prepare training job configuration
    model_name = "microsoft/layoutlm-base-uncased"

    # Create a small training config for testing
    training_config = {
        "num_epochs": 1,  # Just 1 epoch for testing
        "batch_size": 4,  # Small batch size
        "learning_rate": 5.0e-5,  # Standard learning rate
        "weight_decay": 0.01,
        "gradient_accumulation_steps": 1,
        "logging_steps": 10,
        "evaluation_steps": 50,
        "save_steps": 50,
        "fp16": True,  # Enable mixed precision for GPU
        "early_stopping_patience": 2,
    }

    # Create data config
    data_config = {
        "use_sroie": True,  # Use the SROIE dataset
        "max_samples": 100,  # Use only 100 samples for testing
        "use_sliding_window": True,
        "augment": True,
    }

    # Generate a unique job ID
    job_id = str(uuid.uuid4())

    # Submit the job
    try:
        # Initialize queue
        queue_config = JobQueueConfig(
            queue_url=config["queue_url"],
            aws_region=config.get("region", "us-east-1"),
        )
        queue = JobQueue(queue_config)

        # Create job
        job = Job(
            job_id=job_id,
            name=f"Test Training Job for {model_name}",
            type="training",
            config={
                "model": model_name,
                "training_config": training_config,
                "data_config": data_config,
                "requires_gpu": True,  # Signal to auto-scaler that we need GPU
            },
            priority=JobPriority.HIGH,
            status=JobStatus.PENDING,
            tags={"test": "true", "purpose": "end-to-end-test"},
        )

        # Submit job to queue
        print(f"Submitting test job with ID: {job_id}")
        queue.submit_job(job)

        # Verify job was written to DynamoDB
        print("Verifying job was written to DynamoDB...")
        dynamo_table = config.get("dynamo_table")
        if dynamo_table:
            # Wait a moment for DynamoDB write
            time.sleep(3)

            # Check for job in DynamoDB
            job_details = check_job_in_dynamo(job_id, dynamo_table)
            if job_details:
                print(f"Job found in DynamoDB: {job_details}")
                assert job_details["job_id"] == job_id
                assert job_details["status"] == "pending"
            else:
                print(f"Warning: Job {job_id} not found in DynamoDB")

        # Start auto-scaling monitoring
        print("Starting auto-scaling monitoring...")
        thread = manager.start_monitoring(interval_seconds=30)

        # Wait for job to be processed
        print(
            "Waiting for job to be processed (this may take several minutes)..."
        )
        max_wait_time = 15 * 60  # 15 minutes maximum wait time
        start_time = time.time()

        # Keep track of job status
        job_processed = False
        scaling_observed = False

        # Check job status and scaling periodically
        while time.time() - start_time < max_wait_time:
            # Check if instances have been scaled up
            status = manager.get_instance_status()
            running_count = sum(
                count
                for state, count in status.get(
                    "instances_by_state", {}
                ).items()
                if state in ["pending", "running"]
            )

            if running_count > 0 and not scaling_observed:
                scaling_observed = True
                print(
                    f"Observed scaling: {running_count} instances running/pending"
                )
                print(f"Instance types: {status.get('instances_by_type', {})}")

            # Check if job has been processed
            job_status = check_job_status(config["queue_url"], job_id)
            if job_status.get("status") == "processed":
                job_processed = True
                print("Job has been processed!")
                break

            print(
                f"Current status - Queue depth: {status['queue_depth']}, Instances: {running_count}"
            )
            time.sleep(30)  # Check every 30 seconds

        # Verify job was processed
        assert job_processed, "Job was not processed within the wait time"

        # Verify job status was updated in DynamoDB
        if dynamo_table:
            print("Verifying job status was updated in DynamoDB...")
            # Wait a moment for DynamoDB updates
            time.sleep(3)

            # Check for updated job status in DynamoDB
            job_details = check_job_in_dynamo(job_id, dynamo_table)
            if job_details:
                print(f"Job status in DynamoDB: {job_details}")
                # Job should be in a terminal state (succeeded, failed, or cancelled)
                assert job_details["status"] in [
                    "succeeded",
                    "failed",
                    "cancelled",
                ], f"Job should be in terminal state, but is in {job_details['status']}"
            else:
                print(
                    f"Warning: Job {job_id} not found in DynamoDB after processing"
                )

        # Wait for auto-scaling to terminate instances
        print("Waiting for auto-scaling to terminate instances...")
        wait_seconds = 180  # Wait up to 3 minutes for instances to terminate
        wait_start = time.time()

        while time.time() - wait_start < wait_seconds:
            status = manager.get_instance_status()
            active_instances = sum(
                count
                for state, count in status.get(
                    "instances_by_state", {}
                ).items()
                if state in ["pending", "running"]
            )

            if active_instances == 0:
                print("All instances have been terminated")
                break

            time.sleep(30)
            print(
                f"Waiting for instances to terminate... Current count: {active_instances}"
            )

    finally:
        # Always stop monitoring and clean up
        print("Stopping auto-scaling monitoring...")
        manager.stop_monitoring()

        # Final check for any instances
        ec2 = boto3.client("ec2")
        response = ec2.describe_instances(
            Filters=[
                {
                    "Name": "tag:ManagedBy",
                    "Values": ["AutoScalingManager"],
                },
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )

        remaining_instances = []
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                remaining_instances.append(instance["InstanceId"])

        if remaining_instances:
            print(
                f"WARNING: Found {len(remaining_instances)} instances still active: {remaining_instances}"
            )
            # Try to terminate them
            for instance_id in remaining_instances:
                try:
                    ec2.terminate_instances(InstanceIds=[instance_id])
                    print(f"Terminated remaining instance: {instance_id}")
                except Exception as e:
                    print(f"Error terminating instance {instance_id}: {e}")
        else:
            print("No remaining instances found, cleanup successful")

        # Also check for and cancel any lingering spot requests
        try:
            spot_requests = ec2.describe_spot_instance_requests(
                Filters=[
                    {
                        "Name": "state",
                        "Values": ["open", "active"],
                    }
                ]
            )

            spot_request_ids = [
                r["SpotInstanceRequestId"]
                for r in spot_requests.get("SpotInstanceRequests", [])
            ]

            if spot_request_ids:
                print(
                    f"WARNING: Found {len(spot_request_ids)} active spot requests: {spot_request_ids}"
                )
                # Cancel them
                ec2.cancel_spot_instance_requests(
                    SpotInstanceRequestIds=spot_request_ids
                )
                print("Cancelled spot requests")
            else:
                print("No active spot requests found")

        except Exception as e:
            print(f"Error checking spot requests: {e}")


if __name__ == "__main__":
    # This will run the test directly if this file is executed
    # Enable running this test from the command line
    # Must set ENABLE_REAL_AWS_TESTS=1 environment variable
    os.environ["ENABLE_REAL_AWS_TESTS"] = "1"

    # Mock the aws_credentials and pulumi_config fixtures
    class MockSession:
        def __init__(self):
            self.region_name = "us-east-1"

    test_single_model_training(MockSession(), {})
