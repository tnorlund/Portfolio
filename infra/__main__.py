"""Main Pulumi program for AWS infrastructure."""

import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions
import base64

# Import our infrastructure components
import s3_website  # noqa: F401
import api_gateway  # noqa: F401
import raw_bucket  # Import raw bucket module
from dynamo_db import dynamodb_table  # Import DynamoDB table from original code
from spot_interruption import SpotInterruptionHandler  # Import the class
from efs_storage import EFSStorage  # Import the class
from instance_registry import InstanceRegistry  # Import the class
from job_queue import JobQueue  # Import the job queue class

# Import other necessary components
try:
    import lambda_layer  # noqa: F401
    from routes.health_check.infra import health_check_lambda  # noqa: F401
except ImportError:
    # These may not be available in all environments
    pass

# Original exports from main branch
pulumi.export("region", aws.config.region)

# Open template readme and read contents into stack output
try:
    with open("./Pulumi.README.md") as f:
        pulumi.export("readme", f.read())
except FileNotFoundError:
    pulumi.export("readme", "README file not found")

# ML Training Infrastructure
# -------------------------

# Use stack-specific existing key pair from AWS console
stack = pulumi.get_stack()
key_pair_name = (
    f"portfolio-receipt-{stack}"  # Use existing key pairs created in AWS console
)

# Create EC2 Instance Profile for ML training instances
ml_training_role = aws.iam.Role(
    "ml-training-role",
    assume_role_policy="""
    {
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Effect": "Allow"
        }]
    }
    """,
)

# Attach basic policies for S3 access
s3_policy_attachment = aws.iam.RolePolicyAttachment(
    "ml-s3-policy-attachment",
    role=ml_training_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
)

# Create instance profile
ml_instance_profile = aws.iam.InstanceProfile(
    "ml-instance-profile", role=ml_training_role.name
)

# Get default VPC and subnets for EFS
default_vpc = aws.ec2.get_vpc(default=True)
default_subnets = aws.ec2.get_subnets(
    filters=[
        aws.ec2.GetSubnetsFilterArgs(
            name="vpc-id",
            values=[default_vpc.id],
        ),
    ]
)

# Create security group for ML training instances
ml_security_group = aws.ec2.SecurityGroup(
    "ml-security-group",
    description="Security group for ML training instances",
    vpc_id=default_vpc.id,
    ingress=[
        # Allow SSH
        aws.ec2.SecurityGroupIngressArgs(
            from_port=22,
            to_port=22,
            protocol="tcp",
            cidr_blocks=["0.0.0.0/0"],
        ),
        # Allow all traffic between instances in this security group
        aws.ec2.SecurityGroupIngressArgs(
            from_port=0,
            to_port=0,
            protocol="-1",
            self=True,
        ),
    ],
    egress=[
        # Allow all outbound traffic
        aws.ec2.SecurityGroupEgressArgs(
            from_port=0,
            to_port=0,
            protocol="-1",
            cidr_blocks=["0.0.0.0/0"],
        ),
    ],
)

# Create spot interruption handler
spot_handler = SpotInterruptionHandler(
    "ml-training",
    instance_role_name=ml_training_role.name,
    # Optional: Add email for notifications
    # sns_email="your-email@example.com",
)

# Create EFS for shared storage
efs_storage = EFSStorage(
    "ml-training",
    vpc_id=default_vpc.id,
    subnet_ids=default_subnets.ids,
    security_group_ids=[ml_security_group.id],
    instance_role_name=ml_training_role.name,
    # Optional: Configure lifecycle policies
    lifecycle_policies=[{"transition_to_ia": "AFTER_30_DAYS"}],
)

# Create instance registry for auto-registration
instance_registry = InstanceRegistry(
    "ml-training",
    instance_role_name=ml_training_role.name,
    ttl_hours=2,  # Entries expire after 2 hours if not updated
)

# Create job queue for training job management
job_queue = JobQueue(
    "ml-training",
    env=stack,
    tags={
        "Purpose": "ML Training Job Management",
        "ManagedBy": "Pulumi",
    }
)

