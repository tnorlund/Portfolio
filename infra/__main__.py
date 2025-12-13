"""Main Pulumi program for AWS infrastructure."""

import base64
import os

import api_gateway  # noqa: F401
import pulumi
import pulumi_aws as aws
from pulumi import Output

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

from typing import Optional

# Import our infrastructure components
import s3_website  # noqa: F401

# Using the optimized docker-build based base images with scoped contexts
from base_images.base_images import BaseImages
from billing_alerts import BillingAlerts
from chromadb_compaction import create_chromadb_compaction_infrastructure
from combine_receipts_step_functions import CombineReceiptsStepFunction
from create_labels_step_functions import CreateLabelsStepFunction
from currency_validation_step_functions import (
    create_currency_validation_state_machine,
)
from dynamo_db import (
    dynamodb_table,  # Import DynamoDB table from original code
)
from embedding_step_functions import EmbeddingInfrastructure
from label_harmonizer_step_functions import LabelHarmonizerV3StepFunction
from label_suggestion_step_functions import LabelSuggestionStepFunction
from label_validation_agent_step_functions import (
    LabelValidationAgentStepFunction,
)
from metadata_harmonizer_step_functions import MetadataHarmonizerStepFunction
from networking import PublicVpc
from notifications import NotificationSystem
from pulumi import ResourceOptions
from raw_bucket import raw_bucket  # Import the actual bucket instance
from s3_website import site_bucket  # Import the site bucket instance
from security import ChromaSecurity
from upload_images import UploadImages
from validate_metadata import ValidateMetadataStepFunction
from validate_pending_labels import ValidatePendingLabelsStepFunction
from validation_by_merchant import ValidationByMerchantStepFunction
from validation_pipeline import ValidationPipeline

# from spot_interruption import SpotInterruptionHandler
# from efs_storage import EFSStorage
# from instance_registry import InstanceRegistry
# from job_queue import JobQueue
# from ml_packages import MLPackageBuilder
# from networking import VpcForCodeBuild  # Import the new VPC component

# Import other necessary components
try:
    # from infra.components import lambda_layer  # noqa: F401
    from lambda_functions.label_count_cache_updater.infra import (  # noqa: F401
        label_count_cache_updater_lambda,
    )
    from routes.health_check.infra import health_check_lambda  # noqa: F401

    from infra.components import lambda_layer  # noqa: F401

    print("✓ Successfully imported label_count_cache_updater_lambda")
except ImportError as e:
    # These may not be available in all environments
    print(f"⚠️  Failed to import label cache updater: {e}")
    pass
import step_function
from chroma.nat_egress import NatEgress
from chroma.orchestrator import ChromaOrchestrator
from chroma.service import ChromaEcsService
from chroma.workers import ChromaWorkers
from step_function_enhanced import create_enhanced_receipt_processor

# Foundation VPC (public subnets only, no NAT) per Task 350
public_vpc = PublicVpc("foundation")
pulumi.export("foundation_vpc_id", public_vpc.vpc_id)

# (moved DynamoDB gateway endpoint below after NAT creation to reference both route tables)
pulumi.export("foundation_public_subnet_ids", public_vpc.public_subnet_ids)
# (moved S3 gateway endpoint below after NAT creation to reference its route table)

# Create base images for faster Lambda builds (built in parallel, early in infrastructure)
# These contain pre-installed receipt_dynamo and receipt_label packages
base_images = BaseImages("base", pulumi.get_stack())
pulumi.export(
    "dynamo_base_image_url", base_images.dynamo_base_repo.repository_url
)
pulumi.export(
    "label_base_image_url", base_images.label_base_repo.repository_url
)
pulumi.export(
    "agent_base_image_url", base_images.agent_base_repo.repository_url
)

# Export dependency graph information for debugging
dependency_graph_dict = base_images.dependency_graph.to_dict()
pulumi.export(
    "dependency_graph", pulumi.Output.from_input(dependency_graph_dict)
)

