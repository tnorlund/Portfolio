"""AWS integration utilities for Receipt Trainer."""

import fcntl
import json
import logging
import os
import socket
import struct
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from receipt_dynamo.data._pulumi import load_env as _load_pulumi_env

logger = logging.getLogger(__name__)


def load_env(env: str = "dev") -> Dict[str, Any]:
    """Load Pulumi stack outputs for the given environment.

    Args:
        env: Pulumi stack environment (dev/prod)

    Returns:
        Dictionary of stack outputs
    """
    try:
        return _load_pulumi_env(env)
    except Exception as e:
        logger.error(f"Failed to load Pulumi stack outputs for environment {env}: {e}")
        raise


def get_dynamo_table(env: str = "dev") -> str:
    """Get the DynamoDB table name from Pulumi.

    Args:
        env: Pulumi environment ('dev' or 'prod')

    Returns:
        DynamoDB table name
    """
    try:
        # Execute pulumi stack output to get the table name
        command = f"pulumi stack output --stack {env} --json"
        output = subprocess.check_output(command.split(), text=True)
        outputs = json.loads(output)

        # Check if the table name is in the output
        table_name = None
        for key in ["receipts_table", "receiptsTable", "dynamodb_table"]:
            if key in outputs:
                table_name = outputs[key]
                break

        if not table_name:
            raise ValueError(
                f"DynamoDB table name not found in Pulumi stack output for env: {env}"
            )

        return table_name
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get DynamoDB table from Pulumi: {e}")
        # Fallback to environment variable
        table_name = os.environ.get("DYNAMO_TABLE")
        if table_name:
            logger.info(f"Using DynamoDB table from environment variable: {table_name}")
            return table_name
        raise


def get_s3_bucket(bucket_name: Optional[str] = None, env: str = "dev") -> str:
    """Get S3 bucket name from Pulumi stack or direct input.

    Args:
        bucket_name: Optional explicit bucket name
        env: Pulumi stack environment if bucket_name not provided

    Returns:
        S3 bucket name
    """
    if bucket_name:
        return bucket_name

    stack_outputs = load_env(env)
    bucket_name = stack_outputs.get("s3_bucket_name")

    if not bucket_name:
        raise ValueError(
            f"Could not find S3 bucket name in Pulumi stack outputs for environment: {env}"
        )

    return bucket_name


def get_instance_metadata() -> Dict[str, Any]:
    """Get EC2 instance metadata.

    Returns:
        Dictionary with instance metadata
    """
    metadata = {}
    try:
        # Get instance ID
        instance_id_url = "http://169.254.169.254/latest/meta-data/instance-id"
        instance_id = subprocess.check_output(
            ["curl", "-s", instance_id_url], text=True
        )
        metadata["instance_id"] = instance_id

        # Get instance type
        instance_type_url = "http://169.254.169.254/latest/meta-data/instance-type"
        instance_type = subprocess.check_output(
            ["curl", "-s", instance_type_url], text=True
        )
        metadata["instance_type"] = instance_type

        # Get availability zone
        az_url = "http://169.254.169.254/latest/meta-data/placement/availability-zone"
        az = subprocess.check_output(["curl", "-s", az_url], text=True)
        metadata["availability_zone"] = az
        metadata["region"] = az[:-1]  # Remove the AZ letter to get the region

        # Get spot instance information if available
        try:
            spot_url = "http://169.254.169.254/latest/meta-data/spot/instance-action"
            spot_info = subprocess.check_output(
                ["curl", "-s", "-f", spot_url], text=True
            )
            metadata["spot_instance_action"] = json.loads(spot_info)
        except subprocess.CalledProcessError:
            # Not a spot instance or no interruption
            metadata["spot_instance_action"] = None

        return metadata
    except Exception as e:
        logger.warning(f"Failed to get instance metadata: {e}")
        return {"error": str(e)}


