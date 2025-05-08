# ML Training Job Queue System

A job queue system for managing machine learning training jobs using AWS SQS.

## Overview

This module provides a complete job queue system for managing machine learning training jobs, with the following features:

- **Standardized Processing**: Unified interface for job processing across different environments
- **Job Queue**: Submit, process, and manage jobs using AWS SQS
- **Debug Mode**: Enhanced logging and debugging capabilities
- **Test Isolation**: Support for isolated test environments
- **CLI Tools**: Command-line interface for job queue management

## Standardized Job Processing

The standardized job processing system provides a unified interface for processing jobs across different environments:

```python
from receipt_trainer.jobs import (
    Job,
    create_job_processor,
    ProcessingMode,
)

# Create a job processor
processor = create_job_processor(
    processor_type="sqs",  # or "ec2" for EC2-specific functionality
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    dynamo_table="my-dynamo-table",
    handler=my_job_handler_function,
    mode=ProcessingMode.DEBUG,  # Enable debug mode for detailed logging
)

# Submit a job
job = Job(
    name="training-job",
    type="training",
    config={
        "model_name": "layoutlm-base",
        "training_params": {
            "learning_rate": 0.001,
            "batch_size": 32,
            "epochs": 5,
        },
    },
)
job_id = processor.submit_job(job)

# Start processing jobs
processor.start()

# ... later on ...

# Stop processing jobs
processor.stop()
```

### Processing Modes

The job processor supports different modes:

- **NORMAL**: Standard processing mode
- **DEBUG**: Detailed logging for debugging
- **TEST**: Isolated resources for testing

### Job Processor Types

Two job processor types are available:

- **SQSJobProcessor**: Standard processor using AWS SQS for job queueing
- **EC2JobProcessor**: Extended processor with EC2-specific functionality (spot instances, cluster coordination)

## Basic Usage

### Submitting a Job

```python
from receipt_trainer.jobs import Job, JobQueue, JobQueueConfig

# Configure the job queue
queue = JobQueue(JobQueueConfig(
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
))

# Create a job
job = Job(
    name="training-job",
    type="training",
    config={
        "model_name": "layoutlm-base",
        "epochs": 5,
    },
)

# Submit the job
job_id = queue.submit_job(job)
print(f"Submitted job: {job_id}")
```

### Processing Jobs

```python
def process_job(job):
    print(f"Processing job: {job.name} (type: {job.type})")
    # Process the job...
    return True  # Return True for success, False for failure

# Start processing jobs
thread = queue.start_processing(process_job)

# ... later ...

# Stop processing
queue.stop_processing()
```

## Command Line Interface

The job queue system includes a command-line interface for queue management:

```
# Create a queue
python -m receipt_trainer.jobs.cli create-queue my-queue

# Submit a job
python -m receipt_trainer.jobs.cli submit-job QUEUE_URL \
    --type training \
    --name "Training Job" \
    --config '{"model": "layoutlm-base", "epochs": 5}'

# Monitor a queue
python -m receipt_trainer.jobs.cli monitor-queue QUEUE_URL
```

## Examples

An example script demonstrating the standardized job processing system is provided in `examples/standardized_job_example.py`:

```
python -m receipt_trainer.jobs.examples.standardized_job_example --mode=debug
```

An example script demonstrating the job queue system is provided in `examples/train_job_example.py`:

```
python -m receipt_trainer.jobs.examples.train_job_example --create-queue
```

## Architecture

The job queue system consists of the following components:

1. **Job**: Class representing a job with properties, serialization, and status tracking
2. **JobQueue**: Class for interacting with AWS SQS queues
3. **JobProcessor**: Abstract interface for job processing with concrete implementations
4. **CLI**: Command-line interface for job queue management

## Additional Features

- **Long Polling**: The job queue uses long polling to reduce SQS costs and latency
- **Visibility Timeout Management**: Automatically extends visibility timeout for long-running jobs
- **Retries**: Configurable retry strategies for failed jobs
- **Dead-Letter Queue**: Support for dead-letter queues to capture failed jobs
- **Debugging**: Debug mode with detailed logging for troubleshooting
- **Test Isolation**: Test mode with isolated resources for testing

## Job Queue Management

### Create a new job queue with a dead-letter queue:

```python
from receipt_trainer.jobs import create_queue_with_dlq

queue_url, dlq_url = create_queue_with_dlq("my-queue")
```

### Delete a job queue:

```python
from receipt_trainer.jobs import delete_queue

delete_queue("my-queue")
```

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
