#!/usr/bin/env python
"""Cluster monitoring script for distributed training."""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from tabulate import tabulate

from receipt_trainer.utils.infrastructure import (EC2Metadata,
                                                  TrainingEnvironment)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("cluster_monitor")


def format_time_elapsed(timestamp: int) -> str:
    """Format timestamp as an elapsed time string.

    Args:
        timestamp: Unix timestamp

    Returns:
        Formatted elapsed time string
    """
    now = int(time.time())
    elapsed = now - timestamp

    if elapsed < 60:
        return f"{elapsed}s ago"
    elif elapsed < 3600:
        return f"{elapsed // 60}m {elapsed % 60}s ago"
    elif elapsed < 86400:
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        return f"{hours}h {minutes}m ago"
    else:
        days = elapsed // 86400
        hours = (elapsed % 86400) // 3600
        return f"{days}d {hours}h ago"


def format_timestamp(timestamp: int) -> str:
    """Format timestamp as a readable date/time string.

    Args:
        timestamp: Unix timestamp

    Returns:
        Formatted date/time string
    """
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def get_cluster_state(training_env: TrainingEnvironment) -> Dict[str, Any]:
    """Get the current state of the cluster.

    Args:
        training_env: TrainingEnvironment instance

    Returns:
        Dictionary containing cluster state
    """
    state = training_env.get_cluster_state()

    # Add additional information
    if training_env.is_leader():
        state["is_current_instance_leader"] = True
    else:
        state["is_current_instance_leader"] = False

    # Add current instance ID
    state["current_instance"] = EC2Metadata.get_instance_id()

    return state


def print_instance_table(instances: Dict[str, Dict[str, Any]]) -> None:
    """Print a formatted table of instance information.

    Args:
        instances: Dictionary of instance info keyed by instance ID
    """
    if not instances:
        print("No instances found")
        return

    rows = []

    for instance_id, instance in instances.items():
        # Extract instance info
        instance_type = instance.get("instance_type", "unknown")
        status = instance.get("status", "unknown")
        health_status = instance.get("health_status", "unknown")
        gpu_count = instance.get("gpu_count", 0)
        is_leader = "✓" if instance.get("is_leader", False) else ""
        last_heartbeat = instance.get("last_heartbeat")

        # Format heartbeat time
        heartbeat_str = (
            format_time_elapsed(last_heartbeat) if last_heartbeat else "never"
        )

        # Add health indicator
        if health_status == "healthy":
            health_icon = "🟢"
        elif health_status == "degraded":
            health_icon = "🟡"
        elif health_status == "unhealthy":
            health_icon = "🔴"
        else:
            health_icon = "⚪"

        # Check if instance heartbeat is stale
        is_stale = False
        if last_heartbeat and (int(time.time()) - last_heartbeat > 180):  # 3 minutes
            is_stale = True
            health_icon = "⚠️"

        # Get health metrics if available
        health_metrics = instance.get("health_metrics", {})
        cpu_util = health_metrics.get("cpu_utilization", 0)
        mem_util = health_metrics.get("memory_usage", 0)
        gpu_util = health_metrics.get("gpu_utilization", 0)

        # Format utilization as percentage strings
        cpu_str = f"{cpu_util:.1f}%" if cpu_util else "n/a"
        mem_str = f"{mem_util:.1f}%" if mem_util else "n/a"
        gpu_str = f"{gpu_util:.1f}%" if gpu_util else "n/a"

        # Add to rows
        rows.append(
            [
                instance_id[:10] + "...",  # Truncate ID
                instance_type,
                health_icon,
                is_leader,
                gpu_count,
                cpu_str,
                mem_str,
                gpu_str,
                heartbeat_str,
            ]
        )

    # Print table
    headers = [
        "Instance ID",
        "Type",
        "Health",
        "Leader",
        "GPUs",
        "CPU",
        "Mem",
        "GPU",
        "Last Heartbeat",
    ]

    print("\nCLUSTER INSTANCES:")
    print(tabulate(rows, headers=headers, tablefmt="pretty"))


def print_tasks_table(state: Dict[str, Any]) -> None:
    """Print a formatted table of task information.

    Args:
        state: Cluster state dictionary
    """
    # Get tasks from state
    tasks = state.get("tasks", [])

    if not tasks:
        print("\nNo active tasks found")
        return

    rows = []

    for task in tasks:
        # Extract task info
        task_id = task.get("task_id", "unknown")
        task_type = task.get("task_type", "unknown")
        status = task.get("status", "unknown")
        assigned_to = task.get("assigned_to", "unassigned")
        created_at = task.get("created_at")
        last_updated = task.get("last_updated")

        # Format timestamps
        created_str = format_time_elapsed(created_at) if created_at else "unknown"
        updated_str = format_time_elapsed(last_updated) if last_updated else "never"

        # Add to rows
        rows.append(
            [
                task_id[:8] + "...",  # Truncate ID
                task_type,
                status,
                (assigned_to[:10] + "..." if len(assigned_to) > 10 else assigned_to),
                created_str,
                updated_str,
            ]
        )

    # Print table
    headers = [
        "Task ID",
        "Type",
        "Status",
        "Assigned To",
        "Created",
        "Last Updated",
    ]

    print("\nACTIVE TASKS:")
    print(tabulate(rows, headers=headers, tablefmt="pretty"))


