"""Main Pulumi program for AWS infrastructure."""

import os
import sys
from pathlib import Path

# Add parent directory to path so 'infra' package can be imported
sys.path.insert(0, str(Path(__file__).parent.parent))

import pulumi
import pulumi_aws as aws
from pulumi import Output

import api_gateway
from components.http_api_route import (
    RouteDefinition,
    create_lambda_route,
    create_lambda_routes,
)

# Auto-enable Docker BuildKit based on Pulumi config
config = pulumi.Config("portfolio")
if (
    config.get_bool("docker-buildkit") is not False
):  # Default to True if not set
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
from billing_alerts import BillingAlerts
from dynamo_db import (
    dynamodb_table,  # Import DynamoDB table from original code
)
from fix_place_lambda import create_fix_place_lambda
from merge_receipt_lambda import create_merge_receipt_lambda

# Using the optimized docker-build based base images with scoped contexts
from networking import PublicVpc
from notifications import NotificationSystem
from raw_bucket import raw_bucket  # Import the actual bucket instance
from receipt_update_queues import ReceiptUpdateQueues
from resegment_receipt_lambda import create_resegment_receipt_lambda
from s3_website import site_bucket  # Import the site bucket instance
from trigger_reocr_lambda import create_trigger_reocr_lambda
from upload_images import UploadImages

# Receipt Label Validation project name - single source of truth
# This project name is used for LangSmith tracing, bulk export, and viz cache generation
label_validation_project_name = f"receipt-validation-v1-{pulumi.get_stack()}"

# from spot_interruption import SpotInterruptionHandler
# from efs_storage import EFSStorage
# from job_queue import JobQueue
# from ml_packages import MLPackageBuilder
# from networking import VpcForCodeBuild  # Import the new VPC component

# Import other necessary components
try:
    from infra.components import lambda_layer  # noqa: F401 (side effects)
    from lambda_functions.label_count_cache_updater.infra import (
        label_count_cache_updater_lambda,
    )

    print("✓ Successfully imported label_count_cache_updater_lambda")
except ImportError as e:
    # These may not be available in all environments
    print(f"⚠️  Failed to import label cache updater: {e}")
# import step_function  # Legacy - receipt_processor depends on removed receipt_label

# from step_function_enhanced import create_enhanced_receipt_processor  # Legacy
# - depends on receipt_processor which needs receipt_label

# Foundation VPC (public subnets only, no NAT) per Task 350
public_vpc = PublicVpc("foundation")
pulumi.export("foundation_vpc_id", public_vpc.vpc_id)

pulumi.export("foundation_public_subnet_ids", public_vpc.public_subnet_ids)

# VPC security groups retired (VPC prune): no Lambdas remain in the VPC,
# so the shared Lambda-egress and interface-endpoint security groups are
# gone along with the NAT egress layer below.

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

# Shared resources for the label evaluator pipeline (S3 buckets used by the EMR
# analytics and Step Function components created ~1300 lines below).
#
# This MUST be registered early, near the top of the program, rather than next
# to its consumers. Under `pulumi --target`, the engine resolves the target
# closure against the *existing state* and eagerly emits a "same" step for this
# component from state. If the program's own RegisterResource for the component
# arrives after that (which happens when it is constructed late in module
# execution), the engine sees the URN twice and fails with
# "Duplicate resource URN ...; try giving it a unique name". Constructing it
# here lets the program registration reconcile with the engine's same-step.
# Untargeted previews/deploys are unaffected (registration order does not
# matter there), so prod CI is unchanged.
from components.shared_label_evaluator_resources import (
    create_shared_label_evaluator_resources,
)

label_evaluator_shared = create_shared_label_evaluator_resources()

# Note: currency validation, create labels, and validation-by-merchant workflows are
# temporarily disabled to decouple from receipt_label.

# Create the enhanced receipt processor with error handling
# TODO: Receipt processor is legacy and depends on receipt_label which was removed
# This needs to be replaced with the new agent-based pipelines
# enhanced_receipt_processor = create_enhanced_receipt_processor(
#     notification_system
# )

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
# pulumi.export("enhanced_receipt_processor_arn", enhanced_receipt_processor.arn)
# Commented out - legacy code

