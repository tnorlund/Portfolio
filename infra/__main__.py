"""Main Pulumi program for AWS infrastructure."""

import base64
import os

import api_gateway  # noqa: F401
import pulumi
import pulumi_aws as aws

# Auto-enable Docker BuildKit based on Pulumi config
config = pulumi.Config("portfolio")
if config.get_bool("docker-buildkit") != False:  # Default to True if not set
    os.environ["DOCKER_BUILDKIT"] = "1"
    os.environ["COMPOSE_DOCKER_CLI_BUILD"] = (
        "1"  # Also enable for docker-compose
    )

    # Warning if BuildKit might not be inherited by Docker
    if not os.environ.get("DOCKER_BUILDKIT"):
        print("⚠️  DOCKER_BUILDKIT not set in parent environment")
        print("   For best performance, run: export DOCKER_BUILDKIT=1")
        print("   Or use: ./pulumi_up.sh instead of 'pulumi up'")
    else:
        print("✓ Docker BuildKit enabled for faster builds")

# Import our infrastructure components
import s3_website  # noqa: F401
from dynamo_db import (
    dynamodb_table,  # Import DynamoDB table from original code
)

from embedding_step_functions import EmbeddingInfrastructure
from notifications import NotificationSystem
from pulumi import ResourceOptions
from raw_bucket import raw_bucket  # Import the actual bucket instance
from s3_website import site_bucket  # Import the site bucket instance
from upload_images import UploadImages
from validate_merchant_step_functions import ValidateMerchantStepFunctions
from validation_by_merchant import ValidationByMerchantStepFunction
from validation_pipeline import ValidationPipeline

from chromadb_compaction import create_chromadb_compaction_infrastructure

# Using the optimized docker-build based base images with scoped contexts
from base_images.base_images import BaseImages

# from spot_interruption import SpotInterruptionHandler
# from efs_storage import EFSStorage
# from instance_registry import InstanceRegistry
# from job_queue import JobQueue
# from ml_packages import MLPackageBuilder
# from networking import VpcForCodeBuild  # Import the new VPC component
from word_label_step_functions import WordLabelStepFunctions

# Import other necessary components
try:
    # import lambda_layer  # noqa: F401
    import fast_lambda_layer  # noqa: F401
    from lambda_functions.label_count_cache_updater.infra import (  # noqa: F401
        label_count_cache_updater_lambda,
    )
    from routes.health_check.infra import health_check_lambda  # noqa: F401
except ImportError:
    # These may not be available in all environments
    pass
import step_function
from step_function_enhanced import create_enhanced_receipt_processor

# Create the dedicated VPC network infrastructure
# network = VpcForCodeBuild("codebuild-network")

# --- Removed Config reading for VPC resources ---

pulumi.export("region", aws.config.region)

# Open template readme and read contents into stack output
try:
    with open("./Pulumi.README.md") as f:
        pulumi.export("readme", f.read())
except FileNotFoundError:
    pulumi.export("readme", "README file not found")

# Create notification system
# Get email endpoints from portfolio config
portfolio_config = pulumi.Config("portfolio")
notification_emails = portfolio_config.get_object("notification_emails") or []

notification_system = NotificationSystem(
    "receipt-processing",
    email_endpoints=notification_emails,
    tags={
        "Environment": pulumi.get_stack(),
        "Purpose": "Infrastructure Monitoring",
    },
)

# Create base images first - they're used by multiple components
base_images = BaseImages("base-images", stack=pulumi.get_stack())

word_label_step_functions = WordLabelStepFunctions("word-label-step-functions")
validate_merchant_step_functions = ValidateMerchantStepFunctions(
    "validate-merchant"
)
validation_pipeline = ValidationPipeline("validation-pipeline")

# Create ChromaDB compaction infrastructure first (it owns the queues)
chromadb_infrastructure = create_chromadb_compaction_infrastructure(
    name=f"chromadb-{pulumi.get_stack()}",
    dynamodb_table_arn=dynamodb_table.arn,
    dynamodb_stream_arn=dynamodb_table.stream_arn,
    base_images=base_images,
)

# Create embedding infrastructure using ChromaDB's queues
embedding_infrastructure = EmbeddingInfrastructure(
    "embedding-infra", 
    chromadb_queues=chromadb_infrastructure.chromadb_queues,
    base_images=base_images
)
validation_by_merchant_step_functions = ValidationByMerchantStepFunction(
    "validation-by-merchant"
)
upload_images = UploadImages(
    "upload-images", raw_bucket=raw_bucket, site_bucket=site_bucket
)

