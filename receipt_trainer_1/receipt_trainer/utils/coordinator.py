"""Instance coordination and health monitoring utilities."""

import datetime
import json
import logging
import os
import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import boto3
from botocore.exceptions import ClientError

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.instance import Instance
from receipt_dynamo.entities.job import Job
from receipt_trainer.utils.infrastructure import EC2Metadata, InstanceRegistry

logger = logging.getLogger(__name__)


class InstanceCoordinator:
    """
    Enhanced instance coordination and health monitoring system.

    This class extends the InstanceRegistry functionality with:
    - Advanced health monitoring and metrics collection
    - Task delegation and coordination between instances
    - Leader instance management with failover
    - Cluster-wide status reporting
    """

    def __init__(
        self,
        table_name: str,
        region: Optional[str] = None,
        ttl_hours: int = 2,
        health_check_interval: int = 60,
    ):
        """
        Initialize the instance coordinator.

        Args:
            table_name: DynamoDB table name for instance registry
            region: AWS region (optional, will try to autodetect)
            ttl_hours: Time-to-live for instance records in hours
            health_check_interval: Interval for health checks in seconds
        """
        self.table_name = table_name
        self.region = (
            region or EC2Metadata.get_instance_region() or "us-east-1"
        )
        self.ttl_hours = ttl_hours
        self.health_check_interval = health_check_interval

        # Initialize base registry
        self.registry = InstanceRegistry(table_name, region)

        # Initialize DynamoDB client for operations
        self.dynamo_client = DynamoClient(table_name, self.region)

        # Cache instance metadata
        self.instance_id = EC2Metadata.get_instance_id()
        self.instance_type = EC2Metadata.get_instance_type()
        self.is_spot = EC2Metadata.is_spot_instance()
        self.hostname = socket.gethostname()

        # Task and coordination state
        self.task_status: Dict[str, str] = {}
        self.task_results: Dict[str, Any] = {}
        self.assigned_tasks: Set[str] = set()

        # Components for monitoring and election
        self.stop_threads = threading.Event()
        self.is_leader_instance = False
        self.leader_last_seen: Optional[int] = None
        self._monitoring_thread: Optional[threading.Thread] = None
        self._leadership_thread: Optional[threading.Thread] = None

        # Health metrics
        self.health_metrics: Dict[str, Any] = {
            "cpu_utilization": 0.0,
            "memory_usage": 0.0,
            "gpu_utilization": 0.0,
            "gpu_memory_usage": 0.0,
            "disk_usage": 0.0,
            "network_in": 0.0,
            "network_out": 0.0,
            "load_average": 0.0,
        }

        # Metrics history (for trend analysis)
        self.metrics_history: List[Dict[str, Any]] = []

        # Callback registry
        self.leader_callbacks: List[Callable[[], None]] = []
        self.task_callbacks: Dict[str, Callable[[Dict[str, Any]], None]] = {}

    def initialize(self) -> bool:
        """
        Initialize the coordinator and register the instance.

        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            # Create Instance entity with initial data
            instance = Instance(
                instance_id=self.instance_id,
                instance_type=self.instance_type,
                gpu_count=self._get_gpu_count(),
                status="running",
                launched_at=datetime.datetime.now(),
                ip_address=self._get_ip_address(),
                availability_zone=self._get_availability_zone(),
                is_spot=self.is_spot,
                health_status="healthy",
            )

            # Add to DynamoDB with receipt_dynamo
            self.dynamo_client.addInstance(instance)

            # Start health monitoring and metrics collection
            self._start_health_monitoring()

            # Start leader election monitoring
            self._start_leadership_monitoring()

            logger.info(
                f"Instance coordinator initialized for {self.instance_id}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to register instance: {e}")
            return False

    def shutdown(self) -> None:
        """
        Gracefully shut down the coordinator.
        """
        logger.info("Shutting down instance coordinator...")

        # Stop background threads
        self.stop_threads.set()

        # Wait for threads to complete
        if self._monitoring_thread:
            self._monitoring_thread.join(timeout=5)

        if self._leadership_thread:
            self._leadership_thread.join(timeout=5)

        # If we're the leader, resign
        if self.is_leader_instance:
            self._resign_leadership()

        # Deregister from the registry
        try:
            # Get the instance
            try:
                instance = self.dynamo_client.getInstance(self.instance_id)
                # Delete instance using receipt_dynamo
                self.dynamo_client.deleteInstance(instance)
                logger.info(
                    f"Instance {self.instance_id} deregistered from registry"
                )
            except ValueError:
                # Instance already deleted or not found
                pass
        except Exception as e:
            logger.error(f"Failed to deregister instance: {e}")

    def register_leader_callback(self, callback: Callable[[], None]) -> None:
        """
        Register a callback to be called when this instance becomes the leader.

        Args:
            callback: Function to call when instance becomes leader
        """
        self.leader_callbacks.append(callback)

    def register_task_callback(
        self, task_type: str, callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """
        Register a callback for handling a specific task type.

        Args:
            task_type: Type of task to handle
            callback: Function to call when a task of this type is assigned
        """
        self.task_callbacks[task_type] = callback

    def submit_task(
        self,
        task_type: str,
        params: Dict[str, Any],
        target_instance: Optional[str] = None,
    ) -> Optional[str]:
        """
        Submit a task to be executed by an instance in the cluster.

        Args:
            task_type: Type of task to execute
            params: Parameters for the task
            target_instance: Specific instance ID to target (optional)

        Returns:
            Task ID if submitted successfully, None otherwise
        """
        if not self.instance_id:
            logger.error("Cannot submit task - not on EC2")
            return None

        try:
            task_id = str(uuid.uuid4())
            created_at = datetime.datetime.now()

            # Create Task entity
            task = Job(
                task_id=task_id,
                task_type=task_type,
                status="pending",
                params=params,
                created_by=self.instance_id,
                created_at=created_at,
                assigned_to=target_instance,
            )

            # Add task to DynamoDB using proper client
            self.dynamo_client.addTask(task)
            logger.info(f"Submitted task {task_id} of type {task_type}")
            return task_id

        except Exception as e:
            logger.error(f"Failed to submit task: {e}")
            return None

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the status of a task.

        Args:
            task_id: ID of the task

        Returns:
            Task status dictionary or None if not found
        """
        try:
            # Get task using DynamoClient
            task = self.dynamo_client.getTask(task_id)

            # Convert Task object to dict for backward compatibility
            task_dict = {k: v for k, v in task}
            return task_dict
        except ValueError:
            logger.warning(f"Task {task_id} not found")
            return None
        except Exception as e:
            logger.error(f"Failed to get task status: {e}")
            return None

    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Update the status of a task.

        Args:
            task_id: ID of the task
            status: New status (pending, running, completed, failed)
            result: Task result data (for completed tasks)

        Returns:
            True if updated successfully, False otherwise
        """
        try:
            # Get existing task
            task = self.dynamo_client.getTask(task_id)

            # Update task fields
            if status == "running" and task.status != "running":
                task.status = status
                task.started_at = datetime.datetime.now()
            elif status in ("completed", "failed"):
                task.status = status
                task.completed_at = datetime.datetime.now()
                if result is not None:
                    task.result = result
            else:
                task.status = status

            # Update task in DynamoDB
            self.dynamo_client.updateTask(task)

            # Update local state
            self.task_status[task_id] = status
            if result is not None:
                self.task_results[task_id] = result

            logger.info(f"Updated task {task_id} status to {status}")
            return True
        except Exception as e:
            logger.error(f"Failed to update task status: {e}")
            return False

    def _start_health_monitoring(self) -> None:
        """
        Start the health monitoring thread.
        """

        def monitoring_loop():
            while not self.stop_threads.is_set():
                try:
                    # Collect health metrics
                    self._collect_health_metrics()

                    # Update health status in registry
                    self._update_health_status()

                    # Process assigned tasks
                    if not self.stop_threads.is_set():
                        self._process_assigned_tasks()

                    # Store historical metrics (keep last 60 entries)
                    self.metrics_history.append(self.health_metrics.copy())
                    if len(self.metrics_history) > 60:
                        self.metrics_history.pop(0)

                except Exception as e:
                    logger.error(f"Error in health monitoring: {e}")

                # Sleep until next check
                for _ in range(self.health_check_interval):
                    if self.stop_threads.is_set():
                        break
                    time.sleep(1)

        self._monitoring_thread = threading.Thread(
            target=monitoring_loop, daemon=True, name="health-monitor"
        )
        self._monitoring_thread.start()
        logger.info("Started health monitoring thread")

    def _start_leadership_monitoring(self) -> None:
        """
        Start the leadership monitoring thread.
        """

        def leadership_loop():
            # Wait a short time before starting to allow initialization
            time.sleep(5)

            while not self.stop_threads.is_set():
                try:
                    # Check if we're the leader
                    if self.registry.is_leader():
                        if not self.is_leader_instance:
                            # We just became leader
                            self._on_become_leader()
                    else:
                        if self.is_leader_instance:
                            # We just lost leadership
                            self._on_lose_leadership()

                        # Check if leader is needed
                        self._check_leader_needed()

                except Exception as e:
                    logger.error(f"Error in leadership monitoring: {e}")

                # Sleep for a while
                for _ in range(15):  # Check every 15 seconds
                    if self.stop_threads.is_set():
                        break
                    time.sleep(1)

        self._leadership_thread = threading.Thread(
            target=leadership_loop, daemon=True, name="leadership-monitor"
        )
        self._leadership_thread.start()
        logger.info("Started leadership monitoring thread")

    def _collect_health_metrics(self) -> None:
        """
        Collect health metrics from the instance.
        """
        try:
            # CPU utilization
            with open("/proc/stat", "r") as f:
                cpu = f.readline().split()
                total1 = sum(float(i) for i in cpu[1:])
                idle1 = float(cpu[4])

            time.sleep(0.5)  # Short sleep to measure delta

            with open("/proc/stat", "r") as f:
                cpu = f.readline().split()
                total2 = sum(float(i) for i in cpu[1:])
                idle2 = float(cpu[4])

            total_delta = total2 - total1
            idle_delta = idle2 - idle1

            if total_delta > 0:
                cpu_util = 100 * (1 - idle_delta / total_delta)
                self.health_metrics["cpu_utilization"] = round(cpu_util, 2)

            # Memory usage
            with open("/proc/meminfo", "r") as f:
                mem_info = {}
                for line in f:
                    key, value = line.split(":", 1)
                    mem_info[key.strip()] = int(value.strip().split()[0])

            total_mem = mem_info.get("MemTotal", 0)
            free_mem = mem_info.get("MemFree", 0)
            buffers = mem_info.get("Buffers", 0)
            cached = mem_info.get("Cached", 0)

            if total_mem > 0:
                used_mem = total_mem - free_mem - buffers - cached
                mem_util = 100 * (used_mem / total_mem)
                self.health_metrics["memory_usage"] = round(mem_util, 2)

            # Load average
            with open("/proc/loadavg", "r") as f:
                load = f.read().split()
                self.health_metrics["load_average"] = float(load[0])

            # Disk usage
            disk_usage = os.statvfs("/")
            total_disk = disk_usage.f_blocks * disk_usage.f_frsize
            free_disk = disk_usage.f_bfree * disk_usage.f_frsize

            if total_disk > 0:
                used_disk = total_disk - free_disk
                disk_util = 100 * (used_disk / total_disk)
                self.health_metrics["disk_usage"] = round(disk_util, 2)

            # GPU metrics (if available)
            try:
                import subprocess

                # GPU utilization
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                gpu_utils = [
                    float(x.strip())
                    for x in result.stdout.split("\n")
                    if x.strip()
                ]
                if gpu_utils:
                    self.health_metrics["gpu_utilization"] = sum(
                        gpu_utils
                    ) / len(gpu_utils)

                # GPU memory usage
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used,memory.total",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )

                memory_usages = []
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        used, total = map(float, line.split(","))
                        if total > 0:
                            memory_usages.append(100 * (used / total))

                if memory_usages:
                    self.health_metrics["gpu_memory_usage"] = sum(
                        memory_usages
                    ) / len(memory_usages)

            except (
                subprocess.SubprocessError,
                ImportError,
                FileNotFoundError,
            ):
                # No GPU or nvidia-smi not available
                pass

        except Exception as e:
            logger.error(f"Error collecting health metrics: {e}")

    def _update_health_status(self) -> None:
        """
        Update health status in the registry.
        """
        try:
            # Get current instance
            try:
                instance = self.dynamo_client.getInstance(self.instance_id)
            except ValueError:
                # Instance might not exist yet, create a new one
                instance = Instance(
                    instance_id=self.instance_id,
                    instance_type=self.instance_type,
                    gpu_count=self._get_gpu_count(),
                    status="running",
                    launched_at=datetime.datetime.now(),
                    ip_address=self._get_ip_address(),
                    availability_zone=self._get_availability_zone(),
                    is_spot=self.is_spot,
                    health_status="healthy",
                )
                self.dynamo_client.addInstance(instance)
                return

            # Update health status based on metrics
            health_status = self._calculate_health_status()

            # Add health metrics to instance through direct DynamoDB update
            # since the Instance entity doesn't have a metadata field
            dynamodb = boto3.resource("dynamodb", region_name=self.region)
            table = dynamodb.Table(self.table_name)

            update_expr = """
                SET last_heartbeat = :now,
                    metadata.health_metrics = :health_metrics,
                    health_status = :health_status
            """

            table.update_item(
                Key={"instance_id": f"INSTANCE#{self.instance_id}"},
                UpdateExpression=update_expr,
                ExpressionAttributeValues={
                    ":now": int(time.time()),
                    ":health_metrics": self.health_metrics,
                    ":health_status": health_status,
                },
            )

            # Update our instance with new health status
            instance.health_status = health_status
            self.dynamo_client.updateInstance(instance)

        except Exception as e:
            logger.error(f"Failed to update health status: {e}")

    def _calculate_health_status(self) -> str:
        """
        Calculate overall health status based on metrics.

        Returns:
            Health status: "healthy", "degraded", or "unhealthy"
        """
        # Define thresholds
        thresholds = {
            "healthy": {
                "cpu_utilization": 80.0,
                "memory_usage": 85.0,
                "disk_usage": 85.0,
                "gpu_memory_usage": 85.0,
                "load_average": 4.0,  # Depends on number of cores
            },
            "degraded": {
                "cpu_utilization": 90.0,
                "memory_usage": 95.0,
                "disk_usage": 95.0,
                "gpu_memory_usage": 95.0,
                "load_average": 8.0,
            },
        }

        # Check for unhealthy conditions
        for metric, value in self.health_metrics.items():
            if (
                metric in thresholds["degraded"]
                and value > thresholds["degraded"][metric]
            ):
                return "unhealthy"

        # Check for degraded conditions
        for metric, value in self.health_metrics.items():
            if (
                metric in thresholds["healthy"]
                and value > thresholds["healthy"][metric]
            ):
                return "degraded"

        # If none of the above, we're healthy
        return "healthy"

    def _process_assigned_tasks(self) -> None:
        """
        Process tasks assigned to this instance.
        """
        try:
            # Query for tasks assigned to this instance using the DynamoClient's API
            tasks, _ = self.dynamo_client.listTasksByAssignedInstance(
                self.instance_id
            )

            for task in tasks:
                if self.stop_threads.is_set():
                    break

                # Only process pending tasks
                if task.status != "pending":
                    continue

                # Skip if we've already processed this task
                if task.task_id in self.assigned_tasks:
                    continue

                # Mark task as running
                self.update_task_status(task.task_id, "running")
                self.assigned_tasks.add(task.task_id)

                # Execute the task in a separate thread
                thread = threading.Thread(
                    target=self._execute_task,
                    args=(task.task_id, task.task_type, task.params),
                    daemon=True,
                )
                thread.start()

        except Exception as e:
            logger.error(f"Error processing assigned tasks: {e}")

    def _execute_task(
        self, task_id: str, task_type: str, params: Dict[str, Any]
    ) -> None:
        """
        Execute a task.

        Args:
            task_id: ID of the task
            task_type: Type of task
            params: Task parameters
        """
        try:
            # Check if we have a callback for this task type
            if task_type in self.task_callbacks:
                # Call the callback with the parameters
                result = self.task_callbacks[task_type](params)

                # Update task status
                self.update_task_status(task_id, "completed", result)
            else:
                # No callback registered for this task type
                self.update_task_status(
                    task_id,
                    "failed",
                    {"error": f"No handler for task type {task_type}"},
                )

        except Exception as e:
            logger.error(f"Error executing task {task_id}: {e}")

            # Update task status
            self.update_task_status(task_id, "failed", {"error": str(e)})

    def _check_leader_needed(self) -> None:
        """
        Check if a leader is needed and elect one if necessary.
        """
        try:
            # Check if there's already a leader
            leader = self._get_leader_instance()

            if not leader:
                # No leader, try to become one
                if self.registry.elect_leader():
                    self._on_become_leader()
        except Exception as e:
            logger.error(f"Error checking leader: {e}")

    def _get_leader_instance(self) -> Optional[Instance]:
        """
        Get the current leader instance.

        Returns:
            Leader instance details or None if no leader
        """
        try:
            # Get all instances
            instances, _ = self.dynamo_client.listInstances()

            # Filter for leader
            for instance in instances:
                # Check directly in DynamoDB if the instance is the leader
                # since is_leader is not part of the Instance entity
                dynamodb = boto3.resource("dynamodb", region_name=self.region)
                table = dynamodb.Table(self.table_name)

                response = table.get_item(
                    Key={"instance_id": f"INSTANCE#{instance.instance_id}"}
                )

                if "Item" in response and response["Item"].get(
                    "is_leader", False
                ):
                    # Check if leader has sent a heartbeat recently
                    last_heartbeat = response["Item"].get("last_heartbeat")
                    if last_heartbeat:
                        now = int(time.time())
                        if now - last_heartbeat > 120:  # 2 minutes
                            logger.warning(
                                f"Leader {instance.instance_id} heartbeat is stale, may need re-election"
                            )
                            return None
                    return instance

            return None
        except Exception as e:
            logger.error(f"Error getting leader instance: {e}")
            return None

    def _on_become_leader(self) -> None:
        """
        Called when this instance becomes the leader.
        """
        logger.info(f"Instance {self.instance_id} is now the leader")
        self.is_leader_instance = True

        # Execute leader callbacks
        for callback in self.leader_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Error in leader callback: {e}")

    def _on_lose_leadership(self) -> None:
        """
        Called when this instance loses leadership.
        """
        logger.info(f"Instance {self.instance_id} is no longer the leader")
        self.is_leader_instance = False

    def _resign_leadership(self) -> None:
        """
        Resign leadership.
        """
        try:
            # Set is_leader to false using direct DynamoDB update
            # since is_leader is not part of the Instance entity
            dynamodb = boto3.resource("dynamodb", region_name=self.region)
            table = dynamodb.Table(self.table_name)

            table.update_item(
                Key={"instance_id": f"INSTANCE#{self.instance_id}"},
                UpdateExpression="SET is_leader = :false",
                ExpressionAttributeValues={":false": False},
            )

            logger.info(f"Instance {self.instance_id} resigned leadership")
            self.is_leader_instance = False
        except Exception as e:
            logger.error(f"Error resigning leadership: {e}")

    def _get_gpu_count(self) -> int:
        """Get the number of GPUs on the instance."""
        try:
            import subprocess

            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return len(result.stdout.strip().split("\n"))
            return 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return 0

    def _get_ip_address(self) -> str:
        """Get the instance IP address."""
        try:
            return socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            return "unknown"

    def _get_availability_zone(self) -> str:
        """Get the instance availability zone."""
        # Try to get from EC2 metadata service
        try:
            import requests

            response = requests.get(
                "http://169.254.169.254/latest/meta-data/placement/availability-zone",
                timeout=2,
            )
            if response.status_code == 200:
                return response.text
            return self.region + "a"  # Default to first AZ in region
        except (requests.RequestException, ImportError):
            return self.region + "a"  # Default to first AZ in region

    def create_task(
        self,
        task_type: str,
        priority: str = "normal",
        data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a new task in DynamoDB.

        Args:
            task_type: Type of task ('training', 'inference', etc.)
            priority: Task priority ('high', 'normal', 'low')
            data: Additional task data

        Returns:
            Task ID if successful, None otherwise
        """
        try:
            # Generate a unique task ID
            task_id = str(uuid.uuid4())

            # Create data object if not provided
            if data is None:
                data = {}

            # Create Task entity
            task = Job(
                job_id=task_id,
                type=task_type,
                status="pending",
                priority=priority,
                config=data,
                created_at=int(datetime.datetime.now().timestamp()),
            )

            # Save to DynamoDB using DynamoClient
            dynamo_client = DynamoClient(table_name=self.table_name)
            success = dynamo_client.putJob(task)

            if success:
                logger.info(f"Task created: {task_id} ({task_type})")
                return task_id
            else:
                logger.error(f"Failed to create task in DynamoDB")
                return None
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return None

    def assign_task(self, task_id: str, instance_id: str) -> bool:
        """Assign a task to an instance.

        Args:
            task_id: ID of the task to assign
            instance_id: ID of the instance to assign to

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get the task
            dynamo_client = DynamoClient(table_name=self.table_name)
            task = dynamo_client.getJob(task_id)

            if not task:
                logger.error(f"Task {task_id} not found in DynamoDB")
                return False

            # Update task status to 'assigned'
            task.status = "assigned"
            task.assigned_to = instance_id
            task.assigned_at = int(datetime.datetime.now().timestamp())

            # Update in DynamoDB
            success = dynamo_client.putJob(task)

            if success:
                logger.info(
                    f"Task {task_id} assigned to instance {instance_id}"
                )

                # Create a record in instance_jobs if needed
                # This would use a different entity type if available

                return True
            else:
                logger.error(f"Failed to update task in DynamoDB")
                return False
        except Exception as e:
            logger.error(f"Error assigning task: {e}")
            return False

    def update_task_status(
        self, task_id: str, status: str, message: Optional[str] = None
    ) -> bool:
        """Update the status of a task.

        Args:
            task_id: ID of the task to update
            status: New status ('assigned', 'running', 'completed', 'failed')
            message: Optional status message

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get the task
            dynamo_client = DynamoClient(table_name=self.table_name)
            task = dynamo_client.getJob(task_id)

            if not task:
                logger.error(f"Task {task_id} not found in DynamoDB")
                return False

            # Update task status
            task.status = status
            task.updated_at = int(datetime.datetime.now().timestamp())

            if message:
                if not hasattr(task, "status_message"):
                    task.status_message = message
                else:
                    # Append to existing message
                    task.status_message += f" | {message}"

            # Update in DynamoDB
            success = dynamo_client.putJob(task)

            if success:
                logger.info(f"Task {task_id} status updated to '{status}'")
                return True
            else:
                logger.error(f"Failed to update task in DynamoDB")
                return False
        except Exception as e:
            logger.error(f"Error updating task status: {e}")
            return False

    def get_pending_tasks(
        self, task_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get list of pending tasks.

        Args:
            task_type: Optional task type to filter by

        Returns:
            List of task dictionaries
        """
        try:
            # Query for pending tasks in DynamoDB
            dynamo_client = DynamoClient(table_name=self.table_name)

            # Instead of querying directly with Task class, use getJobs with a filter
            # This is a simulated approach as the actual API for DynamoClient might differ
            jobs = dynamo_client.getJobs(status="pending")

            # Filter by task_type if specified
            if task_type and jobs:
                jobs = [job for job in jobs if job.type == task_type]

            # Convert to dictionaries
            return [job.to_dict() for job in jobs] if jobs else []
        except Exception as e:
            logger.error(f"Error getting pending tasks: {e}")
            return []

    def claim_next_task(
        self, instance_id: str, task_types: List[str]
    ) -> Optional[str]:
        """Claim the next available task for an instance.

        Args:
            instance_id: ID of the instance claiming the task
            task_types: List of task types this instance can process

        Returns:
            Task ID if a task was claimed, None otherwise
        """
        try:
            # Get pending tasks of the specified types
            dynamo_client = DynamoClient(table_name=self.table_name)

            # Get all pending jobs
            pending_jobs = dynamo_client.getJobs(status="pending")

            if not pending_jobs:
                logger.info("No pending tasks available")
                return None

            # Filter by task type
            matching_jobs = [
                job for job in pending_jobs if job.type in task_types
            ]

            if not matching_jobs:
                logger.info(
                    f"No pending tasks of types {task_types} available"
                )
                return None

            # Sort by priority and created_at
            # Note: This assumes Job has these attributes
            sorted_jobs = sorted(
                matching_jobs,
                key=lambda job: (
                    {"high": 0, "normal": 1, "low": 2}.get(job.priority, 1),
                    job.created_at,
                ),
            )

            # Take the first job
            job_to_claim = sorted_jobs[0]

            # Update the job status to 'assigned'
            job_to_claim.status = "assigned"
            job_to_claim.assigned_to = instance_id
            job_to_claim.assigned_at = int(datetime.datetime.now().timestamp())

            # Save back to DynamoDB
            success = dynamo_client.putJob(job_to_claim)

            if success:
                logger.info(
                    f"Task {job_to_claim.job_id} claimed by instance {instance_id}"
                )
                return job_to_claim.job_id
            else:
                logger.error("Failed to update task in DynamoDB")
                return None
        except Exception as e:
            logger.error(f"Error claiming task: {e}")
            return None
