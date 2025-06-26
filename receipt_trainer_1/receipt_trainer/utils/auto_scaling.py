"""Auto-scaling manager for EC2 instances based on SQS queue depth.

This module provides functionality to:
1. Monitor SQS queue depth
2. Request/release EC2 instances based on workload
3. Select appropriate instance types based on job requirements
4. Implement cost optimization strategies
"""

import base64
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import boto3
from botocore.exceptions import ClientError
from receipt_trainer.utils.infrastructure import EC2Metadata, InstanceRegistry

logger = logging.getLogger(__name__)


class InstanceType:
    """Instance type specification with pricing and capabilities."""

    def __init__(
        self,
        name: str,
        vcpu: int,
        memory_gb: float,
        gpu_count: int = 0,
        gpu_type: Optional[str] = None,
        spot_price: Optional[float] = None,
        on_demand_price: Optional[float] = None,
    ):
        """Initialize instance type.

        Args:
            name: EC2 instance type name (e.g., 'p3.2xlarge')
            vcpu: Number of vCPUs
            memory_gb: Memory in GB
            gpu_count: Number of GPUs (default: 0)
            gpu_type: GPU type (e.g., 'V100', 'T4')
            spot_price: Current spot price (optional)
            on_demand_price: On-demand price (optional)
        """
        self.name = name
        self.vcpu = vcpu
        self.memory_gb = memory_gb
        self.gpu_count = gpu_count
        self.gpu_type = gpu_type
        self.spot_price = spot_price
        self.on_demand_price = on_demand_price

    @property
    def has_gpu(self) -> bool:
        """Check if instance has GPU."""
        return self.gpu_count > 0

    def get_pricing(self) -> Dict[str, float]:
        """Get current pricing information."""
        if not self.spot_price or not self.on_demand_price:
            self._update_pricing()

        return {
            "spot": self.spot_price or 0.0,
            "on_demand": self.on_demand_price or 0.0,
            "savings": self._calculate_savings(),
        }

    def _calculate_savings(self) -> float:
        """Calculate savings percentage of spot vs on-demand."""
        if not self.spot_price or not self.on_demand_price:
            return 0.0

        return (1 - (self.spot_price / self.on_demand_price)) * 100

    def _update_pricing(self):
        """Update pricing information from AWS API."""
        try:
            # This is a simplified implementation
            # In production, you would want to use the AWS Price List API
            # or maintain a pricing database
            region = EC2Metadata.get_instance_region() or "us-east-1"
            ec2 = boto3.client("ec2", region_name=region)

            # Get spot price
            response = ec2.describe_spot_price_history(
                InstanceTypes=[self.name],
                MaxResults=1,
                ProductDescriptions=["Linux/UNIX"],
            )

            if response.get("SpotPriceHistory"):
                self.spot_price = float(
                    response["SpotPriceHistory"][0]["SpotPrice"]
                )

            # On-demand prices would require AWS Price List API
            # This is just a placeholder
            # In practice, you might maintain a pricing table

        except Exception as e:
            logger.error(f"Error updating pricing for {self.name}: {e}")

    def __str__(self) -> str:
        """Return string representation."""
        gpu_info = (
            f", {self.gpu_count}x {self.gpu_type} GPU" if self.has_gpu else ""
        )
        return f"{self.name} ({self.vcpu} vCPU, {self.memory_gb} GB RAM{gpu_info})"


