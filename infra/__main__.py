"""Main Pulumi program for AWS infrastructure."""

import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions
import base64

# Import our infrastructure components
import s3_website  # noqa: F401
import api_gateway  # noqa: F401
import raw_bucket  # Import raw bucket module
from dynamo_db import (
    dynamodb_table,
)  # Import DynamoDB table from original code
from spot_interruption import SpotInterruptionHandler
from efs_storage import EFSStorage
from instance_registry import InstanceRegistry
from job_queue import JobQueue
from ml_packages import MLPackageBuilder
from networking import VpcForCodeBuild  # Import the new VPC component

# Import other necessary components
try:
    import lambda_layer  # noqa: F401
    from routes.health_check.infra import health_check_lambda  # noqa: F401
except ImportError:
    # These may not be available in all environments
    pass
import step_function

# Create the dedicated VPC network infrastructure
network = VpcForCodeBuild("codebuild-network")

# --- Removed Config reading for VPC resources ---

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
key_pair_name = f"portfolio-receipt-{stack}"  # Use existing key pairs created in AWS console

# Create EC2 Instance Profile for ML training instances
ml_training_role = aws.iam.Role(
    "ml-training-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Effect": "Allow"
        }]
    }""",
)

# Attach basic policies for S3 access
s3_policy_attachment = aws.iam.RolePolicyAttachment(
    "ml-s3-policy-attachment",
    role=ml_training_role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
)

# Create spot interruption handler
spot_handler = SpotInterruptionHandler(
    "ml-training",
    instance_role_name=ml_training_role.name,
)

# Create SNS policy for spot interruption notifications
sns_policy = aws.iam.Policy(
    "ml-training-sns-policy",
    description="Allow ML training instances to subscribe to SNS topics",
    policy=pulumi.Output.all(
        spot_topic_arn=spot_handler.sns_topic_arn,
    ).apply(
        lambda args: f"""{{
            "Version": "2012-10-17",
            "Statement": [
                {{
                    "Effect": "Allow",
                    "Action": [
                        "sns:Subscribe",
                        "sns:Unsubscribe",
                        "sns:ListSubscriptionsByTopic"
                    ],
                    "Resource": "{args['spot_topic_arn']}"
                }}
            ]
        }}"""
    ),
)

# Attach SNS policy to the role
sns_policy_attachment = aws.iam.RolePolicyAttachment(
    "ml-sns-policy-attachment",
    role=ml_training_role.name,
    policy_arn=sns_policy.arn,
    opts=ResourceOptions(
        depends_on=[ml_training_role, spot_handler.sns_topic]
    ),
)

# Create instance profile
ml_instance_profile = aws.iam.InstanceProfile(
    "ml-instance-profile", role=ml_training_role.name
)


# Create EFS storage, referencing the new VPC and SG from the network component
efs_storage = EFSStorage(
    "ml-training-vpc",
    vpc_id=network.vpc_id,  # Use network component output
    subnet_ids=network.private_subnet_ids,  # Use network component output
    security_group_ids=[network.security_group_id],  # Use new security group
    instance_role_name=ml_training_role.name,
    lifecycle_policies=[{"transition_to_ia": "AFTER_30_DAYS"}],
    opts=pulumi.ResourceOptions(
        depends_on=[network],
        replace_on_changes=["vpc_id", "subnet_ids", "security_group_ids"],
        delete_before_replace=True,
    ),  # Depend on network creation
)

# Create VPC endpoints in parallel, using the new VPC and SG from the network component
vpc_endpoints = []
for service in [
    "com.amazonaws.us-east-1.codebuild",
    "com.amazonaws.us-east-1.ecr.api",
    "com.amazonaws.us-east-1.ecr.dkr",
    "com.amazonaws.us-east-1.logs",
    "com.amazonaws.us-east-1.elasticfilesystem",
]:
    private_dns = False  # Keep disabled as per previous findings
    endpoint = aws.ec2.VpcEndpoint(
        f"codebuild-{service.split('.')[-1]}",
        vpc_id=network.vpc_id,  # Use network component output
        service_name=service,
        vpc_endpoint_type="Interface",
        subnet_ids=network.private_subnet_ids,  # Use network component output
        security_group_ids=[
            network.security_group_id
        ],  # Use new security group
        private_dns_enabled=private_dns,
        opts=pulumi.ResourceOptions(
            depends_on=[network]
        ),  # Depend on network creation
    )
    vpc_endpoints.append(endpoint)

# --- Security Group Rule for EFS is now handled within the VpcForCodeBuild component ---
# --- or should be, if not, add it back referencing network outputs ---
# Re-adding here explicitly for clarity, referencing component outputs
# aws.ec2.SecurityGroupRule(
#     "codebuild-efs-nfs-explicit",  # Renamed to avoid conflict if defined in component
#     type="ingress",
#     from_port=2049,
#     to_port=2049,
#     protocol="tcp",
#     security_group_id=network.efs_security_group_id, # Use network component output
#     source_security_group_id=network.codebuild_security_group_id, # Use network component output
#     description="Allow NFS from CodeBuild SG (Explicit)",
#     opts=pulumi.ResourceOptions(depends_on=[network]), # Depend on network creation
# )

# Create instance registry for auto-registration
instance_registry = InstanceRegistry(
    "ml-training",
    instance_role_name=ml_training_role.name,
    dynamodb_table_name=dynamodb_table.name,
    ttl_hours=2,
)

# Create job queue for training job management
job_queue = JobQueue(
    "ml-training",
    env=stack,
    tags={
        "Purpose": "ML Training Job Management",
        "ManagedBy": "Pulumi",
    },
)

# Update the IAM role to allow access to SQS
sqs_policy_document = pulumi.Output.all(
    queue_arn=job_queue.get_queue_arn(), dlq_arn=job_queue.get_dlq_arn()
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

sqs_policy = aws.iam.Policy(
    "ml-training-sqs-policy",
    description="Allow ML training instances to access SQS queues",
    policy=sqs_policy_document,
)

sqs_policy_attachment = aws.iam.RolePolicyAttachment(
    "ml-sqs-policy-attachment",
    role=ml_training_role.name,
    policy_arn=sqs_policy.arn,
    opts=ResourceOptions(depends_on=[ml_training_role]),
)

# IAM policy for EFS access required by EC2 instances
efs_ec2_policy = aws.iam.Policy(
    "ml-training-efs-ec2-policy",
    description="Allow EC2 instances to use EFS and describe necessary resources",
    policy=pulumi.Output.all(
        file_system_id=efs_storage.file_system_id,
        region=aws.config.region,
        account_id=aws.get_caller_identity().account_id,
    ).apply(
        lambda args: f"""{{
            "Version": "2012-10-17",
            "Statement": [
                {{
                    "Effect": "Allow",
                    "Action": [
                        "ec2:DescribeAvailabilityZones",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeNetworkInterfaces",
                        "elasticfilesystem:DescribeMountTargets",
                        "elasticfilesystem:DescribeFileSystems"
                    ],
                    "Resource": "*"
                }},
                {{
                    "Effect": "Allow",
                    "Action": [
                        "elasticfilesystem:ClientMount",
                        "elasticfilesystem:ClientWrite"
                    ],
                    "Resource": "arn:aws:elasticfilesystem:{args['region']}:{args['account_id']}:file-system/{args['file_system_id']}"
                }}
            ]
        }}"""
    ),
)

# Attach this policy to your EC2 instance role
efs_ec2_policy_attachment = aws.iam.RolePolicyAttachment(
    "ml-training-efs-ec2-policy-attachment",
    role=ml_training_role.name,
    policy_arn=efs_ec2_policy.arn,
    opts=pulumi.ResourceOptions(depends_on=[ml_training_role, efs_ec2_policy]),
)

# Generate instance registration script
registration_script = instance_registry.create_registration_script(
    leader_election_enabled=True
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

# Get ML training configuration
ml_training_config = pulumi.Config("ml-training")
force_rebuild = ml_training_config.get_bool("force-rebuild") or False

# Create the package builder using VPC info from network component
ml_package_builder = MLPackageBuilder(
    f"receipt-trainer-{stack}",
    packages=["receipt_trainer"],
    supplementary_packages=["receipt_dynamo"],
    python_version="3.9",
    vpc_id=network.vpc_id,  # Use network component output
    subnet_ids=network.private_subnet_ids,  # Use network component output
    security_group_ids=[network.security_group_id],  # Use new security group
    efs_storage_id=efs_storage.file_system_id,  # Get EFS ID from EFS component
    efs_access_point_id=efs_storage.training_access_point_id,  # Get AP ID from EFS component
    efs_dns_name=efs_storage.file_system_dns_name,  # Get DNS name from EFS component
    force_rebuild=force_rebuild,
    vpc_endpoints=vpc_endpoints,  # Pass created endpoints
    opts=pulumi.ResourceOptions(depends_on=[network] + vpc_endpoints),
)

# Create EC2 Launch Template, referencing SG from network component
launch_template = aws.ec2.LaunchTemplate(
    "ml-training-launch-template",
    image_id=dl_ami.id,
    instance_type="g4dn.xlarge",
    key_name=key_pair_name,
    iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
        name=ml_instance_profile.name,
    ),
    network_interfaces=[
        aws.ec2.LaunchTemplateNetworkInterfaceArgs(
            associate_public_ip_address=True,  # Ensure instances in private subnets don't get public IPs
            security_groups=[
                network.security_group_id
            ],  # Use new security group
            # subnet_id is determined by the ASG's vpc_zone_identifiers
        )
    ],
    user_data=pulumi.Output.all(
        efs_dns_name=efs_storage.file_system_dns_name,
        training_ap_id=efs_storage.training_access_point_id,
        checkpoints_ap_id=efs_storage.checkpoints_access_point_id,
        dynamo_table_name=dynamodb_table.name,
        spot_topic_arn=spot_handler.sns_topic_arn,
        job_queue_url=job_queue.get_queue_url(),
        bucket_name=ml_package_builder.artifact_bucket.bucket,
    ).apply(
        lambda args: base64.b64encode(
            f"""#!/bin/bash
