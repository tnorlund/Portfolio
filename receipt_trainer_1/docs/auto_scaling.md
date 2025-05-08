# Auto-Scaling for Distributed Training

This document explains how to use the auto-scaling functionality for distributed training in Receipt Trainer.

## Overview

The auto-scaling system automatically manages EC2 instances for training based on the SQS job queue depth:

1. **Monitoring** - Continuously monitors the SQS job queue depth
2. **Scaling Up** - Launches EC2 instances when the queue has many pending jobs
3. **Scaling Down** - Terminates instances when the queue is nearly empty
4. **Cost Optimization** - Selects the most cost-effective instance types based on spot pricing

## Requirements

- AWS credentials with permissions to:
  - Launch/terminate EC2 instances
  - Access SQS queues
  - Read DynamoDB tables
  - Mount EFS volumes
- A Pulumi stack with the required infrastructure (or manual configuration)

## Using the Command-Line Tool

The auto-scaling functionality is available through a command-line tool:

```bash
# Start auto-scaling using Pulumi stack (dev or prod)
python -m receipt_trainer.scripts.auto_scale --stack dev --start

# Show current auto-scaling status
python -m receipt_trainer.scripts.auto_scale --stack dev --status

# Stop auto-scaling
python -m receipt_trainer.scripts.auto_scale --stack dev --stop

# Manually scale up by adding instances
python -m receipt_trainer.scripts.auto_scale --stack dev --scale-up 2

# Manually scale down by removing instances
python -m receipt_trainer.scripts.auto_scale --stack dev --scale-down 1
```

### Configuration Options

The command-line tool supports the following options:

```bash
# Specify minimum and maximum instances
python -m receipt_trainer.scripts.auto_scale --stack dev --start --min-instances 2 --max-instances 5

# Run in the background as a daemon
python -m receipt_trainer.scripts.auto_scale --stack dev --start --daemonize

# Set custom monitoring interval
python -m receipt_trainer.scripts.auto_scale --stack dev --start --interval 120

# Use specific instance types (CPU and/or GPU)
python -m receipt_trainer.scripts.auto_scale --stack dev --start \
    --cpu-instances c5.large c5.xlarge \
    --gpu-instances g4dn.xlarge p3.2xlarge
```

## Using the Python API

You can also use the auto-scaling functionality directly in your Python code:

```python
from receipt_trainer.utils.pulumi import create_auto_scaling_manager

# Create a manager from Pulumi stack outputs
manager = create_auto_scaling_manager(
    stack_name="dev",
    min_instances=1,
    max_instances=10,
    gpu_instance_types=["g4dn.xlarge", "p3.2xlarge"]
)

# Start monitoring
manager.start_monitoring(interval_seconds=60)

# Check status
status = manager.get_instance_status()
print(f"Queue depth: {status['queue_depth']}, Instances: {status['total_instances']}")

# Stop monitoring when done
manager.stop_monitoring()
```

## Manual Configuration

If you're not using Pulumi, you can manually configure the auto-scaling manager:

```bash
python -m receipt_trainer.scripts.auto_scale --manual-config \
    --queue https://sqs.us-east-1.amazonaws.com/123456789012/training-queue \
    --dynamo-table receipt-data-table \
    --ami ami-0123456789abcdef \
    --instance-profile receipt-trainer-instance-profile \
    --subnet subnet-abcdef123456 \
    --security-group sg-123456abcdef \
    --start
```

## Example Script

For a simple example of using the auto-scaling functionality, see the included example script:

```bash
python -m receipt_trainer.examples.pulumi_auto_scaling_example \
    --stack dev \
    --min-instances 1 \
    --max-instances 5 \
    --duration 300
```

This script will start the auto-scaling manager, monitor the SQS queue, and automatically launch or terminate instances as needed for 5 minutes.

## End-to-End Testing

The auto-scaling functionality includes true end-to-end tests that actually launch and terminate EC2 instances to verify functionality with real AWS resources. These tests are disabled by default since they incur AWS costs.

### Running End-to-End Tests

To run the end-to-end tests that launch real AWS resources:

```bash
# Run using the convenience script
./scripts/run_e2e_aws_tests.sh

# Or specify an AWS profile
./scripts/run_e2e_aws_tests.sh my-profile
```

The end-to-end test will:

1. Create an auto-scaling manager using your Pulumi stack configuration
2. Launch a real EC2 instance
3. Verify the instance is running
4. Terminate the instance
5. Verify all resources are cleaned up

### Test Safety Features

The end-to-end tests include several safety features:

1. They're skipped by default unless `ENABLE_REAL_AWS_TESTS=1` is set
2. They automatically terminate any instances they create, even if the test fails
3. They perform a final cleanup check to make sure no instances were left running
4. The script includes a confirmation prompt to prevent accidental runs

### AWS Permissions Required

To run the end-to-end tests, your AWS credentials need the following permissions:

- `ec2:DescribeInstances`
- `ec2:RunInstances`
- `ec2:CreateTags`
- `ec2:TerminateInstances`
- `ec2:RequestSpotInstances`
- `ec2:DescribeSpotInstanceRequests`
- `sqs:GetQueueAttributes`

## Additional Notes

- The auto-scaling functionality uses spot instances by default for cost savings
- Instance types are selected based on job requirements (CPU or GPU) and current spot pricing
- The minimum number of instances is maintained even when the queue is empty
- Instances that were not launched by the auto-scaling manager are not affected