# Update the IAM role to allow access to SQS
sqs_policy_document = pulumi.Output.all(
    queue_arn=job_queue.get_queue_arn(), 
    dlq_arn=job_queue.get_dlq_arn()
).apply(
    lambda args: f"""{{
        "Version": "2012-10-17",
        "Statement": [
            {{
                "Effect": "Allow",
                "Action": [
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                    "sqs:GetQueueUrl",
                    "sqs:SendMessage",
                    "sqs:ChangeMessageVisibility"
                ],
                "Resource": [
                    "{args['queue_arn']}",
                    "{args['dlq_arn']}"
                ]
            }}
        ]
    }}"""
)

# Create the policy
sqs_policy = aws.iam.Policy(
    "ml-training-sqs-policy",
    description="Allow ML training instances to access SQS queues",
    policy=sqs_policy_document
)

# Attach the policy to the role
sqs_policy_attachment = aws.iam.RolePolicyAttachment(
    "ml-sqs-policy-attachment",
    role=ml_training_role.name,
    policy_arn=sqs_policy.arn,
    opts=ResourceOptions(depends_on=[ml_training_role])
)

# Generate instance registration script
registration_script = instance_registry.create_registration_script(
    leader_election_enabled=True
)

# Create user data script for EC2 instances
user_data_script = pulumi.Output.all(
    efs_dns_name=efs_storage.file_system_dns_name,
    training_ap_id=efs_storage.training_access_point_id,
    checkpoints_ap_id=efs_storage.checkpoints_access_point_id,
    instance_registry_table=instance_registry.table_name,
    registration_script=registration_script,
    job_queue_url=job_queue.get_queue_url(),
    job_dlq_url=job_queue.get_dlq_url()
).apply(
    lambda args: f"""#!/bin/bash
# User data script for ML training instances

# Set environment variables
echo "export INSTANCE_REGISTRY_TABLE={args['instance_registry_table']}" >> /etc/environment
echo "export EFS_DNS_NAME={args['efs_dns_name']}" >> /etc/environment
echo "export TRAINING_ACCESS_POINT_ID={args['training_ap_id']}" >> /etc/environment
echo "export CHECKPOINTS_ACCESS_POINT_ID={args['checkpoints_ap_id']}" >> /etc/environment
echo "export JOB_QUEUE_URL={args['job_queue_url']}" >> /etc/environment
echo "export JOB_DLQ_URL={args['job_dlq_url']}" >> /etc/environment

# Install necessary packages
apt-get update
apt-get install -y amazon-efs-utils git python3-pip

# Mount EFS access points
mkdir -p /mnt/training
mkdir -p /mnt/checkpoints

# Mount training directory
mount -t efs -o tls,accesspoint={args['training_ap_id']} {args['efs_dns_name']}:/ /mnt/training
echo "{args['efs_dns_name']}:/ /mnt/training efs _netdev,tls,accesspoint={args['training_ap_id']} 0 0" >> /etc/fstab

# Mount checkpoints directory
mount -t efs -o tls,accesspoint={args['checkpoints_ap_id']} {args['efs_dns_name']}:/ /mnt/checkpoints
echo "{args['efs_dns_name']}:/ /mnt/checkpoints efs _netdev,tls,accesspoint={args['checkpoints_ap_id']} 0 0" >> /etc/fstab

# Set up instance registration
cat > /usr/local/bin/register-instance.sh << 'EOL'
{args['registration_script']}
EOL

chmod +x /usr/local/bin/register-instance.sh
/usr/local/bin/register-instance.sh

# Install required Python packages
pip install wandb boto3 torch transformers

# Clone repository and set up environment
git clone https://github.com/yourusername/your-repo.git /home/ubuntu/training
cd /home/ubuntu/training
pip install -r requirements.txt

# Create symlinks to mounted directories
ln -s /mnt/training /home/ubuntu/training/shared
ln -s /mnt/checkpoints /home/ubuntu/training/checkpoints

# Set up environment for training
echo "export PYTHONPATH=/home/ubuntu/training:$PYTHONPATH" >> /home/ubuntu/.bashrc
echo "export CHECKPOINT_DIR=/mnt/checkpoints" >> /home/ubuntu/.bashrc
echo "export JOB_QUEUE_URL={args['job_queue_url']}" >> /home/ubuntu/.bashrc
echo "export JOB_DLQ_URL={args['job_dlq_url']}" >> /home/ubuntu/.bashrc
"""
)

