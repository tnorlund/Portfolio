# Migration Guide: Standardized Job Processing System

This document provides guidance for migrating existing code to the new standardized job processing system.

## Overview

The new standardized job processing system consolidates multiple job processing implementations into a single, consistent pattern. Key improvements include:

- **Unified Interface**: Consistent API across different processing environments
- **Debug Mode**: Enhanced logging and diagnostics
- **Test Isolation**: Dedicated test queues and resources
- **Fixed Inconsistencies**: Resolved `type` vs `job_type` parameter inconsistencies
- **Standardized DynamoDB Access**: Consistent use of DynamoClient

## Migration Steps

### Step 1: Update Job References

The `Job` class now uses `type` consistently as the field name, with a `job_type` property for backward compatibility.

**Old code:**

```python
job = Job(
    name="training-job",
    job_type="training",  # This varies between code paths
    config={...}
)
```

**New code:**

```python
job = Job(
    name="training-job",
    type="training",  # Always use 'type' going forward
    config={...}
)
```

Both styles will work during the transition period, but new code should always use `type`.

### Step 2: Replace Direct Queue Usage with JobProcessor

**Old code:**

```python
from receipt_trainer.jobs import JobQueue, JobQueueConfig

# Configure the job queue
queue = JobQueue(JobQueueConfig(
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
))

# Submit a job
job_id = queue.submit_job(job)

# Process jobs
def process_job(job):
    # Process the job...
    return True

queue.process_jobs(process_job)
```

**New code:**

```python
from receipt_trainer.jobs import create_job_processor, ProcessingMode

# Create a job processor
processor = create_job_processor(
    processor_type="sqs",
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    dynamo_table="my-dynamo-table",
    handler=process_job,
    mode=ProcessingMode.NORMAL,
)

# Submit a job
job_id = processor.submit_job(job)

# Start processing jobs
processor.start()

# ... later ...

# Stop processing jobs
processor.stop()
```

### Step 3: Update EC2 Worker Implementation

For EC2-specific processing, use the `EC2JobProcessor`:

**Old code:**

```python
from receipt_trainer.jobs.worker import process_training_jobs

# Set up processing
process_training_jobs(
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    dynamo_table="my-dynamo-table",
    instance_registry_table="instance-registry",
    handle_spot=True,
)
```

**New code:**

```python
from receipt_trainer.jobs import create_job_processor, ProcessingMode

# Define job handler
def process_job(job):
    # Process the job...
    return True

# Create EC2 job processor
processor = create_job_processor(
    processor_type="ec2",
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    dynamo_table="my-dynamo-table",
    handler=process_job,
    instance_registry_table="instance-registry",
    handle_spot=True,
    enable_coordination=True,
)

# Start processing jobs
processor.start()
```

### Step 4: Add Debug Mode for Troubleshooting

Debug mode provides detailed logging for troubleshooting:

```python
from receipt_trainer.jobs import create_job_processor, ProcessingMode

processor = create_job_processor(
    processor_type="sqs",
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    dynamo_table="my-dynamo-table",
    handler=process_job,
    mode=ProcessingMode.DEBUG,  # Enable debug mode
)
```

Debug logs are written to `job_processor_debug.log` with detailed information about each step of job processing.

### Step 5: Use Test Mode for Isolated Testing

Test mode ensures tests don't interfere with production jobs:

```python
from receipt_trainer.jobs import create_job_processor, ProcessingMode

processor = create_job_processor(
    processor_type="sqs",
    queue_url="test-queue-url",
    dynamo_table="test-table",
    handler=process_job,
    mode=ProcessingMode.TEST,  # Enable test mode
    test_prefix="test_",  # Prefix for job names to ensure isolation
)
```

### Step 6: Replace Direct DynamoDB Calls with DynamoClient

**Old code:**

```python
import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("my-table")
table.put_item(Item={"PK": f"JOB#{job_id}", ...})
```

**New code:**

```python
from receipt_dynamo.data.dynamo_client import DynamoClient

dynamo_client = DynamoClient(table_name="my-table")
dynamo_client.addJob(job_id=job_id, ...)
```

## Example: Complete Migration

Here's a complete example of migrating a job processing script:

**Old code:**

```python
from receipt_trainer.jobs import JobQueue, JobQueueConfig, Job

# Set up queue
queue = JobQueue(JobQueueConfig(
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
))

# Submit a job
job = Job(
    name="training-job",
    job_type="training",
    config={...}
)
queue.submit_job(job)

# Process jobs
def process_job(job):
    # Process the job...
    return True

queue.process_jobs(process_job)
```

**New code:**

```python
from receipt_trainer.jobs import (
    Job,
    create_job_processor,
    ProcessingMode,
)

# Define job handler
def process_job(job):
    # Process the job...
    return True

# Create job processor
processor = create_job_processor(
    processor_type="sqs",
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    dynamo_table="my-dynamo-table",
    handler=process_job,
    mode=ProcessingMode.NORMAL,
)

# Submit a job
job = Job(
    name="training-job",
    type="training",
    config={...}
)
processor.submit_job(job)

# Start processing jobs
processor.start()

# ... later ...

# Stop processing jobs
processor.stop()
```

## Testing

For testing, use the test mode to ensure test jobs don't interfere with production:

```python
processor = create_job_processor(
    processor_type="sqs",
    queue_url="test-queue-url",
    dynamo_table="test-table",
    handler=process_job,
    mode=ProcessingMode.TEST,
    test_prefix="test_",
)
```

## Questions and Support

For questions or support with migration, contact the ML Infrastructure team or file an issue on GitHub.