def print_jobs_table(state: Dict[str, Any]) -> None:
    """Print a formatted table of active jobs.

    Args:
        state: Cluster state dictionary
    """
    # Get jobs from state
    jobs = state.get("active_jobs", {})

    if not jobs:
        print("\nNo active jobs found")
        return

    rows = []

    for job_id, job in jobs.items():
        # Extract job info
        name = job.get("name", "unknown")
        status = job.get("status", "unknown")
        instance_id = job.get("instance_id", "unknown")
        start_time = job.get("start_time")
        end_time = job.get("end_time")

        # Format duration
        if start_time:
            if end_time:
                duration = end_time - start_time
                duration_str = f"{duration // 60}m {duration % 60}s"
            else:
                duration = int(time.time()) - start_time
                duration_str = f"{duration // 60}m {duration % 60}s (running)"
        else:
            duration_str = "unknown"

        # Add to rows
        rows.append(
            [
                job_id[:8] + "...",  # Truncate ID
                name,
                status,
                (instance_id[:10] + "..." if len(instance_id) > 10 else instance_id),
                format_timestamp(start_time) if start_time else "unknown",
                duration_str,
            ]
        )

    # Print table
    headers = [
        "Job ID",
        "Name",
        "Status",
        "Instance",
        "Start Time",
        "Duration",
    ]

    print("\nACTIVE JOBS:")
    print(tabulate(rows, headers=headers, tablefmt="pretty"))


def print_cluster_summary(state: Dict[str, Any]) -> None:
    """Print a summary of cluster state.

    Args:
        state: Cluster state dictionary
    """
    # Extract data
    instances = state.get("instances", {})
    leader_id = state.get("leader_id")
    current_instance = state.get("current_instance")
    capabilities = state.get("capabilities", {})
    is_leader = state.get("is_current_instance_leader", False)

    # Calculate summary stats
    total_instances = len(instances)
    total_gpus = capabilities.get("total_gpus", 0)
    gpu_types = capabilities.get("gpu_types", {})
    instance_types = capabilities.get("instance_types", {})

    # Format leader info
    leader_str = leader_id if leader_id else "None"
    if leader_id and leader_id == current_instance:
        leader_str += " (this instance)"

    # Print summary
    print("\nCLUSTER SUMMARY:")
    print(f"Total Instances: {total_instances}")
    print(f"Total GPUs: {total_gpus}")
    print(f"Leader Instance: {leader_str}")
    print(f"Current Instance: {current_instance}" + (" (leader)" if is_leader else ""))

    if gpu_types:
        print("\nGPU Types:")
        for gpu_type, count in gpu_types.items():
            print(f"  - {gpu_type}: {count}")

    if instance_types:
        print("\nInstance Types:")
        for instance_type, count in instance_types.items():
            print(f"  - {instance_type}: {count}")


def monitor_once(registry_table: str) -> None:
    """Run the monitor once and display current state.

    Args:
        registry_table: DynamoDB registry table name
    """
    try:
        # Set up training environment
        env = TrainingEnvironment(
            registry_table=registry_table,
            setup_efs=False,
            handle_spot=False,
            enable_coordination=True,
        )

        # Get cluster state
        state = get_cluster_state(env)

        # Print information
        print("\n=== CLUSTER MONITOR ===")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Registry Table: {registry_table}")

        # Print summary
        print_cluster_summary(state)

        # Print instance table
        print_instance_table(state.get("instances", {}))

        # Print tasks table
        print_tasks_table(state)

        # Print jobs table
        print_jobs_table(state)

        print("\n=====================")

    except Exception as e:
        logger.error(f"Error monitoring cluster: {e}")
        raise


def monitor_continuously(registry_table: str, interval: int = 10) -> None:
    """Run the monitor continuously with updates at specified interval.

    Args:
        registry_table: DynamoDB registry table name
        interval: Update interval in seconds
    """
    try:
        while True:
            # Clear screen
            os.system("cls" if os.name == "nt" else "clear")

            # Run monitor once
            monitor_once(registry_table)

            # Wait for next update
            print(f"\nUpdating in {interval} seconds... Press Ctrl+C to exit")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user")
    except Exception as e:
        logger.error(f"Error in continuous monitoring: {e}")
        raise


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Monitor ML training cluster")
    parser.add_argument(
        "--registry-table",
        "-r",
        type=str,
        required=True,
        help="DynamoDB registry table name",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=int,
        default=10,
        help="Update interval in seconds for continuous mode",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default is continuous monitoring)",
    )

    args = parser.parse_args()

    try:
        if args.once:
            monitor_once(args.registry_table)
        else:
            monitor_continuously(args.registry_table, args.interval)
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
