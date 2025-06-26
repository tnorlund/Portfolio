"""
Example of using the service layer in the CLI.

This file demonstrates how to refactor the CLI to use the service layer
instead of directly constructing DynamoDB queries.
"""

import argparse
import json
import logging
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo import InstanceService, JobService, QueueService
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_status import JobStatus
from tabulate import tabulate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)

# Table name configuration (should be shared between CLI and processing system)
DYNAMODB_TABLE = "receipt-processing"


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="ML Training Job Management CLI"
    )
    subparsers = parser.add_subparsers(
        dest="command", help="Command to execute"
    )

    # List jobs command
    list_parser = subparsers.add_parser("list-jobs", help="List all jobs")
    list_parser.add_argument(
        "--limit", type=int, help="Maximum number of jobs to list"
    )
    list_parser.add_argument("--status", help="Filter jobs by status")
    list_parser.add_argument(
        "--region", default="us-east-1", help="AWS region"
    )

    # Job details command
    details_parser = subparsers.add_parser(
        "job-details", help="Get details for a job"
    )
    details_parser.add_argument("job_id", help="ID of the job")
    details_parser.add_argument(
        "--region", default="us-east-1", help="AWS region"
    )

    # Submit job command
    submit_parser = subparsers.add_parser(
        "submit-job", help="Submit a new job"
    )
    submit_parser.add_argument("name", help="Name of the job")
    submit_parser.add_argument("description", help="Description of the job")
    submit_parser.add_argument(
        "--config",
        required=True,
        help="Job configuration JSON string or file path",
    )
    submit_parser.add_argument(
        "--priority",
        default="medium",
        help="Job priority (low, medium, high, critical)",
    )
    submit_parser.add_argument(
        "--user", required=True, help="User submitting the job"
    )
    submit_parser.add_argument("--tags", help="Tags in JSON format")
    submit_parser.add_argument(
        "--region", default="us-east-1", help="AWS region"
    )

    # Cancel job command
    cancel_parser = subparsers.add_parser("cancel-job", help="Cancel a job")
    cancel_parser.add_argument("job_id", help="ID of the job to cancel")
    cancel_parser.add_argument(
        "--region", default="us-east-1", help="AWS region"
    )

    # Job logs command
    logs_parser = subparsers.add_parser("job-logs", help="Get logs for a job")
    logs_parser.add_argument("job_id", help="ID of the job")
    logs_parser.add_argument(
        "--region", default="us-east-1", help="AWS region"
    )

    # List instances command
    instances_parser = subparsers.add_parser(
        "list-instances", help="List instances"
    )
    instances_parser.add_argument(
        "--status", help="Filter instances by status"
    )
    instances_parser.add_argument(
        "--limit", type=int, help="Maximum number of instances to list"
    )
    instances_parser.add_argument(
        "--region", default="us-east-1", help="AWS region"
    )

    return parser


def handle_list_jobs(args: argparse.Namespace) -> None:
    """Handle the list-jobs command using the service layer."""
    # Create the job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=args.region)

    # List jobs
    if args.status:
        jobs, _ = job_service.list_jobs_by_status(args.status, args.limit)
    else:
        jobs, _ = job_service.list_jobs(args.limit)

    if not jobs:
        print("No jobs found")
        return

    # Prepare table data
    table_data = []
    for job in jobs:
        # Get the latest status
        job_statuses = job_service.get_job_status_history(job.job_id)
        latest_status = job_statuses[0].status if job_statuses else "unknown"

        # Add to table data
        table_data.append(
            [
                job.job_id,
                job.name,
                (
                    job.description[:30] + "..."
                    if len(job.description) > 30
                    else job.description
                ),
                latest_status,
                job.priority,
                job.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                job.created_by,
            ]
        )

    # Print table
    headers = [
        "Job ID",
        "Name",
        "Description",
        "Status",
        "Priority",
        "Created At",
        "Created By",
    ]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))


