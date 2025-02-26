"""
Command-line interface for managing ML training job queues.
"""

import argparse
import json
import logging
import sys
import uuid
from typing import Dict, Any, Optional, List

from .job import Job, JobStatus, JobPriority
from .queue import JobQueue, JobQueueConfig, JobRetryStrategy
from .aws import (
    create_queue_with_dlq,
    get_queue_url,
    delete_queue,
    purge_queue,
)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(description="ML Training Job Queue Management CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Create queue command
    create_parser = subparsers.add_parser("create-queue", help="Create a job queue with DLQ")
    create_parser.add_argument("queue_name", help="Name of the queue to create")
    create_parser.add_argument("--fifo", action="store_true", help="Create a FIFO queue")
    create_parser.add_argument("--region", help="AWS region")
    create_parser.add_argument("--max-receives", type=int, default=5, help="Max receives before message goes to DLQ")
    create_parser.add_argument("--tags", help="Tags in JSON format")
    
    # Delete queue command
    delete_parser = subparsers.add_parser("delete-queue", help="Delete a job queue")
    delete_parser.add_argument("queue_url", help="URL of the queue to delete")
    delete_parser.add_argument("--region", help="AWS region")
    
    # Purge queue command
    purge_parser = subparsers.add_parser("purge-queue", help="Purge all messages from a queue")
    purge_parser.add_argument("queue_url", help="URL of the queue to purge")
    purge_parser.add_argument("--region", help="AWS region")
    
    # Submit job command
    submit_parser = subparsers.add_parser("submit-job", help="Submit a job to the queue")
    submit_parser.add_argument("queue_url", help="URL of the queue")
    submit_parser.add_argument("--name", required=True, help="Name of the job")
    submit_parser.add_argument("--type", required=True, help="Type of the job")
    submit_parser.add_argument("--config", required=True, help="Job configuration in JSON format")
    submit_parser.add_argument("--priority", choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"], default="MEDIUM", help="Job priority")
    submit_parser.add_argument("--tags", help="Tags in JSON format")
    submit_parser.add_argument("--region", help="AWS region")
    
    # List pending jobs command
    list_parser = subparsers.add_parser("list-jobs", help="List jobs in the queue")
    list_parser.add_argument("queue_url", help="URL of the queue")
    list_parser.add_argument("--count", type=int, default=10, help="Maximum number of jobs to list")
    list_parser.add_argument("--region", help="AWS region")
    
    # Get queue attributes command
    attributes_parser = subparsers.add_parser("queue-attributes", help="Get queue attributes")
    attributes_parser.add_argument("queue_url", help="URL of the queue")
    attributes_parser.add_argument("--region", help="AWS region")
    
    return parser


def handle_create_queue(args: argparse.Namespace) -> None:
    """Handle the create-queue command."""
    tags = {}
    if args.tags:
        try:
            tags = json.loads(args.tags)
        except json.JSONDecodeError:
            logger.error("Invalid JSON format for tags")
            return
    
    queue_url, dlq_url = create_queue_with_dlq(
        queue_name=args.queue_name,
        fifo=args.fifo,
        max_receives=args.max_receives,
        tags=tags,
        region_name=args.region,
    )
    
    if queue_url and dlq_url:
        print(f"Queue URL: {queue_url}")
        print(f"DLQ URL: {dlq_url}")
    else:
        print("Failed to create queue")


def handle_delete_queue(args: argparse.Namespace) -> None:
    """Handle the delete-queue command."""
    success = delete_queue(args.queue_url, args.region)
    if success:
        print(f"Queue {args.queue_url} deleted successfully")
    else:
        print(f"Failed to delete queue {args.queue_url}")


def handle_purge_queue(args: argparse.Namespace) -> None:
    """Handle the purge-queue command."""
    success = purge_queue(args.queue_url, args.region)
    if success:
        print(f"Queue {args.queue_url} purged successfully")
    else:
        print(f"Failed to purge queue {args.queue_url}")


def handle_submit_job(args: argparse.Namespace) -> None:
    """Handle the submit-job command."""
    try:
        config = json.loads(args.config)
    except json.JSONDecodeError:
        logger.error("Invalid JSON format for config")
        return
    
    tags = {}
    if args.tags:
        try:
            tags = json.loads(args.tags)
        except json.JSONDecodeError:
            logger.error("Invalid JSON format for tags")
            return
    
    # Create job
    job = Job(
        name=args.name,
        type=args.type,
        config=config,
        job_id=str(uuid.uuid4()),
        priority=JobPriority[args.priority],
        tags=tags,
    )
    
    # Create queue config
    queue_config = JobQueueConfig(
        queue_url=args.queue_url,
        aws_region=args.region,
    )
    
    # Create queue
    queue = JobQueue(queue_config)
    
    # Submit job
    job_id = queue.submit_job(job)
    
    if job_id:
        print(f"Job submitted successfully with ID: {job_id}")
    else:
        print("Failed to submit job")


def handle_list_jobs(args: argparse.Namespace) -> None:
    """Handle the list-jobs command."""
    # Create queue config
    queue_config = JobQueueConfig(
        queue_url=args.queue_url,
        aws_region=args.region,
    )
    
    # Create queue
    queue = JobQueue(queue_config)
    
    # Receive jobs (but don't process them)
    job_tuples = queue.receive_jobs(max_messages=args.count)
    
    if not job_tuples:
        print("No jobs found in the queue")
        return
    
    # Print job details
    print(f"Found {len(job_tuples)} jobs:")
    for i, (job, receipt_handle) in enumerate(job_tuples, 1):
        print(f"\nJob {i}:")
        print(f"  ID: {job.job_id}")
        print(f"  Name: {job.name}")
        print(f"  Type: {job.type}")
        print(f"  Priority: {job.priority.name}")
        print(f"  Status: {job.status.name}")
        print(f"  Attempt: {job.attempt_count}")
        if job.created_at:
            print(f"  Created: {job.created_at}")
        if job.tags:
            print(f"  Tags: {job.tags}")
    
    # Keep the messages visible to others by not deleting them
    # In a real scenario, you might want to increase the visibility timeout
    # and then let them become visible again


def handle_queue_attributes(args: argparse.Namespace) -> None:
    """Handle the queue-attributes command."""
    # Create queue config
    queue_config = JobQueueConfig(
        queue_url=args.queue_url,
        aws_region=args.region,
    )
    
    # Create queue
    queue = JobQueue(queue_config)
    
    # Get queue attributes
    attributes = queue.get_queue_attributes()
    
    if attributes:
        print("Queue Attributes:")
        for key, value in attributes.items():
            print(f"  {key}: {value}")
    else:
        print("Failed to get queue attributes")


def main() -> None:
    """Main entry point for the CLI."""
    parser = create_parser()
    args = parser.parse_args()
    
    if args.command == "create-queue":
        handle_create_queue(args)
    elif args.command == "delete-queue":
        handle_delete_queue(args)
    elif args.command == "purge-queue":
        handle_purge_queue(args)
    elif args.command == "submit-job":
        handle_submit_job(args)
    elif args.command == "list-jobs":
        handle_list_jobs(args)
    elif args.command == "queue-attributes":
        handle_queue_attributes(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main() 