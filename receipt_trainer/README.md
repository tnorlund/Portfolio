# Receipt Trainer

A Python package for training LayoutLM on AWS.

## Architecture Overview

The `receipt_trainer` package integrates with AWS services and the DynamoDB table design to provide a robust system for training machine learning models on receipt data. The system is designed to handle spot instance interruptions gracefully while maintaining training progress.

## Training Job Workflow

1. Job Queue Polling

- Poll the SQS job queue to retrieve the next available job

2. Job Initialization

- Query DynamoDB for the job configuration using the job ID
- Check if this is a new job or a resumed job
- For resumed jobs, locate the latest checkpoint in /mnt/checkpoints or S3
- Set the appropriate starting epoch, step, and parameters

3. Checkpointing and Status Updates

- Regularly update the job status in DynamoDB
- Save checkpoints with detailed metadata including epoch, step, and metrics
- Increase checkpoint frequency when approaching expected spot termination
- Use atomic file operations to ensure checkpoints aren't corrupted by interruptions

4. Graceful Interruption Handling:

- Implement a signal handler for spot termination notifications
- Perform an emergency checkpoint when a termination signal is received
- Update the job status to "INTERRUPTED" in DynamoDB
- Release the job back to the queue with its progress information

## Working with SQS Job Queue

The training infrastructure uses AWS SQS FIFO queues to manage job distribution. This section explains how to properly interact with the queues.

### SQS Queue Configuration

The job queue defined in the Pulumi infrastructure has the following key characteristics:

- **FIFO Queue**: Ensures jobs are processed in order and once only
- **Content-Based Deduplication**: Prevents duplicate job submissions
- **Visibility Timeout**: Set to 30 minutes (1800 seconds)
- **Message Retention**: 4 days (345600 seconds)
- **Dead Letter Queue**: Messages are sent to DLQ after 5 failed processing attempts

## Creating and submitting a job

```python
import json
import uuid
import time
import boto3
from datetime import datetime

# Load environment configuration
def load_environment(env='dev'):
    """Load environment configuration"""
    # This would normally come from a configuration file or environment variables
    # The values below are just examples
    return {
        'job_queue_url': 'https://sqs.us-east-1.amazonaws.com/123456789012/ml-training-job-queue-dev.fifo',
        'queue_id': 'main-training-queue',  # Logical queue ID in DynamoDB
        'dynamo_table': 'ReceiptsTable',
        'region': 'us-east-1'
    }

def create_training_job(
    model_name,
    dataset_id,
    batch_size=8,
    learning_rate=5e-5,
    num_epochs=3,
    priority=0
):
    """Create a training job definition"""
    job_id = str(uuid.uuid4())

    job = {
        'job_id': job_id,
        'name': f"Training {model_name} on {dataset_id}",
        'type': 'training',
        'created_at': datetime.utcnow().isoformat(),
        'status': 'PENDING',
        'priority': priority,
        'config': {
            'model': {
                'name': model_name,
                'type': 'layoutlm',
            },
            'training': {
                'batch_size': batch_size,
                'learning_rate': learning_rate,
                'num_epochs': num_epochs,
                'optimizer': 'adamw',
                'weight_decay': 0.01,
                'warmup_steps': 0,
                'max_grad_norm': 1.0,
            },
            'dataset': {
                'id': dataset_id,
                'type': 'receipts',
            }
        }
    }

    return job

def submit_job_to_sqs(job_id, queue_url, model_name, region=None):
    """
    Submit a lightweight job reference to the SQS queue.

    Unlike storing the entire job in SQS, we only send the job_id as
    the actual job data is stored in DynamoDB.
    """
    sqs_client = boto3.client('sqs', region_name=region)

    # Create a simplified message with just the job ID
    message = {
        'job_id': job_id,
        'enqueued_at': datetime.utcnow().isoformat()
    }

    response = sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
        MessageGroupId=f"job-{model_name}",  # Group by model type
        MessageDeduplicationId=f"{job_id}-{int(time.time())}"  # Ensure unique
    )

    print(f"Submitted job {job_id} to SQS. Message ID: {response.get('MessageId')}")
    return response

def store_job_in_dynamo(job, table_name, region=None):
    """Store job definition in DynamoDB"""
    dynamodb = boto3.client('dynamodb', region_name=region)

    item = {
        'PK': {'S': f"JOB#{job['job_id']}"},
        'SK': {'S': 'JOB'},
        'GSI1PK': {'S': f"STATUS#{job['status']}"},
        'GSI1SK': {'S': f"CREATED#{job['created_at']}"},
        'TYPE': {'S': 'JOB'},
        'name': {'S': job['name']},
        'description': {'S': f"Training job for {job['config']['model']['name']}"},
        'created_at': {'S': job['created_at']},
        'status': {'S': job['status']},
        'priority': {'N': str(job['priority'])},
        'job_config': {'S': json.dumps(job['config'])}
    }

    response = dynamodb.put_item(
        TableName=table_name,
        Item=item
    )

    print(f"Stored job {job['job_id']} in DynamoDB")
    return response

def add_job_to_queue_in_dynamo(job_id, queue_id, priority, table_name, region=None):
    """
    Create a Queue Job entity in DynamoDB to track the relationship
    between the job and queue.
    """
    dynamodb = boto3.client('dynamodb', region_name=region)

    now = datetime.utcnow().isoformat()

    # Create the Queue Job entity
    queue_job_item = {
        'PK': {'S': f"QUEUE#{queue_id}"},
        'SK': {'S': f"JOB#{job_id}"},
        'GSI1PK': {'S': f"JOB#{job_id}"},  # For finding all queues for a job
        'GSI1SK': {'S': f"QUEUE#{queue_id}"},
        'TYPE': {'S': 'QUEUE_JOB'},
        'queue_id': {'S': queue_id},
        'job_id': {'S': job_id},
        'added_at': {'S': now},
        'priority': {'N': str(priority)},
        'status': {'S': 'pending'}
    }

    response = dynamodb.put_item(
        TableName=table_name,
        Item=queue_job_item
    )

    print(f"Added job {job_id} to queue {queue_id} in DynamoDB")
    return response

def main():
    # Load environment configuration
    env_config = load_environment('dev')

    # Create a training job
    job = create_training_job(
        model_name='layoutlm-base-uncased',
        dataset_id='receipts-v1',
        batch_size=8,
        learning_rate=5e-5,
        num_epochs=3,
        priority=10  # Higher priority
    )

    # Store job in DynamoDB
    store_job_in_dynamo(
        job=job,
        table_name=env_config['dynamo_table'],
        region=env_config['region']
    )

    # Create a Queue Job entity in DynamoDB
    add_job_to_queue_in_dynamo(
        job_id=job['job_id'],
        queue_id=env_config['queue_id'],
        priority=job['priority'],
        table_name=env_config['dynamo_table'],
        region=env_config['region']
    )

    # Submit job reference to SQS queue (lightweight message)
    submit_job_to_sqs(
        job_id=job['job_id'],
        queue_url=env_config['job_queue_url'],
        model_name=job['config']['model']['name'],
        region=env_config['region']
    )

    print(f"Job {job['job_id']} submitted successfully")
    return job['job_id']

if __name__ == '__main__':
    main()
```

