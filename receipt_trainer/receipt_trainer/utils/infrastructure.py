"""Infrastructure utilities for training on AWS.

This module provides utilities for seamless integration with AWS infrastructure:
- EC2 metadata detection
- Spot instance interruption handling
- EFS mounting and management
- Instance registry integration
"""

import os
import sys
import signal
import json
import time
import logging
import subprocess
import socket
import uuid
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Callable

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class EC2Metadata:
    """Utilities for interacting with EC2 instance metadata."""

    BASE_URL = "http://169.254.169.254/latest/meta-data/"
    TIMEOUT = 2  # seconds

    @classmethod
    def is_ec2_instance(cls) -> bool:
        """Check if code is running on an EC2 instance."""
        try:
            socket.setdefaulttimeout(cls.TIMEOUT)
            requests.get(cls.BASE_URL, timeout=cls.TIMEOUT)
            return True
        except (requests.RequestException, socket.timeout):
            return False

    @classmethod
    def get_instance_id(cls) -> Optional[str]:
        """Get the EC2 instance ID."""
        if not cls.is_ec2_instance():
            return None
        try:
            response = requests.get(
                f"{cls.BASE_URL}instance-id", timeout=cls.TIMEOUT
            )
            return response.text
        except requests.RequestException:
            return None

    @classmethod
    def get_instance_type(cls) -> Optional[str]:
        """Get the EC2 instance type."""
        if not cls.is_ec2_instance():
            return None
        try:
            response = requests.get(
                f"{cls.BASE_URL}instance-type", timeout=cls.TIMEOUT
            )
            return response.text
        except requests.RequestException:
            return None

    @classmethod
    def is_spot_instance(cls) -> bool:
        """Check if running on a spot instance."""
        if not cls.is_ec2_instance():
            return False
        try:
            response = requests.get(
                f"{cls.BASE_URL}instance-life-cycle", timeout=cls.TIMEOUT
            )
            return response.text == "spot"
        except requests.RequestException:
            # Try alternative approach if metadata endpoint fails
            try:
                # Check for spot termination notice URL (only available on spot)
                requests.get(
                    "http://169.254.169.254/latest/meta-data/spot/termination-time",
                    timeout=cls.TIMEOUT,
                )
                return True
            except requests.RequestException:
                return False

    @classmethod
    def get_instance_region(cls) -> Optional[str]:
        """Get the AWS region for the instance."""
        if not cls.is_ec2_instance():
            return None
        try:
            response = requests.get(
                f"{cls.BASE_URL}placement/region", timeout=cls.TIMEOUT
            )
            return response.text
        except requests.RequestException:
            return os.environ.get("AWS_DEFAULT_REGION")