# Create the enhanced receipt processor with error handling
enhanced_receipt_processor = create_enhanced_receipt_processor(
    notification_system
)

pulumi.export("ocr_job_queue_url", upload_images.ocr_queue.url)
pulumi.export("ocr_results_queue_url", upload_images.ocr_results_queue.url)

# Export notification topics
pulumi.export(
    "step_function_failure_topic_arn",
    notification_system.step_function_topic_arn,
)
pulumi.export(
    "critical_error_topic_arn", notification_system.critical_error_topic_arn
)

# Export enhanced step function ARN
pulumi.export("enhanced_receipt_processor_arn", enhanced_receipt_processor.arn)
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

# ChromaDB compaction infrastructure already created above

# Create spot interruption handler
# spot_handler = SpotInterruptionHandler(
#     "ml-training",
#     instance_role_name=ml_training_role.name,
# )

# Create SNS policy for spot interruption notifications
# sns_policy = aws.iam.Policy(
#     "ml-training-sns-policy",
#     description="Allow ML training instances to subscribe to SNS topics",
#     policy=pulumi.Output.all(
#         spot_topic_arn=spot_handler.sns_topic_arn,
#     ).apply(
#         lambda args: f"""{{
#             "Version": "2012-10-17",
#             "Statement": [
#                 {{
#                     "Effect": "Allow",
#                     "Action": [
#                         "sns:Subscribe",
#                         "sns:Unsubscribe",
#                         "sns:ListSubscriptionsByTopic"
#                     ],
#                     "Resource": "{args['spot_topic_arn']}"
#                 }}
#             ]
#         }}"""
#     ),
# )

# Attach SNS policy to the role
# sns_policy_attachment = aws.iam.RolePolicyAttachment(
#     "ml-sns-policy-attachment",
#     role=ml_training_role.name,
#     policy_arn=sns_policy.arn,
#     opts=ResourceOptions(depends_on=[ml_training_role, spot_handler.sns_topic]),
# )

# Create instance profile
# ml_instance_profile = aws.iam.InstanceProfile(
#     "ml-instance-profile", role=ml_training_role.name
# )


# Create EFS storage, referencing the new VPC and SG from the network component
# efs_storage = EFSStorage(
#     "ml-training-vpc",
#     vpc_id=network.vpc_id,  # Use network component output
#     subnet_ids=network.private_subnet_ids,  # Use network component output
#     security_group_ids=[network.security_group_id],  # Use new security group
#     instance_role_name=ml_training_role.name,
#     lifecycle_policies=[{"transition_to_ia": "AFTER_30_DAYS"}],
#     opts=pulumi.ResourceOptions(
#         depends_on=[network],
#         replace_on_changes=["vpc_id", "subnet_ids", "security_group_ids"],
#         delete_before_replace=True,
#     ),  # Depend on network creation
# )

# Create VPC endpoints in parallel, using the new VPC and SG from the network component
# vpc_endpoints = []
# for service in [
#     "com.amazonaws.us-east-1.codebuild",
#     "com.amazonaws.us-east-1.ecr.api",
#     "com.amazonaws.us-east-1.ecr.dkr",
#     "com.amazonaws.us-east-1.logs",
#     "com.amazonaws.us-east-1.elasticfilesystem",
# ]:
#     private_dns = False  # Keep disabled as per previous findings
#     endpoint = aws.ec2.VpcEndpoint(
#         f"codebuild-{service.split('.')[-1]}",
#         vpc_id=network.vpc_id,  # Use network component output
#         service_name=service,
#         vpc_endpoint_type="Interface",
#         subnet_ids=network.private_subnet_ids,  # Use network component output
#         security_group_ids=[network.security_group_id],  # Use new security group
#         private_dns_enabled=private_dns,
#         opts=pulumi.ResourceOptions(depends_on=[network]),  # Depend on network creation
#     )
#     vpc_endpoints.append(endpoint)

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
# instance_registry = InstanceRegistry(
#     "ml-training",
#     instance_role_name=ml_training_role.name,
#     dynamodb_table_name=dynamodb_table.name,
#     ttl_hours=2,
# )

# Create job queue for training job management
# job_queue = JobQueue(
#     "ml-training",
#     env=stack,
#     tags={
#         "Purpose": "ML Training Job Management",
#         "ManagedBy": "Pulumi",
#     },
# )