# Task 2: Security (depends on VPC)
security = ChromaSecurity("chroma", vpc_id=public_vpc.vpc_id)
pulumi.export("sg_lambda_id", security.sg_lambda_id)
pulumi.export("sg_chroma_id", security.sg_chroma_id)
pulumi.export("ecs_task_role_arn", security.ecs_task_role_arn)
pulumi.export("lambda_role_arn", security.lambda_role_arn)
pulumi.export("step_functions_role_arn", security.step_functions_role_arn)

# Task 3 snapshot bucket not used; shared_chromadb_buckets provides storage

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

validation_pipeline = ValidationPipeline("validation-pipeline")

# Import shared ChromaDB bucket (created in chromadb_buckets.py for route access)
from chromadb_buckets import shared_chromadb_buckets

# Create ChromaDB compaction infrastructure using shared bucket
# Create currency validation state machine
currency_validation_state_machine = create_currency_validation_state_machine(
    notification_system
)

# Create labels state machine (creates/updates labels with PENDING status)
# No VPC needed - downloads from DynamoDB, needs internet for Ollama API
# Uses base image for faster builds and smaller S3 uploads
create_labels_sf = CreateLabelsStepFunction(
    f"create-labels-{pulumi.get_stack()}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    max_concurrency=3,  # Reduced to avoid Ollama rate limiting (matches validate_pending_labels)
    base_image_uri=base_images.label_base_image.tags[
        0
    ],  # Use label base image with receipt_dynamo + receipt_label
)

validation_by_merchant_step_functions = ValidationByMerchantStepFunction(
    "validation-by-merchant"
)

# Create the enhanced receipt processor with error handling
enhanced_receipt_processor = create_enhanced_receipt_processor(
    notification_system
)

# Export notification topics
pulumi.export(
    "step_function_failure_topic_arn",
    notification_system.step_function_topic_arn,
)
pulumi.export(
    "critical_error_topic_arn", notification_system.critical_error_topic_arn
)

# Create billing alerts for CloudWatch custom metrics costs
billing_alerts = BillingAlerts(
    "cloudwatch-metrics",
    sns_topic_arn=notification_system.critical_error_topic_arn,
    thresholds={
        "warning": 10.0,  # $10/month
        "critical": 25.0,  # $25/month
        "emergency": 50.0,  # $50/month
    },
    tags={
        "Environment": pulumi.get_stack(),
        "Purpose": "Cost Monitoring",
    },
)

# Export enhanced step function ARN
pulumi.export("enhanced_receipt_processor_arn", enhanced_receipt_processor.arn)

# Task 6: ECS Service (scale-to-zero) using our Chroma container
chroma_service = ChromaEcsService(
    name=f"chroma-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    public_subnet_ids=public_vpc.public_subnet_ids,
    security_group_id=security.sg_chroma_id,
    task_role_arn=security.ecs_task_role_arn,
    task_role_name=security.ecs_task_role_name,
    execution_role_arn=security.ecs_task_execution_role_arn,
    chromadb_bucket_name=shared_chromadb_buckets.bucket_name,
    collection="lines",
    desired_count=0,
)

pulumi.export("chroma_cluster_arn", chroma_service.cluster.arn)
pulumi.export("chroma_service_arn", chroma_service.svc.arn)
pulumi.export("chroma_service_dns", chroma_service.endpoint_dns)

# Task 7: Workers - Lambda functions that query Chroma via HTTP
workers = ChromaWorkers(
    name=f"chroma-workers-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    subnets=public_vpc.public_subnet_ids,
    security_group_id=security.sg_lambda_id,
    dynamodb_table_name=dynamodb_table.name,
    chroma_service_dns=chroma_service.endpoint_dns,
)

pulumi.export("chroma_query_words_lambda_arn", workers.query_words.arn)

# NAT egress (public subnet) + private subnets for Lambda internet access
nat = NatEgress(
    name=f"nat-egress-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    public_subnet_id=public_vpc.public_subnet_ids.apply(lambda ids: ids[0]),
)
pulumi.export("nat_instance_id", nat.nat_instance_id)
pulumi.export("nat_private_subnet_ids", nat.private_subnet_ids)