class EFSManager:
    """Utilities for working with EFS mounts."""

    @classmethod
    def is_efs_mounted(cls, mount_point: str) -> bool:
        """Check if an EFS filesystem is mounted at the specified path."""
        if not os.path.exists(mount_point):
            return False

        try:
            df_output = subprocess.check_output(
                ["df", "-t", "nfs4", mount_point],
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
            return mount_point in df_output
        except subprocess.CalledProcessError:
            return False

    @classmethod
    def mount_efs(
        cls,
        dns_name: str,
        mount_point: str,
        access_point_id: Optional[str] = None,
    ) -> bool:
        """Mount an EFS filesystem.

        Args:
            dns_name: EFS DNS name
            mount_point: Local mount point
            access_point_id: Optional access point ID

        Returns:
            True if successful, False otherwise
        """
        if cls.is_efs_mounted(mount_point):
            logger.info(f"EFS already mounted at {mount_point}")
            return True

        # Ensure mount point exists
        os.makedirs(mount_point, exist_ok=True)

        # Construct mount command
        mount_options = "tls,noresvport"
        if access_point_id:
            mount_options += f",accesspoint={access_point_id}"

        mount_cmd = [
            "mount",
            "-t",
            "efs",
            "-o",
            mount_options,
            f"{dns_name}:/",
            mount_point,
        ]

        try:
            # Execute mount command
            subprocess.check_call(mount_cmd)
            logger.info(f"Successfully mounted EFS at {mount_point}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to mount EFS: {e}")
            return False

    @classmethod
    def setup_training_mounts(cls) -> bool:
        """Set up standard EFS mounts for training.

        This will look for environment variables:
        - EFS_DNS_NAME
        - TRAINING_ACCESS_POINT_ID
        - CHECKPOINTS_ACCESS_POINT_ID

        Returns:
            True if all mounts successful, False otherwise
        """
        efs_dns_name = os.environ.get("EFS_DNS_NAME")
        training_ap_id = os.environ.get("TRAINING_ACCESS_POINT_ID")
        checkpoints_ap_id = os.environ.get("CHECKPOINTS_ACCESS_POINT_ID")

        if not efs_dns_name:
            logger.warning("EFS_DNS_NAME not set, skipping EFS mounts")
            return False

        # Mount training directory
        training_success = cls.mount_efs(
            efs_dns_name, "/mnt/training", training_ap_id
        )

        # Mount checkpoints directory
        checkpoints_success = cls.mount_efs(
            efs_dns_name, "/mnt/checkpoints", checkpoints_ap_id
        )

        return training_success and checkpoints_success


class InstanceRegistry:
    """Client for the instance registry in DynamoDB."""

    def __init__(self, table_name: str, region: Optional[str] = None):
        """Initialize the instance registry client.

        Args:
            table_name: Name of the DynamoDB table
            region: AWS region (optional, will try to autodetect)
        """
        self.table_name = table_name
        self.region = (
            region or EC2Metadata.get_instance_region() or "us-east-1"
        )
        self.dynamodb = boto3.resource("dynamodb", region_name=self.region)
        self.table = self.dynamodb.Table(table_name)

        # Cache instance metadata
        self.instance_id = EC2Metadata.get_instance_id()
        self.instance_type = EC2Metadata.get_instance_type()
        self.is_spot = EC2Metadata.is_spot_instance()

    def register_instance(self, ttl_hours: int = 2) -> bool:
        """Register this instance in the registry.

        Args:
            ttl_hours: Time-to-live in hours

        Returns:
            True if successful, False otherwise
        """
        if not self.instance_id:
            logger.warning("Cannot register instance - not on EC2")
            return False

        # Get GPU information
        gpu_count, gpu_info = self._get_gpu_info()

        # Calculate TTL
        ttl = int(datetime.now().timestamp() + (ttl_hours * 3600))

        # Create registry item
        try:
            self.table.put_item(
                Item={
                    "instance_id": self.instance_id,
                    "status": "running",
                    "instance_type": self.instance_type,
                    "is_spot": self.is_spot,
                    "registration_time": int(datetime.now().timestamp()),
                    "ttl": ttl,
                    "gpu_count": gpu_count,
                    "gpu_info": gpu_info,
                    "is_leader": False,
                }
            )
            logger.info(f"Registered instance {self.instance_id} in registry")
            return True
        except ClientError as e:
            logger.error(f"Failed to register instance: {e}")
            return False

    def heartbeat(self, ttl_hours: int = 2) -> bool:
        """Update the instance heartbeat.

        Args:
            ttl_hours: Time-to-live in hours

        Returns:
            True if successful, False otherwise
        """
        if not self.instance_id:
            return False

        # Calculate TTL
        ttl = int(datetime.now().timestamp() + (ttl_hours * 3600))

        try:
            self.table.update_item(
                Key={"instance_id": self.instance_id},
                UpdateExpression="SET last_heartbeat = :now, ttl = :ttl",
                ExpressionAttributeValues={
                    ":now": int(datetime.now().timestamp()),
                    ":ttl": ttl,
                },
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to update heartbeat: {e}")
            return False

    def elect_leader(self) -> bool:
        """Try to elect this instance as the leader.

        Returns:
            True if this instance is now the leader, False otherwise
        """
        if not self.instance_id:
            return False

        try:
            # First check if there's already a leader
            response = self.table.scan(
                FilterExpression="is_leader = :true",
                ExpressionAttributeValues={":true": True},
                Limit=1,
            )

            if response.get("Items"):
                logger.info("A leader already exists")
                return False

            # Try to become leader using conditional update
            self.table.update_item(
                Key={"instance_id": self.instance_id},
                UpdateExpression="SET is_leader = :true",
                ConditionExpression="attribute_exists(instance_id)",
                ExpressionAttributeValues={":true": True},
            )
            logger.info(f"Instance {self.instance_id} is now the leader")
            return True
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                # Another instance might have become leader first
                logger.info("Failed to become leader - condition failed")
            else:
                logger.error(f"Error in leader election: {e}")
            return False

    def is_leader(self) -> bool:
        """Check if this instance is the leader.

        Returns:
            True if this instance is the leader, False otherwise
        """
        if not self.instance_id:
            return False

        try:
            response = self.table.get_item(
                Key={"instance_id": self.instance_id}
            )
            item = response.get("Item", {})
            return item.get("is_leader", False)
        except ClientError as e:
            logger.error(f"Failed to check leader status: {e}")
            return False

    def _get_gpu_info(self) -> Tuple[int, str]:
        """Get GPU information.

        Returns:
            Tuple of (gpu_count, gpu_info_string)
        """
        try:
            # Try using nvidia-smi
            gpu_output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader",
                ],
                universal_newlines=True,
            )
            gpu_lines = gpu_output.strip().split("\n")
            gpu_count = len(gpu_lines)
            gpu_info = "|".join(line.strip() for line in gpu_lines)
            return gpu_count, gpu_info
        except (subprocess.CalledProcessError, FileNotFoundError):
            # No GPU or nvidia-smi not available
            return 0, "none"