# Update the IAM role to allow access to SQS
# sqs_policy_document = pulumi.Output.all(
#     queue_arn=job_queue.get_queue_arn(), dlq_arn=job_queue.get_dlq_arn()
# ).apply(
#     lambda args: f"""{{
#         "Version": "2012-10-17",
#         "Statement": [
#             {{
#                 "Effect": "Allow",
#                 "Action": [
#                     "sqs:ReceiveMessage",
#                     "sqs:DeleteMessage",
#                     "sqs:GetQueueAttributes",
#                     "sqs:GetQueueUrl",
#                     "sqs:SendMessage",
#                     "sqs:ChangeMessageVisibility"
#                 ],
#                 "Resource": [
#                     "{args['queue_arn']}",
#                     "{args['dlq_arn']}"
#                 ]
#             }}
#         ]
#     }}"""
# )

# sqs_policy = aws.iam.Policy(
#     "ml-training-sqs-policy",
#     description="Allow ML training instances to access SQS queues",
#     policy=sqs_policy_document,
# )

# sqs_policy_attachment = aws.iam.RolePolicyAttachment(
#     "ml-sqs-policy-attachment",
#     role=ml_training_role.name,
#     policy_arn=sqs_policy.arn,
#     opts=ResourceOptions(depends_on=[ml_training_role]),
# )

# # IAM policy for EFS access required by EC2 instances
# efs_ec2_policy = aws.iam.Policy(
#     "ml-training-efs-ec2-policy",
#     description="Allow EC2 instances to use EFS and describe necessary resources",
#     policy=pulumi.Output.all(
#         file_system_id=efs_storage.file_system_id,
#         region=aws.config.region,
#         account_id=aws.get_caller_identity().account_id,
#     ).apply(
#         lambda args: f"""{{
#             "Version": "2012-10-17",
#             "Statement": [
#                 {{
#                     "Effect": "Allow",
#                     "Action": [
#                         "ec2:DescribeAvailabilityZones",
#                         "ec2:DescribeSubnets",
#                         "ec2:DescribeNetworkInterfaces",
#                         "elasticfilesystem:DescribeMountTargets",
#                         "elasticfilesystem:DescribeFileSystems"
#                     ],
#                     "Resource": "*"
#                 }},
#                 {{
#                     "Effect": "Allow",
#                     "Action": [
#                         "elasticfilesystem:ClientMount",
#                         "elasticfilesystem:ClientWrite"
#                     ],
#                     "Resource": "arn:aws:elasticfilesystem:{args['region']}:{args['account_id']}:file-system/{args['file_system_id']}"
#                 }}
#             ]
#         }}"""
#     ),
# )

# # Attach this policy to your EC2 instance role
# efs_ec2_policy_attachment = aws.iam.RolePolicyAttachment(
#     "ml-training-efs-ec2-policy-attachment",
#     role=ml_training_role.name,
#     policy_arn=efs_ec2_policy.arn,
#     opts=pulumi.ResourceOptions(depends_on=[ml_training_role, efs_ec2_policy]),
# )

# # Generate instance registration script
# registration_script = instance_registry.create_registration_script(
#     leader_election_enabled=True
# )

# # Get the latest Deep Learning AMI
# dl_ami = aws.ec2.get_ami(
#     most_recent=True,
#     owners=["amazon"],
#     filters=[
#         aws.ec2.GetAmiFilterArgs(
#             name="name", values=["Deep Learning AMI GPU PyTorch*"]
#         ),
#         aws.ec2.GetAmiFilterArgs(name="architecture", values=["x86_64"]),
#         aws.ec2.GetAmiFilterArgs(name="virtualization-type", values=["hvm"]),
#     ],
# )

# # Get ML training configuration
# ml_training_config = pulumi.Config("ml-training")
# force_rebuild = ml_training_config.get_bool("force-rebuild") or False

# # Create the package builder using VPC info from network component
# ml_package_builder = MLPackageBuilder(
#     f"receipt-trainer-{stack}",
#     packages=["receipt_trainer"],
#     supplementary_packages=["receipt_dynamo"],
#     python_version="3.12",
#     vpc_id=network.vpc_id,  # Use network component output
#     subnet_ids=network.private_subnet_ids,  # Use network component output
#     security_group_ids=[network.security_group_id],  # Use new security group
#     efs_storage_id=efs_storage.file_system_id,  # Get EFS ID from EFS component
#     efs_access_point_id=efs_storage.training_access_point_id,  # Get AP ID from EFS component
#     efs_dns_name=efs_storage.file_system_dns_name,  # Get DNS name from EFS component
#     force_rebuild=force_rebuild,
#     vpc_endpoints=vpc_endpoints,  # Pass created endpoints
#     opts=pulumi.ResourceOptions(depends_on=[network] + vpc_endpoints),
# )

