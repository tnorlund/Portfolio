# Receipt Trainer

A Python package for training OCR models on receipt images using distributed computing on AWS.

## Features

- Train LayoutLM models for receipt field extraction
- Distributed training using SQS, EFS, and EC2 Spot Instances
- Auto-resumable training from checkpoints on EFS
- Fault-tolerant job processing with retry mechanisms
- Metrics and logs stored in DynamoDB
- Support for spot instance interruption handling

## Installation

```bash
pip install receipt-trainer
```

Or for development:

```bash
git clone https://github.com/yourusername/receipt-trainer.git
cd receipt-trainer
pip install -e .
```

## Distributed Training Architecture

The receipt-trainer package supports a distributed training architecture using AWS services:

![Architecture Diagram](docs/images/architecture.png)

### Components:

1. **SQS Queue**: Jobs are submitted to an SQS queue
2. **EC2 Spot Instances**: Process jobs from the queue
3. **EFS File System**: Shared storage for datasets, checkpoints, and models
4. **DynamoDB**: Stores job metadata, metrics, and instance registry

## Setting Up AWS Infrastructure

### 1. Create EFS File System

```bash
# Create EFS file system
aws efs create-file-system --performance-mode generalPurpose --throughput-mode bursting --encrypted --tags Key=Name,Value=receipt-trainer

# Create access points for training data and checkpoints
aws efs create-access-point --file-system-id fs-xxxxxxxx --posix-user Uid=1000,Gid=1000 --root-directory Path=/training --tags Key=Name,Value=training
aws efs create-access-point --file-system-id fs-xxxxxxxx --posix-user Uid=1000,Gid=1000 --root-directory Path=/checkpoints --tags Key=Name,Value=checkpoints
```

### 2. Create DynamoDB Tables

```bash
# Create job tracking table
aws dynamodb create-table --table-name receipt-trainer-jobs \
    --attribute-definitions AttributeName=job_id,AttributeType=S \
    --key-schema AttributeName=job_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

# Create instance registry table
aws dynamodb create-table --table-name receipt-trainer-instances \
    --attribute-definitions AttributeName=instance_id,AttributeType=S \
    --key-schema AttributeName=instance_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST
```

### 3. Create SQS Queue

```bash
# Create main job queue
aws sqs create-queue --queue-name receipt-trainer-jobs \
    --attributes FifoQueue=true,ContentBasedDeduplication=true

# Create dead-letter queue for failed jobs
aws sqs create-queue --queue-name receipt-trainer-jobs-dlq \
    --attributes FifoQueue=true,ContentBasedDeduplication=true
```

### 4. Configure EC2 Spot Fleet

Create an EC2 Launch Template with the user-data script from `scripts/ec2_user_data.sh`, then create a spot fleet request:

```bash
aws ec2 request-spot-fleet --spot-fleet-request-config file://spot-fleet-config.json
```

Example spot-fleet-config.json:

```json
{
  "SpotPrice": "1.00",
  "TargetCapacity": 2,
  "AllocationStrategy": "capacityOptimized",
  "LaunchTemplateConfigs": [
    {
      "LaunchTemplateSpecification": {
        "LaunchTemplateId": "lt-0abc123def456789",
        "Version": "1"
      },
      "Overrides": [
        { "InstanceType": "p3.2xlarge", "SubnetId": "subnet-abc123" },
        { "InstanceType": "g4dn.xlarge", "SubnetId": "subnet-def456" }
      ]
    }
  ],
  "Type": "maintain",
  "IamFleetRole": "arn:aws:iam::123456789012:role/SpotFleetRole"
}
```

## Usage

### 1. Submit a Training Job

```bash
# Submit a job using a YAML configuration file
receipt-trainer submit-job \
    --config examples/training_job.yaml \
    --queue-url https://sqs.us-east-1.amazonaws.com/123456789012/receipt-trainer-jobs.fifo \
    --priority high
```

### 2. Run Hyperparameter Sweep

```bash
# Run a hyperparameter sweep with default parameters
python -m receipt_trainer.scripts.hyperparameter_sweep --stack dev --monitor

# Customize the hyperparameter search space
python -m receipt_trainer.scripts.hyperparameter_sweep \
  --stack dev \
  --learning-rates 5e-5,3e-5,1e-5 \
  --batch-sizes 4,8,16 \
  --epochs 3,5,10 \
  --max-samples 1000 \
  --monitor
```

For more details on hyperparameter sweeps, see the [Model Training Guide](docs/model_training.md).

### 3. Run a Worker Manually

```bash
# Start a worker to process jobs from the queue
receipt-trainer start-worker \
    --queue-url https://sqs.us-east-1.amazonaws.com/123456789012/receipt-trainer-jobs.fifo \
    --dynamo-table receipt-trainer-jobs \
    --instance-registry-table receipt-trainer-instances \
    --mount-efs
```

### 4. Monitor Jobs

```bash
# Monitor job status
receipt-trainer job-status --job-id 12345678-abcd-efgh-ijkl-123456789abc

# View job details
receipt-trainer job-details --job-id 12345678-abcd-efgh-ijkl-123456789abc
```

## Programmatic Usage

```python
from receipt_trainer.jobs.submit import submit_training_job
from receipt_trainer.config import TrainingConfig, DataConfig

# Create configurations
training_config = TrainingConfig(
    num_epochs=5,
    batch_size=8,
    learning_rate=5e-5,
    weight_decay=0.01
)

data_config = DataConfig(
    dataset_name="internal-receipts",
    train_split="train",
    eval_split="validation"
)

# Submit job
job_id = submit_training_job(
    model_name="microsoft/layoutlm-base-uncased",
    training_config=training_config,
    data_config=data_config,
    queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/receipt-trainer-jobs.fifo",
    priority="high"
)

print(f"Submitted job: {job_id}")
```

## Fault Tolerance and Spot Instance Handling

The training system is designed to be fault-tolerant and handle spot instance interruptions seamlessly:

1. When a spot instance receives a termination notice, it:

   - Saves a checkpoint to EFS
   - Updates the job status in DynamoDB
   - Releases the SQS message with a short visibility timeout

2. When a new instance starts, it:
   - Mounts the same EFS file systems
   - Picks up the job from SQS
   - Finds the latest checkpoint
   - Resumes training from where it left off

This creates a completely seamless experience where spot interruptions are handled automatically without losing training progress.

## License

MIT License
