#!/bin/bash
# User data script for EC2 spot instances to process training jobs
# This script is run when the instance starts

# Exit on any error
set -e

# Log everything
exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1

echo "Starting user data script at $(date)"

# Set environment variables
export AWS_DEFAULT_REGION=us-east-1  # Replace with your region
export EFS_DNS_NAME=fs-xxxxxxxx.efs.us-east-1.amazonaws.com  # Replace with your EFS DNS name
export TRAINING_ACCESS_POINT_ID=fsap-xxxxxxxxxxxxx  # Replace with your EFS access point for training data
export CHECKPOINTS_ACCESS_POINT_ID=fsap-xxxxxxxxxxxxx  # Replace with your EFS access point for checkpoints
export INSTANCE_REGISTRY_TABLE=receipt-trainer-instances  # Replace with your instance registry table
export DYNAMODB_TABLE=receipt-trainer-jobs  # Replace with your DynamoDB table for jobs
export SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/xxxxxxxxxxxx/receipt-trainer-jobs  # Replace with your SQS queue URL

# Create mount points
sudo mkdir -p /mnt/training
sudo mkdir -p /mnt/checkpoints

# Install required packages
echo "Installing required packages..."
sudo yum update -y
sudo yum install -y amazon-efs-utils git python3-pip

# Install NVIDIA drivers and CUDA (if using GPU instances)
if lspci | grep -i nvidia > /dev/null; then
    echo "NVIDIA GPU detected, installing NVIDIA drivers and CUDA..."
    sudo yum install -y gcc kernel-devel-$(uname -r)

    # Get the latest NVIDIA driver installer
    curl -O https://us.download.nvidia.com/tesla/470.82.01/NVIDIA-Linux-x86_64-470.82.01.run
    sudo sh NVIDIA-Linux-x86_64-470.82.01.run --silent

    # Install CUDA
    curl -O https://developer.download.nvidia.com/compute/cuda/11.4.3/local_installers/cuda_11.4.3_470.82.01_linux.run
    sudo sh cuda_11.4.3_470.82.01_linux.run --silent --toolkit

    # Set up CUDA environment
    echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
    echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
    source ~/.bashrc

    # Test NVIDIA installation
    nvidia-smi
fi

# Clone repository (if not using pre-built AMI)
echo "Cloning repository..."
if [ ! -d "/home/ec2-user/receipt-trainer" ]; then
    cd /home/ec2-user
    git clone https://github.com/your-username/receipt-trainer.git
    cd receipt-trainer

    # Install package
    pip3 install -e .
    pip3 install -r requirements.txt
fi

# Mount EFS volumes
echo "Mounting EFS volumes..."
sudo mount -t efs -o tls,accesspoint=$TRAINING_ACCESS_POINT_ID $EFS_DNS_NAME /mnt/training
sudo mount -t efs -o tls,accesspoint=$CHECKPOINTS_ACCESS_POINT_ID $EFS_DNS_NAME /mnt/checkpoints

# Set ownership to ec2-user
sudo chown ec2-user:ec2-user /mnt/training
sudo chown ec2-user:ec2-user /mnt/checkpoints

# Report instance information
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type)
echo "Running on instance $INSTANCE_ID of type $INSTANCE_TYPE"

# Check for spot instance
if curl -s http://169.254.169.254/latest/meta-data/spot/instance-action -o /dev/null; then
    echo "Running as a spot instance"
    IS_SPOT=true
else
    echo "Running as an on-demand instance"
    IS_SPOT=false
fi

# Start the worker process
echo "Starting receipt-trainer worker process..."
cd /home/ec2-user/receipt-trainer

# Start worker in background with log file
nohup python3 -m receipt_trainer.jobs.cli start-worker \
    --queue-url $SQS_QUEUE_URL \
    --dynamo-table $DYNAMODB_TABLE \
    --instance-registry-table $INSTANCE_REGISTRY_TABLE \
    --mount-efs \
    --verbose > /home/ec2-user/worker.log 2>&1 &

# Save process ID
echo $! > /home/ec2-user/worker.pid

echo "Worker started successfully"
echo "User data script completed at $(date)"