class AutoScalingManager:
    """Manager for auto-scaling EC2 instances based on SQS queue depth."""

    # Default instance types to consider, in order of preference
    DEFAULT_CPU_INSTANCES = [
        "c5.large",
        "c5.xlarge",
        "c5.2xlarge",
        "c5.4xlarge",
    ]
    DEFAULT_GPU_INSTANCES = [
        "g4dn.xlarge",
        "g4dn.2xlarge",
        "p3.2xlarge",
        "p3.8xlarge",
    ]

    # Scaling thresholds
    DEFAULT_SCALE_UP_THRESHOLD = (
        5  # Queue items per instance before scaling up
    )
    DEFAULT_SCALE_DOWN_THRESHOLD = (
        1  # Queue items per instance before scaling down
    )
    DEFAULT_MAX_INSTANCES = 10  # Maximum number of instances to run
    DEFAULT_MIN_INSTANCES = 1  # Minimum number of instances to keep running

    def __init__(
        self,
        queue_url: str,
        instance_ami: str,
        instance_profile: str,
        subnet_id: str,
        security_group_id: str,
        instance_registry_table: Optional[str] = None,
        key_name: Optional[str] = None,
        cpu_instance_types: Optional[List[str]] = None,
        gpu_instance_types: Optional[List[str]] = None,
        max_instances: int = DEFAULT_MAX_INSTANCES,
        min_instances: int = DEFAULT_MIN_INSTANCES,
        user_data: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        region: Optional[str] = None,
    ):
        """Initialize auto-scaling manager.

        Args:
            queue_url: SQS queue URL to monitor
            instance_ami: AMI ID for new instances
            instance_profile: IAM instance profile name
            subnet_id: VPC subnet ID
            security_group_id: Security group ID
            instance_registry_table: DynamoDB table for instance registry
            key_name: SSH key name (optional)
            cpu_instance_types: List of CPU instance types to use
            gpu_instance_types: List of GPU instance types to use
            max_instances: Maximum number of instances to run
            min_instances: Minimum number of instances to keep running
            user_data: EC2 user data script
            tags: Tags to add to instances
            region: AWS region (will auto-detect if not provided)
        """
        self.queue_url = queue_url
        self.instance_ami = instance_ami
        self.instance_profile = instance_profile
        self.subnet_id = subnet_id
        self.security_group_id = security_group_id
        self.instance_registry_table = instance_registry_table
        self.key_name = key_name

        self.cpu_instance_types = (
            cpu_instance_types or self.DEFAULT_CPU_INSTANCES
        )
        self.gpu_instance_types = (
            gpu_instance_types or self.DEFAULT_GPU_INSTANCES
        )

        self.max_instances = max_instances
        self.min_instances = min_instances
        self.user_data = user_data
        self.tags = tags or {"Purpose": "ReceiptTrainer"}

        # Add name tag if not present
        if "Name" not in self.tags:
            self.tags["Name"] = "ReceiptTrainer-Worker"

        # Add managed-by tag
        self.tags["ManagedBy"] = "AutoScalingManager"

        # Set up AWS clients
        self.region = (
            region or EC2Metadata.get_instance_region() or "us-east-1"
        )
        self.ec2 = boto3.client("ec2", region_name=self.region)
        self.sqs = boto3.client("sqs", region_name=self.region)

        # Set up instance registry if table name provided
        self.instance_registry = None
        if instance_registry_table:
            self.instance_registry = InstanceRegistry(
                instance_registry_table, self.region
            )

        # Set up monitoring thread
        self._stop_monitoring = threading.Event()
        self._monitoring_thread = None

        # Initialize instance type info cache
        self._instance_type_cache = {}
        self._initialize_instance_types()

        logger.info(f"Initialized AutoScalingManager for queue {queue_url}")

    def _initialize_instance_types(self):
        """Initialize instance type information."""
        try:
            # Get instance type information
            response = self.ec2.describe_instance_types(
                InstanceTypes=self.cpu_instance_types + self.gpu_instance_types
            )

            for instance_type in response.get("InstanceTypes", []):
                name = instance_type["InstanceType"]
                vcpu = instance_type["VCpuInfo"]["DefaultVCpus"]
                memory_gb = instance_type["MemoryInfo"]["SizeInMiB"] / 1024.0

                # Check for GPUs
                gpu_count = 0
                gpu_type = None

                gpu_info = instance_type.get("GpuInfo", {})
                if gpu_info:
                    gpus = gpu_info.get("Gpus", [])
                    if gpus:
                        gpu_count = len(gpus)
                        gpu_type = gpus[0].get("Name")

                # Create instance type object
                self._instance_type_cache[name] = InstanceType(
                    name=name,
                    vcpu=vcpu,
                    memory_gb=memory_gb,
                    gpu_count=gpu_count,
                    gpu_type=gpu_type,
                )

            logger.info(
                f"Initialized {len(self._instance_type_cache)} instance types"
            )

        except Exception as e:
            logger.error(f"Error initializing instance types: {e}")

    def start_monitoring(self, interval_seconds: int = 60) -> threading.Thread:
        """Start monitoring the queue and auto-scaling instances.

        Args:
            interval_seconds: How often to check queue depth and scale

        Returns:
            The monitoring thread
        """
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            logger.warning("Monitoring thread is already running")
            return self._monitoring_thread

        self._stop_monitoring.clear()

        def monitoring_loop():
            """Background thread for queue monitoring and scaling."""
            logger.info(
                f"Starting queue monitoring with {interval_seconds}s interval"
            )

            while not self._stop_monitoring.is_set():
                try:
                    # Check queue depth and scale accordingly
                    self._check_and_scale()

                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}")

                # Sleep until next check
                time.sleep(interval_seconds)

            logger.info("Queue monitoring stopped")

        self._monitoring_thread = threading.Thread(
            target=monitoring_loop,
            daemon=True,
        )
        self._monitoring_thread.start()

        return self._monitoring_thread

    def stop_monitoring(self):
        """Stop the monitoring thread."""
        if (
            not self._monitoring_thread
            or not self._monitoring_thread.is_alive()
        ):
            logger.warning("Monitoring thread is not running")
            return

        logger.info("Stopping queue monitoring...")
        self._stop_monitoring.set()
        self._monitoring_thread.join(timeout=10)
        logger.info("Queue monitoring stopped")

    def _check_and_scale(self):
        """Check queue depth and scale instances accordingly."""
        # Get queue depth
        queue_depth = self._get_queue_depth()
        logger.debug(f"Current queue depth: {queue_depth}")

        # Get current instances
        current_instances = self._get_managed_instances()
        running_count = len(
            [i for i in current_instances if i["State"] == "running"]
        )
        pending_count = len(
            [i for i in current_instances if i["State"] == "pending"]
        )
        total_count = running_count + pending_count

        logger.debug(
            f"Current instances: {running_count} running, {pending_count} pending"
        )

        # Analyze queue to determine if we need GPU instances
        need_gpu = self._analyze_queue_for_gpu_requirements()

        # Calculate target instance count
        items_per_instance = max(1, queue_depth / max(1, running_count))

        if (
            items_per_instance >= self.DEFAULT_SCALE_UP_THRESHOLD
            and total_count < self.max_instances
        ):
            # Need to scale up
            target_count = min(
                self.max_instances,
                total_count
                + max(1, queue_depth // self.DEFAULT_SCALE_UP_THRESHOLD),
            )
            instances_to_add = target_count - total_count

            if instances_to_add > 0:
                logger.info(
                    f"Scaling up: adding {instances_to_add} instances (queue depth: {queue_depth})"
                )
                self._launch_instances(instances_to_add, need_gpu)

        elif (
            items_per_instance <= self.DEFAULT_SCALE_DOWN_THRESHOLD
            and running_count > self.min_instances
        ):
            # Need to scale down
            target_count = max(
                self.min_instances,
                min(
                    running_count, queue_depth + 1
                ),  # Keep at least queue_depth + 1 instances
            )
            instances_to_remove = running_count - target_count

            if instances_to_remove > 0:
                logger.info(
                    f"Scaling down: removing {instances_to_remove} instances (queue depth: {queue_depth})"
                )
                self._terminate_instances(instances_to_remove)

    def _get_queue_depth(self) -> int:
        """Get the current queue depth.

        Returns:
            Number of messages in the queue
        """
        try:
            response = self.sqs.get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                ],
            )

            visible = int(
                response["Attributes"].get("ApproximateNumberOfMessages", "0")
            )
            not_visible = int(
                response["Attributes"].get(
                    "ApproximateNumberOfMessagesNotVisible", "0"
                )
            )

            # For scaling purposes, we primarily care about visible messages
            # Not visible messages are already being processed
            return visible

        except Exception as e:
            logger.error(f"Error getting queue depth: {e}")
            return 0

    def _analyze_queue_for_gpu_requirements(self) -> bool:
        """Analyze the queue to determine if GPU instances are needed.

        Returns:
            True if GPU instances are needed, False otherwise
        """
        try:
            # Peek at messages in the queue
            response = self.sqs.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=10,
                VisibilityTimeout=5,  # Short timeout as we're just peeking
                AttributeNames=["All"],
                MessageAttributeNames=["All"],
            )

            messages = response.get("Messages", [])

            # Return messages to the queue immediately
            for message in messages:
                self.sqs.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                    VisibilityTimeout=0,
                )

            # Check if any job requires GPU
            for message in messages:
                try:
                    body = json.loads(message.get("Body", "{}"))
                    job_config = body.get("job_config", {})

                    # Heuristics to determine if a job needs GPU
                    # 1. Check for explicit GPU requirement
                    if job_config.get("requires_gpu", False):
                        return True

                    # 2. Check training config for GPU indicators
                    training_config = job_config.get("training_config", {})

                    # Large batch sizes or model parameters often indicate GPU usage
                    batch_size = training_config.get("batch_size", 0)
                    if batch_size >= 32:
                        return True

                    # Certain model architectures typically need GPUs
                    model_name = job_config.get("model_name", "").lower()
                    gpu_keywords = [
                        "bert",
                        "gpt",
                        "layoutlm",
                        "t5",
                        "roberta",
                        "vit",
                        "yolo",
                    ]

                    if any(keyword in model_name for keyword in gpu_keywords):
                        return True

                except Exception:
                    # If we can't parse the message, assume CPU is sufficient
                    pass

            # Default to false if no clear indicators
            return False

        except Exception as e:
            logger.error(f"Error analyzing queue for GPU requirements: {e}")
            return False

    def _get_managed_instances(self) -> List[Dict[str, Any]]:
        """Get all instances managed by this auto-scaler.

        Returns:
            List of instance information dictionaries
        """
        try:
            # Get instances with our management tag
            response = self.ec2.describe_instances(
                Filters=[
                    {
                        "Name": "tag:ManagedBy",
                        "Values": ["AutoScalingManager"],
                    },
                    {
                        "Name": "instance-state-name",
                        "Values": [
                            "pending",
                            "running",
                            "stopping",
                            "stopped",
                        ],
                    },
                ]
            )

            instances = []
            for reservation in response.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instances.append(
                        {
                            "InstanceId": instance["InstanceId"],
                            "InstanceType": instance["InstanceType"],
                            "State": instance["State"]["Name"],
                            "LaunchTime": instance.get("LaunchTime"),
                        }
                    )

            return instances

        except Exception as e:
            logger.error(f"Error getting managed instances: {e}")
            return []

    def _select_optimal_instance_type(self, need_gpu: bool) -> str:
        """Select the optimal instance type based on requirements and pricing.

        Args:
            need_gpu: Whether GPU is required

        Returns:
            Instance type name
        """
        # Choose from GPU or CPU instance types
        candidate_types = (
            self.gpu_instance_types if need_gpu else self.cpu_instance_types
        )

        # Get spot price information
        try:
            response = self.ec2.describe_spot_price_history(
                InstanceTypes=candidate_types,
                ProductDescriptions=["Linux/UNIX"],
                MaxResults=len(candidate_types),
            )

            # Build price map
            price_map = {}
            for price_item in response.get("SpotPriceHistory", []):
                instance_type = price_item["InstanceType"]
                price = float(price_item["SpotPrice"])

                if (
                    instance_type not in price_map
                    or price < price_map[instance_type]
                ):
                    price_map[instance_type] = price

            # If we have pricing information, select the cheapest option
            if price_map:
                sorted_types = sorted(price_map.items(), key=lambda x: x[1])
                return sorted_types[0][0]

        except Exception as e:
            logger.warning(
                f"Error getting spot pricing, using default instance type: {e}"
            )

        # Fallback to first type in the list if no pricing data
        return candidate_types[0]

    def _launch_instances(self, count: int, need_gpu: bool):
        """Launch new EC2 instances.

        Args:
            count: Number of instances to launch
            need_gpu: Whether GPU is required
        """
        instance_type = self._select_optimal_instance_type(need_gpu)
        logger.info(f"Selected instance type: {instance_type}")

        # Convert tags to EC2 format
        ec2_tags = [{"Key": k, "Value": v} for k, v in self.tags.items()]

        try:
            # Prepare user data
            user_data = self.user_data
            if user_data:
                if not user_data.startswith("#!/bin/"):
                    # Encode if not a script
                    user_data = base64.b64encode(user_data.encode()).decode()
                else:
                    # It's a script, encode as base64
                    user_data = base64.b64encode(
                        user_data.encode("utf-8")
                    ).decode("utf-8")

            # Prepare launch specification
            launch_spec = {
                "ImageId": self.instance_ami,
                "InstanceType": instance_type,
                "SecurityGroupIds": [self.security_group_id],
                "SubnetId": self.subnet_id,
                "IamInstanceProfile": {"Name": self.instance_profile},
            }

            # Only add UserData if it's provided
            if user_data:
                launch_spec["UserData"] = user_data

            # Only add KeyName if it's provided
            if self.key_name:
                launch_spec["KeyName"] = self.key_name

            # Request spot instances
            response = self.ec2.request_spot_instances(
                InstanceCount=count,
                LaunchSpecification=launch_spec,
            )

            # Get spot request IDs
            spot_request_ids = [
                request["SpotInstanceRequestId"]
                for request in response.get("SpotInstanceRequests", [])
            ]

            logger.info(f"Submitted {len(spot_request_ids)} spot requests")

            # Tag spot requests
            if spot_request_ids:
                self.ec2.create_tags(
                    Resources=spot_request_ids,
                    Tags=ec2_tags,
                )

            # Wait for spot instances to be fulfilled
            fulfilled_instances = []
            max_wait_time = 300  # 5 minutes
            start_time = time.time()

            while (
                spot_request_ids and time.time() - start_time < max_wait_time
            ):
                time.sleep(15)  # Check every 15 seconds

                response = self.ec2.describe_spot_instance_requests(
                    SpotInstanceRequestIds=spot_request_ids,
                )

                for request in response.get("SpotInstanceRequests", []):
                    request_id = request["SpotInstanceRequestId"]
                    status = request["Status"]["Code"]

                    if status == "fulfilled" and "InstanceId" in request:
                        instance_id = request["InstanceId"]
                        fulfilled_instances.append(instance_id)
                        spot_request_ids.remove(request_id)
                        logger.info(
                            f"Spot request {request_id} fulfilled with instance {instance_id}"
                        )

            # Tag instances
            if fulfilled_instances:
                self.ec2.create_tags(
                    Resources=fulfilled_instances,
                    Tags=ec2_tags,
                )

                logger.info(
                    f"Successfully launched {len(fulfilled_instances)} instances"
                )

            # Log unfulfilled requests
            if spot_request_ids:
                logger.warning(
                    f"{len(spot_request_ids)} spot requests not fulfilled within timeout"
                )

        except Exception as e:
            logger.error(f"Error launching instances: {e}")

    def _terminate_instances(self, count: int):
        """Terminate EC2 instances.

        Args:
            count: Number of instances to terminate
        """
        try:
            # Get current instances
            instances = self._get_managed_instances()

            # Sort by launch time (terminate newest first to preserve older instances)
            running_instances = [
                i for i in instances if i["State"] == "running"
            ]

            # Sort newest first (we'll terminate the newest instances first)
            running_instances.sort(
                key=lambda x: x.get("LaunchTime", time.time()), reverse=True
            )

            # Select instances to terminate
            to_terminate = running_instances[:count]
            instance_ids = [i["InstanceId"] for i in to_terminate]

            if not instance_ids:
                logger.warning("No instances to terminate")
                return

            logger.info(
                f"Terminating {len(instance_ids)} instances: {instance_ids}"
            )

            # Terminate instances
            self.ec2.terminate_instances(
                InstanceIds=instance_ids,
            )

            logger.info(
                f"Successfully initiated termination for {len(instance_ids)} instances"
            )

        except Exception as e:
            logger.error(f"Error terminating instances: {e}")

    def get_instance_status(self) -> Dict[str, Any]:
        """Get current auto-scaling status.

        Returns:
            Dictionary of status information
        """
        instances = self._get_managed_instances()

        # Count instances by state
        state_counts = {}
        for instance in instances:
            state = instance["State"]
            state_counts[state] = state_counts.get(state, 0) + 1

        # Count by instance type
        type_counts = {}
        for instance in instances:
            instance_type = instance["InstanceType"]
            type_counts[instance_type] = type_counts.get(instance_type, 0) + 1

        # Get queue information
        queue_depth = self._get_queue_depth()

        return {
            "queue_depth": queue_depth,
            "total_instances": len(instances),
            "instances_by_state": state_counts,
            "instances_by_type": type_counts,
            "monitoring_active": self._monitoring_thread is not None
            and self._monitoring_thread.is_alive(),
        }


