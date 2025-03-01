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
from typing import Dict, Any, Optional, List
from tabulate import tabulate
import yaml

from .job import Job, JobStatus, JobPriority
from .queue import JobQueue, JobQueueConfig, JobRetryStrategy
from .job_definition import LayoutLMJobDefinition
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
    monitor_parser.add_argument("--refresh", type=int, default=5, help="Refresh interval in seconds")
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


def handle_submit_job_file(args: argparse.Namespace) -> None:
    """Handle the submit-job-file command."""
    # Check if file exists
    if not os.path.exists(args.job_file):
        logger.error(f"Job definition file {args.job_file} not found")
        return
    
    try:
        # Determine file type by extension
        if args.job_file.lower().endswith('.yaml') or args.job_file.lower().endswith('.yml'):
            job_definition = LayoutLMJobDefinition.from_yaml(args.job_file)
        elif args.job_file.lower().endswith('.json'):
            job_definition = LayoutLMJobDefinition.from_json(args.job_file)
        else:
            logger.error("Unsupported file format. Use YAML or JSON files.")
            return
        
        # Convert job definition to job config
        job_config = job_definition.to_job_config()
        
        # Create job
        job = Job(
            name=job_definition.name,
            type="layoutlm_training",
            config=job_config,
            job_id=str(uuid.uuid4()),
            priority=JobPriority[args.priority] if args.priority else JobPriority.MEDIUM,
            tags={"name": job_definition.name} if job_definition.tags else {},
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
            print(f"Job '{job_definition.name}' submitted successfully with ID: {job_id}")
            print(f"Job description: {job_definition.description}")
        else:
            print("Failed to submit job")
            
    except Exception as e:
        logger.error(f"Error submitting job: {str(e)}")


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


def handle_job_status(args: argparse.Namespace) -> None:
    """Handle the job-status command."""
    # Note: This is a stub implementation. In a real implementation,
    # you would query DynamoDB to get the job status
    try:
        # Import the necessary modules
        from receipt_dynamo.entities.job_status import JobStatus as DynamoJobStatus
        import boto3
        from boto3.dynamodb.conditions import Key
        
        # Create DynamoDB client
        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(args.table)
        
        # Query for the latest status of the job
        response = table.query(
            KeyConditionExpression=Key('PK').eq(f"JOB#{args.job_id}"),
            ScanIndexForward=False,  # Sort descending by SK to get the latest status first
            Limit=1
        )
        
        if response['Items']:
            status_item = response['Items'][0]
            print(f"Job ID: {args.job_id}")
            print(f"Status: {status_item.get('status', 'unknown')}")
            print(f"Updated At: {status_item.get('updated_at', 'unknown')}")
            print(f"Progress: {status_item.get('progress', 'N/A')}%") if 'progress' in status_item else print("Progress: N/A")
            print(f"Message: {status_item.get('message', 'N/A')}")
            print(f"Instance: {status_item.get('instance_id', 'N/A')}")
        else:
            print(f"No status found for job {args.job_id}")
            
    except ImportError:
        print("receipt_dynamo package not installed or configured properly")
    except Exception as e:
        logger.error(f"Error getting job status: {str(e)}")


def handle_monitor_job(args: argparse.Namespace) -> None:
    """Handle the monitor-job command."""
    try:
        # Import the necessary modules
        from receipt_dynamo.entities.job_status import JobStatus as DynamoJobStatus
        import boto3
        from boto3.dynamodb.conditions import Key
        
        # Create DynamoDB client
        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(args.table)
        
        print(f"Monitoring job {args.job_id}. Press Ctrl+C to stop...")
        
        try:
            while True:
                # Clear the screen (works on most terminals)
                os.system('cls' if os.name == 'nt' else 'clear')
                
                # Query for the latest status of the job
                response = table.query(
                    KeyConditionExpression=Key('PK').eq(f"JOB#{args.job_id}"),
                    ScanIndexForward=False,
                    Limit=1
                )
                
                if response['Items']:
                    status_item = response['Items'][0]
                    status = status_item.get('status', 'unknown')
                    
                    print(f"=== Job {args.job_id} ===")
                    print(f"Status: {status}")
                    print(f"Updated At: {status_item.get('updated_at', 'unknown')}")
                    if 'progress' in status_item:
                        progress = float(status_item['progress'])
                        bar_length = 40
                        filled_length = int(bar_length * progress / 100)
                        bar = '█' * filled_length + '-' * (bar_length - filled_length)
                        print(f"Progress: [{bar}] {progress:.1f}%")
                    else:
                        print("Progress: N/A")
                    print(f"Message: {status_item.get('message', 'N/A')}")
                    print(f"Instance: {status_item.get('instance_id', 'N/A')}")
                    
                    # If job is in a terminal state, break the loop
                    if status in ['succeeded', 'failed', 'cancelled']:
                        print(f"\nJob has {status}. Monitoring stopped.")
                        break
                else:
                    print(f"No status found for job {args.job_id}")
                
                print(f"\nRefreshing in {args.refresh} seconds... (Ctrl+C to stop)")
                time.sleep(args.refresh)
                
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user.")
            
    except ImportError:
        print("receipt_dynamo package not installed or configured properly")
    except Exception as e:
        logger.error(f"Error monitoring job: {str(e)}")


def handle_cancel_job(args: argparse.Namespace) -> None:
    """Handle the cancel-job command."""
    try:
        # Import the necessary modules
        from receipt_dynamo.entities.job_status import JobStatus as DynamoJobStatus
        import boto3
        from datetime import datetime
        
        # Create DynamoDB client
        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(args.table)
        
        # Create a new job status item for cancellation
        job_status = DynamoJobStatus(
            job_id=args.job_id,
            status="cancelled",
            updated_at=datetime.now(),
            progress=None,
            message="Job cancelled by user via CLI",
            updated_by="CLI",
            instance_id=None
        )
        
        # Put the item in DynamoDB
        table.put_item(Item=job_status.to_item())
        
        print(f"Job {args.job_id} has been marked for cancellation")
        print("Note: The job will be cancelled when the processing instance receives the cancellation signal")
        
    except ImportError:
        print("receipt_dynamo package not installed or configured properly")
    except Exception as e:
        logger.error(f"Error cancelling job: {str(e)}")


def handle_job_logs(args: argparse.Namespace) -> None:
    """Handle the job-logs command."""
    try:
        # Import the necessary modules
        import boto3
        from boto3.dynamodb.conditions import Key
        
        # Create DynamoDB client
        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(args.table)
        
        # Query for logs of the job
        response = table.query(
            KeyConditionExpression=Key('PK').eq(f"JOB#{args.job_id}"),
            ScanIndexForward=True,  # Sort ascending to get logs in chronological order
            Limit=args.limit
        )
        
        if response['Items']:
            print(f"Logs for job {args.job_id} (showing up to {args.limit} entries):")
            print("-" * 80)
            
            for log in response['Items']:
                timestamp = log.get('timestamp', 'unknown')
                level = log.get('level', 'INFO')
                message = log.get('message', 'No message')
                source = log.get('source', 'unknown')
                
                print(f"[{timestamp}] [{level}] [{source}]: {message}")
                
            if len(response['Items']) == args.limit:
                print(f"\nShowing first {args.limit} logs. Use --limit to see more.")
        else:
            print(f"No logs found for job {args.job_id}")
            
    except Exception as e:
        logger.error(f"Error retrieving job logs: {str(e)}")


def handle_list_instances(args: argparse.Namespace) -> None:
    """Handle the list-instances command."""
    try:
        # Import the necessary modules
        import boto3
        from boto3.dynamodb.conditions import Key
        
        # Create DynamoDB client
        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(args.table)
        
        # Scan for active instances
        response = table.scan()
        
        if response['Items']:
            instances = response['Items']
            headers = ["Instance ID", "Instance Type", "Status", "Last Heartbeat", "Current Job", "Uptime (hrs)"]
            rows = []
            
            for instance in instances:
                instance_id = instance.get('instance_id', 'unknown')
                instance_type = instance.get('instance_type', 'unknown')
                status = instance.get('status', 'unknown')
                last_heartbeat = instance.get('last_heartbeat', 'unknown')
                current_job = instance.get('current_job_id', 'None')
                
                # Calculate uptime if start_time is available
                uptime = "N/A"
                if 'start_time' in instance:
                    try:
                        start_time = datetime.fromisoformat(instance['start_time'])
                        now = datetime.now()
                        uptime_seconds = (now - start_time).total_seconds()
                        uptime = f"{uptime_seconds / 3600:.1f}"
                    except:
                        pass
                
                rows.append([instance_id, instance_type, status, last_heartbeat, current_job, uptime])
            
            print(tabulate(rows, headers=headers, tablefmt="grid"))
            print(f"\nTotal instances: {len(instances)}")
        else:
            print("No active training instances found")
            
    except Exception as e:
        logger.error(f"Error listing instances: {str(e)}")


def handle_list_all_jobs(args: argparse.Namespace) -> None:
    """Handle the list-all-jobs command."""
    try:
        # Import the necessary modules
        import boto3
        from boto3.dynamodb.conditions import Key
        
        # Create DynamoDB client
        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(args.table)
        
        if args.status:
            # Query the GSI1 index for jobs with the specified status
            response = table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key('GSI1PK').eq(f"STATUS#{args.status}"),
                Limit=args.limit
            )
        else:
            # Scan for all jobs
            response = table.scan(Limit=args.limit)
        
        if response['Items']:
            jobs = response['Items']
            headers = ["Job ID", "Name", "Type", "Status", "Created At", "Duration"]
            rows = []
            
            for job in jobs:
                job_id = job.get('job_id', 'unknown')
                name = job.get('name', 'unknown')
                job_type = job.get('type', 'unknown')
                status = job.get('status', 'unknown')
                created_at = job.get('created_at', 'unknown')
                
                # Calculate duration if start_time and completed_at are available
                duration = "N/A"
                if 'started_at' in job:
                    if 'completed_at' in job:
                        try:
                            started = float(job['started_at'])
                            completed = float(job['completed_at'])
                            duration_seconds = completed - started
                            if duration_seconds < 60:
                                duration = f"{duration_seconds:.1f}s"
                            elif duration_seconds < 3600:
                                duration = f"{duration_seconds / 60:.1f}m"
                            else:
                                duration = f"{duration_seconds / 3600:.1f}h"
                        except:
                            pass
                    else:
                        duration = "Running"
                
                rows.append([job_id, name, job_type, status, created_at, duration])
            
            print(tabulate(rows, headers=headers, tablefmt="grid"))
            print(f"\nShowing {len(jobs)} jobs" + (f" with status '{args.status}'" if args.status else ""))
            
            if len(jobs) == args.limit:
                print(f"Results limited to {args.limit} jobs. Use --limit to show more.")
        else:
            print("No jobs found" + (f" with status '{args.status}'" if args.status else ""))
            
    except Exception as e:
        logger.error(f"Error listing jobs: {str(e)}")