# Create ChromaDB compaction infrastructure using shared bucket
# Now that nat is defined, we can use both public and private subnets for EFS
# Create EFS with mount targets in unique AZs only
# EFS only allows one mount target per AZ, so we need to deduplicate by AZ

# Strategy: Select first public subnet + first private subnet
# Based on subnet query: public subnets are in us-east-1a and us-east-1b
# Both private subnets are in us-east-1b
# So we need: first public (us-east-1a) + first private (us-east-1b) = 2 unique AZs
unique_efs_subnets = Output.all(
    public_vpc.public_subnet_ids, nat.private_subnet_ids
).apply(
    lambda args: [args[0][0], args[1][0]]  # First public + first private
)

# Compaction lambda needs to be in private subnets for EFS access
# Use only first private subnet to ensure Lambda is in subnet with EFS mount target
compaction_lambda_subnets = nat.private_subnet_ids.apply(
    lambda ids: [ids[0]]
)  # Single subnet for EFS access

compaction_use_efs = portfolio_config.get_bool("compaction-use-efs")
if compaction_use_efs is None:
    compaction_use_efs = False  # default to S3-only mode to avoid EFS costs
compaction_storage_mode = "s3"  # Force S3-only mode to avoid EFS costs

chromadb_infrastructure = create_chromadb_compaction_infrastructure(
    name=f"chromadb-{pulumi.get_stack()}",
    dynamodb_table_arn=dynamodb_table.arn,
    dynamodb_stream_arn=dynamodb_table.stream_arn,
    chromadb_buckets=shared_chromadb_buckets,
    vpc_id=public_vpc.vpc_id,
    subnet_ids=compaction_lambda_subnets,  # Private subnets only for Lambda
    efs_subnet_ids=unique_efs_subnets,  # EFS requires unique AZs (public + first private)
    lambda_security_group_id=security.sg_lambda_id,
    use_efs=compaction_use_efs,
    storage_mode=compaction_storage_mode,
)

# Create embedding infrastructure using shared bucket and queues
# Depend on EFS mount targets if EFS exists (Lambda needs mount targets in "available" state)
embedding_depends_on = (
    chromadb_infrastructure.efs.mount_targets
    if chromadb_infrastructure.efs
    else None
)

embedding_infrastructure = EmbeddingInfrastructure(
    f"embedding-infra-{pulumi.get_stack()}",
    chromadb_queues=chromadb_infrastructure.chromadb_queues,
    chromadb_buckets=shared_chromadb_buckets,
    vpc_subnet_ids=compaction_lambda_subnets,  # Use same subnets as compaction Lambda
    lambda_security_group_id=security.sg_lambda_id,
    efs_access_point_arn=(
        chromadb_infrastructure.efs.access_point_arn
        if chromadb_infrastructure.efs
        else None
    ),
    efs_mount_targets=(
        chromadb_infrastructure.efs.mount_targets
        if chromadb_infrastructure.efs
        else None
    ),  # Pass mount targets dependency
    opts=ResourceOptions(depends_on=embedding_depends_on),
)

# Add S3 Gateway Endpoint for faster S3 access from both public and private subnets
s3_gateway_endpoint = aws.ec2.VpcEndpoint(
    f"s3-gateway-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.s3",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_vpc.public_route_table_id, nat.private_rt.id],
)

# Provide private access to DynamoDB from both public and private subnets (no NAT required)
dynamodb_gateway_endpoint = aws.ec2.VpcEndpoint(
    f"dynamodb-gateway-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.dynamodb",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_vpc.public_route_table_id, nat.private_rt.id],
)

# CloudWatch Logs Interface Endpoint for faster logging from VPC Lambdas
# Single AZ for cost savings ($0.12/day savings per endpoint)
# Lambda functions can access endpoints from any AZ in the VPC
# If endpoint AZ fails, Lambda falls back to NAT (slower but works)
# Get stack name for conditional logic (reused later in file)
stack = pulumi.get_stack()
# Use single AZ for both dev and prod - AZ failures are rare (< 0.1%)
# and Lambda functions have fallback to NAT Instance
logs_endpoint_subnets = public_vpc.public_subnet_ids.apply(
    lambda ids: [ids[0]]
)  # Single AZ

