"""End-to-end tests for auto-scaling functionality."""

import pytest
import boto3
import time
import os
from botocore.exceptions import ClientError
from unittest.mock import patch, MagicMock

from receipt_dynamo.data._pulumi import load_env
from receipt_trainer.utils.pulumi import (
    get_auto_scaling_config,
    create_auto_scaling_manager,
)
from receipt_trainer.utils.auto_scaling import (
    AutoScalingManager,
    generate_training_worker_user_data,
)


@pytest.fixture(scope="session")
def pulumi_config():
    """Fixture that provides Pulumi stack configuration."""
    env_vars = load_env("dev")
    if not env_vars:
        pytest.skip("Pulumi stack outputs not found")
    return env_vars


@pytest.fixture(scope="session")
def aws_credentials():
    """Check if AWS credentials are available.

    Skip tests if AWS credentials aren't configured.
    """
    session = boto3.Session()
    if not session.get_credentials():
        pytest.skip("AWS credentials not found")
    return session


@pytest.mark.end_to_end
def test_create_auto_scaling_manager():
    """Test creating an auto-scaling manager from Pulumi stack."""
    # Use mock to avoid AWS API calls
    with patch("boto3.client") as mock_boto:
        # Mock EC2 and SQS clients
        mock_ec2 = MagicMock()
        mock_sqs = MagicMock()
        mock_boto.side_effect = lambda service, **kwargs: {
            "ec2": mock_ec2,
            "sqs": mock_sqs,
        }.get(service)

        # Create manager
        manager = create_auto_scaling_manager("dev")

        # Verify manager is created
        assert manager is not None
        assert isinstance(manager, AutoScalingManager)

        # Verify manager attributes
        assert manager.min_instances == 1
        assert manager.max_instances == 10
        assert isinstance(manager.cpu_instance_types, list)
        assert isinstance(manager.gpu_instance_types, list)


@pytest.mark.end_to_end
def test_generate_training_worker_user_data():
    """Test generating the user data script for EC2 instances."""
    user_data = generate_training_worker_user_data(
        queue_url="https://example.com/queue",
        dynamo_table="test-table",
        instance_registry_table="registry-table",
        s3_bucket="test-bucket",
        efs_id="fs-12345",
    )

    # Verify script content
    assert user_data is not None
    assert isinstance(user_data, str)
    assert user_data.startswith("#!/bin/bash")

    # Check for key environment variables
    assert 'export QUEUE_URL="https://example.com/queue"' in user_data
    assert 'export DYNAMO_TABLE="test-table"' in user_data
    assert 'export INSTANCE_REGISTRY_TABLE="registry-table"' in user_data
    assert 'export S3_BUCKET="test-bucket"' in user_data

    # Check for EFS mounting
    assert "Mounting EFS fs-12345" in user_data
    assert "mount -t efs fs-12345:/" in user_data


@pytest.mark.end_to_end
def test_auto_scaling_status():
    """Test getting instance status."""
    # Use mock to avoid AWS API calls
    with patch("boto3.client") as mock_boto:
        # Mock EC2 and SQS clients
        mock_ec2 = MagicMock()
        mock_sqs = MagicMock()

        # Set up mock response for SQS queue attributes
        mock_sqs.get_queue_attributes.return_value = {
            "Attributes": {
                "ApproximateNumberOfMessages": "5",
                "ApproximateNumberOfMessagesNotVisible": "2",
            }
        }

        # Set up mock response for EC2 describe_instances
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-12345",
                            "InstanceType": "c5.xlarge",
                            "State": {"Name": "running"},
                            "LaunchTime": "2023-05-01T12:00:00",
                        },
                        {
                            "InstanceId": "i-67890",
                            "InstanceType": "g4dn.xlarge",
                            "State": {"Name": "running"},
                            "LaunchTime": "2023-05-01T13:00:00",
                        },
                    ]
                }
            ]
        }

        # Set up boto3 client mock
        mock_boto.side_effect = lambda service, **kwargs: {
            "ec2": mock_ec2,
            "sqs": mock_sqs,
        }.get(service)

        # Create auto-scaling manager with mock
        manager = AutoScalingManager(
            queue_url="https://example.com/queue",
            instance_ami="ami-12345",
            instance_profile="test-profile",
            subnet_id="subnet-12345",
            security_group_id="sg-12345",
        )

        # Get status
        status = manager.get_instance_status()

        # Verify status
        assert status is not None
        assert status["queue_depth"] == 5
        assert status["total_instances"] == 2
        assert status["instances_by_state"] == {"running": 2}
        assert status["instances_by_type"] == {
            "c5.xlarge": 1,
            "g4dn.xlarge": 1,
        }
        assert status["monitoring_active"] is False