def register_instance(
    registry_table: str,
    instance_id: Optional[str] = None,
    status: str = "running",
    ttl_hours: int = 1,
) -> bool:
    """Register this instance in the instance registry.

    Args:
        registry_table: Name of the DynamoDB registry table
        instance_id: Optional instance ID (if None, will be fetched from metadata)
        status: Initial status to set
        ttl_hours: TTL in hours for the registry entry

    Returns:
        True if registration was successful
    """
    try:
        # Get metadata if instance_id not provided
        if not instance_id:
            metadata = get_instance_metadata()
            instance_id = metadata.get("instance_id")
            if not instance_id:
                logger.error("Failed to get instance ID from metadata")
                return False

            # Extract other useful metadata
            instance_type = metadata.get("instance_type", "unknown")
            availability_zone = metadata.get("availability_zone", "unknown")
            region = metadata.get(
                "region", "us-east-1"
            )  # Default to us-east-1 if not found
        else:
            # If instance_id is provided, we might not have other metadata
            metadata = get_instance_metadata()
            instance_type = metadata.get("instance_type", "unknown")
            availability_zone = metadata.get("availability_zone", "unknown")
            region = metadata.get("region", "us-east-1")

        # Detect GPUs
        gpu_info = "none"
        gpu_count = 0
        try:
            nvidia_smi_output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader",
                ],
                text=True,
            )
            gpu_info = nvidia_smi_output.replace("\n", "|").strip("|")
            gpu_count_output = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=count", "--format=csv,noheader"],
                text=True,
            )
            gpu_count = int(gpu_count_output.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.info("No GPUs detected or nvidia-smi not available")

        # Calculate TTL
        ttl = int(time.time()) + (ttl_hours * 3600)

        # Register in DynamoDB
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(registry_table)

        response = table.put_item(
            Item={
                "instance_id": instance_id,
                "status": status,
                "instance_type": instance_type,
                "availability_zone": availability_zone,
                "registration_time": int(time.time()),
                "ttl": ttl,
                "gpu_info": gpu_info,
                "gpu_count": gpu_count,
                "is_leader": False,
                "hostname": socket.gethostname(),
            }
        )

        logger.info(f"Instance {instance_id} registered successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to register instance: {e}")
        return False


def update_instance_status(
    registry_table: str,
    instance_id: Optional[str] = None,
    status: str = "running",
    ttl_hours: int = 1,
    additional_attributes: Optional[Dict[str, Any]] = None,
) -> bool:
    """Update instance status in the registry.

    Args:
        registry_table: Name of the DynamoDB registry table
        instance_id: Optional instance ID (if None, will be fetched from metadata)
        status: Status to set
        ttl_hours: TTL in hours for the registry entry
        additional_attributes: Additional attributes to update

    Returns:
        True if update was successful
    """
    try:
        # Get metadata if instance_id not provided
        if not instance_id:
            metadata = get_instance_metadata()
            instance_id = metadata.get("instance_id")
            if not instance_id:
                logger.error("Failed to get instance ID from metadata")
                return False

        # Get region from metadata
        metadata = get_instance_metadata()
        region = metadata.get("region", "us-east-1")

        # Calculate TTL
        ttl = int(time.time()) + (ttl_hours * 3600)

        # Build update expression
        update_expression = (
            "SET #status = :status, ttl = :ttl, last_heartbeat = :heartbeat"
        )
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":ttl": ttl,
            ":heartbeat": int(time.time()),
        }

        # Add additional attributes
        if additional_attributes:
            for i, (key, value) in enumerate(additional_attributes.items()):
                update_expression += f", #{i} = :{i}"
                expression_attribute_names[f"#{i}"] = key
                expression_attribute_values[f":{i}"] = value

        # Update in DynamoDB
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(registry_table)

        response = table.update_item(
            Key={"instance_id": instance_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="UPDATED_NEW",
        )

        logger.info(f"Instance {instance_id} status updated to '{status}'")
        return True
    except Exception as e:
        logger.error(f"Failed to update instance status: {e}")
        return False


def deregister_instance(registry_table: str, instance_id: Optional[str] = None) -> bool:
    """Deregister this instance from the registry.

    Args:
        registry_table: Name of the DynamoDB registry table
        instance_id: Optional instance ID (if None, will be fetched from metadata)

    Returns:
        True if deregistration was successful
    """
    try:
        # Get metadata if instance_id not provided
        if not instance_id:
            metadata = get_instance_metadata()
            instance_id = metadata.get("instance_id")
            if not instance_id:
                logger.error("Failed to get instance ID from metadata")
                return False

        # Get region from metadata
        metadata = get_instance_metadata()
        region = metadata.get("region", "us-east-1")

        # Remove from DynamoDB
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(registry_table)

        response = table.delete_item(Key={"instance_id": instance_id})

        logger.info(f"Instance {instance_id} deregistered successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to deregister instance: {e}")
        return False


def elect_leader(registry_table: str, instance_id: Optional[str] = None) -> bool:
    """Try to elect this instance as the leader.

    Args:
        registry_table: Name of the DynamoDB registry table
        instance_id: Optional instance ID (if None, will be fetched from metadata)

    Returns:
        True if this instance was elected leader
    """
    try:
        # Get metadata if instance_id not provided
        if not instance_id:
            metadata = get_instance_metadata()
            instance_id = metadata.get("instance_id")
            if not instance_id:
                logger.error("Failed to get instance ID from metadata")
                return False

        # Get region from metadata
        metadata = get_instance_metadata()
        region = metadata.get("region", "us-east-1")

        # Check if there's already a leader
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(registry_table)

        # Query for existing leaders
        response = table.scan(
            FilterExpression="is_leader = :true",
            ExpressionAttributeValues={":true": True},
            Select="COUNT",
        )

        leader_count = response.get("Count", 0)

        if leader_count == 0:
            # No leader, try to become one
            try:
                response = table.update_item(
                    Key={"instance_id": instance_id},
                    UpdateExpression="SET is_leader = :true",
                    ConditionExpression="attribute_exists(instance_id)",
                    ExpressionAttributeValues={":true": True},
                    ReturnValues="UPDATED_NEW",
                )

                logger.info(f"Instance {instance_id} elected as leader")
                return True
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    logger.info(
                        f"Leader election failed, instance {instance_id} not registered or leader already elected"
                    )
                    return False
                raise
        else:
            logger.info(f"Leader already exists, instance {instance_id} not elected")
            return False
    except Exception as e:
        logger.error(f"Error during leader election: {e}")
        return False


def get_leader_instance(registry_table: str) -> Optional[Dict[str, Any]]:
    """Get the current leader instance.

    Args:
        registry_table: Name of the DynamoDB registry table

    Returns:
        Dictionary with leader instance details or None if no leader
    """
    try:
        # Get region from metadata
        metadata = get_instance_metadata()
        region = metadata.get("region", "us-east-1")

        # Query DynamoDB for leader
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(registry_table)

        response = table.scan(
            FilterExpression="is_leader = :true",
            ExpressionAttributeValues={":true": True},
        )

        items = response.get("Items", [])

        if items:
            return items[0]
        else:
            logger.info("No leader instance found")
            return None
    except Exception as e:
        logger.error(f"Failed to get leader instance: {e}")
        return None


def get_all_instances(
    registry_table: str, status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get all registered instances, optionally filtered by status.

    Args:
        registry_table: Name of the DynamoDB registry table
        status: Optional status to filter by

    Returns:
        List of instance details
    """
    try:
        # Get region from metadata
        metadata = get_instance_metadata()
        region = metadata.get("region", "us-east-1")

        # Query DynamoDB
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(registry_table)

        if status:
            # Use GSI to query by status
            response = table.query(
                IndexName="StatusIndex",
                KeyConditionExpression="#status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":status": status},
            )
        else:
            # Scan for all instances
            response = table.scan()

        return response.get("Items", [])
    except Exception as e:
        logger.error(f"Failed to get instances: {e}")
        return []


def setup_efs_mounts() -> bool:
    """Set up EFS mounts using environment variables.

    Returns:
        True if mounts were successful
    """
    try:
        # Get EFS information from environment
        efs_dns_name = os.environ.get("EFS_DNS_NAME")
        training_ap_id = os.environ.get("TRAINING_ACCESS_POINT_ID")
        checkpoints_ap_id = os.environ.get("CHECKPOINTS_ACCESS_POINT_ID")

        if not all([efs_dns_name, training_ap_id, checkpoints_ap_id]):
            logger.error("EFS environment variables not set")
            return False

        # Create mount directories
        os.makedirs("/mnt/training", exist_ok=True)
        os.makedirs("/mnt/checkpoints", exist_ok=True)

        # Mount training directory
        training_cmd = f"mount -t efs -o tls,accesspoint={training_ap_id} {efs_dns_name}:/ /mnt/training"
        subprocess.run(training_cmd.split(), check=True)

        # Mount checkpoints directory
        checkpoints_cmd = f"mount -t efs -o tls,accesspoint={checkpoints_ap_id} {efs_dns_name}:/ /mnt/checkpoints"
        subprocess.run(checkpoints_cmd.split(), check=True)

        # Check if mounts are successful
        mounts = subprocess.check_output(["mount"], text=True)

        if "/mnt/training" in mounts and "/mnt/checkpoints" in mounts:
            logger.info("EFS mounts set up successfully")
            return True
        else:
            logger.error("EFS mount verification failed")
            return False
    except Exception as e:
        logger.error(f"Failed to set up EFS mounts: {e}")
        return False


def check_spot_interruption() -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Check if this spot instance is scheduled for interruption.

    Returns:
        Tuple of (is_interrupted, interruption_details)
    """
    try:
        # Try to get spot instance action from metadata service
        url = "http://169.254.169.254/latest/meta-data/spot/instance-action"
        try:
            output = subprocess.check_output(["curl", "-s", "-f", url], text=True)
            interruption = json.loads(output)
            return True, interruption
        except subprocess.CalledProcessError:
            # No interruption or not a spot instance
            return False, None
    except Exception as e:
        logger.error(f"Error checking spot interruption: {e}")
        return False, None


def acquire_lock(lock_file: str, timeout: int = 60) -> Optional[int]:
    """Acquire a file lock for synchronized access across instances.

    Args:
        lock_file: Path to the lock file
        timeout: Maximum time to wait for lock in seconds

    Returns:
        File descriptor if lock acquired, None if timeout
    """
    start_time = time.time()
    fd = open(lock_file, "w")

    while time.time() - start_time < timeout:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except IOError:
            # Lock is held by another process
            time.sleep(1)

    # Timeout expired
    fd.close()
    return None


def release_lock(fd: int) -> None:
    """Release a previously acquired file lock.

    Args:
        fd: File descriptor of the lock file
    """
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def notify_spot_interruption(
    sns_topic_arn: str,
    instance_id: Optional[str] = None,
    message: Optional[str] = None,
) -> bool:
    """Notify an SNS topic about spot interruption.

    Args:
        sns_topic_arn: ARN of the SNS topic
        instance_id: Optional instance ID (if None, will be fetched from metadata)
        message: Optional custom message

    Returns:
        True if notification was successful
    """
    try:
        # Get metadata if instance_id not provided
        if not instance_id:
            metadata = get_instance_metadata()
            instance_id = metadata.get("instance_id")
            if not instance_id:
                logger.error("Failed to get instance ID from metadata")
                return False

        # Get region from metadata
        metadata = get_instance_metadata()
        region = metadata.get("region", "us-east-1")

        # Create message
        if not message:
            interruption_time = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            message = f"Spot instance {instance_id} is scheduled for interruption at {interruption_time}"

        # Publish to SNS
        sns = boto3.client("sns", region_name=region)
        response = sns.publish(
            TopicArn=sns_topic_arn,
            Message=message,
            Subject=f"Spot Instance Interruption: {instance_id}",
        )

        logger.info(f"Spot interruption notification sent to {sns_topic_arn}")
        return True
    except Exception as e:
        logger.error(f"Failed to send spot interruption notification: {e}")
        return False