def handle_job_details(args: argparse.Namespace) -> None:
    """Handle the job-details command using the service layer."""
    # Create the job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=args.region)

    try:
        # Get job and its status history
        job, statuses = job_service.get_job_with_status(args.job_id)

        # Print job details
        print(f"Job Details for {job.job_id}:")
        print(f"  Name: {job.name}")
        print(f"  Description: {job.description}")
        print(f"  Created: {job.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Created By: {job.created_by}")
        print(f"  Priority: {job.priority}")

        # Print status history
        print("\nStatus History:")
        for status in statuses:
            print(
                f"  {status.timestamp.strftime('%Y-%m-%d %H:%M:%S')}: {status.status} - {status.message}"
            )

        # Print config summary (truncated for large configs)
        print("\nConfiguration:")
        config_str = json.dumps(job.job_config, indent=2)
        if len(config_str) > 500:
            print(
                f"{config_str[:500]}...\n(truncated, use --full-config to see all)"
            )
        else:
            print(config_str)

        # Get logs
        logs = job_service.get_job_logs(args.job_id)
        if logs:
            print("\nLogs:")
            for log in logs[:10]:  # Show only the 10 most recent logs
                print(
                    f"  {log.timestamp.strftime('%Y-%m-%d %H:%M:%S')} [{log.log_level}] {log.message}"
                )
            if len(logs) > 10:
                print(f"  ... and {len(logs) - 10} more logs")

        # Get metrics
        metrics = job_service.get_job_metrics(args.job_id)
        if metrics:
            print("\nMetrics:")
            metrics_table = []
            for metric in metrics[:10]:  # Show only the 10 most recent metrics
                metrics_table.append(
                    [
                        metric.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        metric.metric_name,
                        metric.metric_value,
                    ]
                )
            print(
                tabulate(
                    metrics_table,
                    headers=["Timestamp", "Metric", "Value"],
                    tablefmt="simple",
                )
            )
            if len(metrics) > 10:
                print(f"  ... and {len(metrics) - 10} more metrics")

    except Exception as e:
        print(f"Error: {str(e)}")


def handle_submit_job(args: argparse.Namespace) -> None:
    """Handle the submit-job command using the service layer."""
    # Create the job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=args.region)

    # Parse job config
    if args.config.startswith("{"):
        # Config is a JSON string
        try:
            job_config = json.loads(args.config)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON configuration: {str(e)}")
            return
    else:
        # Config is a file path
        try:
            with open(args.config, "r") as f:
                job_config = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"Error reading configuration file: {str(e)}")
            return

    # Parse tags
    tags = {}
    if args.tags:
        try:
            tags = json.loads(args.tags)
        except json.JSONDecodeError as e:
            print(f"Error parsing tags: {str(e)}")
            return

    # Generate job ID
    job_id = str(uuid.uuid4())

    try:
        # Create the job
        job = job_service.create_job(
            job_id=job_id,
            name=args.name,
            description=args.description,
            created_by=args.user,
            status="pending",
            priority=args.priority,
            job_config=job_config,
            tags=tags,
        )

        # Add initial status
        job_service.add_job_status(
            job_id=job_id,
            status="pending",
            message="Job created",
        )

        print(f"Job submitted successfully!")
        print(f"Job ID: {job_id}")
        print(f"Status: pending")

    except Exception as e:
        print(f"Error submitting job: {str(e)}")


def handle_cancel_job(args: argparse.Namespace) -> None:
    """Handle the cancel-job command using the service layer."""
    # Create the job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=args.region)

    try:
        # Get the job
        job = job_service.get_job(args.job_id)

        # Add cancelled status
        job_service.add_job_status(
            job_id=job.job_id,
            status="cancelled",
            message="Job cancelled by user",
        )

        print(f"Job {job.job_id} has been cancelled")

    except Exception as e:
        print(f"Error cancelling job: {str(e)}")


def handle_job_logs(args: argparse.Namespace) -> None:
    """Handle the job-logs command using the service layer."""
    # Create the job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=args.region)

    try:
        # Get the logs
        logs = job_service.get_job_logs(args.job_id)

        if not logs:
            print(f"No logs found for job {args.job_id}")
            return

        # Print logs
        print(f"Logs for job {args.job_id}:")
        for log in logs:
            print(
                f"{log.timestamp.strftime('%Y-%m-%d %H:%M:%S')} [{log.log_level}] {log.message}"
            )

    except Exception as e:
        print(f"Error retrieving logs: {str(e)}")


def handle_list_instances(args: argparse.Namespace) -> None:
    """Handle the list-instances command using the service layer."""
    # Create the instance service
    instance_service = InstanceService(
        table_name=DYNAMODB_TABLE, region=args.region
    )

    # List instances
    if args.status:
        instances, _ = instance_service.list_instances_by_status(
            args.status, args.limit
        )
    else:
        instances, _ = instance_service.list_instances(args.limit)

    if not instances:
        print("No instances found")
        return

    # Prepare table data
    table_data = []
    for instance in instances:
        # Add to table data
        table_data.append(
            [
                instance.instance_id,
                instance.instance_type,
                instance.status,
                instance.region,
                instance.availability_zone,
                instance.hostname,
                instance.ip_address,
                instance.last_seen.strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )

    # Print table
    headers = [
        "Instance ID",
        "Type",
        "Status",
        "Region",
        "AZ",
        "Hostname",
        "IP",
        "Last Seen",
    ]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))


def main() -> None:
    """Main entry point for the CLI."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Route to the appropriate handler
    if args.command == "list-jobs":
        handle_list_jobs(args)
    elif args.command == "job-details":
        handle_job_details(args)
    elif args.command == "submit-job":
        handle_submit_job(args)
    elif args.command == "cancel-job":
        handle_cancel_job(args)
    elif args.command == "job-logs":
        handle_job_logs(args)
    elif args.command == "list-instances":
        handle_list_instances(args)
    else:
        print(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
