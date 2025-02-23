# AWS Spot Instance Training Infrastructure

This Pulumi project sets up AWS infrastructure for running machine learning training jobs on Spot instances. The infrastructure includes:

- S3 bucket for storing logs, checkpoints, and artifacts
- IAM roles and policies for EC2 instance access
- EC2 launch template with Deep Learning AMI
- Auto Scaling Group configured for Spot instances
- Security group for network access

## Prerequisites

- Python 3.7+
- Pulumi CLI
- AWS CLI configured with appropriate credentials
- Weights & Biases account (for experiment tracking)

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure AWS region (optional):
   ```bash
   pulumi config set aws:region us-west-2
   ```

3. Deploy the infrastructure:
   ```bash
   pulumi up
   ```

## Usage

1. The Auto Scaling Group starts with 0 instances. To start training, update the desired capacity:
   ```bash
   aws autoscaling set-desired-capacity --auto-scaling-group-name $(pulumi stack output asg_name) --desired-capacity 1
   ```

2. Monitor your instances in the AWS Console or using AWS CLI.

3. When training is complete, scale down to 0:
   ```bash
   aws autoscaling set-desired-capacity --auto-scaling-group-name $(pulumi stack output asg_name) --desired-capacity 0
   ```

## Clean Up

To destroy all created resources:
```bash
pulumi destroy
```

## Important Notes

- The infrastructure uses Spot instances for cost optimization
- Instance types include p3.2xlarge, p3.8xlarge, and g4dn.xlarge
- S3 bucket has lifecycle rules to move old data to cheaper storage tiers
- Security group allows SSH access (port 22) - modify as needed
- Remember to scale down when not in use to avoid unnecessary costs 