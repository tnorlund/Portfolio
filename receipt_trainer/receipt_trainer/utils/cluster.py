"""Cluster management for coordinated machine learning tasks."""

import os
import time
import json
import logging
import threading
from typing import Dict, List, Optional, Any, Set, Callable

from receipt_trainer.utils.coordinator import InstanceCoordinator
from receipt_trainer.utils.infrastructure import EC2Metadata, InstanceRegistry

logger = logging.getLogger(__name__)


class ClusterManager:
    """
    Cluster management for coordinated machine learning tasks.

    This class provides high-level cluster management capabilities for ML tasks:
    - Task distribution based on instance capabilities
    - Coordinated operations like dataset preprocessing
    - Load balancing across instances
    - Centralized monitoring of training progress
    """

    def __init__(
        self,
        registry_table: str,
        region: Optional[str] = None,
    ):
        """
        Initialize the cluster manager.

        Args:
            registry_table: DynamoDB table name for instance registry
            region: AWS region (optional, will try to autodetect)
        """
        self.registry_table = registry_table
        self.region = (
            region or EC2Metadata.get_instance_region() or "us-east-1"
        )

        # Initialize coordinator
        self.coordinator = InstanceCoordinator(registry_table, region)

        # Cluster state
        self.cluster_instances: Dict[str, Dict[str, Any]] = {}
        self.cluster_capabilities: Dict[str, Any] = {}
        self.active_training_jobs: Dict[str, Dict[str, Any]] = {}

        # Leader state
        self.is_leader = False
        self.last_cluster_scan = 0
        self.task_distribution_lock = threading.Lock()

        # Register leader callback
        self.coordinator.register_leader_callback(self._on_become_leader)

        # Register task callbacks
        self.coordinator.register_task_callback(
            "preprocess_dataset", self._handle_preprocess_task
        )
        self.coordinator.register_task_callback(
            "training_job", self._handle_training_task
        )
        self.coordinator.register_task_callback(
            "evaluation", self._handle_evaluation_task
        )
        self.coordinator.register_task_callback(
            "cluster_state", self._handle_cluster_state_task
        )

    def initialize(self) -> bool:
        """
        Initialize the cluster manager.

        Returns:
            True if initialization was successful, False otherwise
        """
        return self.coordinator.initialize()

    def shutdown(self) -> None:
        """
        Shut down the cluster manager.
        """
        self.coordinator.shutdown()

    def get_cluster_state(self) -> Dict[str, Any]:
        """
        Get the current state of the cluster.

        Returns:
            Dictionary with cluster state information
        """
        # If we're the leader, we have this info cached
        if self.is_leader and self.cluster_instances:
            return {
                "instances": self.cluster_instances,
                "leader_id": self.coordinator.instance_id,
                "active_jobs": self.active_training_jobs,
                "capabilities": self.cluster_capabilities,
                "timestamp": int(time.time()),
            }

        # Otherwise, we need to scan the registry
        instances = self._scan_instances()
        leader = self._find_leader(instances)

        return {
            "instances": instances,
            "leader_id": leader.get("instance_id") if leader else None,
            "active_jobs": {},  # We don't have this info if we're not the leader
            "timestamp": int(time.time()),
        }

    def submit_preprocessing_job(
        self, dataset_name: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """
        Submit a dataset preprocessing job to the cluster.

        Args:
            dataset_name: Name of the dataset to preprocess
            params: Preprocessing parameters

        Returns:
            Task ID if submitted successfully, None otherwise
        """
        return self.coordinator.submit_task(
            "preprocess_dataset", {"dataset_name": dataset_name, **params}
        )

    def submit_training_job(
        self,
        job_id: str,
        model_name: str,
        params: Dict[str, Any],
        requirements: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Submit a training job to the cluster.

        Args:
            job_id: ID of the job
            model_name: Name of the model to train
            params: Training parameters
            requirements: Job requirements (GPU, memory, etc.)

        Returns:
            Task ID if submitted successfully, None otherwise
        """
        if self.is_leader:
            # We're the leader, so we can distribute the job directly
            target_instance = self._find_best_instance_for_job(requirements)

            if target_instance:
                return self.coordinator.submit_task(
                    "training_job",
                    {"job_id": job_id, "model_name": model_name, **params},
                    target_instance,
                )
            else:
                logger.warning(f"No suitable instance found for job {job_id}")
                return None
        else:
            # We're not the leader, submit without target (leader will distribute)
            return self.coordinator.submit_task(
                "training_job",
                {
                    "job_id": job_id,
                    "model_name": model_name,
                    "requirements": requirements or {},
                    **params,
                },
            )

    def evaluate_model(
        self, model_path: str, dataset_name: str, params: Dict[str, Any]
    ) -> Optional[str]:
        """
        Submit a model evaluation job to the cluster.

        Args:
            model_path: Path to the model to evaluate
            dataset_name: Name of the dataset to use for evaluation
            params: Evaluation parameters

        Returns:
            Task ID if submitted successfully, None otherwise
        """
        return self.coordinator.submit_task(
            "evaluation",
            {"model_path": model_path, "dataset_name": dataset_name, **params},
        )

    def _scan_instances(self) -> Dict[str, Dict[str, Any]]:
        """
        Scan for all instances in the registry.

        Returns:
            Dictionary of instance information keyed by instance ID
        """
        try:
            instances = {}
            registry = InstanceRegistry(self.registry_table, self.region)

            # Query DynamoDB for instances
            import boto3
            from boto3.dynamodb.conditions import Key

            dynamodb = boto3.resource("dynamodb", region_name=self.region)
            table = dynamodb.Table(self.registry_table)

            # Filter for instances (exclude task records)
            response = table.scan(
                FilterExpression="attribute_not_exists(task_id)"
            )

            for item in response.get("Items", []):
                instance_id = item.get("instance_id")
                if instance_id and not instance_id.startswith("TASK#"):
                    instances[instance_id] = item

            return instances
        except Exception as e:
            logger.error(f"Error scanning instances: {e}")
            return {}

    def _find_leader(
        self, instances: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Find the leader instance in the registry.

        Args:
            instances: Dictionary of instances keyed by instance ID

        Returns:
            Leader instance details or None if no leader
        """
        for instance_id, instance in instances.items():
            if instance.get("is_leader", False):
                return instance

        return None

    def _on_become_leader(self) -> None:
        """
        Callback when this instance becomes the leader.
        """
        logger.info(
            f"Cluster manager: instance {self.coordinator.instance_id} is now the leader"
        )
        self.is_leader = True

        # Start leader-specific tasks
        self._scan_cluster()
        self._start_leader_monitoring()

        # Notify other instances of leader change
        self._broadcast_leader_change()

    def _scan_cluster(self) -> None:
        """
        Scan the cluster for instances and capabilities.
        """
        try:
            logger.info("Scanning cluster instances and capabilities")

            # Scan instances
            self.cluster_instances = self._scan_instances()
            self.last_cluster_scan = int(time.time())

            # Calculate cluster capabilities
            self.cluster_capabilities = self._calculate_cluster_capabilities()

            logger.info(
                f"Cluster has {len(self.cluster_instances)} instances with these capabilities: {self.cluster_capabilities}"
            )
        except Exception as e:
            logger.error(f"Error scanning cluster: {e}")

    def _calculate_cluster_capabilities(self) -> Dict[str, Any]:
        """
        Calculate the overall capabilities of the cluster.

        Returns:
            Dictionary of cluster capabilities
        """
        capabilities = {
            "total_instances": len(self.cluster_instances),
            "total_gpus": 0,
            "gpu_types": {},
            "instance_types": {},
            "running_jobs": 0,
            "available_memory_gb": 0,
        }

        # Process instance data
        for instance_id, instance in self.cluster_instances.items():
            gpu_count = instance.get("gpu_count", 0)
            gpu_info = instance.get("gpu_info", "none")
            instance_type = instance.get("instance_type", "unknown")

            # Count GPUs
            capabilities["total_gpus"] += gpu_count

            # Track GPU types
            if gpu_info != "none" and gpu_count > 0:
                if gpu_info in capabilities["gpu_types"]:
                    capabilities["gpu_types"][gpu_info] += gpu_count
                else:
                    capabilities["gpu_types"][gpu_info] = gpu_count

            # Track instance types
            if instance_type in capabilities["instance_types"]:
                capabilities["instance_types"][instance_type] += 1
            else:
                capabilities["instance_types"][instance_type] = 1

        return capabilities

    def _start_leader_monitoring(self) -> None:
        """
        Start leader-specific monitoring and task distribution.
        """
        if (
            hasattr(self, "_leader_thread")
            and self._leader_thread
            and self._leader_thread.is_alive()
        ):
            # Thread already running
            return

        def leader_loop():
            while (
                self.is_leader and not self.coordinator.stop_threads.is_set()
            ):
                try:
                    # Scan cluster periodically
                    now = int(time.time())
                    if now - self.last_cluster_scan > 60:  # Scan every minute
                        self._scan_cluster()

                    # Distribute pending tasks
                    self._distribute_pending_tasks()

                    # Check for dead jobs
                    self._cleanup_dead_jobs()

                except Exception as e:
                    logger.error(f"Error in leader monitoring: {e}")

                # Sleep for a while
                for _ in range(10):  # Check every 10 seconds
                    if (
                        not self.is_leader
                        or self.coordinator.stop_threads.is_set()
                    ):
                        break
                    time.sleep(1)

        self._leader_thread = threading.Thread(
            target=leader_loop, daemon=True, name="leader-monitor"
        )
        self._leader_thread.start()
        logger.info("Started leader monitoring thread")

    def _broadcast_leader_change(self) -> None:
        """
        Broadcast that this instance is now the leader.
        """
        # Submit a cluster state task that all instances will see
        self.coordinator.submit_task(
            "cluster_state",
            {
                "action": "leader_changed",
                "leader_id": self.coordinator.instance_id,
                "timestamp": int(time.time()),
            },
        )

    def _distribute_pending_tasks(self) -> None:
        """
        Distribute pending tasks to appropriate instances.
        """
        if not self.is_leader:
            return

        with self.task_distribution_lock:
            try:
                # Find pending tasks without an assigned instance
                import boto3

                dynamodb = boto3.resource("dynamodb", region_name=self.region)
                table = dynamodb.Table(self.registry_table)

                response = table.scan(
                    FilterExpression="attribute_exists(task_id) AND #status = :status AND attribute_not_exists(assigned_to)",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={":status": "pending"},
                )

                for task in response.get("Items", []):
                    task_id = task.get("task_id")
                    task_type = task.get("task_type")

                    if not task_id or not task_type:
                        continue

                    # Find the best instance for this task
                    if task_type == "training_job":
                        requirements = task.get("params", {}).get(
                            "requirements", {}
                        )
                        target_instance = self._find_best_instance_for_job(
                            requirements
                        )
                    elif task_type == "preprocess_dataset":
                        # Preprocessing can use any instance
                        target_instance = self._find_least_busy_instance()
                    elif task_type == "evaluation":
                        # Evaluations should prefer GPU instances but can use any
                        target_instance = (
                            self._find_best_instance_for_evaluation()
                        )
                    else:
                        # For unknown task types, use least busy
                        target_instance = self._find_least_busy_instance()

                    if target_instance:
                        # Assign task to instance
                        table.update_item(
                            Key={"instance_id": f"TASK#{task_id}"},
                            UpdateExpression="SET assigned_to = :instance, last_updated = :now",
                            ExpressionAttributeValues={
                                ":instance": target_instance,
                                ":now": int(time.time()),
                            },
                        )

                        logger.info(
                            f"Assigned task {task_id} ({task_type}) to instance {target_instance}"
                        )
                    else:
                        logger.warning(
                            f"No suitable instance found for task {task_id} ({task_type})"
                        )

            except Exception as e:
                logger.error(f"Error distributing tasks: {e}")

    def _find_best_instance_for_job(
        self, requirements: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Find the best instance for a training job based on requirements.

        Args:
            requirements: Job requirements (GPU, memory, etc.)

        Returns:
            Instance ID of the best instance, or None if no suitable instance found
        """
        if not requirements:
            requirements = {}

        # Extract requirements
        gpu_required = requirements.get("gpu_required", True)
        min_gpu_count = requirements.get("min_gpu_count", 1)
        min_memory_gb = requirements.get("min_memory_gb", 0)

        candidates = []

        for instance_id, instance in self.cluster_instances.items():
            # Skip instances that are not healthy
            health_status = instance.get("health_status", "unknown")
            if health_status == "unhealthy":
                continue

            # Check GPU requirements
            gpu_count = instance.get("gpu_count", 0)
            if gpu_required and gpu_count < min_gpu_count:
                continue

            # Check memory requirements (if available)
            # This would require instances to report their memory

            # Check instance status
            instance_tasks = self._count_instance_tasks(instance_id)

            # Add to candidates
            candidates.append(
                {
                    "instance_id": instance_id,
                    "gpu_count": gpu_count,
                    "active_tasks": instance_tasks,
                    "health_status": health_status,
                }
            )

        if not candidates:
            return None

        # Sort candidates by:
        # 1. Number of active tasks (ascending)
        # 2. GPU count (descending)
        # 3. Health status (prefer healthy over degraded)
        candidates.sort(
            key=lambda x: (
                x["active_tasks"],
                -x["gpu_count"],
                0 if x["health_status"] == "healthy" else 1,
            )
        )

        return candidates[0]["instance_id"]

    def _find_least_busy_instance(self) -> Optional[str]:
        """
        Find the least busy instance.

        Returns:
            Instance ID of the least busy instance, or None if no instances found
        """
        candidates = []

        for instance_id, instance in self.cluster_instances.items():
            # Skip instances that are not healthy
            health_status = instance.get("health_status", "unknown")
            if health_status == "unhealthy":
                continue

            # Check instance status
            instance_tasks = self._count_instance_tasks(instance_id)

            # Add to candidates
            candidates.append(
                {
                    "instance_id": instance_id,
                    "active_tasks": instance_tasks,
                    "health_status": health_status,
                }
            )

        if not candidates:
            return None

        # Sort candidates by number of active tasks
        candidates.sort(key=lambda x: x["active_tasks"])

        return candidates[0]["instance_id"]

    def _find_best_instance_for_evaluation(self) -> Optional[str]:
        """
        Find the best instance for model evaluation.

        Returns:
            Instance ID of the best instance for evaluation, or None if no instances found
        """
        # For evaluation, prefer instances with GPUs but don't require them
        gpu_instances = []
        non_gpu_instances = []

        for instance_id, instance in self.cluster_instances.items():
            # Skip instances that are not healthy
            health_status = instance.get("health_status", "unknown")
            if health_status == "unhealthy":
                continue

            # Check GPU availability
            gpu_count = instance.get("gpu_count", 0)

            # Check instance status
            instance_tasks = self._count_instance_tasks(instance_id)

            # Add to appropriate list
            if gpu_count > 0:
                gpu_instances.append(
                    {
                        "instance_id": instance_id,
                        "gpu_count": gpu_count,
                        "active_tasks": instance_tasks,
                        "health_status": health_status,
                    }
                )
            else:
                non_gpu_instances.append(
                    {
                        "instance_id": instance_id,
                        "active_tasks": instance_tasks,
                        "health_status": health_status,
                    }
                )

        # Sort both lists by number of active tasks
        gpu_instances.sort(key=lambda x: x["active_tasks"])
        non_gpu_instances.sort(key=lambda x: x["active_tasks"])

        # Prefer GPU instances if available, otherwise use non-GPU
        if gpu_instances:
            return gpu_instances[0]["instance_id"]
        elif non_gpu_instances:
            return non_gpu_instances[0]["instance_id"]
        else:
            return None

    def _count_instance_tasks(self, instance_id: str) -> int:
        """
        Count the number of active tasks for an instance.

        Args:
            instance_id: Instance ID

        Returns:
            Number of active tasks
        """
        try:
            import boto3

            dynamodb = boto3.resource("dynamodb", region_name=self.region)
            table = dynamodb.Table(self.registry_table)

            response = table.scan(
                FilterExpression="assigned_to = :instance AND #status IN (:pending, :running)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":instance": instance_id,
                    ":pending": "pending",
                    ":running": "running",
                },
            )

            return len(response.get("Items", []))
        except Exception as e:
            logger.error(f"Error counting instance tasks: {e}")
            return 0

    def _cleanup_dead_jobs(self) -> None:
        """
        Clean up tasks associated with dead instances.
        """
        if not self.is_leader:
            return

        try:
            # Find instances that haven't sent heartbeats in a while
            dead_instances = []
            now = int(time.time())

            for instance_id, instance in self.cluster_instances.items():
                last_heartbeat = instance.get("last_heartbeat")
                if last_heartbeat and now - last_heartbeat > 300:  # 5 minutes
                    logger.warning(
                        f"Instance {instance_id} appears to be dead (no heartbeat for 5+ minutes)"
                    )
                    dead_instances.append(instance_id)

            if not dead_instances:
                return

            # Find tasks assigned to dead instances
            import boto3

            dynamodb = boto3.resource("dynamodb", region_name=self.region)
            table = dynamodb.Table(self.registry_table)

            for dead_instance in dead_instances:
                # Query for tasks assigned to this dead instance
                response = table.scan(
                    FilterExpression="assigned_to = :instance AND #status IN (:pending, :running)",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":instance": dead_instance,
                        ":pending": "pending",
                        ":running": "running",
                    },
                )

                # Reset these tasks
                for task in response.get("Items", []):
                    task_id = task.get("task_id")
                    if task_id:
                        # Reset the task
                        table.update_item(
                            Key={"instance_id": f"TASK#{task_id}"},
                            UpdateExpression="REMOVE assigned_to SET #status = :pending, last_updated = :now",
                            ExpressionAttributeNames={"#status": "status"},
                            ExpressionAttributeValues={
                                ":pending": "pending",
                                ":now": int(time.time()),
                            },
                        )

                        logger.info(
                            f"Reset task {task_id} from dead instance {dead_instance}"
                        )

                # Remove dead instance from our cache
                if dead_instance in self.cluster_instances:
                    del self.cluster_instances[dead_instance]

        except Exception as e:
            logger.error(f"Error cleaning up dead jobs: {e}")

    # Task handler methods
    def _handle_preprocess_task(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle a dataset preprocessing task.

        Args:
            params: Task parameters

        Returns:
            Task result
        """
        dataset_name = params.get("dataset_name")

        logger.info(f"Processing dataset {dataset_name}")

        # Actual implementation would preprocess the dataset
        # This is just a placeholder

        return {
            "success": True,
            "dataset_name": dataset_name,
            "processed_items": 1000,
            "processing_time": 60,
        }

    def _handle_training_task(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a training task.

        Args:
            params: Task parameters

        Returns:
            Task result
        """
        job_id = params.get("job_id")
        model_name = params.get("model_name")

        logger.info(f"Training job {job_id} for model {model_name}")

        # Actual implementation would train the model and return metrics
        # This is just a placeholder

        return {
            "success": True,
            "job_id": job_id,
            "model_name": model_name,
            "metrics": {
                "final_loss": 0.1,
                "accuracy": 0.95,
                "epochs_completed": 10,
                "training_time": 3600,
            },
        }

    def _handle_evaluation_task(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle an evaluation task.

        Args:
            params: Task parameters

        Returns:
            Task result
        """
        model_path = params.get("model_path")
        dataset_name = params.get("dataset_name")

        logger.info(
            f"Evaluating model at {model_path} on dataset {dataset_name}"
        )

        # Actual implementation would evaluate the model and return metrics
        # This is just a placeholder

        return {
            "success": True,
            "model_path": model_path,
            "dataset_name": dataset_name,
            "metrics": {
                "precision": 0.92,
                "recall": 0.89,
                "f1": 0.905,
                "accuracy": 0.94,
            },
        }

    def _handle_cluster_state_task(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle a cluster state task.

        Args:
            params: Task parameters

        Returns:
            Task result
        """
        action = params.get("action")

        if action == "leader_changed":
            leader_id = params.get("leader_id")
            logger.info(f"Leader changed to {leader_id}")

            # Update our leader information
            if leader_id != self.coordinator.instance_id:
                self.is_leader = False

        return {"success": True, "action": action, "acknowledged": True}
