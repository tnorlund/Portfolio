"""Utilities for accessing Pulumi stack outputs.

This module provides easy access to Pulumi stack outputs for configuring AWS resources.
"""

import logging
import os
from typing import Any, Dict, Optional

from receipt_dynamo.data._pulumi import load_env
from receipt_trainer.utils.auto_scaling import (
    AutoScalingManager,
    generate_training_worker_user_data,
)

logger = logging.getLogger(__name__)


def get_auto_scaling_config(
    stack_name: str = None,
    min_instances: int = 1,
    max_instances: int = 10,
    cpu_instance_types: Optional[list] = None,
    gpu_instance_types: Optional[list] = None,
) -> Dict[str, Any]:
    """Get auto-scaling configuration from Pulumi stack outputs.

    Args:
        stack_name: Pulumi stack name (dev, prod, etc.) - defaults to env var
        min_instances: Minimum number of instances to maintain
        max_instances: Maximum number of instances to run
        cpu_instance_types: List of CPU instance types (optional)
        gpu_instance_types: List of GPU instance types (optional)

    Returns:
        Dictionary with configuration for AutoScalingManager
    """
    # Use environment variable if stack_name not provided
    if not stack_name:
        stack_name = os.environ.get("PULUMI_STACK", "dev")

    logger.info(f"Loading configuration from Pulumi stack: {stack_name}")

    # Load Pulumi stack outputs
    stack_outputs = load_env(stack_name)

    # Default instance types
    default_cpu_types = ["c5.large", "c5.xlarge", "c5.2xlarge", "c5.4xlarge"]
    default_gpu_types = [
        "g4dn.xlarge",
        "g4dn.2xlarge",
        "p3.2xlarge",
        "p3.8xlarge",
    ]

    # Extract relevant outputs
    config = {
        "queue_url": stack_outputs.get("job_queue_url"),
        "dynamo_table": stack_outputs.get("dynamodb_table_name"),
        "instance_registry_table": stack_outputs.get(
            "instance_registry_table_name"
        ),
        "instance_ami": stack_outputs.get("training_ami_id"),
        "instance_profile": stack_outputs.get(
            "training_instance_profile_name"
        ),
        "subnet_id": stack_outputs.get("training_subnet_id"),
        "security_group_id": stack_outputs.get("training_security_group_id"),
        "efs_id": stack_outputs.get("training_efs_id"),
        "s3_bucket": stack_outputs.get("training_data_bucket_name"),
        "min_instances": min_instances,
        "max_instances": max_instances,
        "cpu_instance_types": cpu_instance_types or default_cpu_types,
        "gpu_instance_types": gpu_instance_types or default_gpu_types,
    }

    # Log available configuration
    logger.info(
        f"Loaded configuration: {', '.join(k for k, v in config.items() if v)}"
    )

    # Check for required values
    required_keys = [
        "queue_url",
        "dynamo_table",
        "instance_ami",
        "instance_profile",
        "subnet_id",
        "security_group_id",
    ]

    missing_keys = [k for k in required_keys if not config.get(k)]
    if missing_keys:
        logger.warning(
            f"Missing required configuration: {', '.join(missing_keys)}"
        )

    return config


def create_auto_scaling_manager(
    stack_name: Optional[str] = None,
    min_instances: int = 1,
    max_instances: int = 10,
    cpu_instance_types: Optional[list] = None,
    gpu_instance_types: Optional[list] = None,
) -> AutoScalingManager:
    """Create an auto-scaling manager configured from Pulumi stack outputs.

    Args:
        stack_name: Pulumi stack name (dev, prod, etc.)
        min_instances: Minimum number of instances to maintain
        max_instances: Maximum number of instances to run
        cpu_instance_types: List of CPU instance types to use
        gpu_instance_types: List of GPU instance types to use

    Returns:
        Configured AutoScalingManager instance
    """
    # Get configuration from stack outputs
    config = get_auto_scaling_config(
        stack_name=stack_name,
        min_instances=min_instances,
        max_instances=max_instances,
        cpu_instance_types=cpu_instance_types,
        gpu_instance_types=gpu_instance_types,
    )

    # Generate user data script
    user_data = generate_training_worker_user_data(
        queue_url=config["queue_url"],
        dynamo_table=config["dynamo_table"],
        instance_registry_table=config.get("instance_registry_table"),
        s3_bucket=config.get("s3_bucket"),
        efs_id=config.get("efs_id"),
    )

    # Create auto-scaling manager
    manager = AutoScalingManager(
        queue_url=config["queue_url"],
        instance_ami=config["instance_ami"],
        instance_profile=config["instance_profile"],
        subnet_id=config["subnet_id"],
        security_group_id=config["security_group_id"],
        instance_registry_table=config.get("instance_registry_table"),
        cpu_instance_types=config["cpu_instance_types"],
        gpu_instance_types=config["gpu_instance_types"],
        min_instances=min_instances,
        max_instances=max_instances,
        user_data=user_data,
        tags={
            "Name": f"ReceiptTrainer-Worker-{stack_name}",
            "Environment": stack_name,
            "Purpose": "ML Training",
            "ManagedBy": "AutoScalingManager",
        },
    )

    logger.info(f"Created auto-scaling manager for stack: {stack_name}")
    return manager