class SpotInstanceHandler:
    """Handler for AWS Spot Instance interruptions."""

    def __init__(
        self,
        callback=None,
        checkpoint_dir: Optional[str] = None,
        job_id: Optional[str] = None,
    ):
        """Initialize the spot instance handler.

        Args:
            callback: Function to call on interruption (optional)
            checkpoint_dir: Directory for emergency checkpoints (optional)
            job_id: Current job ID (optional)
        """
        self.callback = callback
        self.checkpoint_dir = checkpoint_dir
        self.job_id = job_id
        self.is_spot = EC2Metadata.is_spot_instance()
        self.handler_registered = False

    def register_handler(self):
        """Register the SIGTERM handler for spot interruptions.

        Returns:
            True if handler was registered, False otherwise
        """
        if not self.is_spot:
            logger.info("Not running on a spot instance, handler not needed")
            return False

        if self.handler_registered:
            return True

        def handle_sigterm(*args):
            """Handle SIGTERM signal (2-minute warning for spot termination)."""
            logger.warning(
                "Received SIGTERM - Spot Instance interruption imminent!"
            )

            # Create emergency checkpoint path if needed
            if self.checkpoint_dir and self.job_id:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                checkpoint_path = os.path.join(
                    self.checkpoint_dir, self.job_id, f"emergency_{timestamp}"
                )
                os.makedirs(checkpoint_path, exist_ok=True)
                logger.info(
                    f"Created emergency checkpoint directory: {checkpoint_path}"
                )
            else:
                checkpoint_path = None

            # Call custom callback if provided
            if self.callback:
                try:
                    self.callback(checkpoint_path)
                except Exception as e:
                    logger.error(f"Error in spot interruption callback: {e}")

            # Exit gracefully - instance will be terminated by AWS
            logger.info("Spot Instance handler completed, exiting gracefully")
            sys.exit(0)

        # Register handler for SIGTERM
        signal.signal(signal.SIGTERM, handle_sigterm)
        self.handler_registered = True
        logger.info("Registered Spot Instance interruption handler")
        return True

    @classmethod
    def monitor_spot_termination(cls):
        """Start a background thread to monitor for spot termination notices.

        This provides extra protection beyond the SIGTERM handler.
        """
        import threading

        def check_termination():
            while True:
                try:
                    r = requests.get(
                        "http://169.254.169.254/latest/meta-data/spot/termination-time",
                        timeout=2,
                    )
                    if r.status_code == 200:
                        logger.warning("Spot termination notice detected!")
                        # Send SIGTERM to self to trigger handler
                        os.kill(os.getpid(), signal.SIGTERM)
                        break
                except requests.RequestException:
                    # No termination notice yet
                    pass

                # Sleep before checking again
                time.sleep(5)

        # Start monitoring thread if running on spot
        if EC2Metadata.is_spot_instance():
            thread = threading.Thread(target=check_termination, daemon=True)
            thread.start()
            logger.info("Started spot termination monitoring thread")
            return thread
        return None