## `receipt_trainer.train` Template

```python
import argparse
import boto3
import json
import signal
import sys
import time
import os
from datetime import datetime
from pathlib import Path
import torch

from receipt_trainer.models import get_model
from receipt_trainer.data import get_datasets
from receipt_trainer.utils import dynamo, sqs, checkpointing

def parse_args():
    parser = argparse.ArgumentParser(description="Training job processor")
    parser.add_argument("--checkpoint-dir", required=True, help="Directory for checkpoints")
    parser.add_argument("--job-queue", required=True, help="SQS queue URL for jobs")
    parser.add_argument("--instance-id", required=True, help="EC2 instance ID")
    parser.add_argument("--region", required=True, help="AWS region")
    return parser.parse_args()

def setup_interruption_handler(trainer, job_id, queue_id, instance_id, dynamo_client, sqs_client, queue_url):
    """Set up handler for spot instance interruption"""
    def handler(signum, frame):
        print(f"Received interruption signal. Performing emergency checkpoint...")
        # Save emergency checkpoint
        trainer.save_checkpoint("emergency")

        timestamp = datetime.utcnow().isoformat()

        # Update job status in DynamoDB
        dynamo.update_job_status(
            dynamo_client,
            job_id,
            "INTERRUPTED",
            f"Interrupted at epoch {trainer.epoch}, step {trainer.step}"
        )

        # Update queue job status
        dynamo.update_queue_job_status(
            dynamo_client,
            queue_id,
            job_id,
            "interrupted",
            f"Spot instance interrupted at {timestamp}"
        )

        # Re-queue the job with progress information
        sqs.requeue_job(
            sqs_client,
            queue_url,
            job_id,
            {
                "checkpoint": trainer.last_checkpoint_path,
                "epoch": trainer.epoch,
                "step": trainer.step
            }
        )

        # Update instance status (using valid values from Instance entity)
        dynamo.update_instance(
            dynamo_client,
            instance_id,
            status="terminated",
            health_status="unhealthy",
            reason="Spot instance termination"
        )

        sys.exit(0)

    signal.signal(signal.SIGTERM, handler)
    return handler

def main():
    args = parse_args()

    # Initialize AWS clients
    dynamo_client = boto3.client('dynamodb', region_name=args.region)
    sqs_client = boto3.client('sqs', region_name=args.region)
    s3_client = boto3.client('s3', region_name=args.region)

    # Register instance as available for jobs (using valid values from Instance entity)
    try:
        dynamo.update_instance(
            dynamo_client,
            args.instance_id,
            status="running",
            health_status="healthy"
        )
    except Exception as e:
        raise Exception(f"Failed to update instance's status: {e}")

    # Extract queue ID from the queue URL (last part of the path, without .fifo)
    queue_name = args.job_queue.split('/')[-1]
    queue_id = queue_name.replace('.fifo', '')

    # Main job processing loop
    while True:
        try:
            # Poll for next job
            job_message = sqs.receive_job(sqs_client, args.job_queue)
            if not job_message:
                time.sleep(10)
                continue

            # Extract job ID and receipt handle
            job_id = job_message['job_id']
            receipt_handle = job_message['receipt_handle']

            # Get queue job information (priority, status, etc)
            queue_job = dynamo.get_queue_job(dynamo_client, queue_id, job_id)

            if not queue_job or queue_job.get('status') == 'completed':
                # Job already completed or removed from queue - delete message and continue
                sqs.delete_message(sqs_client, args.job_queue, receipt_handle)
                continue

            # Get job configuration from DynamoDB
            job_config = dynamo.get_job_config(dynamo_client, job_id)

            # Update job status to RUNNING in both Job and QueueJob entities
            dynamo.update_job_status(
                dynamo_client,
                job_id,
                "RUNNING",
                f"Job started on instance {args.instance_id}"
            )

            dynamo.update_queue_job_status(
                dynamo_client,
                queue_id,
                job_id,
                "processing",
                f"Job being processed by instance {args.instance_id}"
            )

            # Update instance to show which job it's running
            dynamo.link_instance_to_job(dynamo_client, args.instance_id, job_id)

            # Initialize datasets
            train_dataset, val_dataset = get_datasets(job_config)

            # Initialize model
            model = get_model(job_config)

            # Check for checkpoint to resume from
            start_epoch = 0
            start_step = 0

            if 'checkpoint' in job_message:
                checkpoint_path = job_message['checkpoint']
                start_epoch = job_message.get('epoch', 0)
                start_step = job_message.get('step', 0)
                checkpointing.load_checkpoint(model, checkpoint_path)
                print(f"Resuming from checkpoint at epoch {start_epoch}, step {start_step}")

            # Initialize trainer
            trainer = Trainer(
                model=model,
                train_dataset=train_dataset,
                val_dataset=val_dataset,
                checkpoint_dir=args.checkpoint_dir,
                job_id=job_id,
                job_config=job_config,
                start_epoch=start_epoch,
                start_step=start_step,
                dynamo_client=dynamo_client,
                s3_client=s3_client
            )

            # Set up interruption handler
            setup_interruption_handler(
                trainer,
                job_id,
                queue_id,
                args.instance_id,
                dynamo_client,
                sqs_client,
                args.job_queue
            )

            # Start training
            trainer.train()

            # If training completes successfully, update status in both Job and QueueJob entities
            dynamo.update_job_status(
                dynamo_client,
                job_id,
                "COMPLETED",
                f"Training completed successfully by instance {args.instance_id}"
            )

            dynamo.update_queue_job_status(
                dynamo_client,
                queue_id,
                job_id,
                "completed",
                f"Job completed by instance {args.instance_id}"
            )

            # Delete the message from the queue
            sqs.delete_message(sqs_client, args.job_queue, receipt_handle)

            # Update instance status back to available
            dynamo.update_instance(
                dynamo_client,
                args.instance_id,
                status="running",
                health_status="healthy"
            )
            dynamo.unlink_instance_from_job(dynamo_client, args.instance_id, job_id)

        except Exception as e:
            print(f"Error processing job: {e}")
            # Update job and instance status on error
            if 'job_id' in locals() and 'queue_id' in locals():
                # Update Job status
                dynamo.update_job_status(
                    dynamo_client,
                    job_id,
                    "ERROR",
                    str(e)
                )

                # Update QueueJob status
                dynamo.update_queue_job_status(
                    dynamo_client,
                    queue_id,
                    job_id,
                    "error",
                    f"Error: {str(e)}"
                )

            # Update instance health status to unhealthy
            dynamo.update_instance(
                dynamo_client,
                args.instance_id,
                status="running",  # Keep running but mark as unhealthy
                health_status="unhealthy",
                reason=f"Error processing job: {str(e)}"
            )
            time.sleep(30)  # Wait before trying again

if __name__ == "__main__":
    main()
```