@pytest.mark.end_to_end
@pytest.mark.real_aws
@pytest.mark.skipif(
    not os.environ.get("ENABLE_REAL_AWS_TESTS"),
    reason="Real AWS tests disabled. Set ENABLE_REAL_AWS_TESTS=1 to run",
)
def test_real_auto_scaling_e2e(aws_credentials, pulumi_config):
    """
    True end-to-end test that launches and terminates EC2 instances.

    This test will:
    1. Create an auto-scaling manager from Pulumi config
    2. Start monitoring the SQS queue
    3. Manually scale up to add one instance
    4. Wait for the instance to be launched
    5. Verify the instance appears in the status
    6. Manually scale down to remove the instance
    7. Verify the instance is terminated

    Note: This test requires AWS credentials and costs money.
    Skip by default and only run when explicitly enabled.
    """
    # Get config and account info
    config = get_auto_scaling_config("dev")
    session = boto3.Session()
    account_id = session.client("sts").get_caller_identity().get("Account")

    print(f"\nRunning end-to-end test with AWS account: {account_id}")
    print(f"Using config from stack: dev")

    # Create auto-scaling manager
    manager = create_auto_scaling_manager(
        stack_name="dev", min_instances=1, max_instances=3
    )

    # Keep track of created instances for additional verification
    launched_instance_ids = []

    # Ensure we start with clean state
    print("Checking initial status...")
    initial_status = manager.get_instance_status()
    if initial_status["total_instances"] > 0:
        print(
            f"Found {initial_status['total_instances']} pre-existing instances, cleaning up..."
        )
        for i in range(initial_status["total_instances"]):
            manager._terminate_instances(1)
            time.sleep(2)  # Brief pause between terminations

    try:
        # Start monitoring (don't actually need this for manual scaling, but test it)
        print("Starting auto-scaling monitoring...")
        thread = manager.start_monitoring(interval_seconds=60)

        # Give it a moment to initialize
        time.sleep(2)

        # Verify monitoring is running
        status = manager.get_instance_status()
        assert status["monitoring_active"] is True
        print(f"Monitoring active: {status['monitoring_active']}")

        # Manual launch of an instance
        print("Manually launching one instance...")
        manager._launch_instances(1, need_gpu=False)

        # Wait for instance to launch (spot instances can take a bit)
        print("Waiting for instance to launch (may take up to 3 minutes)...")
        max_wait = 180  # 3 minutes should be enough for spot instance
        wait_start = time.time()
        launched = False

        while time.time() - wait_start < max_wait:
            status = manager.get_instance_status()
            running = sum(
                count
                for state, count in status.get(
                    "instances_by_state", {}
                ).items()
                if state in ["pending", "running"]
            )
            if running >= 1:
                launched = True
                print(
                    f"Instance(s) launched successfully: {status['instances_by_state']}"
                )
                break
            time.sleep(10)
            print("Still waiting for instance launch...")

        # Verify instance was launched
        if not launched:
            pytest.fail("Timed out waiting for instance to launch")

        # Now verify we can see the instance
        status = manager.get_instance_status()
        assert status["total_instances"] >= 1
        print(f"Instance status: {status['instances_by_state']}")

        # Give the instance a bit more time if it's still pending
        if status.get("instances_by_state", {}).get("pending", 0) > 0:
            print("Waiting for pending instance to become running...")
            time.sleep(30)
            status = manager.get_instance_status()
            print(f"Updated instance status: {status['instances_by_state']}")

        # Get instance details
        instances = manager._get_managed_instances()
        instance_ids = [i["InstanceId"] for i in instances]
        launched_instance_ids.extend(instance_ids)
        print(f"Launched instance IDs: {instance_ids}")

        # Now terminate the instance
        print("Terminating the instance...")
        manager._terminate_instances(len(instances))

        # Wait for termination to complete
        print("Waiting for instance to terminate...")
        terminated = False
        wait_start = time.time()

        while time.time() - wait_start < 90:  # 1.5 minutes should be enough
            status = manager.get_instance_status()
            # Exclude terminated instances from count
            active = sum(
                count
                for state, count in status.get(
                    "instances_by_state", {}
                ).items()
                if state not in ["terminated", "shutting-down"]
            )
            if active == 0:
                terminated = True
                break
            time.sleep(10)
            print("Still waiting for instance termination...")

        # Verify instances are gone or terminating
        status = manager.get_instance_status()
        active_instances = sum(
            count
            for state, count in status.get("instances_by_state", {}).items()
            if state not in ["terminated", "shutting-down"]
        )
        assert active_instances == 0, "Instances were not terminated"
        print(
            f"All instances terminated or terminating: {status['instances_by_state']}"
        )

    finally:
        # Always stop monitoring and clean up, even if tests fail
        print("Stopping monitoring...")
        manager.stop_monitoring()

        # Final thorough check for any instances we launched
        if launched_instance_ids:
            print(
                f"Performing final verification on instances: {launched_instance_ids}"
            )

            # Use direct boto3 call to verify instance status
            ec2 = boto3.client("ec2")
            response = ec2.describe_instances(
                InstanceIds=launched_instance_ids
            )

            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_id = instance["InstanceId"]
                    state = instance["State"]["Name"]

                    if state not in ["terminated", "shutting-down"]:
                        print(
                            f"WARNING: Instance {instance_id} is still {state}, attempting to terminate..."
                        )
                        try:
                            ec2.terminate_instances(InstanceIds=[instance_id])
                            print(
                                f"Termination request sent for instance {instance_id}"
                            )
                        except Exception as e:
                            print(
                                f"Error terminating instance {instance_id}: {e}"
                            )
                    else:
                        print(f"Instance {instance_id} is {state} as expected")

        # Final check to make sure no instances are left running
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
            # Try one more time to terminate them
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