class TrainingEnvironment:
    """Setup and manage the training environment."""

    def __init__(
        self,
        job_id: Optional[str] = None,
        registry_table: Optional[str] = None,
        setup_efs: bool = True,
        handle_spot: bool = True,
        enable_coordination: bool = False,
    ):
        """Initialize training environment.

        Args:
            job_id: Current job ID (optional)
            registry_table: Instance registry table name (optional)
            setup_efs: Whether to set up EFS mounts (default: True)
            handle_spot: Whether to set up spot interruption handling (default: True)
            enable_coordination: Whether to enable enhanced coordination (default: False)
        """
        self.job_id = job_id
        self.registry_table = registry_table
        self.instance_registry = None
        self.instance_coordinator = None
        self.cluster_manager = None
        self.spot_handler = None
        self.enable_coordination = enable_coordination
        self._heartbeat_thread = None

        # Initialize components
        if setup_efs:
            self.setup_efs()

        if registry_table:
            self.setup_registry()

        if handle_spot and EC2Metadata.is_spot_instance():
            self.setup_spot_handler()

    def setup_efs(self) -> bool:
        """Set up EFS mounts.

        Returns:
            True if successful, False otherwise
        """
        return EFSManager.setup_training_mounts()

    def setup_registry(self) -> bool:
        """Set up instance registry.

        Returns:
            True if successful, False otherwise
        """
        if not self.registry_table:
            logger.warning("No registry table specified")
            return False

        # Initialize basic registry client
        self.instance_registry = InstanceRegistry(self.registry_table)
        result = self.instance_registry.register_instance()

        # If enhanced coordination is enabled, initialize the coordinator
        if result and self.enable_coordination:
            try:
                # Import here to avoid circular imports
                from receipt_trainer.utils.coordinator import (
                    InstanceCoordinator,
                )
                from receipt_trainer.utils.cluster import ClusterManager

                # Initialize coordinator
                self.instance_coordinator = InstanceCoordinator(
                    self.registry_table
                )
                coord_result = self.instance_coordinator.initialize()

                if coord_result:
                    logger.info(
                        "Instance coordinator initialized successfully"
                    )

                    # Initialize cluster manager
                    self.cluster_manager = ClusterManager(self.registry_table)
                    cluster_result = self.cluster_manager.initialize()

                    if cluster_result:
                        logger.info("Cluster manager initialized successfully")
                    else:
                        logger.warning("Failed to initialize cluster manager")
                else:
                    logger.warning("Failed to initialize instance coordinator")
            except ImportError:
                logger.info("Enhanced coordination modules not available")
            except Exception as e:
                logger.error(f"Error setting up enhanced coordination: {e}")

        return result

    def setup_spot_handler(self, checkpoint_callback=None) -> bool:
        """Set up spot instance interruption handler.

        Args:
            checkpoint_callback: Callback function for checkpointing (optional)

        Returns:
            True if successful, False otherwise
        """
        self.spot_handler = SpotInstanceHandler(
            callback=checkpoint_callback,
            checkpoint_dir=(
                "/mnt/checkpoints"
                if os.path.exists("/mnt/checkpoints")
                else None
            ),
            job_id=self.job_id,
        )
        return self.spot_handler.register_handler()

    def start_heartbeat_thread(
        self, interval_seconds: int = 60
    ) -> Optional[Any]:
        """Start a background thread to send heartbeats to the registry.

        Args:
            interval_seconds: Interval between heartbeats in seconds

        Returns:
            Thread object if started, None otherwise
        """
        if not self.instance_registry and not self.instance_coordinator:
            return None

        import threading

        def heartbeat_loop():
            while True:
                try:
                    # Regular registry heartbeat
                    if self.instance_registry:
                        self.instance_registry.heartbeat()

                    # The instance coordinator handles its own heartbeats
                    # in its health monitoring thread
                except Exception as e:
                    logger.error(f"Error sending heartbeat: {e}")

                time.sleep(interval_seconds)

        self._heartbeat_thread = threading.Thread(
            target=heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()
        logger.info(
            f"Started heartbeat thread with interval {interval_seconds}s"
        )
        return self._heartbeat_thread

    def register_task_handler(
        self,
        task_type: str,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> bool:
        """Register a handler for a specific task type.

        Args:
            task_type: Type of task to handle
            handler: Function to call when a task of this type is assigned

        Returns:
            True if registered successfully, False otherwise
        """
        if self.instance_coordinator:
            self.instance_coordinator.register_task_callback(
                task_type, handler
            )
            logger.info(f"Registered handler for task type '{task_type}'")
            return True
        else:
            logger.warning(
                f"Cannot register task handler '{task_type}' - coordinator not initialized"
            )
            return False

    def register_leader_handler(self, handler: Callable[[], None]) -> bool:
        """Register a handler to be called when this instance becomes the leader.

        Args:
            handler: Function to call when instance becomes leader

        Returns:
            True if registered successfully, False otherwise
        """
        if self.instance_coordinator:
            self.instance_coordinator.register_leader_callback(handler)
            logger.info("Registered leader callback handler")
            return True
        else:
            logger.warning(
                "Cannot register leader handler - coordinator not initialized"
            )
            return False

    def get_cluster_state(self) -> Dict[str, Any]:
        """Get the current state of the cluster.

        Returns:
            Dictionary with cluster state information
        """
        if self.cluster_manager:
            return self.cluster_manager.get_cluster_state()
        elif self.instance_registry:
            # Basic info from registry
            try:
                import boto3

                dynamodb = boto3.resource(
                    "dynamodb", region_name=EC2Metadata.get_instance_region()
                )
                table = dynamodb.Table(self.registry_table)

                response = table.scan()
                instances = [
                    item
                    for item in response.get("Items", [])
                    if "instance_id" in item
                    and not item["instance_id"].startswith("TASK#")
                ]

                return {"instances": instances, "timestamp": int(time.time())}
            except Exception as e:
                logger.error(f"Error getting cluster state: {e}")
                return {"error": str(e)}
        else:
            return {"error": "No registry or cluster manager available"}

    def is_leader(self) -> bool:
        """Check if this instance is the leader.

        Returns:
            True if this instance is the leader, False otherwise
        """
        if self.instance_coordinator:
            return self.instance_coordinator.is_leader_instance
        elif self.instance_registry:
            return self.instance_registry.is_leader()
        else:
            return False

    def cleanup(self) -> None:
        """Clean up resources on shutdown."""
        logger.info("Cleaning up training environment...")

        # Stop heartbeat thread
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            if hasattr(self, "_stop_heartbeat"):
                self._stop_heartbeat.set()

        # Shut down coordinator and cluster manager
        if self.cluster_manager:
            try:
                self.cluster_manager.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down cluster manager: {e}")

        elif self.instance_coordinator:
            try:
                self.instance_coordinator.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down instance coordinator: {e}")

        # Deregister from registry if needed
        elif self.instance_registry:
            try:
                import boto3

                dynamodb = boto3.resource(
                    "dynamodb", region_name=EC2Metadata.get_instance_region()
                )
                table = dynamodb.Table(self.registry_table)

                instance_id = EC2Metadata.get_instance_id()
                if instance_id:
                    table.delete_item(Key={"instance_id": instance_id})
                    logger.info(
                        f"Deregistered instance {instance_id} from registry"
                    )
            except Exception as e:
                logger.error(f"Error deregistering from registry: {e}")

        logger.info("Training environment cleanup complete")

    @classmethod
    def find_latest_checkpoint(cls, job_id: str) -> Optional[str]:
        """Find the latest checkpoint for a job.

        Args:
            job_id: Job ID

        Returns:
            Path to latest checkpoint, or None if not found
        """
        checkpoint_base = f"/mnt/checkpoints/{job_id}"
        if not os.path.exists(checkpoint_base):
            return None

        # Get all checkpoint directories
        try:
            ckpt_dirs = [
                d
                for d in os.listdir(checkpoint_base)
                if os.path.isdir(os.path.join(checkpoint_base, d))
            ]

            if not ckpt_dirs:
                return None

            # Sort by timestamp (assuming directories include timestamps)
            # This will sort alphabetically, so prefix timestamps with YYYYMMDD_HHMMSS
            latest_dir = sorted(ckpt_dirs)[-1]
            return os.path.join(checkpoint_base, latest_dir)
        except (FileNotFoundError, PermissionError) as e:
            logger.error(f"Error finding checkpoints: {e}")
            return None