# # Create EC2 Launch Template, referencing SG from network component
# launch_template = aws.ec2.LaunchTemplate(
#     "ml-training-launch-template",
#     image_id=dl_ami.id,
#     instance_type="g4dn.xlarge",
#     key_name=key_pair_name,
#     iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
#         name=ml_instance_profile.name,
#     ),
#     network_interfaces=[
#         aws.ec2.LaunchTemplateNetworkInterfaceArgs(
#             associate_public_ip_address=True,  # Ensure instances in private subnets don't get public IPs
#             security_groups=[network.security_group_id],  # Use new security group
#             # subnet_id is determined by the ASG's vpc_zone_identifiers
#         )
#     ],
#     user_data=pulumi.Output.all(
#         efs_dns_name=efs_storage.file_system_dns_name,
#         training_ap_id=efs_storage.training_access_point_id,
#         checkpoints_ap_id=efs_storage.checkpoints_access_point_id,
#         dynamo_table_name=dynamodb_table.name,
#         spot_topic_arn=spot_handler.sns_topic_arn,
#         job_queue_url=job_queue.get_queue_url(),
#         bucket_name=ml_package_builder.artifact_bucket.bucket,
#     ).apply(
#         lambda args: base64.b64encode(
#             f"""#!/bin/bash
# # Install required utilities
# yum update -y
# yum install -y amazon-efs-utils awscli jq

# # Set environment variable for DynamoDB table name
# export DYNAMO_TABLE_NAME={args['dynamo_table_name']}

# # Activate the PyTorch Conda environment (adjust path/environment name as needed)
# source /opt/conda/bin/activate pytorch

# # Create mount points
# mkdir -p /mnt/training || echo "Failed to create training mount point"
# mkdir -p /mnt/checkpoints || echo "Failed to create checkpoints mount point"

# # Mount EFS access points
# mount -t efs -o tls,accesspoint={args['training_ap_id']} {args['efs_dns_name']}:/ /mnt/training || echo "Failed to mount EFS training"
# echo "{args['efs_dns_name']}:/ /mnt/training efs _netdev,tls,accesspoint={args['training_ap_id']} 0 0" >> /etc/fstab || echo "Failed to add EFS training to fstab"

# mount -t efs -o tls,accesspoint={args['checkpoints_ap_id']} {args['efs_dns_name']}:/ /mnt/checkpoints || echo "Failed to mount EFS checkpoints"
# echo "{args['efs_dns_name']}:/ /mnt/checkpoints efs _netdev,tls,accesspoint={args['checkpoints_ap_id']} 0 0" >> /etc/fstab || echo "Failed to add EFS checkpoints to fstab"

# # Get instance metadata
# export INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
# export REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)
# export INSTANCE_TYPE=$(curl -s http://169.254.169.254/latest/meta-data/instance-type)
# export AZ=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone)
# export IP_ADDRESS=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
# export IS_SPOT=$(curl -s http://169.254.169.254/latest/meta-data/instance-life-cycle | grep -q "spot" && echo "true" || echo "false")
# # Determine GPU count in a generic manner
# if command -v nvidia-smi >/dev/null 2>&1; then
#     export GPU_COUNT=$(nvidia-smi --query-gpu=count --format=csv,noheader 2>/dev/null)
#     if [[ $GPU_COUNT =~ ^[0-9]+$ ]]; then
#         echo "Detected NVIDIA GPUs: $GPU_COUNT"
#     else
#         echo "nvidia-smi did not return a valid count. Assuming GPU_COUNT=0."
#         export GPU_COUNT=0
#     fi
# else
#     echo "nvidia-smi not found. Setting GPU_COUNT=0."
#     export GPU_COUNT=0
# fi

# cat <<EOF
# ###############################
# # Instance Metadata Summary
# ###############################
# Instance ID:       $INSTANCE_ID
# Region:            $REGION
# Instance Type:     $INSTANCE_TYPE
# Availability Zone: $AZ
# Local IP:          $IP_ADDRESS
# Is Spot Instance:  $IS_SPOT
# Detected GPUs:     $GPU_COUNT
# ###############################
# EOF