def handle_job_details(args: argparse.Namespace) -> None:
    """Handle the job-details command."""
    try:
        # Import the necessary modules
        import boto3
        from boto3.dynamodb.conditions import Key
        import json
        
        # Create DynamoDB client
        dynamodb = boto3.resource('dynamodb', region_name=args.region)
        table = dynamodb.Table(args.table)
        
        # Get the job item
        response = table.get_item(
            Key={
                'PK': f"JOB#{args.job_id}",
                'SK': f"JOB#{args.job_id}"
            }
        )
        
        if 'Item' in response:
            job = response['Item']
            
            print(f"===== Job Details: {args.job_id} =====")
            print(f"Name: {job.get('name', 'N/A')}")
            print(f"Type: {job.get('type', 'N/A')}")
            print(f"Status: {job.get('status', 'N/A')}")
            print(f"Priority: {job.get('priority', 'N/A')}")
            print(f"Created At: {job.get('created_at', 'N/A')}")
            
            if 'started_at' in job:
                print(f"Started At: {job.get('started_at', 'N/A')}")
            
            if 'completed_at' in job:
                print(f"Completed At: {job.get('completed_at', 'N/A')}")
            
            print(f"Attempt Count: {job.get('attempt_count', 'N/A')}")
            
            if 'error_message' in job:
                print(f"Error Message: {job.get('error_message', 'N/A')}")
            
            if 'tags' in job:
                print("\nTags:")
                for key, value in job['tags'].items():
                    print(f"  {key}: {value}")
            
            if 'dependencies' in job and job['dependencies']:
                print("\nDependencies:")
                for dep in job['dependencies']:
                    print(f"  {dep}")
            
            if 'config' in job:
                print("\nConfiguration Summary:")
                try:
                    if isinstance(job['config'], str):
                        config = json.loads(job['config'])
                    else:
                        config = job['config']
                    
                    # Print a summary of the most important config values
                    if 'model' in config:
                        print(f"  Model: {config['model'].get('type', 'N/A')} {config['model'].get('version', 'N/A')}")
                        print(f"  Pretrained Model: {config['model'].get('pretrained_model_name', 'N/A')}")
                    
                    if 'training' in config:
                        print(f"  Epochs: {config['training'].get('epochs', 'N/A')}")
                        print(f"  Batch Size: {config['training'].get('batch_size', 'N/A')}")
                        print(f"  Learning Rate: {config['training'].get('learning_rate', 'N/A')}")
                    
                    if 'resources' in config:
                        print(f"  Instance Type: {config['resources'].get('instance_type', 'N/A')}")
                        print(f"  GPU Count: {config['resources'].get('min_gpu_count', 'N/A')}")
                        print(f"  Spot Instance: {config['resources'].get('spot_instance', 'N/A')}")
                    
                    # Option to dump the full config
                    print("\nUse '--full-config' to see the complete configuration (not implemented yet)")
                    
                except Exception as e:
                    print(f"  Error parsing config: {str(e)}")
            
        else:
            print(f"No job found with ID {args.job_id}")
            
    except Exception as e:
        logger.error(f"Error retrieving job details: {str(e)}")


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