logs_interface_endpoint = aws.ec2.VpcEndpoint(
    f"logs-interface-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.logs",
    vpc_endpoint_type="Interface",
    subnet_ids=logs_endpoint_subnets,  # Conditional: single AZ for dev, multi-AZ for prod
    security_group_ids=[security.sg_vpce_id],
    private_dns_enabled=True,
)

# SQS Interface Endpoint for cost-effective SQS access from both public and private subnets
# Enables upload lambda to use EFS (private subnets) while accessing SQS without internet
# Single AZ for cost savings ($0.12/day savings)
# Lambda functions can access endpoints from any AZ in the VPC
# If endpoint AZ fails, Lambda falls back to NAT (slower but works)
sqs_endpoint_subnets = public_vpc.public_subnet_ids.apply(
    lambda ids: [ids[0]]
)  # Single AZ

sqs_interface_endpoint = aws.ec2.VpcEndpoint(
    f"sqs-interface-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.sqs",
    vpc_endpoint_type="Interface",
    subnet_ids=sqs_endpoint_subnets,  # Conditional: single AZ for dev, multi-AZ for prod
    security_group_ids=[security.sg_vpce_id],
    private_dns_enabled=True,
)

# Recreate workers to use NAT private subnets for egress
workers_nat = ChromaWorkers(
    name=f"chroma-workers-nat-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    subnets=nat.private_subnet_ids,
    security_group_id=security.sg_lambda_id,
    dynamodb_table_name=dynamodb_table.name,
    chroma_service_dns=chroma_service.endpoint_dns,
)
pulumi.export("chroma_query_words_lambda_nat_arn", workers_nat.query_words.arn)

# Task 8: Orchestration - Step Functions to scale up, wait, run, scale down
orchestrator = ChromaOrchestrator(
    name=f"chroma-orchestrator-{pulumi.get_stack()}",
    cluster_arn=chroma_service.cluster.arn,
    service_arn=chroma_service.svc.arn,
    chroma_endpoint=chroma_service.endpoint_dns,
    worker_lambda_arn=workers_nat.query_words.arn,
    nat_instance_id=nat.nat_instance_id,
    lambda_role_name=security.lambda_role_arn,
    subnets=public_vpc.public_subnet_ids,
    security_group_id=security.sg_lambda_id,
)

pulumi.export("chroma_orchestrator_sfn_arn", orchestrator.state_machine.arn)

# ValidateMerchantStepFunctions removed - redundant with LangGraph metadata creation
# Metadata is now created by:
# - Upload OCR Handler (LangGraph)
# - Upload Container Handler (LangGraph)
# - Embedding polling handlers (LangGraph)
# Consolidation and batch cleaning can be added as standalone Lambdas if needed

# Wire upload-images after NAT and Chroma are available so it can reach OpenAI and Chroma
# When using EFS, use only first private subnet to ensure Lambda is in subnet with EFS mount target
upload_images_subnets = nat.private_subnet_ids.apply(
    lambda ids: [ids[0]]
)  # Single subnet for EFS access

upload_images = UploadImages(
    "upload-images",
    raw_bucket=raw_bucket,
    site_bucket=site_bucket,
    chromadb_bucket_name=embedding_infrastructure.chromadb_buckets.bucket_name,
    embed_ndjson_queue_url=None,  # Will use internal queue
    vpc_subnet_ids=upload_images_subnets,  # Single subnet when EFS is used
    security_group_id=security.sg_lambda_id,
    chroma_http_endpoint=chroma_service.endpoint_dns,
    ecs_cluster_arn=chroma_service.cluster.arn,
    ecs_service_arn=chroma_service.svc.arn,
    nat_instance_id=nat.nat_instance_id,
    efs_access_point_arn=(
        chromadb_infrastructure.efs.access_point_arn
        if chromadb_infrastructure.efs
        else None
    ),
)

pulumi.export("ocr_job_queue_url", upload_images.ocr_queue.url)
pulumi.export("ocr_results_queue_url", upload_images.ocr_results_queue.url)