# NAT egress + private subnets retired (VPC prune): the last two VPC
# Lambdas (process-ocr, word-similarity cache generator) now run outside
# the VPC with default Lambda egress — the NAT existed only for them.

# Summary/line-item update pipeline + stream processor. The
# ``queues_name``/``lambdas_name`` prefixes are frozen physical-resource
# identities inherited from the retired vector-store compaction stack
# (see docs/chroma-removal/); renaming them would replace live queues.
receipt_update_queues = ReceiptUpdateQueues(
    f"receipt-updates-{pulumi.get_stack()}",
    queues_name=f"chromadb-{pulumi.get_stack()}-queues",
    lambdas_name=f"chromadb-{pulumi.get_stack()}",
    dynamodb_table_arn=dynamodb_table.arn,
    dynamodb_stream_arn=dynamodb_table.stream_arn,
)

# S3 Gateway Endpoint (free) for faster S3 access from the public subnets
s3_gateway_endpoint = aws.ec2.VpcEndpoint(
    f"s3-gateway-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.s3",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_vpc.public_route_table_id],
)

# DynamoDB Gateway Endpoint (free) for private access from the public subnets
dynamodb_gateway_endpoint = aws.ec2.VpcEndpoint(
    f"dynamodb-gateway-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.dynamodb",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_vpc.public_route_table_id],
)

# Interface endpoints (logs, sqs) retired (VPC prune): they billed hourly
# and existed only for the private-subnet Lambdas, which now run outside
# the VPC with default egress.
# Get stack name for conditional logic (reused later in file)
stack = pulumi.get_stack()

# Word Similarity Cache Generator Lambda (VPC prune: runs outside the VPC
# now — the DynamoDB-Gateway latency optimization died with the NAT layer)
from routes.word_similarity_cache_generator.infra import (
    create_word_similarity_cache_generator,
)

word_similarity_cache_generator = create_word_similarity_cache_generator()

pulumi.export(
    "word_similarity_cache_generator_lambda_arn",
    word_similarity_cache_generator.lambda_function.arn,
)
pulumi.export(
    "word_similarity_cache_bucket_name",
    word_similarity_cache_generator.cache_bucket.id,
)

# Word Similarity API Lambda (depends on cache generator bucket)
from routes.word_similarity.infra import create_word_similarity_lambda

word_similarity_lambda = create_word_similarity_lambda(
    cache_bucket_name=word_similarity_cache_generator.cache_bucket.id,
)

pulumi.export("word_similarity_lambda_arn", word_similarity_lambda.arn)
pulumi.export("word_similarity_lambda_name", word_similarity_lambda.name)

# This Lambda is created after the cache bucket, so its API route is registered
# here rather than in api_gateway's static route manifest.
create_lambda_route(
    api=api_gateway.api,
    integration_name="word_similarity_lambda_integration",
    route_name="word_similarity_route",
    route_key="GET /word_similarity",
    lambda_function=word_similarity_lambda,
    permission_name="word_similarity_lambda_permission",
)

# ValidateMerchantStepFunctions removed - redundant with LangGraph metadata creation
# Metadata is now created by:
# - Upload OCR Handler (LangGraph)
# - Upload Container Handler (LangGraph)
# - Embedding polling handlers (LangGraph)
# Consolidation and batch cleaning can be added as standalone Lambdas if needed

# upload-images runs outside the VPC (VPC prune): default Lambda egress
# reaches Google Places / OpenAI / OpenRouter / LangSmith directly.
upload_images = UploadImages(
    "upload-images",
    raw_bucket=raw_bucket,
    site_bucket=site_bucket,
    label_validation_project_name=label_validation_project_name,
    # Post-re-OCR line-item refresh (summary recompute -> stream ->
    # LINE_ITEMS stage)
    summary_queue_url=receipt_update_queues.summary_queue_url,
    summary_queue_arn=receipt_update_queues.summary_queue_arn,
)

pulumi.export("ocr_job_queue_url", upload_images.ocr_queue.url)
pulumi.export("ocr_results_queue_url", upload_images.ocr_results_queue.url)
pulumi.export(
    "llm_validation_queue_url", upload_images.llm_validation_queue.url
)

# ML Training Infrastructure
# -------------------------
# LayoutLM training via SageMaker (toggle via config: ml-training:enable-sagemaker)