# Get the latest Deep Learning AMI
dl_ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["amazon"],
    filters=[
        aws.ec2.GetAmiFilterArgs(
            name="name", values=["Deep Learning AMI GPU PyTorch*"]
        ),
        aws.ec2.GetAmiFilterArgs(name="architecture", values=["x86_64"]),
        aws.ec2.GetAmiFilterArgs(name="virtualization-type", values=["hvm"]),
    ],
)

# Create EC2 Launch Template
launch_template = aws.ec2.LaunchTemplate(
    "ml-training-launch-template",
    image_id=dl_ami.id,  # Use the Deep Learning AMI we found
    instance_type="p3.2xlarge",  # Default instance type with GPU
    key_name=key_pair_name,  # Reference existing key pair created in AWS console
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        name=ml_instance_profile.name,
    ),
    vpc_security_group_ids=[ml_security_group.id],
    user_data=user_data_script.apply(
        lambda s: base64.b64encode(s.encode("utf-8")).decode("utf-8")
    ),
    tag_specifications=[
        aws.ec2.LaunchTemplateTagSpecificationArgs(
            resource_type="instance",
            tags={
                "Name": "ML-Training-Instance",
                "Purpose": "ML Model Training",
                "ManagedBy": "Pulumi",
            },
        ),
    ],
)

# Create Auto Scaling Group
asg = aws.autoscaling.Group(
    "ml-training-asg",
    max_size=4,
    min_size=0,
    desired_capacity=0,  # Start with 0 instances, scale up when needed
    vpc_zone_identifiers=default_subnets.ids,
    mixed_instances_policy=aws.autoscaling.GroupMixedInstancesPolicyArgs(
        instances_distribution=aws.autoscaling.GroupMixedInstancesPolicyInstancesDistributionArgs(
            on_demand_base_capacity=0,
            on_demand_percentage_above_base_capacity=0,  # Use 100% spot instances
            spot_allocation_strategy="capacity-optimized",  # Optimize for availability
        ),
        launch_template=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateArgs(
            launch_template_specification=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateLaunchTemplateSpecificationArgs(
                launch_template_id=launch_template.id,
                version="$Latest",
            ),
            overrides=[
                # Define multiple instance types for better spot availability
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="p3.2xlarge",
                ),
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="p3.8xlarge",
                ),
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="p3.16xlarge",
                ),
                # Add g4dn instances as well
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="g4dn.xlarge",
                ),
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="g4dn.2xlarge",
                ),
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="g4dn.4xlarge",
                ),
            ],
        ),
    ),
    # Configure health checks
    health_check_type="EC2",
    health_check_grace_period=300,
    # Add tags
    tags=[
        aws.autoscaling.GroupTagArgs(
            key="Name",
            value="ML-Training-ASG",
            propagate_at_launch=True,
        ),
        aws.autoscaling.GroupTagArgs(
            key="Purpose",
            value="ML Training",
            propagate_at_launch=True,
        ),
    ],
)

# Create a simple scaling policy based on CPU utilization
scaling_policy = aws.autoscaling.Policy(
    "ml-training-scaling-policy",
    autoscaling_group_name=asg.name,
    policy_type="TargetTrackingScaling",
    target_tracking_configuration=aws.autoscaling.PolicyTargetTrackingConfigurationArgs(
        predefined_metric_specification=aws.autoscaling.PolicyTargetTrackingConfigurationPredefinedMetricSpecificationArgs(
            predefined_metric_type="ASGAverageCPUUtilization",
        ),
        target_value=70.0,  # Target CPU utilization of 70%
        disable_scale_in=False,
    ),
)

# ML Infrastructure Exports
pulumi.export("instance_registry_table", instance_registry.table_name)
pulumi.export("efs_dns_name", efs_storage.file_system_dns_name)
pulumi.export("efs_training_access_point", efs_storage.training_access_point_id)
pulumi.export("efs_checkpoints_access_point", efs_storage.checkpoints_access_point_id)
pulumi.export("spot_interruption_sns_topic", spot_handler.sns_topic_arn)
pulumi.export("launch_template_id", launch_template.id)
pulumi.export("auto_scaling_group_name", asg.name)
pulumi.export("deep_learning_ami_id", dl_ami.id)
pulumi.export("deep_learning_ami_name", dl_ami.name)
pulumi.export("job_queue_url", job_queue.get_queue_url())
pulumi.export("job_dlq_url", job_queue.get_dlq_url())