# ML Training Infrastructure
# -------------------------
# Minimal LayoutLM training infra (toggle via config)
from layoutlm_training.component import LayoutLMTrainingInfra

ml_cfg = pulumi.Config("ml-training")
enable_minimal = ml_cfg.get_bool("enable-minimal") or False

# Training bucket - either from new training infra or existing bucket name
layoutlm_training_bucket_name: Optional[Output[str]] = None

if enable_minimal:
    training = LayoutLMTrainingInfra(
        "layoutlm",
        dynamodb_table_name=dynamodb_table.name,
    )
    layoutlm_training_bucket_name = training.bucket.bucket
    pulumi.export("layoutlm_training_bucket", training.bucket.bucket)
    pulumi.export("layoutlm_training_queue_url", training.queue.url)
    pulumi.export("layoutlm_training_asg_name", training.asg.name)
else:
    # Check if training bucket name is provided as config (for inference-only usage)
    training_bucket_config = ml_cfg.get("training-bucket-name")
    if training_bucket_config:
        layoutlm_training_bucket_name = Output.from_input(
            training_bucket_config
        )

# Create LayoutLM inference API if we have a training bucket (either from training infra or config)
if layoutlm_training_bucket_name is not None:
    from routes.layoutlm_inference.infra import (
        create_layoutlm_inference_lambda,
    )
    from routes.layoutlm_inference_cache_generator.infra import (
        create_layoutlm_inference_cache_generator,
    )

    # Create cache generator (which creates the cache bucket)
    layoutlm_cache_generator = create_layoutlm_inference_cache_generator(
        layoutlm_training_bucket=layoutlm_training_bucket_name,
    )

    # Create the API Lambda only after the cache bucket exists
    # No placeholder bucket names - only use the real bucket
    layoutlm_inference_lambda = create_layoutlm_inference_lambda(
        cache_bucket_name=layoutlm_cache_generator.cache_bucket.id,
    )

    # Export the Lambda so api_gateway.py can use it
    # Set it as a module-level variable in the inference module
    import routes.layoutlm_inference.infra as inference_module

    inference_module.layoutlm_inference_lambda = layoutlm_inference_lambda

    # Create API Gateway route for layoutlm_inference
    # This must be done here after Lambda is created, not in api_gateway.py
    # because api_gateway.py is imported before the Lambda exists
    import api_gateway

    if hasattr(api_gateway, "api"):
        integration_layoutlm_inference = aws.apigatewayv2.Integration(
            "layoutlm_inference_lambda_integration",
            api_id=api_gateway.api.id,
            integration_type="AWS_PROXY",
            integration_uri=layoutlm_inference_lambda.invoke_arn,
            integration_method="POST",
            payload_format_version="2.0",
        )
        route_layoutlm_inference = aws.apigatewayv2.Route(
            "layoutlm_inference_route",
            api_id=api_gateway.api.id,
            route_key="GET /layoutlm_inference",
            target=integration_layoutlm_inference.id.apply(
                lambda id: f"integrations/{id}"
            ),
            opts=pulumi.ResourceOptions(
                replace_on_changes=["route_key", "target"],
                delete_before_replace=True,
            ),
        )
        # Also add alias route with hyphens for convenience
        route_layoutlm_inference_cache = aws.apigatewayv2.Route(
            "layoutlm_inference_cache_route",
            api_id=api_gateway.api.id,
            route_key="GET /layoutlm-inference-cache",
            target=integration_layoutlm_inference.id.apply(
                lambda id: f"integrations/{id}"
            ),
            opts=pulumi.ResourceOptions(
                replace_on_changes=["route_key", "target"],
                delete_before_replace=True,
            ),
        )
        lambda_permission_layoutlm_inference = aws.lambda_.Permission(
            "layoutlm_inference_lambda_permission",
            action="lambda:InvokeFunction",
            function=layoutlm_inference_lambda.name,
            principal="apigateway.amazonaws.com",
            source_arn=api_gateway.api.execution_arn.apply(
                lambda arn: f"{arn}/*/*"
            ),
        )

    pulumi.export(
        "layoutlm_inference_cache_bucket",
        layoutlm_cache_generator.cache_bucket.id,
    )


