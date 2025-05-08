#!/usr/bin/env python3
"""Cluster monitoring utility for the distributed training infrastructure."""

import os
import sys
import time
import argparse
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from tabulate import tabulate

from receipt_trainer.utils.infrastructure import EC2Metadata
from receipt_trainer.utils.coordinator import InstanceCoordinator
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.task import Task


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Monitor the Receipt Trainer cluster status"
    )
    parser.add_argument(
        "--table", required=True, help="DynamoDB table for instance registry"
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region (defaults to EC2 instance region or us-east-1)",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=10,
        help="Refresh interval in seconds (default: 10)",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=20,
        help="Maximum number of tasks to display (default: 20)",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Don't clear screen between refreshes",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    return parser.parse_args()


def get_instances(dynamo_client: DynamoClient) -> List[Dict[str, Any]]:
    """Get all instance information from DynamoDB.

    Args:
        dynamo_client: DynamoDB client for operations

    Returns:
        List of instance information dictionaries
    """
    try:
        import boto3

        dynamodb = boto3.resource(
            "dynamodb", region_name=dynamo_client._client.meta.region_name
        )
        table = dynamodb.Table(dynamo_client.table_name)

        # Filter for instances (exclude task records)
        response = table.scan(FilterExpression="attribute_not_exists(task_id)")

        return response.get("Items", [])
    except Exception as e:
        print(f"Error scanning instances: {e}")
        return []


def get_all_tasks(dynamo_client: DynamoClient) -> List[Task]:
    """Get all tasks from DynamoDB.

    Args:
        dynamo_client: DynamoDB client for operations

    Returns:
        List of Task objects
    """
    tasks = []

    # Get tasks by different statuses
    for status in ["pending", "running", "completed", "failed"]:
        status_tasks, last_key = dynamo_client.listTasksByStatus(status)
        tasks.extend(status_tasks)

        # If there are more tasks, continue fetching
        while last_key:
            status_tasks, last_key = dynamo_client.listTasksByStatus(
                status, last_key
            )
            tasks.extend(status_tasks)

    return tasks


def format_time_ago(timestamp: str) -> str:
    """Format a timestamp as a human-readable "time ago" string.

    Args:
        timestamp: ISO format timestamp

    Returns:
        String with time difference
    """
    try:
        dt = datetime.fromisoformat(timestamp)
        now = datetime.now()
        diff = now - dt

        if diff.days > 0:
            return f"{diff.days}d ago"
        elif diff.seconds >= 3600:
            return f"{diff.seconds // 3600}h ago"
        elif diff.seconds >= 60:
            return f"{diff.seconds // 60}m ago"
        else:
            return f"{diff.seconds}s ago"
    except (ValueError, TypeError):
        return "unknown"


def display_instances(instances: List[Dict[str, Any]]) -> None:
    """Display instance information in a table.

    Args:
        instances: List of instance information dictionaries
    """
    if not instances:
        print("No instances found")
        return

    # Prepare data for tabulate
    headers = [
        "Instance ID",
        "Type",
        "Status",
        "Health",
        "GPU",
        "Leader",
        "Last Heartbeat",
    ]
    rows = []

    for instance in instances:
        instance_id = instance.get("instance_id", "unknown")
        if instance_id.startswith("TASK#"):
            continue

        instance_type = instance.get("instance_type", "unknown")
        status = instance.get("status", "unknown")
        health = instance.get("health_status", "unknown")

        # Get GPU information
        gpu_count = instance.get("gpu_count", 0)
        gpu_info = f"{gpu_count} GPUs" if gpu_count > 0 else "No GPU"

        # Leader status
        is_leader = "✓" if instance.get("is_leader", False) else ""

        # Heartbeat
        last_heartbeat = instance.get("last_heartbeat", 0)
        if isinstance(last_heartbeat, (int, float)):
            last_seen = datetime.fromtimestamp(last_heartbeat)
            time_diff = datetime.now() - last_seen
            if time_diff < timedelta(minutes=2):
                heartbeat_str = f"{time_diff.seconds}s ago"
            elif time_diff < timedelta(hours=1):
                heartbeat_str = f"{time_diff.seconds // 60}m ago"
            else:
                heartbeat_str = f"{time_diff.seconds // 3600}h ago"
        else:
            heartbeat_str = "never"

        rows.append(
            [
                instance_id,
                instance_type,
                status,
                health,
                gpu_info,
                is_leader,
                heartbeat_str,
            ]
        )

    print(tabulate(rows, headers=headers, tablefmt="pretty"))


def display_tasks(tasks: List[Task], max_tasks: int = 20) -> None:
    """Display task information in a table.

    Args:
        tasks: List of Task objects
        max_tasks: Maximum number of tasks to display
    """
    if not tasks:
        print("No tasks found")
        return

    # Prepare data for tabulate
    headers = [
        "Task ID",
        "Type",
        "Status",
        "Created",
        "Assigned To",
        "Started",
        "Completed",
    ]
    rows = []

    # Sort tasks by creation time (newest first)
    sorted_tasks = sorted(
        tasks,
        key=lambda t: t.created_at if isinstance(t.created_at, str) else "",
        reverse=True,
    )

    # Limit number of tasks displayed
    display_tasks = sorted_tasks[:max_tasks]

    for task in display_tasks:
        # Format dates as relative time
        created_at = (
            format_time_ago(task.created_at) if task.created_at else "unknown"
        )
        started_at = (
            format_time_ago(task.started_at) if task.started_at else ""
        )
        completed_at = (
            format_time_ago(task.completed_at) if task.completed_at else ""
        )

        rows.append(
            [
                task.task_id[:8] + "...",  # Truncate ID for display
                task.task_type,
                task.status,
                created_at,
                task.assigned_to if task.assigned_to else "-",
                started_at,
                completed_at,
            ]
        )

    print(tabulate(rows, headers=headers, tablefmt="pretty"))

    if len(sorted_tasks) > max_tasks:
        print(f"\nNote: {len(sorted_tasks) - max_tasks} more tasks not shown")


def display_task_details(task: Task) -> None:
    """Display detailed information about a specific task.

    Args:
        task: Task object
    """
    print(f"\nTask Details: {task.task_id}")
    print(f"Type: {task.task_type}")
    print(f"Status: {task.status}")
    print(f"Created by: {task.created_by}")
    print(f"Created at: {task.created_at}")

    if task.assigned_to:
        print(f"Assigned to: {task.assigned_to}")

    if task.started_at:
        print(f"Started at: {task.started_at}")

    if task.completed_at:
        print(f"Completed at: {task.completed_at}")

    print("\nParameters:")
    print(json.dumps(task.params, indent=2))

    if task.result:
        print("\nResult:")
        print(json.dumps(task.result, indent=2))


def display_cluster_summary(
    instances: List[Dict[str, Any]], tasks: List[Task]
) -> None:
    """Display a summary of the cluster state.

    Args:
        instances: List of instance information dictionaries
        tasks: List of Task objects
    """
    # Count instances by status
    instance_count = len(
        [
            i
            for i in instances
            if not i.get("instance_id", "").startswith("TASK#")
        ]
    )
    healthy_count = len(
        [i for i in instances if i.get("health_status") == "healthy"]
    )
    degraded_count = len(
        [i for i in instances if i.get("health_status") == "degraded"]
    )
    unhealthy_count = len(
        [i for i in instances if i.get("health_status") == "unhealthy"]
    )

    # Count GPU instances
    gpu_instances = len([i for i in instances if i.get("gpu_count", 0) > 0])
    gpu_count = sum(i.get("gpu_count", 0) for i in instances)

    # Count tasks by status
    pending_tasks = len([t for t in tasks if t.status == "pending"])
    running_tasks = len([t for t in tasks if t.status == "running"])
    completed_tasks = len([t for t in tasks if t.status == "completed"])
    failed_tasks = len([t for t in tasks if t.status == "failed"])

    # Find leader
    leader = next((i for i in instances if i.get("is_leader", False)), None)
    leader_id = leader.get("instance_id", "None") if leader else "None"

    print("\nCluster Summary:")
    print(
        f"Instances: {instance_count} total ({healthy_count} healthy, {degraded_count} degraded, {unhealthy_count} unhealthy)"
    )
    print(f"GPUs: {gpu_count} total across {gpu_instances} instances")
    print(
        f"Tasks: {len(tasks)} total ({pending_tasks} pending, {running_tasks} running, {completed_tasks} completed, {failed_tasks} failed)"
    )
    print(f"Leader: {leader_id}")


def display_json_output(
    instances: List[Dict[str, Any]], tasks: List[Task]
) -> None:
    """Display all information as JSON.

    Args:
        instances: List of instance information dictionaries
        tasks: List of Task objects
    """
    # Convert tasks to dictionaries
    task_dicts = []
    for task in tasks:
        task_dict = {}
        for key, value in task:
            task_dict[key] = value
        task_dicts.append(task_dict)

    # Create output structure
    output = {
        "timestamp": datetime.now().isoformat(),
        "instances": instances,
        "tasks": task_dicts,
        "summary": {
            "instance_count": len(instances),
            "task_count": len(tasks),
            "leader": next(
                (
                    i.get("instance_id")
                    for i in instances
                    if i.get("is_leader", False)
                ),
                None,
            ),
        },
    }

    print(json.dumps(output, indent=2))


def main():
    """Main entry point for the cluster monitor."""
    args = parse_args()

    # Initialize DynamoDB client
    region = args.region or EC2Metadata.get_instance_region() or "us-east-1"
    dynamo_client = DynamoClient(args.table, region)

    try:
        while True:
            # Get data
            instances = get_instances(dynamo_client)
            tasks = get_all_tasks(dynamo_client)

            # Clear screen if requested
            if not args.no_clear:
                os.system("cls" if os.name == "nt" else "clear")

            # Display current time
            print(
                f"Receipt Trainer Cluster Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print(f"Connected to table: {args.table} (Region: {region})")
            print("-" * 80)

            if args.output == "json":
                display_json_output(instances, tasks)
            else:
                # Display cluster information
                display_cluster_summary(instances, tasks)

                print("\nInstances:")
                display_instances(instances)

                print("\nRecent Tasks:")
                display_tasks(tasks, args.max_tasks)

            if args.refresh <= 0:
                break

            print(
                f"\nRefreshing in {args.refresh} seconds (Ctrl+C to exit)..."
            )
            time.sleep(args.refresh)

    except KeyboardInterrupt:
        print("\nMonitor terminated by user")
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