ml_cfg = pulumi.Config("ml-training")
enable_sagemaker = ml_cfg.get_bool("enable-sagemaker") or False

# Training bucket - either from SageMaker training infra or existing bucket name
layoutlm_training_bucket_name: Optional[Output[str]] = None

if enable_sagemaker:
    from sagemaker_training import SageMakerTrainingInfra

    sagemaker_training = SageMakerTrainingInfra(
        "layoutlm-sagemaker",
        dynamodb_table_name=dynamodb_table.name,
        raw_bucket_arn=upload_images.image_bucket.arn,
    )
    layoutlm_training_bucket_name = sagemaker_training.output_bucket.bucket
    pulumi.export(
        "layoutlm_training_bucket", sagemaker_training.output_bucket.bucket
    )
    pulumi.export(
        "layoutlm_sagemaker_ecr_repo",
        sagemaker_training.ecr_repo.repository_url,
    )
    pulumi.export(
        "layoutlm_sagemaker_role_arn", sagemaker_training.sagemaker_role.arn
    )
    pulumi.export(
        "layoutlm_start_training_lambda",
        sagemaker_training.start_training_lambda.arn,
    )
    pulumi.export(
        "layoutlm_codebuild_project", sagemaker_training.codebuild_project.name
    )
    # Export model location for Swift OCR CLI to download LayoutLM model
    pulumi.export(
        "layoutlm_model_s3_bucket", sagemaker_training.output_bucket.bucket
    )
    pulumi.export("layoutlm_model_s3_key", "coreml/layoutlm-coreml-bundle.zip")

    # Per-epoch checkpoint evaluation. Reuses the training container image and
    # execution role to launch an ephemeral SageMaker Processing job that scores
    # every checkpoint on the frozen val set. Auto-trigger on training
    # completion is opt-in (ml-training:auto-epoch-eval) to avoid surprise GPU
    # cost.
    from sagemaker_epoch_eval import EpochEvalInfra

    epoch_eval = EpochEvalInfra(
        "layoutlm-epoch-eval",
        ecr_repo_url=sagemaker_training.ecr_repo.repository_url,
        sagemaker_role_arn=sagemaker_training.sagemaker_role.arn,
        output_bucket=sagemaker_training.output_bucket.bucket,
        dynamodb_table_name=dynamodb_table.name,
        region=aws.get_region().name,
        account_id=aws.get_caller_identity().account_id,
        enable_auto_trigger=ml_cfg.get_bool("auto-epoch-eval") or False,
    )
    pulumi.export(
        "layoutlm_epoch_eval_trigger_lambda",
        epoch_eval.trigger_lambda.arn,
    )
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
    from routes.layoutlm_inference_cache_generator.step_function import (
        create_batch_cache_generator,
    )

    # The cache generator Lambda resolves the active model from DynamoDB
    # at runtime (Job tagged with active_model=true). Falls back to
    # auto-detecting the latest model from the training bucket.
    layoutlm_cache_generator = create_layoutlm_inference_cache_generator(
        layoutlm_training_bucket=layoutlm_training_bucket_name,
    )

    # Create Step Function for batch cache generation (weekly)
    layoutlm_batch_cache_generator = create_batch_cache_generator(
        inference_lambda_arn=layoutlm_cache_generator.lambda_function.arn,
    )

    # Create the API Lambda only after the cache bucket exists
    # No placeholder bucket names - only use the real bucket
    layoutlm_inference_lambda = create_layoutlm_inference_lambda(
        cache_bucket_name=layoutlm_cache_generator.cache_bucket.id,
        training_bucket_name=layoutlm_training_bucket_name,
    )

    # Export the Lambda so api_gateway.py can use it
    # Set it as a module-level variable in the inference module
    import routes.layoutlm_inference.infra as inference_module

    inference_module.layoutlm_inference_lambda = layoutlm_inference_lambda

    # Per-epoch evaluation API: serves epoch-eval/ artifacts from the training
    # bucket (written by the eval-checkpoints Processing job).
    import routes.layoutlm_epochs.infra as epochs_module
    from routes.layoutlm_epochs.infra import create_layoutlm_epochs_lambda

    layoutlm_epochs_lambda = create_layoutlm_epochs_lambda(
        cache_bucket_name=layoutlm_training_bucket_name,
    )
    epochs_module.layoutlm_epochs_lambda = layoutlm_epochs_lambda

    # These routes are late-bound because the model and cache buckets determine
    # whether their Lambda functions exist for this stack.
    create_lambda_routes(
        api=api_gateway.api,
        integration_name="layoutlm_inference_lambda_integration",
        lambda_function=layoutlm_inference_lambda,
        routes=(
            RouteDefinition(
                "layoutlm_inference_route", "GET /layoutlm_inference"
            ),
            RouteDefinition(
                "layoutlm_inference_cache_route",
                "GET /layoutlm-inference-cache",
            ),
        ),
        permission_name="layoutlm_inference_lambda_permission",
    )
    create_lambda_route(
        api=api_gateway.api,
        integration_name="layoutlm_epochs_lambda_integration",
        route_name="layoutlm_epochs_route",
        route_key="GET /layoutlm_epochs",
        lambda_function=layoutlm_epochs_lambda,
        permission_name="layoutlm_epochs_lambda_permission",
    )

    pulumi.export(
        "layoutlm_inference_cache_bucket",
        layoutlm_cache_generator.cache_bucket.id,
    )
    pulumi.export(
        "layoutlm_batch_cache_state_machine_arn",
        layoutlm_batch_cache_generator.state_machine.arn,
    )