# Use stack-specific existing key pair from AWS console
# (stack variable already defined earlier for VPC endpoint configuration)
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
pulumi.export("chromadb_bucket_name", shared_chromadb_buckets.bucket_name)
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

# Export the embedding infrastructure ChromaDB bucket (the one actually used!)
pulumi.export(
    "embedding_chromadb_bucket_name",
    embedding_infrastructure.chromadb_buckets.bucket_name,
)
pulumi.export(
    "embedding_chromadb_bucket_arn",
    embedding_infrastructure.chromadb_buckets.bucket_arn,
)

# Export label cache updater if successfully imported
try:
    from lambda_functions.label_count_cache_updater.infra import (
        cache_update_schedule,
        label_count_cache_updater_lambda,
    )

    pulumi.export(
        "label_cache_updater_lambda_arn", label_count_cache_updater_lambda.arn
    )
    pulumi.export(
        "label_cache_updater_lambda_name",
        label_count_cache_updater_lambda.name,
    )
    pulumi.export("label_cache_update_schedule_arn", cache_update_schedule.arn)
except ImportError:
    # Cache updater not available in this environment
    pass

# Create validate pending labels Step Function
# No VPC needed - downloads ChromaDB from S3, needs internet for Ollama API
validate_pending_labels_sf = ValidatePendingLabelsStepFunction(
    f"validate-pending-labels-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    chromadb_bucket_name=embedding_infrastructure.chromadb_buckets.bucket_name,
    chromadb_bucket_arn=embedding_infrastructure.chromadb_buckets.bucket_arn,
    # No VPC - Lambda downloads ChromaDB from S3 and needs internet for Ollama API
    # DynamoDB and S3 access via gateway endpoints work from anywhere
)

pulumi.export(
    "validate_pending_labels_sf_arn",
    validate_pending_labels_sf.state_machine_arn,
)
pulumi.export(
    "validate_pending_labels_list_lambda_arn",
    validate_pending_labels_sf.list_pending_labels_lambda_arn,
)
pulumi.export(
    "validate_pending_labels_validate_lambda_arn",
    validate_pending_labels_sf.validate_receipt_lambda_arn,
)

# Combine Receipts Step Function - deployed to all stacks (dev, prod, etc.)
# This workflow combines multiple receipts into single receipts based on LLM analysis.
# Note: NDJSON export to embedding queue is optional. When embed_ndjson_queue_url=None,
# the NDJSON export step is silently skipped (see combine_receipts_logic.py:406).
# The workflow still creates embeddings and ChromaDB deltas directly, but does not
# queue NDJSON files for downstream embedding processing. If production requires
# NDJSON export to the embedding queue, provide the queue URL here.
combine_receipts_sf = CombineReceiptsStepFunction(
    f"combine-receipts-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    chromadb_bucket_name=embedding_infrastructure.chromadb_buckets.bucket_name,
    chromadb_bucket_arn=embedding_infrastructure.chromadb_buckets.bucket_arn,
    raw_bucket_name=raw_bucket.bucket,
    site_bucket_name=site_bucket.bucket,
    # Reuse the embedding batch/artifacts bucket from embedding infrastructure
    artifacts_bucket_name=embedding_infrastructure.batch_bucket.bucket,
    artifacts_bucket_arn=embedding_infrastructure.batch_bucket.arn,
    # NDJSON export is optional - workflow creates embeddings directly
    # embed_ndjson_queue_url parameter was removed as it was unused
    embed_ndjson_queue_arn=None,
)

pulumi.export("combine_receipts_sf_arn", combine_receipts_sf.state_machine_arn)
pulumi.export(
    "combine_receipts_batch_bucket_name", combine_receipts_sf.batch_bucket_name
)

