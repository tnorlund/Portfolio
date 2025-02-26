# ML Training Job Queue System

A job queue system for managing machine learning training jobs using AWS SQS.

## Overview

This module provides a complete job queue system for managing machine learning training jobs, with the following features:

- **Job Definition**: Define training jobs with configuration, priority, and dependencies
- **Job Queue**: Submit, process, and manage jobs using AWS SQS
- **Retry Mechanism**: Automatic retries with configurable backoff strategies
- **CLI Tools**: Command-line interface for job queue management
- **AWS Integration**: Utilities for creating and managing SQS queues

## Usage

### Creating a Queue

To create a queue for ML training jobs:

```python
from receipt_trainer.jobs import create_queue_with_dlq

# Create a queue with a dead-letter queue
queue_url, dlq_url = create_queue_with_dlq(
    queue_name="ml-training-jobs",
    region_name="us-west-2"
)
```

### Defining and Submitting a Job

To define and submit a training job:

```python
from receipt_trainer.jobs import Job, JobPriority, JobQueue, JobQueueConfig

# Create a job
job = Job(
    name="Train ResNet50 on receipts-v1",
    type="model_training",
    config={
        "model_name": "resnet50",
        "dataset_id": "receipts-v1",
        "hyperparameters": {
            "learning_rate": 0.001,
            "batch_size": 32,
            "epochs": 10,
        },
        "output_bucket": "my-ml-models",
    },
    priority=JobPriority.HIGH,
    tags={"environment": "development"}
)

# Configure the job queue
queue_config = JobQueueConfig(
    queue_url=queue_url,
    aws_region="us-west-2"
)

# Create the queue and submit the job
queue = JobQueue(queue_config)
job_id = queue.submit_job(job)
```

### Processing Jobs

To process jobs from the queue:

```python
from receipt_trainer.jobs import JobQueue, JobQueueConfig

# Configure the job queue
queue_config = JobQueueConfig(
    queue_url=queue_url,
    aws_region="us-west-2"
)

# Create the queue
queue = JobQueue(queue_config)

# Define a job handler
def process_job(job):
    """Process a job. Return True if successful, False otherwise."""
    print(f"Processing job: {job.name}")
    # Your ML training logic here
    return True

# Process jobs in a background thread
thread = queue.start_processing(process_job)

# Later, stop processing
queue.stop_processing()
thread.join()
```

### Command-Line Interface

The job queue system includes a command-line interface for queue management:

```bash
# Create a queue
python -m receipt_trainer.jobs.cli create-queue ml-training-jobs --region us-west-2

# Submit a job
python -m receipt_trainer.jobs.cli submit-job QUEUE_URL \
    --name "Train ResNet50" \
    --type "model_training" \
    --config '{"model_name": "resnet50", "dataset_id": "receipts-v1"}' \
    --priority "HIGH"

# List jobs in the queue
python -m receipt_trainer.jobs.cli list-jobs QUEUE_URL

# Get queue attributes
python -m receipt_trainer.jobs.cli queue-attributes QUEUE_URL
```

## Example

An example script demonstrating the job queue system is provided in `examples/train_job_example.py`:

```bash
# Navigate to the examples directory
cd examples

# Create a queue, submit jobs, and process them
python train_job_example.py --create-queue --submit --process --num-jobs 5
```

## Architecture

The job queue system consists of the following components:

1. **Job**: Defines a training job with configuration, status, and metadata
2. **JobQueue**: Manages job submission, processing, and retries using AWS SQS
3. **AWS Utilities**: Functions for creating and managing SQS queues
4. **CLI**: Command-line interface for job queue management

## Best Practices

- **Job Timeout**: Set appropriate timeout values for jobs to prevent them from running indefinitely
- **Retry Strategy**: Choose a retry strategy based on the nature of your ML training jobs
- **Dead-Letter Queue**: Use the DLQ to capture failed jobs for later analysis
- **Long Polling**: The job queue uses long polling to reduce SQS costs and latency 