def generate_training_worker_user_data(
    queue_url: str,
    dynamo_table: str,
    instance_registry_table: Optional[str] = None,
    s3_bucket: Optional[str] = None,
    efs_id: Optional[str] = None,
    efs_mount_point: str = "/mnt/efs",
    logging_level: str = "INFO",
    worker_script_path: str = "/usr/local/bin/receipt-training-worker",
) -> str:
    """Generate user data script for training worker instances.

    Args:
        queue_url: SQS queue URL
        dynamo_table: DynamoDB table for receipt data
        instance_registry_table: DynamoDB table for instance registry
        s3_bucket: S3 bucket for artifacts
        efs_id: EFS file system ID for mounting
        efs_mount_point: Where to mount EFS
        logging_level: Logging level
        worker_script_path: Path to worker script

    Returns:
        EC2 user data script
    """
    script = """#!/bin/bash
# ReceiptTrainer Worker initialization script

# Set environment variables
export QUEUE_URL="{queue_url}"
export DYNAMO_TABLE="{dynamo_table}"
export LOGGING_LEVEL="{logging_level}"
""".format(
        queue_url=queue_url,
        dynamo_table=dynamo_table,
        logging_level=logging_level,
    )

    # Add optional environment variables
    if instance_registry_table:
        script += (
            f'export INSTANCE_REGISTRY_TABLE="{instance_registry_table}"\n'
        )

    if s3_bucket:
        script += f'export S3_BUCKET="{s3_bucket}"\n'

    # Add EFS mounting if specified
    if efs_id:
        script += f"""
# Mount EFS file system
mkdir -p {efs_mount_point}
echo "Mounting EFS {efs_id} to {efs_mount_point}..."
mount -t efs {efs_id}:/ {efs_mount_point}
if [ $? -eq 0 ]; then
    echo "EFS mounted successfully"
    # Add to fstab for persistence
    echo "{efs_id}:/ {efs_mount_point} efs tls,_netdev 0 0" >> /etc/fstab
else
    echo "Error mounting EFS" >&2
fi

export EFS_MOUNT_POINT="{efs_mount_point}"
"""

    # Add worker script
    script += f"""
# Start the worker process
echo "Starting receipt training worker..."
{worker_script_path} --queue $QUEUE_URL --dynamo-table $DYNAMO_TABLE > /var/log/receipt-worker.log 2>&1 &

echo "Worker initialization completed."
"""

    return script
