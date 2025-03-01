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

# Receipt Training Job CLI

This module provides a command-line interface for managing ML training jobs in the Receipt Trainer system.

## Installation

The CLI is included with the Receipt Trainer package:

```shell
pip install receipt-trainer
```

## Usage

### Job Queue Management

Create a new job queue with a dead-letter queue:

```shell
python -m receipt_trainer.jobs.cli create-queue my-training-queue --fifo
```

Delete a job queue:

```shell
python -m receipt_trainer.jobs.cli delete-queue <queue-url>
```

Purge all messages from a queue:

```shell
python -m receipt_trainer.jobs.cli purge-queue <queue-url>
```

Get queue attributes:

```shell
python -m receipt_trainer.jobs.cli queue-attributes <queue-url>
```

### Job Submission

Submit a job directly:

```shell
python -m receipt_trainer.jobs.cli submit-job <queue-url> \
  --name "My Training Job" \
  --type "layoutlm_training" \
  --config '{"model": "layoutlm", "epochs": 10}' \
  --priority HIGH
```

Submit a job from a YAML/JSON file (recommended):

```shell
python -m receipt_trainer.jobs.cli submit-job-file <queue-url> path/to/job_definition.yaml
```

Example job definition files can be found in the `examples` directory.

### Job Monitoring

List all jobs in a queue:

```shell
python -m receipt_trainer.jobs.cli list-jobs <queue-url> --count 20
```

List all jobs across all statuses:

```shell
python -m receipt_trainer.jobs.cli list-all-jobs
```

Filter jobs by status:

```shell
python -m receipt_trainer.jobs.cli list-all-jobs --status running
```

Get the status of a specific job:

```shell
python -m receipt_trainer.jobs.cli job-status <job-id>
```

Monitor a job in real-time (with auto-refresh):

```shell
python -m receipt_trainer.jobs.cli monitor-job <job-id> --refresh 3
```

View detailed job information:

```shell
python -m receipt_trainer.jobs.cli job-details <job-id>
```

View job logs:

```shell
python -m receipt_trainer.jobs.cli job-logs <job-id> --limit 50
```

### Job Management

Cancel a running job:

```shell
python -m receipt_trainer.jobs.cli cancel-job <job-id>
```

### Infrastructure Management

List active training instances:

```shell
python -m receipt_trainer.jobs.cli list-instances
```

## Job Definition Format

Job definitions can be specified in YAML or JSON. See the examples directory for a sample job definition.

Key components of a job definition include:

- Basic information (name, description, etc.)
- Resource requirements (instance type, GPU count)
- Model configuration (pretrained model, sequence length)
- Training parameters (epochs, batch size, learning rate)
- Dataset configuration (data sources)
- Output configuration (where to save models)
- Monitoring and notification settings

Example:

```yaml
job:
  name: "LayoutLM Training Job"
  description: "Training LayoutLM on receipt data"
  resources:
    instance_type: "p3.2xlarge"
    min_gpu_count: 1
  model:
    type: "layoutlm"
    version: "v2"
  training:
    epochs: 10
    batch_size: 4
    learning_rate: 3.0e-5
  # (other configuration sections)
```

## AWS Region Configuration

By default, the CLI uses the AWS region configured in your environment. You can override this for any command using the `--region` flag:

```shell
python -m receipt_trainer.jobs.cli job-status <job-id> --region us-west-2
```

## Environment Variables

The CLI respects the following environment variables:

- `AWS_REGION`: Default AWS region
- `AWS_PROFILE`: AWS credentials profile to use

## Logging

The CLI logs messages to stdout. You can adjust logging verbosity using Python's logging configuration. 