# Label Harmonizer V3 Step Function (whole receipt processing)
label_harmonizer_v3_sf = LabelHarmonizerV3StepFunction(
    f"label-harmonizer-v3-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    chromadb_bucket_name=embedding_infrastructure.chromadb_buckets.bucket_name,
    chromadb_bucket_arn=embedding_infrastructure.chromadb_buckets.bucket_arn,
    max_concurrency=5,  # Process 5 batches in parallel
    batch_size=50,  # 50 receipts per batch
)

pulumi.export(
    "label_harmonizer_v3_sf_arn", label_harmonizer_v3_sf.state_machine_arn
)
pulumi.export(
    "label_harmonizer_v3_batch_bucket_name",
    label_harmonizer_v3_sf.batch_bucket_name,
)

# Label Validation Agent Step Function (NEEDS_REVIEW labels)
label_validation_agent_sf = LabelValidationAgentStepFunction(
    f"label-validation-agent-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    chromadb_bucket_name=embedding_infrastructure.chromadb_buckets.bucket_name,
    chromadb_bucket_arn=embedding_infrastructure.chromadb_buckets.bucket_arn,
)

pulumi.export(
    "label_validation_agent_sf_arn",
    label_validation_agent_sf.state_machine_arn,
)
pulumi.export(
    "label_validation_agent_batch_bucket_name",
    label_validation_agent_sf.batch_bucket_name,
)

# Label Suggestion Agent Step Function (unlabeled words → suggestions)
label_suggestion_sf = LabelSuggestionStepFunction(
    f"label-suggestion-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    chromadb_bucket_name=embedding_infrastructure.chromadb_buckets.bucket_name,
    chromadb_bucket_arn=embedding_infrastructure.chromadb_buckets.bucket_arn,
)

pulumi.export("label_suggestion_sf_arn", label_suggestion_sf.state_machine_arn)
pulumi.export(
    "label_suggestion_batch_bucket_name", label_suggestion_sf.batch_bucket_name
)

# Export create labels step function
pulumi.export(
    "create_labels_sf_arn",
    create_labels_sf.state_machine_arn,
)
pulumi.export(
    "create_labels_list_lambda_arn",
    create_labels_sf.list_receipts_lambda_arn,
)
pulumi.export(
    "create_labels_create_lambda_arn",
    create_labels_sf.create_labels_lambda_arn,
)

# Metadata Harmonizer Step Function (place_id-based harmonization)
# Uses shared_chromadb_buckets (same as embedding_infrastructure.chromadb_buckets)
# This is where ChromaDB snapshots are stored by the compaction process
# Uses agent base image for maximum optimization (70-90% reduction in build time)
metadata_harmonizer_sf = MetadataHarmonizerStepFunction(
    f"metadata-harmonizer-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    chromadb_bucket_name=shared_chromadb_buckets.bucket_name,
    chromadb_bucket_arn=shared_chromadb_buckets.bucket_arn,
    base_image_uri=base_images.agent_base_image.tags[
        0
    ],  # Use agent base image with all common packages (dynamo, chroma, upload, places, label)
)

pulumi.export(
    "metadata_harmonizer_sf_arn", metadata_harmonizer_sf.state_machine_arn
)
pulumi.export(
    "metadata_harmonizer_batch_bucket_name",
    metadata_harmonizer_sf.batch_bucket_name,
)

# Create validate metadata Step Function
# No VPC needed - downloads ChromaDB from S3, needs internet for Ollama API
validate_metadata_sf = ValidateMetadataStepFunction(
    f"validate-metadata-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    chromadb_bucket_name=embedding_infrastructure.chromadb_buckets.bucket_name,
    chromadb_bucket_arn=embedding_infrastructure.chromadb_buckets.bucket_arn,
    # No VPC - Lambda downloads ChromaDB from S3 and needs internet for Ollama API
    # DynamoDB and S3 access via gateway endpoints work from anywhere
)

pulumi.export(
    "validate_metadata_sf_arn",
    validate_metadata_sf.state_machine_arn,
)
pulumi.export(
    "validate_metadata_list_lambda_arn",
    validate_metadata_sf.list_metadata_lambda_arn,
)
pulumi.export(
    "validate_metadata_validate_lambda_arn",
    validate_metadata_sf.validate_metadata_lambda_arn,
)