# # Download and setup training code
# cd /mnt/training
# aws s3 cp s3://{args['bucket_name']}/output/receipt_trainer/wheels/receipt_trainer-0.1.0-py3-none-any.whl /tmp/ || echo "Failed to download receipt_trainer"
# aws s3 cp s3://{args['bucket_name']}/output/receipt_dynamo/wheels/receipt_dynamo-0.1.0-py3-none-any.whl /tmp/ || echo "Failed to download receipt_dynamo"

# # Install the package with pip (this will also install dependencies if specified in setup.py)
# pip install /tmp/receipt_dynamo-0.1.0-py3-none-any.whl
# pip install /tmp/receipt_trainer-0.1.0-py3-none-any.whl

# # (Optional) Verify installation of key modules
# python -c "import receipt_trainer; print('ReceiptTrainer module loaded successfully')"
# python -c "import transformers; print('Transformers version:', getattr(transformers, '__version__', 'unknown'))"
# python -c "import datasets; print('Datasets version:', getattr(datasets, '__version__', 'unknown'))"

# # Register instance using the receipt_dynamo package
# python -c "
# import os
# from datetime import datetime

# from receipt_dynamo import DynamoClient, Instance

# table_name = os.environ['DYNAMO_TABLE_NAME']
# region = os.environ['REGION']

# instance_id = os.environ['INSTANCE_ID']
# instance_type = os.environ['INSTANCE_TYPE']
# gpu_count = int(os.environ['GPU_COUNT'])
# ip_address = os.environ['IP_ADDRESS']
# availability_zone = os.environ['AZ']
# is_spot = (os.environ['IS_SPOT'].lower() == 'true')

# dynamo_client = DynamoClient(table_name=table_name, region_name=region)

# instance = Instance(
#     instance_id=instance_id,
#     instance_type=instance_type,
#     gpu_count=gpu_count,
#     status='pending',
#     launched_at=datetime.utcnow().isoformat(),
#     ip_address=ip_address,
#     availability_zone=availability_zone,
#     is_spot=is_spot,
#     health_status='healthy',
# )

# dynamo_client.add_instance(instance)
# " || echo "Failed to register instance"

# # Subscribe to spot interruption notifications
# aws sns subscribe \
#     --topic-arn {args['spot_topic_arn']} \
#     --protocol http \
#     --notification-endpoint http://169.254.169.254/latest/meta-data/spot/instance-action \
#     --region $REGION || echo "Failed to subscribe to spot interruption notifications"

# # Start training job
# cd /mnt/training
# python -m receipt_trainer.train \
#     --checkpoint-dir /mnt/checkpoints \
#     --job-queue {args['job_queue_url']} \
#     --instance-id $INSTANCE_ID \
#     --region $REGION &

# # Monitor spot interruption
# while true; do
#     if [ -f /tmp/spot-interruption-notice ]; then
#         # Update the instance using receipt_dynamo
#         python -c "
# import os
# from datetime import datetime

# from receipt_dynamo import DynamoClient

# table_name = os.environ['DYNAMO_TABLE_NAME']
# region = os.environ['REGION']
# instance_id = os.environ['INSTANCE_ID']

# dynamo_client = DynamoClient(table_name=table_name, region_name=region)

# # Fetch the current record
# instance = dynamo_client.get_instance(instance_id)

# # Adjust fields to reflect termination
# instance.status = 'terminated'
# instance.launched_at = datetime.utcnow().isoformat()  # or store a termination timestamp if desired
# instance.health_status = 'unhealthy'

# # Write changes back to DynamoDB
# dynamo_client.update_instance(instance)
# " || echo "Failed to update instance"
#         break
#     fi
#     sleep 5
# done
# """.encode(
#                 "utf-8"
#             )
#         ).decode("utf-8")
#     ),
#     tag_specifications=[
#         aws.ec2.LaunchTemplateTagSpecificationArgs(
#             resource_type="instance",
#             tags={
#                 "Name": "ML-Training-Instance",
#                 "Purpose": "ML Model Training",
#                 "ManagedBy": "Pulumi",
#             },
#         ),
#     ],
#     opts=pulumi.ResourceOptions(depends_on=[network]),  # Depend on network creation
# )