# Use stack-specific existing key pair from AWS console
# (stack variable already defined earlier for VPC endpoint configuration)
# Use existing key pairs created in AWS console
key_pair_name = f"portfolio-receipt-{stack}"

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


pulumi.export(
    "stream_processor_function_arn",
    receipt_update_queues.stream_processor_arn,
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

# validate_pending_labels_sf, create_labels_sf, and validate_metadata_sf remain
# disabled until we refactor those flows off receipt_label.

# Fix Place Lambda (for correcting incorrect ReceiptPlace records)
# Can be invoked with: {image_id, receipt_id, reason}
fix_place_lambda = create_fix_place_lambda(
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
)
pulumi.export("fix_place_lambda_arn", fix_place_lambda.lambda_arn)
pulumi.export("fix_place_lambda_name", fix_place_lambda.lambda_function.name)
pulumi.export("fix_place_lambda_role_name", fix_place_lambda.lambda_role_name)

# The receipt and ATS inboxes share the account's sole active SES receipt rule
# set. Construct them together before the MCP gateway so the ATS reader can be
# exposed through its own OAuth scope without creating a second mail plane.
email_inbox = None
ats_inbox = None
email_inbox_enabled = portfolio_config.get_bool("email_receipt_inbox_enabled")
ats_inbox_enabled = portfolio_config.get_bool("ats_verification_inbox_enabled")
if ats_inbox_enabled and not email_inbox_enabled:
    raise ValueError(
        "portfolio:ats_verification_inbox_enabled requires "
        "portfolio:email_receipt_inbox_enabled so both recipients share the "
        "active SES receipt rule set"
    )
if email_inbox_enabled:
    from email_receipt_inbox import EmailReceiptInbox

    email_inbox = EmailReceiptInbox("email-receipt-inbox")
    pulumi.export("email_receipt_inbox_address", email_inbox.address)
    pulumi.export("email_receipt_inbox_bucket", email_inbox.bucket.bucket)

if ats_inbox_enabled and email_inbox is not None:
    from ats_verification_inbox import AtsVerificationInbox

    ats_inbox = AtsVerificationInbox(
        "ats-verification-inbox",
        domain=email_inbox.domain,
        rule_set_name=email_inbox.rule_set.rule_set_name,
    )
    pulumi.export("ats_verification_inbox_address", ats_inbox.address)
    pulumi.export("ats_verification_inbox_bucket", ats_inbox.bucket.bucket)
    pulumi.export("ats_verification_codes_table", ats_inbox.table.name)

# Receipt MCP Server Lambda
from mcp_server_lambda import McpServerLambda

mcp_server = McpServerLambda(
    "receipt-mcp",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
)
pulumi.export("mcp_server_lambda_arn", mcp_server.lambda_arn)
pulumi.export("mcp_server_iam_url", mcp_server.function_url)

# Glyph Studio MCP Server Lambda
from glyph_mcp_lambda import GlyphMcpLambda

glyph_mcp_server = GlyphMcpLambda("glyph-mcp")
pulumi.export("glyph_mcp_server_lambda_arn", glyph_mcp_server.lambda_arn)
pulumi.export("glyph_mcp_server_iam_url", glyph_mcp_server.function_url)

# Shared OAuth ingress for off-the-shelf remote MCP clients. The direct
# Function URLs above require SigV4 and are retained for signed internal use.
from mcp_auth_gateway import McpAuthGateway

mcp_auth_gateway = McpAuthGateway(
    "portfolio-mcp-auth",
    receipt_lambda=mcp_server.lambda_function,
    glyph_lambda=glyph_mcp_server.lambda_function,
    ats_lambda=ats_inbox.mcp_lambda if ats_inbox is not None else None,
)
pulumi.export("mcp_server_url", mcp_auth_gateway.receipt_url)
pulumi.export("glyph_mcp_server_url", mcp_auth_gateway.glyph_url)
if mcp_auth_gateway.ats_url is not None:
    pulumi.export("ats_mcp_server_url", mcp_auth_gateway.ats_url)
pulumi.export("mcp_oauth_issuer_url", mcp_auth_gateway.issuer_url)
pulumi.export("mcp_oauth_user_pool_id", mcp_auth_gateway.user_pool.id)
pulumi.export(
    "mcp_oauth_interactive_client_id",
    mcp_auth_gateway.interactive_client.id,
)
pulumi.export(
    "mcp_oauth_automation_secret_arn",
    mcp_auth_gateway.automation_secret_arn,
)
if mcp_auth_gateway.ats_automation_secret_arn is not None:
    pulumi.export(
        "mcp_oauth_ats_automation_secret_arn",
        mcp_auth_gateway.ats_automation_secret_arn,
    )

# Web analytics query layer: Glue + Athena over the CloudFront access logs,
# read by the analytics_* MCP tools. No new pipeline — just a queryable view
# of logs that already exist.
# Glue/Athena names are account-global, and the analytics source is the prod
# CloudFront logs — so only build this on the prod stack to avoid dev/prod
# collisions. The MCP tools use a fixed DB name and query via account-level
# creds, so they work regardless of which stack the caller runs in.
if pulumi.get_stack() == "prod":
    from components.web_analytics import WebAnalytics  # noqa: E402
    from s3_website import cloudfront_logs_bucket  # noqa: E402

    # GA4 second source is optional: the extractor Lambda is only built when
    # both the service-account key (secret) and property id are configured.
    #   pulumi config set --secret portfolio:gaServiceAccountKey @key.json
    #   pulumi config set portfolio:gaPropertyId 542366301
    # GitHub traffic snapshotter is optional too (14-day API window, so daily
    # snapshots build durable history):
    #   pulumi config set --secret portfolio:githubTrafficToken <PAT>
    #   pulumi config set portfolio:githubTrafficRepos tnorlund/Portfolio
    _analytics_cfg = pulumi.Config()
    web_analytics = WebAnalytics(
        "web-analytics",
        cloudfront_logs_bucket=cloudfront_logs_bucket.bucket,
        log_prefix="cloudfront/prod/",
        ga_service_account_key=_analytics_cfg.get_secret(
            "gaServiceAccountKey"
        ),
        ga_property_id=_analytics_cfg.get("gaPropertyId"),
        github_token=_analytics_cfg.get_secret("githubTrafficToken"),
        github_repos=_analytics_cfg.get("githubTrafficRepos"),
    )
    # Let the MCP Lambda role run Athena/Glue/S3 reads for the analytics tools.
    aws.iam.RolePolicyAttachment(
        "receipt-mcp-analytics-read",
        role=mcp_server.lambda_role_name,
        policy_arn=web_analytics.read_policy_arn,
    )
    pulumi.export("analytics_database", web_analytics.database_name)
    pulumi.export("analytics_workgroup", web_analytics.workgroup_name)
    pulumi.export("analytics_read_policy_arn", web_analytics.read_policy_arn)
else:
    # The analytics layer (and its managed read policy) exist only on the
    # prod stack, but the analytics_* MCP tools run from every stack's
    # Lambda — Glue/Athena names are account-global. Reuse prod's policy
    # via stack reference so non-prod roles survive recreation with their
    # analytics access intact. Requires prod to have deployed at least
    # once after it began exporting analytics_read_policy_arn.
    _prod_ref = pulumi.StackReference("tnorlund/portfolio/prod")
    aws.iam.RolePolicyAttachment(
        "receipt-mcp-analytics-read",
        role=mcp_server.lambda_role_name,
        policy_arn=_prod_ref.get_output("analytics_read_policy_arn"),
    )

# Merge Receipt Lambda (for merging receipt fragments into a single receipt)
# Can be invoked with: {image_id, receipt_ids: [2, 3], dry_run: false}
merge_receipt_lambda = create_merge_receipt_lambda(
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    raw_bucket_name=raw_bucket.bucket,
    site_bucket_name=site_bucket.bucket,
    image_bucket_name=upload_images.image_bucket.bucket,
)
pulumi.export("merge_receipt_lambda_arn", merge_receipt_lambda.lambda_arn)
pulumi.export(
    "merge_receipt_lambda_name", merge_receipt_lambda.lambda_function.name
)

# Receipt Re-segmentation Lambda (one source receipt -> N guarded outputs)
resegment_receipt_lambda = create_resegment_receipt_lambda(
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    raw_bucket_name=raw_bucket.bucket,
    site_bucket_name=site_bucket.bucket,
    image_bucket_name=upload_images.image_bucket.bucket,
)
pulumi.export(
    "resegment_receipt_lambda_arn", resegment_receipt_lambda.lambda_arn
)
pulumi.export(
    "resegment_receipt_lambda_name",
    resegment_receipt_lambda.lambda_function.name,
)

# Trigger Re-OCR Lambda (for manually triggering regional re-OCR)
# Can be invoked with: {image_id, receipt_id, reocr_region, reocr_reason}
trigger_reocr_lambda = create_trigger_reocr_lambda(
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    ocr_job_queue_url=upload_images.ocr_queue.url,
    ocr_job_queue_arn=upload_images.ocr_queue.arn,
)
pulumi.export("trigger_reocr_lambda_arn", trigger_reocr_lambda.lambda_arn)
pulumi.export(
    "trigger_reocr_lambda_name", trigger_reocr_lambda.lambda_function.name
)

# Label Refresh Lambda: RETIRED with the vector-store teardown (see
# docs/chroma-removal/); the pipeline-consolidation plan called for its
# removal.

# LangSmith Bulk Export infrastructure (for Parquet exports)
from components.langsmith_bulk_export import LangSmithBulkExport

# Label Evaluator project export
langsmith_bulk_export = LangSmithBulkExport(
    f"langsmith-export-{stack}",
    project_name=f"label-evaluator-{stack}",
)
pulumi.export(
    "langsmith_export_bucket", langsmith_bulk_export.export_bucket.id
)
pulumi.export(
    "langsmith_setup_lambda", langsmith_bulk_export.setup_lambda.name
)
pulumi.export(
    "langsmith_trigger_lambda", langsmith_bulk_export.trigger_lambda.name
)

# Receipt Label Validation project export
label_validation_export = LangSmithBulkExport(
    f"label-validation-export-{stack}",
    project_name=label_validation_project_name,
)
pulumi.export(
    "label_validation_export_bucket", label_validation_export.export_bucket.id
)
pulumi.export(
    "label_validation_setup_lambda", label_validation_export.setup_lambda.name
)
pulumi.export(
    "label_validation_trigger_lambda",
    label_validation_export.trigger_lambda.name,
)
pulumi.export("label_validation_project_name", label_validation_project_name)

# EMR Serverless Analytics infrastructure (for Spark analytics on LangSmith traces)
from components.emr_serverless_analytics import create_emr_serverless_analytics

# Shared resources for the label evaluator pipeline (buckets used by multiple
# components) are constructed near the top of this program (search for
# label_evaluator_shared) so that `pulumi --target` reconciles cleanly; see the
# comment there. The instance is reused here via label_evaluator_shared.

emr_analytics = create_emr_serverless_analytics(
    langsmith_export_bucket_arn=langsmith_bulk_export.export_bucket.arn,
    # Shared buckets - grant EMR job access
    cache_bucket_arn=label_evaluator_shared.viz_cache_bucket_arn,
    batch_bucket_arn=label_evaluator_shared.batch_bucket_arn,
)
pulumi.export("emr_application_id", emr_analytics.emr_application.id)
pulumi.export("emr_analytics_bucket", emr_analytics.analytics_bucket.id)
pulumi.export("emr_artifacts_bucket", emr_analytics.artifacts_bucket.id)
pulumi.export(
    "emr_python_environment_uri",
    emr_analytics.python_environment_uri,
)
pulumi.export(
    "label_evaluator_viz_cache_merged_bucket",
    label_evaluator_shared.viz_cache_bucket_name,
)

# Label Evaluator Step Function: RETIRED 2026-09-02 (vector-store
# teardown, closing #1523); the pipeline-consolidation plan supersedes it.
# Shared resources it merely referenced (OCR queue, EMR analytics,
# LangSmith bulk export, label_evaluator_shared viz-cache/batch buckets)
# all remain — the viz-cache API routes keep serving the frozen cache.

# CoreML Export Queue Infrastructure (for exporting LayoutLM models to CoreML on macOS)
# Only create if SageMaker training is enabled (we need the training bucket)
if enable_sagemaker and layoutlm_training_bucket_name is not None:
    from infra.components.lambda_layer import dynamo_layer
    from infra.coreml_export import CoreMLExportComponent

    coreml_export = CoreMLExportComponent(
        f"coreml-export-{stack}",
        dynamodb_table_name=dynamodb_table.name,
        dynamodb_table_arn=dynamodb_table.arn,
        layoutlm_bucket_name=layoutlm_training_bucket_name,
        layoutlm_bucket_arn=sagemaker_training.output_bucket.arn,
        lambda_layer_arn=dynamo_layer.arn,
    )

    pulumi.export("coreml_export_job_queue_url", coreml_export.job_queue_url)
    pulumi.export(
        "coreml_export_results_queue_url", coreml_export.results_queue_url
    )
    pulumi.export(
        "coreml_export_process_results_lambda_arn",
        coreml_export.process_results_lambda.arn,
    )

from routes.label_validation_timeline.infra import (
    create_label_validation_timeline_lambda,
)

# Label Validation Timeline API (S3-cached timeline for animated visualization)
from routes.label_validation_timeline_cache.infra import (
    create_label_validation_timeline_cache,
)

# Create cache generator (which creates the cache bucket)
timeline_cache_bucket, timeline_cache_generator_lambda = (
    create_label_validation_timeline_cache()
)

# Create the API Lambda using the cache bucket
label_validation_timeline_lambda = create_label_validation_timeline_lambda(
    cache_bucket_name=timeline_cache_bucket.id,
)

create_lambda_route(
    api=api_gateway.api,
    integration_name="label_validation_timeline_lambda_integration",
    route_name="label_validation_timeline_route",
    route_key="GET /label_validation_timeline",
    lambda_function=label_validation_timeline_lambda,
    permission_name="label_validation_timeline_lambda_permission",
)

pulumi.export(
    "label_validation_timeline_cache_bucket", timeline_cache_bucket.id
)
pulumi.export(
    "label_validation_timeline_cache_generator_lambda",
    timeline_cache_generator_lambda.name,
)

# Label Evaluator Visualization Cache API
# Note: The viz-cache Step Function has been removed - viz-cache generation
# is now handled by the Label Evaluator Step Function's merged EMR job.
from routes.label_evaluator_viz_cache.infra import (
    create_label_evaluator_viz_cache,
)

# Create API Gateway route for label evaluator visualization
if hasattr(api_gateway, "api"):
    # Create API Lambda to serve viz-cache data (reads from shared bucket)
    label_evaluator_viz_cache = create_label_evaluator_viz_cache(
        cache_bucket_name=label_evaluator_shared.viz_cache_bucket_name,
    )
    pulumi.export(
        "label_evaluator_viz_cache_bucket",
        label_evaluator_shared.viz_cache_bucket_name,
    )

    create_lambda_route(
        api=api_gateway.api,
        integration_name="label_evaluator_viz_cache_integration",
        route_name="label_evaluator_viz_cache_route",
        route_key="GET /label_evaluator/visualization",
        lambda_function=label_evaluator_viz_cache.api_lambda,
        permission_name="label_evaluator_viz_lambda_permission",
    )

    # Additional label evaluator visualization endpoints (same Lambda, different paths)
    for viz_name in [
        "financial_math",
        "diff",
        "journey",
        "patterns",
        "evidence",
        "dedup",
        "within_receipt",
        "receipt_health",
        "receipt_health_issues",
    ]:
        create_lambda_route(
            api=api_gateway.api,
            integration_name=f"label_evaluator_{viz_name}_integration",
            route_name=f"label_evaluator_{viz_name}_route",
            route_key=f"GET /label_evaluator/{viz_name}",
            lambda_function=label_evaluator_viz_cache.api_lambda,
        )

    create_lambda_route(
        api=api_gateway.api,
        integration_name=(
            "label_evaluator_receipt_health_issues_post_integration"
        ),
        route_name="label_evaluator_receipt_health_issues_post_route",
        route_key="POST /label_evaluator/receipt_health_issues",
        lambda_function=label_evaluator_viz_cache.api_lambda,
        authorization_type="AWS_IAM",
    )

    # Label Validation Visualization Cache (uses label_validation_project_name)
    from routes.label_validation_viz_cache import (
        create_label_validation_viz_cache,
    )

    label_validation_viz_cache = create_label_validation_viz_cache(
        f"label-validation-viz-{stack}",
        langsmith_export_bucket=label_validation_export.export_bucket.id,
        langsmith_api_key=config.require_secret("LANGCHAIN_API_KEY"),
        langsmith_tenant_id=config.require("LANGSMITH_TENANT_ID"),
        langsmith_project_name=label_validation_project_name,
        dynamodb_table_name=dynamodb_table.name,
        dynamodb_table_arn=dynamodb_table.arn,
        emr_application_id=emr_analytics.emr_application.id,
        emr_job_role_arn=emr_analytics.emr_job_role.arn,
        spark_artifacts_bucket=emr_analytics.artifacts_bucket.id,
        setup_lambda_name=label_validation_export.setup_lambda.name,
        setup_lambda_arn=label_validation_export.setup_lambda.arn,
    )
    pulumi.export(
        "label_validation_viz_cache_bucket",
        label_validation_viz_cache.cache_bucket.id,
    )
    pulumi.export(
        "label_validation_viz_step_function_arn",
        label_validation_viz_cache.step_function.arn,
    )

    create_lambda_route(
        api=api_gateway.api,
        integration_name="label_validation_viz_cache_integration",
        route_name="label_validation_viz_cache_route",
        route_key="GET /label_validation/visualization",
        lambda_function=label_validation_viz_cache.api_lambda,
        permission_name="label_validation_viz_lambda_permission",
    )

# QA Agent Step Function pipeline
from qa_agent_step_functions import QAAgentStepFunction

qa_agent_sf = QAAgentStepFunction(
    f"qa-agent-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
    # EMR Serverless
    emr_application_id=emr_analytics.emr_application.id,
    emr_job_execution_role_arn=emr_analytics.emr_job_role.arn,
    langsmith_export_bucket=langsmith_bulk_export.export_bucket.id,
    analytics_output_bucket=emr_analytics.analytics_bucket.id,
    spark_artifacts_bucket=emr_analytics.artifacts_bucket.id,
    # LangSmith export lambdas — use the langsmith_bulk_export component's trigger
    # (correct SSM_PREFIX → correct destination → correct S3 bucket)
    trigger_export_lambda_arn=langsmith_bulk_export.trigger_lambda.arn,
    check_export_lambda_arn=label_validation_viz_cache.check_export_lambda.arn,
)

pulumi.export("qa_agent_sf_arn", qa_agent_sf.state_machine_arn)
pulumi.export("qa_agent_batch_bucket_name", qa_agent_sf.batch_bucket_name)

# QA Visualization Cache API
from routes.qa_viz_cache.infra import create_qa_viz_cache

if hasattr(api_gateway, "api"):
    qa_viz_cache = create_qa_viz_cache(
        cache_bucket_name=qa_agent_sf.batch_bucket_name,
    )
    pulumi.export("qa_viz_cache_bucket", qa_agent_sf.batch_bucket_name)

    create_lambda_route(
        api=api_gateway.api,
        integration_name="qa_viz_cache_integration",
        route_name="qa_viz_cache_route",
        route_key="GET /qa/visualization",
        lambda_function=qa_viz_cache.api_lambda,
        permission_name="qa_viz_lambda_permission",
    )
