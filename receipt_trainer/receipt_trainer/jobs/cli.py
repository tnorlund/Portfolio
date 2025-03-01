"""
Command-line interface for managing ML training job queues.
"""

import argparse
import json
import logging
import sys
import uuid
import time
import os
from typing import Dict, Any, Optional, List, Tuple
from tabulate import tabulate
import yaml
from datetime import datetime

# Import service layer from receipt_dynamo
from receipt_dynamo import JobService, QueueService, InstanceService
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_status import JobStatus
from receipt_dynamo.entities.instance import Instance

# Local imports
from .job import JobPriority
from .job_definition import LayoutLMJobDefinition
from .config import (
    DYNAMODB_TABLE,
    DEFAULT_REGION,
    JOB_STATUS_PENDING,
    JOB_STATUS_CANCELLED,
    JOB_PRIORITY_MEDIUM,
)
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
    
    # Submit job from file command
    submit_file_parser = subparsers.add_parser("submit-job-file", help="Submit a job defined in a YAML/JSON file")
    submit_file_parser.add_argument("queue_url", help="URL of the queue")
    submit_file_parser.add_argument("job_file", help="Path to the job definition file (YAML or JSON)")
    submit_file_parser.add_argument("--priority", choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"], help="Override job priority")
    submit_file_parser.add_argument("--region", help="AWS region")
    
    # List pending jobs command
    list_parser = subparsers.add_parser("list-jobs", help="List jobs in the queue")
    list_parser.add_argument("queue_url", help="URL of the queue")
    list_parser.add_argument("--count", type=int, default=10, help="Maximum number of jobs to list")
    list_parser.add_argument("--region", help="AWS region")
    
    # Get queue attributes command
    attributes_parser = subparsers.add_parser("queue-attributes", help="Get queue attributes")
    attributes_parser.add_argument("queue_url", help="URL of the queue")
    attributes_parser.add_argument("--region", help="AWS region")
    
    # Get job status command
    status_parser = subparsers.add_parser("job-status", help="Get the status of a job")
    status_parser.add_argument("job_id", help="ID of the job")
    status_parser.add_argument("--table", help="DynamoDB table name for job status", default="JobStatus")
    status_parser.add_argument("--region", help="AWS region")
    
    # Monitor job command
    monitor_parser = subparsers.add_parser("monitor-job", help="Monitor a job's status in real-time")
    monitor_parser.add_argument("job_id", help="ID of the job")
    monitor_parser.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds")
    monitor_parser.add_argument("--table", help="DynamoDB table name for job status", default="JobStatus")
    monitor_parser.add_argument("--region", help="AWS region")
    
    # Cancel job command
    cancel_parser = subparsers.add_parser("cancel-job", help="Cancel a job")
    cancel_parser.add_argument("job_id", help="ID of the job")
    cancel_parser.add_argument("--table", help="DynamoDB table name for job status", default="JobStatus")
    cancel_parser.add_argument("--region", help="AWS region")
    
    # List job logs command
    logs_parser = subparsers.add_parser("job-logs", help="Get logs for a job")
    logs_parser.add_argument("job_id", help="ID of the job")
    logs_parser.add_argument("--table", help="DynamoDB table name for job logs", default="JobLogs")
    logs_parser.add_argument("--limit", type=int, default=20, help="Maximum number of log entries")
    logs_parser.add_argument("--region", help="AWS region")
    
    # List active instances command
    instances_parser = subparsers.add_parser("list-instances", help="List active training instances")
    instances_parser.add_argument("--table", help="DynamoDB table name for instances", default="Instances")
    instances_parser.add_argument("--region", help="AWS region")
    
    # List all jobs command (across DynamoDB)
    all_jobs_parser = subparsers.add_parser("list-all-jobs", help="List all jobs across all statuses")
    all_jobs_parser.add_argument("--status", choices=["pending", "running", "succeeded", "failed", "cancelled", "interrupted"], 
                               help="Filter by job status")
    all_jobs_parser.add_argument("--limit", type=int, default=20, help="Maximum number of jobs to list")
    all_jobs_parser.add_argument("--table", help="DynamoDB table name for jobs", default="Jobs")
    all_jobs_parser.add_argument("--region", help="AWS region")
    
    # Get job details command
    job_details_parser = subparsers.add_parser("job-details", help="Get detailed information about a job")
    job_details_parser.add_argument("job_id", help="ID of the job")
    job_details_parser.add_argument("--table", help="DynamoDB table name for jobs", default="Jobs")
    job_details_parser.add_argument("--region", help="AWS region")
    
    return parser


def handle_create_queue(args: argparse.Namespace) -> None:
    """Handle the create-queue command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create queue service
    queue_service = QueueService(region=region)
    
    tags = {}
    if args.tags:
        try:
            tags = json.loads(args.tags)
        except json.JSONDecodeError:
            print("Invalid JSON format for tags")
            logger.error("Invalid JSON format for tags")
            return
    
    try:
        queue_url, dlq_url = queue_service.create_queue_with_dlq(
            queue_name=args.queue_name,
            fifo=args.fifo,
            max_receives=args.max_receives,
            tags=tags
        )
        
        if queue_url and dlq_url:
            print(f"Queue URL: {queue_url}")
            print(f"DLQ URL: {dlq_url}")
        else:
            print("Failed to create queue")
    except Exception as e:
        print(f"Error creating queue: {str(e)}")
        logger.error(f"Error in handle_create_queue: {str(e)}")


def handle_delete_queue(args: argparse.Namespace) -> None:
    """Handle the delete-queue command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create queue service
    queue_service = QueueService(region=region)
    
    try:
        success = queue_service.delete_queue(args.queue_url)
        if success:
            print(f"Queue {args.queue_url} deleted successfully")
        else:
            print(f"Failed to delete queue {args.queue_url}")
    except Exception as e:
        print(f"Error deleting queue: {str(e)}")
        logger.error(f"Error in handle_delete_queue: {str(e)}")


def handle_purge_queue(args: argparse.Namespace) -> None:
    """Handle the purge-queue command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create queue service
    queue_service = QueueService(region=region)
    
    try:
        success = queue_service.purge_queue(args.queue_url)
        if success:
            print(f"Queue {args.queue_url} purged successfully")
        else:
            print(f"Failed to purge queue {args.queue_url}")
    except Exception as e:
        print(f"Error purging queue: {str(e)}")
        logger.error(f"Error in handle_purge_queue: {str(e)}")


def handle_submit_job(args: argparse.Namespace) -> None:
    """Handle the submit-job command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Parse job config
    try:
        config = json.loads(args.config)
    except json.JSONDecodeError:
        logger.error("Invalid JSON format for config")
        return
    
    # Parse tags
    tags = {}
    if args.tags:
        try:
            tags = json.loads(args.tags)
        except json.JSONDecodeError:
            logger.error("Invalid JSON format for tags")
            return
    
    # Generate job ID
    job_id = str(uuid.uuid4())
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    try:
        # Create the job
        job = job_service.create_job(
            job_id=job_id,
            name=args.name,
            description=f"Job of type {args.type}",
            created_by=args.user if hasattr(args, 'user') else "cli-user",
            status=JOB_STATUS_PENDING,
            priority=args.priority.lower() if hasattr(args, 'priority') else JOB_PRIORITY_MEDIUM,
            job_config=config,
            tags=tags,
        )
        
        # Add initial status
        job_service.add_job_status(
            job_id=job_id,
            status=JOB_STATUS_PENDING,
            message="Job created",
        )
        
        print(f"Job submitted successfully with ID: {job_id}")
        
    except Exception as e:
        logger.error(f"Error submitting job: {str(e)}")
        print(f"Failed to submit job: {str(e)}")
        return
    
    # If a queue URL was provided, also add the job to the queue
    if hasattr(args, 'queue_url') and args.queue_url:
        try:
            # Create queue service
            queue_service = QueueService(table_name=DYNAMODB_TABLE, region=region)
            
            # Extract queue ID from URL (assuming it's the last part of the URL)
            queue_url_parts = args.queue_url.split('/')
            queue_id = queue_url_parts[-1]
            
            # Add job to queue
            priority = 0
            if hasattr(args, 'priority'):
                if args.priority.upper() == "LOW":
                    priority = 0
                elif args.priority.upper() == "MEDIUM":
                    priority = 10
                elif args.priority.upper() == "HIGH":
                    priority = 20
                elif args.priority.upper() == "CRITICAL":
                    priority = 30
            
            queue_job = queue_service.add_job_to_queue(
                queue_id=queue_id,
                job_id=job_id,
                priority=priority,
                metadata={"source": "cli"}
            )
            
            print(f"Job added to queue {queue_id}")
            
        except Exception as e:
            logger.error(f"Error adding job to queue: {str(e)}")
            print(f"Warning: Job created but could not be added to queue: {str(e)}")


def handle_submit_job_file(args: argparse.Namespace) -> None:
    """Handle the submit-job-file command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create services
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    queue_service = QueueService(region=region)
    
    # Check if file exists
    if not os.path.exists(args.job_file):
        print(f"Job definition file {args.job_file} not found")
        logger.error(f"Job definition file {args.job_file} not found")
        return
    
    try:
        # Determine file type by extension
        if args.job_file.lower().endswith('.yaml') or args.job_file.lower().endswith('.yml'):
            job_definition = LayoutLMJobDefinition.from_yaml(args.job_file)
        elif args.job_file.lower().endswith('.json'):
            job_definition = LayoutLMJobDefinition.from_json(args.job_file)
        else:
            print("Unsupported file format. Use YAML or JSON files.")
            logger.error("Unsupported file format. Use YAML or JSON files.")
            return
        
        # Convert job definition to job config
        job_config = job_definition.to_job_config()
        
        # Create job
        job_id = job_service.create_job(
            name=job_definition.name,
            job_type="layoutlm_training",
            config=job_config,
            priority=args.priority.upper() if args.priority else "MEDIUM",
            tags=job_definition.tags if job_definition.tags else {},
            description=job_definition.description,
            created_by="CLI"
        )
        
        if job_id:
            print(f"Job '{job_definition.name}' created successfully with ID: {job_id}")
            
            # Add job to queue if queue_url is provided
            if args.queue_url:
                try:
                    # Extract queue ID from URL (assuming it's the last part of the URL)
                    queue_url_parts = args.queue_url.split('/')
                    queue_id = queue_url_parts[-1]
                    
                    # Map priority string to numeric value
                    priority_value = 0
                    if args.priority:
                        if args.priority.upper() == "LOW":
                            priority_value = 0
                        elif args.priority.upper() == "MEDIUM":
                            priority_value = 10
                        elif args.priority.upper() == "HIGH":
                            priority_value = 20
                        elif args.priority.upper() == "CRITICAL":
                            priority_value = 30
                    
                    # Add job to queue
                    queue_service.add_job_to_queue(
                        queue_url=args.queue_url,
                        job_id=job_id,
                        priority=priority_value,
                        metadata={"source": "cli"}
                    )
                    
                    print(f"Job added to queue {queue_id}")
                except Exception as e:
                    print(f"Warning: Job created but could not be added to queue: {str(e)}")
                    logger.error(f"Error adding job to queue: {str(e)}")
        else:
            print("Failed to create job")
            
    except Exception as e:
        print(f"Error submitting job: {str(e)}")
        logger.error(f"Error in handle_submit_job_file: {str(e)}")


def handle_list_jobs(args: argparse.Namespace) -> None:
    """Handle the list-jobs command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    # List jobs
    limit = args.count if hasattr(args, 'count') else 20
    
    if hasattr(args, 'status') and args.status:
        jobs, last_key = job_service.list_jobs_by_status(args.status, limit)
    elif hasattr(args, 'user') and args.user:
        jobs, last_key = job_service.list_jobs_by_user(args.user, limit)
    else:
        jobs, last_key = job_service.list_jobs(limit)
    
    if not jobs:
        print("No jobs found")
        return
    
    # Prepare table data
    table_data = []
    for job in jobs:
        # Get the latest status
        job_statuses = job_service.get_job_status_history(job.job_id)
        latest_status = job_statuses[0].status if job_statuses else job.status
        
        # Add to table data
        table_data.append([
            job.job_id,
            job.name,
            job.description[:30] + "..." if len(job.description) > 30 else job.description,
            latest_status,
            job.priority,
            job.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            job.created_by,
        ])
    
    # Print table
    headers = ["Job ID", "Name", "Description", "Status", "Priority", "Created At", "Created By"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    
    # If there are more results, indicate it
    if last_key:
        print("\nMore results available. Use pagination to view more.")


def handle_queue_attributes(args: argparse.Namespace) -> None:
    """Handle the queue-attributes command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create queue service
    queue_service = QueueService(region=region)
    
    try:
        # Get queue attributes
        attributes = queue_service.get_queue_attributes(args.queue_url)
        
        if attributes:
            print("Queue Attributes:")
            for key, value in attributes.items():
                print(f"  {key}: {value}")
        else:
            print("Failed to get queue attributes")
    except Exception as e:
        print(f"Error getting queue attributes: {str(e)}")
        logger.error(f"Error in handle_queue_attributes: {str(e)}")


def handle_job_status(args: argparse.Namespace) -> None:
    """Handle the job-status command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    try:
        # Get job statuses
        statuses = job_service.get_job_status_history(args.job_id)
        
        if statuses:
            # Sort by timestamp descending to get most recent first
            statuses.sort(key=lambda s: s.timestamp, reverse=True)
            latest_status = statuses[0]
            
            print(f"Job ID: {args.job_id}")
            print(f"Current Status: {latest_status.status}")
            print(f"Last Updated: {latest_status.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Status Message: {latest_status.message}")
            
            if len(statuses) > 1:
                print("\nStatus History:")
                for status in statuses[1:]:  # Skip the first one as we already displayed it
                    print(f"  {status.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {status.status}: {status.message}")
        else:
            # If no status history, try to get the job itself
            try:
                job = job_service.get_job(args.job_id)
                print(f"Job ID: {args.job_id}")
                print(f"Job exists but has no status history.")
                print(f"Status from job record: {job.status}")
            except Exception:
                print(f"No status history found for job {args.job_id}")
                print(f"Job may not exist or has not been updated with status information.")
    
    except Exception as e:
        print(f"Error retrieving job status: {str(e)}")
        logger.error(f"Error in handle_job_status: {str(e)}")


def handle_monitor_job(args: argparse.Namespace) -> None:
    """Handle the monitor-job command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    # How often to refresh the status (in seconds)
    refresh_interval = args.interval if hasattr(args, 'interval') else 5
    
    try:
        print(f"Monitoring job {args.job_id}. Press Ctrl+C to stop...")
        
        try:
            while True:
                # Clear the screen (works on most terminals)
                os.system('cls' if os.name == 'nt' else 'clear')
                
                # Get job with status
                job = job_service.get_job_with_status(args.job_id)
                
                if job:
                    print(f"Job ID: {job.job_id}")
                    print(f"Name: {job.name}")
                    print(f"Type: {job.job_type}")
                    print(f"Status: {job.status}")
                    print(f"Created: {job.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    # Get status history
                    statuses = job_service.get_job_status_history(args.job_id)
                    if statuses:
                        statuses.sort(key=lambda s: s.timestamp, reverse=True)
                        latest_status = statuses[0]
                        
                        print(f"Latest Status Update: {latest_status.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
                        print(f"Status Message: {latest_status.message}")
                        if hasattr(latest_status, 'progress') and latest_status.progress is not None:
                            print(f"Progress: {latest_status.progress}%")
                        if hasattr(latest_status, 'instance_id') and latest_status.instance_id:
                            print(f"Processing Instance: {latest_status.instance_id}")
                        
                        # Show recent history
                        if len(statuses) > 1:
                            print("\nRecent Status History:")
                            for status in statuses[1:6]:  # Show up to 5 previous statuses
                                print(f"  {status.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {status.status}: {status.message}")
                    
                    # Check if job is in a terminal state
                    if job.status in ["completed", "failed", "cancelled", "error"]:
                        print(f"\nJob has reached terminal state: {job.status}")
                        print("Monitoring will end in 10 seconds...")
                        time.sleep(10)
                        break
                else:
                    print(f"No information found for job {args.job_id}")
                
                # Wait before refreshing
                time.sleep(refresh_interval)
                
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user")
            
    except Exception as e:
        print(f"Error monitoring job: {str(e)}")
        logger.error(f"Error in handle_monitor_job: {str(e)}")


def handle_cancel_job(args: argparse.Namespace) -> None:
    """Handle the cancel-job command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    try:
        # Cancel the job
        job_service.cancel_job(
            job_id=args.job_id, 
            message="Job cancelled by user via CLI",
            updated_by="CLI"
        )
        
        print(f"Job {args.job_id} has been marked for cancellation")
        print("Note: The job will be cancelled when the processing instance receives the cancellation signal")
        
    except Exception as e:
        print(f"Error cancelling job: {str(e)}")
        logger.error(f"Error in handle_cancel_job: {str(e)}")


def handle_job_logs(args: argparse.Namespace) -> None:
    """Handle the job-logs command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    try:
        # Get logs for the job
        logs = job_service.get_job_logs(args.job_id, limit=args.limit)
        
        if logs:
            print(f"Logs for job {args.job_id} (showing up to {args.limit} entries):")
            print("-" * 80)
            
            for log in logs:
                timestamp = log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(log, 'timestamp') else 'unknown'
                level = log.level if hasattr(log, 'level') else 'INFO'
                message = log.message if hasattr(log, 'message') else 'No message'
                source = log.source if hasattr(log, 'source') else 'unknown'
                
                print(f"[{timestamp}] [{level}] [{source}]: {message}")
                
            if len(logs) == args.limit:
                print(f"\nShowing first {args.limit} logs. Use --limit to see more.")
        else:
            print(f"No logs found for job {args.job_id}")
            
    except Exception as e:
        print(f"Error retrieving job logs: {str(e)}")
        logger.error(f"Error in handle_job_logs: {str(e)}")


def handle_list_instances(args: argparse.Namespace) -> None:
    """Handle the list-instances command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create instance service
    instance_service = InstanceService(table_name=DYNAMODB_TABLE, region=region)
    
    try:
        # Get all instances
        instances = instance_service.list_instances()
        
        if instances:
            headers = ["Instance ID", "Instance Type", "Status", "Last Heartbeat", "Current Job", "Uptime (hrs)"]
            rows = []
            
            for instance in instances:
                # Calculate uptime if start_time is available
                uptime = "N/A"
                if hasattr(instance, 'start_time') and instance.start_time:
                    try:
                        now = datetime.now()
                        uptime_seconds = (now - instance.start_time).total_seconds()
                        uptime = f"{uptime_seconds / 3600:.1f}"
                    except:
                        pass
                
                last_heartbeat = instance.last_heartbeat.strftime('%Y-%m-%d %H:%M:%S') if hasattr(instance, 'last_heartbeat') and instance.last_heartbeat else "N/A"
                current_job = instance.current_job_id if hasattr(instance, 'current_job_id') and instance.current_job_id else "None"
                
                rows.append([
                    instance.instance_id, 
                    instance.instance_type, 
                    instance.status, 
                    last_heartbeat, 
                    current_job, 
                    uptime
                ])
            
            print(tabulate(rows, headers=headers, tablefmt="grid"))
            print(f"\nTotal instances: {len(instances)}")
        else:
            print("No instances found")
            
    except Exception as e:
        print(f"Error listing instances: {str(e)}")
        logger.error(f"Error in handle_list_instances: {str(e)}")


def handle_list_all_jobs(args: argparse.Namespace) -> None:
    """Handle the list-all-jobs command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    try:
        # Get jobs based on status filter if provided
        if args.status:
            jobs = job_service.list_jobs_by_status(args.status, limit=args.limit)
        else:
            jobs = job_service.list_jobs(limit=args.limit)
        
        if jobs:
            headers = ["Job ID", "Name", "Type", "Status", "Created At", "Duration", "Creator"]
            rows = []
            
            for job in jobs:
                # Calculate duration if completed
                duration = "N/A"
                if hasattr(job, 'completed_at') and job.completed_at and hasattr(job, 'created_at') and job.created_at:
                    try:
                        duration_seconds = (job.completed_at - job.created_at).total_seconds()
                        # Format duration as hours:minutes:seconds
                        hours, remainder = divmod(duration_seconds, 3600)
                        minutes, seconds = divmod(remainder, 60)
                        duration = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
                    except:
                        pass
                
                created_at = job.created_at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(job, 'created_at') and job.created_at else "N/A"
                creator = job.created_by if hasattr(job, 'created_by') and job.created_by else "N/A"
                
                rows.append([
                    job.job_id, 
                    job.name, 
                    job.job_type, 
                    job.status, 
                    created_at, 
                    duration,
                    creator
                ])
            
            print(tabulate(rows, headers=headers, tablefmt="grid"))
            print(f"\nTotal jobs: {len(jobs)}")
            
            if len(jobs) == args.limit:
                print(f"Showing first {args.limit} jobs. Use --limit to see more.")
        else:
            print("No jobs found")
            
    except Exception as e:
        print(f"Error listing jobs: {str(e)}")
        logger.error(f"Error in handle_list_all_jobs: {str(e)}")


def handle_job_details(args: argparse.Namespace) -> None:
    """Handle the job-details command using the service layer."""
    region = args.region if hasattr(args, 'region') and args.region else DEFAULT_REGION
    
    # Create job service
    job_service = JobService(table_name=DYNAMODB_TABLE, region=region)
    
    try:
        # Get job and its status history
        job, statuses = job_service.get_job_with_status(args.job_id)
        
        print(f"===== Job Details: {args.job_id} =====")
        print(f"Name: {job.name}")
        print(f"Description: {job.description}")
        print(f"Status: {job.status}")
        print(f"Priority: {job.priority}")
        print(f"Created At: {job.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Created By: {job.created_by}")
        
        if job.tags:
            print("\nTags:")
            for key, value in job.tags.items():
                print(f"  {key}: {value}")
        
        # Print status history
        if statuses:
            print("\nStatus History:")
            for status in sorted(statuses, key=lambda s: s.timestamp, reverse=True):
                print(f"  {status.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {status.status}: {status.message}")
        
        # Print configuration summary
        print("\nConfiguration:")
        config_str = json.dumps(job.job_config, indent=2)
        if len(config_str) > 500:
            print(f"{config_str[:500]}...\n(truncated)")
        else:
            print(config_str)
        
        # Get job logs
        try:
            logs = job_service.get_job_logs(args.job_id)
            if logs:
                print("\nLogs:")
                for log in sorted(logs, key=lambda l: l.timestamp, reverse=True)[:10]:
                    print(f"  {log.timestamp.strftime('%Y-%m-%d %H:%M:%S')} [{log.log_level}] {log.message}")
                if len(logs) > 10:
                    print(f"  ... and {len(logs) - 10} more logs")
        except Exception as e:
            logger.warning(f"Could not fetch logs: {str(e)}")
        
        # Get metrics
        try:
            metrics = job_service.get_job_metrics(args.job_id)
            if metrics:
                print("\nMetrics:")
                metrics_table = []
                for metric in sorted(metrics, key=lambda m: m.timestamp, reverse=True)[:10]:
                    metrics_table.append([
                        metric.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                        metric.metric_name,
                        metric.metric_value,
                    ])
                print(tabulate(metrics_table, headers=["Timestamp", "Metric", "Value"], tablefmt="simple"))
                if len(metrics) > 10:
                    print(f"  ... and {len(metrics) - 10} more metrics")
        except Exception as e:
            logger.warning(f"Could not fetch metrics: {str(e)}")
        
        # Get checkpoints
        try:
            checkpoints = job_service.get_job_checkpoints(args.job_id)
            if checkpoints:
                print("\nCheckpoints:")
                for checkpoint in sorted(checkpoints, key=lambda c: c.created_at, reverse=True):
                    print(f"  {checkpoint.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {checkpoint.checkpoint_name}")
        except Exception as e:
            logger.warning(f"Could not fetch checkpoints: {str(e)}")
        
    except Exception as e:
        print(f"Error retrieving job details: {str(e)}")
        logger.error(f"Error in handle_job_details: {str(e)}")


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
    elif args.command == "submit-job-file":
        handle_submit_job_file(args)
    elif args.command == "list-jobs":
        handle_list_jobs(args)
    elif args.command == "queue-attributes":
        handle_queue_attributes(args)
    elif args.command == "job-status":
        handle_job_status(args)
    elif args.command == "monitor-job":
        handle_monitor_job(args)
    elif args.command == "cancel-job":
        handle_cancel_job(args)
    elif args.command == "job-logs":
        handle_job_logs(args)
    elif args.command == "list-instances":
        handle_list_instances(args)
    elif args.command == "list-all-jobs":
        handle_list_all_jobs(args)
    elif args.command == "job-details":
        handle_job_details(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main() 