# Install required utilities
yum update -y
yum install -y amazon-efs-utils awscli jq

# Activate the PyTorch Conda environment (adjust path/environment name as needed)
source /opt/conda/bin/activate pytorch

# Create mount points
mkdir -p /mnt/training
mkdir -p /mnt/checkpoints

# Mount EFS access points
mount -t efs -o tls,accesspoint={args['training_ap_id']} {args['efs_dns_name']}:/ /mnt/training
echo "{args['efs_dns_name']}:/ /mnt/training efs _netdev,tls,accesspoint={args['training_ap_id']} 0 0" >> /etc/fstab

mount -t efs -o tls,accesspoint={args['checkpoints_ap_id']} {args['efs_dns_name']}:/ /mnt/checkpoints
echo "{args['efs_dns_name']}:/ /mnt/checkpoints efs _netdev,tls,accesspoint={args['checkpoints_ap_id']} 0 0" >> /etc/fstab

# Get instance metadata
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type)
AZ=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone)
IP_ADDRESS=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
IS_SPOT=$(curl -s http://169.254.169.254/latest/meta-data/instance-life-cycle | grep -q "spot" && echo "true" || echo "false")
# Determine GPU count in a generic manner
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_COUNT=$(nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null)
    if [[ $GPU_COUNT =~ ^[0-9]+$ ]]; then
        echo "Detected NVIDIA GPUs: $GPU_COUNT"
    else
        echo "nvidia-smi did not return a valid count. Assuming GPU_COUNT=0."
        GPU_COUNT=0
    fi
else
    echo "nvidia-smi not found. Setting GPU_COUNT=0."
    GPU_COUNT=0
fi

#TODO: use the python package to register the instance
# Register instance with DynamoDB using Instance entity schema
aws dynamodb put-item \
    --table-name {args['dynamo_table_name']} \
    --item '{{"PK":{{"S":"INSTANCE#$INSTANCE_ID"}},"SK":{{"S":"INSTANCE"}},"GSI1PK":{{"S":"STATUS#running"}},"GSI1SK":{{"S":"INSTANCE#$INSTANCE_ID"}},"TYPE":{{"S":"INSTANCE"}},"instance_type":{{"S":"$INSTANCE_TYPE"}},"gpu_count":{{"N":"$GPU_COUNT"}},"status":{{"S":"running"}},"launched_at":{{"S":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}},"ip_address":{{"S":"$IP_ADDRESS"}},"availability_zone":{{"S":"$AZ"}},"is_spot":{{"BOOL":$IS_SPOT}},"health_status":{{"S":"healthy"}}}}' \
    --region $REGION

# Subscribe to spot interruption notifications
aws sns subscribe \
    --topic-arn {args['spot_topic_arn']} \
    --protocol http \
    --notification-endpoint http://169.254.169.254/latest/meta-data/spot/instance-action \
    --region $REGION

# Download and setup training code
cd /mnt/training
aws s3 cp s3://{args['bucket_name']}/output/receipt_trainer/wheels/receipt_trainer-0.1.0-py3-none-any.whl /tmp/

# Install the package with pip (this will also install dependencies if specified in setup.py)
pip install /tmp/receipt_trainer-0.1.0-py3-none-any.whl

# (Optional) Verify installation of key modules
python -c "import receipt_trainer; print('ReceiptTrainer module loaded successfully')"
python -c "import transformers; print('Transformers version:', getattr(transformers, '__version__', 'unknown'))"
python -c "import datasets; print('Datasets version:', getattr(datasets, '__version__', 'unknown'))"

# Start training job
cd /mnt/training
python -m receipt_trainer.train \
    --checkpoint-dir /mnt/checkpoints \
    --job-queue {args['job_queue_url']} \
    --instance-id $INSTANCE_ID \
    --region $REGION &

# Monitor spot interruption
while true; do
    if [ -f /tmp/spot-interruption-notice ]; then
        #TODO: use the python package to update the instance
        # Handle spot interruption
        aws dynamodb update-item \
            --table-name {args['dynamo_table_name']} \
            --key '{{"PK":{{"S":"INSTANCE#$INSTANCE_ID"}},"SK":{{"S":"INSTANCE"}}}}' \
            --update-expression "SET #s = :s, #t = :t, #h = :h" \
            --expression-attribute-names '{{"#s":"status","#t":"launched_at","#h":"health_status"}}' \
            --expression-attribute-values '{{":s":{{"S":"terminated"}},":t":{{"S":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}},":h":{{"S":"unhealthy"}}}}' \
            --region $REGION
        break
    fi
    sleep 5
done
""".encode(
                "utf-8"
            )
        ).decode("utf-8")
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
    opts=pulumi.ResourceOptions(
        depends_on=[network]
    ),  # Depend on network creation
)

# Create Auto Scaling Group using private subnets from network component
asg = aws.autoscaling.Group(
    "ml-training-asg",
    max_size=4,
    min_size=0,
    desired_capacity=0,
    vpc_zone_identifiers=network.private_subnet_ids,  # Use network component output
    mixed_instances_policy=aws.autoscaling.GroupMixedInstancesPolicyArgs(
        instances_distribution=aws.autoscaling.GroupMixedInstancesPolicyInstancesDistributionArgs(
            on_demand_base_capacity=0,
            on_demand_percentage_above_base_capacity=0,
            spot_allocation_strategy="capacity-optimized",
        ),
        launch_template=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateArgs(
            launch_template_specification=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateLaunchTemplateSpecificationArgs(
                launch_template_id=launch_template.id,
                version="$Latest",
            ),
            overrides=[
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="g4dn.xlarge",
                ),
                aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
                    instance_type="g5.xlarge",
                ),
            ],
        ),
    ),
    health_check_type="EC2",
    health_check_grace_period=300,
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
    opts=pulumi.ResourceOptions(
        depends_on=[launch_template]
    ),  # Depend on launch template
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
        target_value=70.0,
        disable_scale_in=False,
    ),
)

# --- Adjusted Exports ---
pulumi.export("vpc_id", network.vpc_id)
pulumi.export("private_subnet_ids", network.private_subnet_ids)
pulumi.export("public_subnet_ids", network.public_subnet_ids)
pulumi.export(
    "security_group_id", network.security_group_id
)  # Updated export name

pulumi.export("instance_registry_table", instance_registry.table_name)
pulumi.export("efs_dns_name", efs_storage.file_system_dns_name)
pulumi.export(
    "efs_training_access_point", efs_storage.training_access_point_id
)
pulumi.export(
    "efs_checkpoints_access_point", efs_storage.checkpoints_access_point_id
)
pulumi.export("spot_interruption_sns_topic", spot_handler.sns_topic_arn)
pulumi.export("launch_template_id", launch_template.id)
pulumi.export("auto_scaling_group_name", asg.name)
pulumi.export("deep_learning_ami_id", dl_ami.id)
pulumi.export("deep_learning_ami_name", dl_ami.name)
pulumi.export("job_queue_url", job_queue.get_queue_url())
pulumi.export("job_dlq_url", job_queue.get_dlq_url())

pulumi.export("training_ami_id", dl_ami.id)
pulumi.export("training_instance_profile_name", ml_instance_profile.name)


def get_first_subnet(subnets):
    return subnets[0]


pulumi.export(
    "training_subnet_id", network.private_subnet_ids.apply(get_first_subnet)
)

pulumi.export("training_efs_id", efs_storage.file_system_id)
pulumi.export("instance_registry_table_name", instance_registry.table_name)
pulumi.export("ml_packages_built", ml_package_builder.packages)