# # Create Auto Scaling Group using private subnets from network component
# asg = aws.autoscaling.Group(
#     "ml-training-asg",
#     max_size=4,
#     min_size=0,
#     desired_capacity=0,
#     vpc_zone_identifiers=network.public_subnet_ids,  # Use network component output
#     mixed_instances_policy=aws.autoscaling.GroupMixedInstancesPolicyArgs(
#         instances_distribution=aws.autoscaling.GroupMixedInstancesPolicyInstancesDistributionArgs(
#             on_demand_base_capacity=0,
#             on_demand_percentage_above_base_capacity=0,
#             spot_allocation_strategy="capacity-optimized",
#         ),
#         launch_template=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateArgs(
#             launch_template_specification=aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateLaunchTemplateSpecificationArgs(
#                 launch_template_id=launch_template.id,
#                 version="$Latest",
#             ),
#             overrides=[
#                 aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
#                     instance_type="g4dn.xlarge",
#                 ),
#                 aws.autoscaling.GroupMixedInstancesPolicyLaunchTemplateOverrideArgs(
#                     instance_type="g5.xlarge",
#                 ),
#             ],
#         ),
#     ),
#     health_check_type="EC2",
#     health_check_grace_period=300,
#     tags=[
#         aws.autoscaling.GroupTagArgs(
#             key="Name",
#             value="ML-Training-ASG",
#             propagate_at_launch=True,
#         ),
#         aws.autoscaling.GroupTagArgs(
#             key="Purpose",
#             value="ML Training",
#             propagate_at_launch=True,
#         ),
#     ],
#     opts=pulumi.ResourceOptions(
#         depends_on=[launch_template]
#     ),  # Depend on launch template
# )

# # Create a simple scaling policy based on CPU utilization
# scaling_policy = aws.autoscaling.Policy(
#     "ml-training-scaling-policy",
#     autoscaling_group_name=asg.name,
#     policy_type="TargetTrackingScaling",
#     target_tracking_configuration=aws.autoscaling.PolicyTargetTrackingConfigurationArgs(
#         predefined_metric_specification=aws.autoscaling.PolicyTargetTrackingConfigurationPredefinedMetricSpecificationArgs(
#             predefined_metric_type="ASGAverageCPUUtilization",
#         ),
#         target_value=70.0,
#         disable_scale_in=False,
#     ),
# )

# # --- Adjusted Exports ---
# pulumi.export("vpc_id", network.vpc_id)
# pulumi.export("private_subnet_ids", network.private_subnet_ids)
# pulumi.export("public_subnet_ids", network.public_subnet_ids)
# pulumi.export("security_group_id", network.security_group_id)  # Updated export name

# pulumi.export("instance_registry_table", instance_registry.table_name)
# pulumi.export("efs_dns_name", efs_storage.file_system_dns_name)
# pulumi.export("efs_training_access_point", efs_storage.training_access_point_id)
# pulumi.export("efs_checkpoints_access_point", efs_storage.checkpoints_access_point_id)
# pulumi.export("spot_interruption_sns_topic", spot_handler.sns_topic_arn)
# pulumi.export("launch_template_id", launch_template.id)
# pulumi.export("auto_scaling_group_name", asg.name)
# pulumi.export("deep_learning_ami_id", dl_ami.id)
# pulumi.export("deep_learning_ami_name", dl_ami.name)
# pulumi.export("job_queue_url", job_queue.get_queue_url())
# pulumi.export("job_dlq_url", job_queue.get_dlq_url())

# pulumi.export("training_ami_id", dl_ami.id)
# pulumi.export("training_instance_profile_name", ml_instance_profile.name)


# def get_first_subnet(subnets):
#     return subnets[0]


# pulumi.export("training_subnet_id", network.private_subnet_ids.apply(get_first_subnet))

# pulumi.export("training_efs_id", efs_storage.file_system_id)
# pulumi.export("instance_registry_table_name", instance_registry.table_name)
# pulumi.export("ml_packages_built", ml_package_builder.packages)

# ChromaDB infrastructure exports (hybrid deployment)
pulumi.export("chromadb_bucket_name", chromadb_infrastructure.bucket_name)
pulumi.export(
    "chromadb_lines_queue_url", chromadb_infrastructure.lines_queue_url
)
pulumi.export(
    "chromadb_words_queue_url", chromadb_infrastructure.words_queue_url
)
pulumi.export(
    "stream_processor_function_arn",
    chromadb_infrastructure.stream_processor_arn,
)
pulumi.export(
    "enhanced_compaction_function_arn",
    chromadb_infrastructure.enhanced_compaction_arn,
)
