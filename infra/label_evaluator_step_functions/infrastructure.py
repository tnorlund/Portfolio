"""
Pulumi infrastructure for Label Evaluator Step Function with LangSmith Tracing.

This component creates a Step Function with per-receipt traces in LangSmith,
providing complete visibility into each receipt's label validation and LLM review.

The workflow has two phases:
1. Pattern Learning (per-merchant, once):
   - LearnLineItemPatterns: LLM learns line item structure (single/multi-line, positions)
   - BuildMerchantPatterns: Compute geometric patterns from training receipts

2. Per-Receipt Validation (parallel):
   - LoadReceiptData: Load words/labels from DynamoDB
   - ParallelReview:
     - FlagGeometricAnomalies: Deterministic pattern analysis (6 detection rules)
     - ReviewCurrencyLabels: LLM reviews currency-type labels (prices, totals)
     - ReviewMetadataLabels: LLM reviews metadata-type labels (merchant, address)
   - ReviewFlaggedLabels: LLM reviews flagged words with ChromaDB similarity evidence

Each receipt gets its own LangSmith trace with metadata:
  - image_id: Receipt image identifier
  - receipt_id: Receipt number within image
  - merchant_name: Merchant name for filtering

This enables filtering by specific receipts in LangSmith and provides
complete visibility into each receipt's validation process.
"""

import json
import os
from typing import Optional

import pulumi
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.cloudwatch import LogGroup
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
)
from pulumi_aws.s3 import (
    Bucket,
    BucketVersioningV2,
    BucketVersioningV2VersioningConfigurationArgs,
)
from pulumi_aws.sfn import StateMachine, StateMachineLoggingConfigurationArgs

# Import Step Function state builders
from label_evaluator_step_functions.step_function_states import (
    EmrConfig,
    LambdaArns,
    RuntimeConfig,
    create_step_function_definition,
)

# Import shared components
try:
    from codebuild_docker_image import CodeBuildDockerImage
    from lambda_layer import dynamo_layer
except ImportError as e:
    raise ImportError(
        "Required modules 'codebuild_docker_image' and 'lambda_layer' not found."
    ) from e

# Load secrets from Pulumi config
config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
openrouter_api_key = config.require_secret("OPENROUTER_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")

# Label evaluator specific config
evaluator_config = Config("label-evaluator")


class LabelEvaluatorStepFunction(ComponentResource):
    """
    Step Function infrastructure for label evaluation with LangSmith tracing.

    This provides unified traces across the entire workflow, making pattern
    computation, evaluations, and LLM calls visible in a single LangSmith trace.
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: Optional[pulumi.Input[str]] = None,
        chromadb_bucket_arn: Optional[pulumi.Input[str]] = None,
        # EMR Serverless Analytics integration (optional)
        emr_application_id: Optional[pulumi.Input[str]] = None,
        emr_job_execution_role_arn: Optional[pulumi.Input[str]] = None,
        langsmith_export_bucket: Optional[pulumi.Input[str]] = None,
        analytics_output_bucket: Optional[pulumi.Input[str]] = None,
        spark_artifacts_bucket: Optional[pulumi.Input[str]] = None,
        # Viz-cache integration (optional, enables merged analytics+viz-cache)
        cache_bucket: Optional[pulumi.Input[str]] = None,
        langsmith_api_key: Optional[pulumi.Input[str]] = None,
        langsmith_tenant_id: Optional[pulumi.Input[str]] = None,
        setup_lambda_name: Optional[pulumi.Input[str]] = None,
        setup_lambda_arn: Optional[pulumi.Input[str]] = None,
        # External batch bucket (optional - if not provided, creates one internally)
        # Use this to break circular dependencies with EMR analytics
        batch_bucket_name: Optional[pulumi.Input[str]] = None,
        batch_bucket_arn: Optional[pulumi.Input[str]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"label-evaluator-step-function:{name}", name, None, opts
        )
        stack = pulumi.get_stack()

        self.chromadb_bucket_name = chromadb_bucket_name
        self.chromadb_bucket_arn = chromadb_bucket_arn

        # EMR Analytics config
        self.emr_enabled = emr_application_id is not None
        self.emr_application_id = emr_application_id
        self.emr_job_execution_role_arn = emr_job_execution_role_arn
        self.langsmith_export_bucket = langsmith_export_bucket
        self.analytics_output_bucket = analytics_output_bucket

        # Viz-cache config (enables merged EMR job)
        self.viz_cache_enabled = (
            cache_bucket is not None
            and langsmith_api_key is not None
            and setup_lambda_arn is not None
        )
        self.cache_bucket = cache_bucket
        self.langsmith_api_key = langsmith_api_key
        self.langsmith_tenant_id = langsmith_tenant_id
        self.setup_lambda_name = setup_lambda_name
        self.setup_lambda_arn = setup_lambda_arn
        self.spark_artifacts_bucket = spark_artifacts_bucket

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        # Use external bucket if provided (to break circular dependencies with EMR)
        # Otherwise create one internally for backward compatibility
        self._external_batch_bucket = batch_bucket_name is not None
        if self._external_batch_bucket:
            # Use the externally-provided bucket
            self._batch_bucket_name = pulumi.Output.from_input(
                batch_bucket_name
            )
            self._batch_bucket_arn = pulumi.Output.from_input(batch_bucket_arn)
            # Create a dummy object to satisfy attribute access patterns
            # (lambdas reference self.batch_bucket.bucket)

            class ExternalBucketRef:
                """Reference to an externally-created bucket."""

                def __init__(self, bucket_name, bucket_arn):
                    self.id = bucket_name
                    self.bucket = bucket_name
                    self.arn = bucket_arn

            self.batch_bucket = ExternalBucketRef(
                self._batch_bucket_name, self._batch_bucket_arn
            )
        else:
            # Create bucket internally (original behavior)
            is_prod = stack in ("prod", "production")
            self.batch_bucket = Bucket(
                f"{name}-batch-bucket",
                force_destroy=not is_prod,
                tags={"environment": stack, "purpose": "label-evaluator"},
                opts=ResourceOptions(parent=self),
            )

            BucketVersioningV2(
                f"{name}-batch-bucket-versioning",
                bucket=self.batch_bucket.id,
                versioning_configuration=BucketVersioningV2VersioningConfigurationArgs(
                    status="Enabled"
                ),
                opts=ResourceOptions(parent=self.batch_bucket),
            )

        # ============================================================
        # IAM Roles
        # ============================================================

        # Step Function role
        sfn_role = Role(
            f"{name}-sfn-role",
            name=f"{name}-sfn-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "states.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Lambda execution role
        lambda_role = Role(
            f"{name}-lambda-role",
            name=f"{name}-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Basic Lambda execution
        RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # ECR permissions for container Lambda
        RolePolicy(
            f"{name}-lambda-ecr-policy",
            role=lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["ecr:GetAuthorizationToken"],
                            "Resource": "*",
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "ecr:BatchGetImage",
                                "ecr:GetDownloadUrlForLayer",
                            ],
                            "Resource": f"arn:aws:ecr:*:*:repository/{name}-*",
                        },
                    ],
                }
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # DynamoDB access policy
        RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=lambda_role.id,
            policy=Output.all(dynamodb_table_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:Query",
                                    "dynamodb:Scan",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/index/*",
                                ],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=lambda_role),
        )

        # S3 access policy (includes ChromaDB bucket if provided)
        if chromadb_bucket_arn:
            RolePolicy(
                f"{name}-lambda-s3-policy",
                role=lambda_role.id,
                policy=Output.all(
                    self.batch_bucket.arn, chromadb_bucket_arn
                ).apply(
                    lambda args: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "s3:GetObject",
                                        "s3:PutObject",
                                        "s3:DeleteObject",
                                        "s3:ListBucket",
                                    ],
                                    "Resource": [
                                        args[0],
                                        f"{args[0]}/*",
                                        args[1],
                                        f"{args[1]}/*",
                                    ],
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=lambda_role),
            )
        else:
            RolePolicy(
                f"{name}-lambda-s3-policy",
                role=lambda_role.id,
                policy=self.batch_bucket.arn.apply(
                    lambda arn: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "s3:GetObject",
                                        "s3:PutObject",
                                        "s3:DeleteObject",
                                        "s3:ListBucket",
                                    ],
                                    "Resource": [arn, f"{arn}/*"],
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=lambda_role),
            )

        # ============================================================
        # Paths
        # ============================================================
        CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
        HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")
        UTILS_DIR = os.path.join(CURRENT_DIR, "lambdas", "utils")

        # Common environment for tracing
        # LANGCHAIN_TRACING_V2=true enables LangChain auto-tracing so LLM calls
        # get full inputs/outputs/tokens/structured-output detail in LangSmith.
        # Manual per-receipt RunTree traces still work because child_trace and
        # related context managers use tracing_context(parent=run_tree) to set
        # the correct parent for auto-traced LLM runs.
        tracing_env = {
            "LANGCHAIN_API_KEY": langchain_api_key,
            "LANGCHAIN_TRACING_V2": "true",
            "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
            "LANGCHAIN_PROJECT": config.get("langchain_project")
            or "label-evaluator",
        }

        # ============================================================
        # Zip Lambdas (Traced versions)
        # ============================================================

        # list_merchants Lambda
        list_merchants_lambda = Function(
            f"{name}-list-merchants",
            name=f"{name}-list-merchants",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_merchants.handler",
            code=AssetArchive(
                {
                    "list_merchants.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "list_merchants.py")
                    ),
                    # Include handlers directory for types
                    "handlers/__init__.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "__init__.py")
                    ),
                    "handlers/evaluator_types.py": FileAsset(
                        os.path.join(CURRENT_DIR, "evaluator_types.py")
                    ),
                    # Include tracing utilities
                    "tracing.py": FileAsset(
                        os.path.join(UTILS_DIR, "tracing.py")
                    ),
                }
            ),
            timeout=300,
            memory_size=512,
            layers=[dynamo_layer.arn] if dynamo_layer else [],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                    **tracing_env,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # list_all_receipts Lambda (for flattened two-phase architecture)
        list_all_receipts_lambda = Function(
            f"{name}-list-all-receipts",
            name=f"{name}-list-all-receipts",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_all_receipts.handler",
            code=AssetArchive(
                {
                    "list_all_receipts.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "list_all_receipts.py")
                    ),
                    # Include handlers directory for types
                    "handlers/__init__.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "__init__.py")
                    ),
                    "handlers/evaluator_types.py": FileAsset(
                        os.path.join(CURRENT_DIR, "evaluator_types.py")
                    ),
                    "tracing.py": FileAsset(
                        os.path.join(UTILS_DIR, "tracing.py")
                    ),
                }
            ),
            timeout=300,
            memory_size=512,
            layers=[dynamo_layer.arn] if dynamo_layer else [],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                    **tracing_env,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # fetch_receipt_data Lambda
        fetch_receipt_data_lambda = Function(
            f"{name}-fetch-receipt-data",
            name=f"{name}-fetch-receipt-data",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="fetch_receipt_data.handler",
            code=AssetArchive(
                {
                    "fetch_receipt_data.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "fetch_receipt_data.py")
                    ),
                    "utils/__init__.py": FileAsset(
                        os.path.join(UTILS_DIR, "__init__.py")
                    ),
                    "utils/serialization.py": FileAsset(
                        os.path.join(UTILS_DIR, "serialization.py")
                    ),
                    "utils/emf_metrics.py": FileAsset(
                        os.path.join(UTILS_DIR, "emf_metrics.py")
                    ),
                }
            ),
            timeout=60,
            memory_size=512,
            layers=[dynamo_layer.arn] if dynamo_layer else [],
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                    **tracing_env,
                }
            ),
            opts=ResourceOptions(
                parent=self,
                ignore_changes=["layers"],
            ),
        )

        # aggregate_results Lambda
        aggregate_results_lambda = Function(
            f"{name}-aggregate-results",
            name=f"{name}-aggregate-results",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="aggregate_results.handler",
            code=AssetArchive(
                {
                    "aggregate_results.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "aggregate_results.py")
                    ),
                    "tracing.py": FileAsset(
                        os.path.join(UTILS_DIR, "tracing.py")
                    ),
                }
            ),
            timeout=120,
            memory_size=512,
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                    **tracing_env,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # final_aggregate Lambda
        final_aggregate_lambda = Function(
            f"{name}-final-aggregate",
            name=f"{name}-final-aggregate",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="final_aggregate.handler",
            code=AssetArchive(
                {
                    "final_aggregate.py": FileAsset(
                        os.path.join(HANDLERS_DIR, "final_aggregate.py")
                    ),
                    "tracing.py": FileAsset(
                        os.path.join(UTILS_DIR, "tracing.py")
                    ),
                }
            ),
            timeout=300,
            memory_size=512,
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BATCH_BUCKET": self.batch_bucket.bucket,
                    **tracing_env,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Container Lambda: compute_patterns
        # ============================================================
        compute_patterns_config = {
            "role_arn": lambda_role.arn,
            "timeout": 600,
            "memory_size": 10240,
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                **tracing_env,
            },
        }

        compute_patterns_docker_image = CodeBuildDockerImage(
            f"{name}-cp-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.compute_patterns"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-compute-patterns",
            lambda_config=compute_patterns_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        compute_patterns_lambda = compute_patterns_docker_image.lambda_function

        # ============================================================
        # Container Lambda: evaluate_labels
        # ============================================================
        evaluate_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 300,
            "memory_size": 512,
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                **tracing_env,
            },
        }

        evaluate_docker_image = CodeBuildDockerImage(
            f"{name}-eval-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.evaluate"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-evaluate-labels",
            lambda_config=evaluate_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        evaluate_labels_lambda = evaluate_docker_image.lambda_function

        # ============================================================
        # Container Lambda: discover_patterns (LLM)
        # Uses httpx for OpenRouter API calls - requires container for dependencies
        # ============================================================
        discover_patterns_config = {
            "role_arn": lambda_role.arn,
            "timeout": 180,  # 3 minutes for LLM call
            "memory_size": 512,
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "BATCH_BUCKET": self.batch_bucket.bucket,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                **tracing_env,
            },
        }

        discover_patterns_docker_image = CodeBuildDockerImage(
            f"{name}-dp-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.discover_patterns"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-discover-patterns",
            lambda_config=discover_patterns_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        discover_patterns_lambda = (
            discover_patterns_docker_image.lambda_function
        )

        # ============================================================
        # Container Lambda: llm_review (LLM)
        # ============================================================
        llm_review_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes
            "memory_size": 3072,  # 3 GB for LLM
            "tags": {"environment": stack},
            "ephemeral_storage": 512,  # Default; no S3 snapshots needed
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "CHROMADB_BUCKET": chromadb_bucket_name or "",
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb",
                # Chroma Cloud read configuration (falls back to S3 snapshots)
                "CHROMA_CLOUD_ENABLED": (
                    config.get("CHROMA_CLOUD_ENABLED") or "false"
                ),
                "CHROMA_CLOUD_API_KEY": (
                    config.get_secret("CHROMA_CLOUD_API_KEY") or ""
                ),
                "CHROMA_CLOUD_TENANT": (
                    config.get("CHROMA_CLOUD_TENANT") or ""
                ),
                "CHROMA_CLOUD_DATABASE": (
                    config.get("CHROMA_CLOUD_DATABASE") or ""
                ),
                **tracing_env,
                "MAX_ISSUES_PER_LLM_CALL": "8",
                "CIRCUIT_BREAKER_THRESHOLD": "5",
                "LLM_MAX_JITTER_SECONDS": "0.25",
            },
        }

        llm_review_docker_image = CodeBuildDockerImage(
            f"{name}-llm-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.llm"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-llm-review",
            lambda_config=llm_review_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        llm_review_lambda = llm_review_docker_image.lambda_function

        # ============================================================
        # Container Lambda: evaluate_currency_labels (LLM)
        # Evaluates currency labels using line item patterns
        # ============================================================
        currency_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 300,  # 5 minutes
            "memory_size": 512,
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                **tracing_env,
            },
        }

        currency_docker_image = CodeBuildDockerImage(
            f"{name}-currency-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.currency"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-evaluate-currency",
            lambda_config=currency_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        evaluate_currency_lambda = currency_docker_image.lambda_function

        # ============================================================
        # Container Lambda: evaluate_metadata_labels (LLM)
        # Evaluates metadata labels using ReceiptPlace data
        # ============================================================
        metadata_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 300,  # 5 minutes
            "memory_size": 512,
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                **tracing_env,
            },
        }

        metadata_docker_image = CodeBuildDockerImage(
            f"{name}-metadata-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.metadata"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-evaluate-metadata",
            lambda_config=metadata_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        evaluate_metadata_lambda = metadata_docker_image.lambda_function

        # ============================================================
        # Container Lambda: evaluate_financial_labels (LLM)
        # Validates financial math after currency/metadata corrections
        # Runs AFTER ParallelReview to get corrected labels
        # ============================================================
        financial_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 300,  # 5 minutes
            "memory_size": 512,
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                **tracing_env,
            },
        }

        financial_docker_image = CodeBuildDockerImage(
            f"{name}-financial-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.financial"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-evaluate-financial",
            lambda_config=financial_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        evaluate_financial_lambda = financial_docker_image.lambda_function

        # ============================================================
        # Container Lambda: close_receipt_trace (minimal)
        # Closes receipt trace after parallel evaluation completes
        # ============================================================
        close_trace_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 30,  # 30 seconds (minimal work)
            "memory_size": 256,  # Minimal memory
            "tags": {"environment": stack},
            "environment": {
                **tracing_env,
            },
        }

        close_trace_docker_image = CodeBuildDockerImage(
            f"{name}-close-trace-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.close_trace"
            ),
            build_context_path=".",
            source_paths=[
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-close-trace",
            lambda_config=close_trace_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        close_trace_lambda = close_trace_docker_image.lambda_function

        # ============================================================
        # Container Lambda: unified_receipt_evaluator (NEW)
        # Consolidates currency, metadata, financial, and LLM review
        # Uses asyncio for concurrent LLM calls
        # ============================================================
        unified_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes (covers all evaluations)
            "memory_size": 3072,  # 3 GB for ChromaDB + LLM
            "tags": {"environment": stack},
            "ephemeral_storage": 10240,  # 10 GB for ChromaDB
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "CHROMADB_BUCKET": chromadb_bucket_name or "",
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb",
                # Chroma Cloud read configuration (falls back to S3 snapshots)
                "CHROMA_CLOUD_ENABLED": (
                    config.get("CHROMA_CLOUD_ENABLED") or "false"
                ),
                "CHROMA_CLOUD_API_KEY": (
                    config.get_secret("CHROMA_CLOUD_API_KEY") or ""
                ),
                "CHROMA_CLOUD_TENANT": (
                    config.get("CHROMA_CLOUD_TENANT") or ""
                ),
                "CHROMA_CLOUD_DATABASE": (
                    config.get("CHROMA_CLOUD_DATABASE") or ""
                ),
                **tracing_env,
                "MAX_ISSUES_PER_LLM_CALL": "15",
                "LLM_MAX_JITTER_SECONDS": "0.25",
            },
        }

        unified_docker_image = CodeBuildDockerImage(
            f"{name}-unified-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.unified"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-unified-evaluator",
            lambda_config=unified_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        unified_evaluator_lambda = unified_docker_image.lambda_function

        # ============================================================
        # Container Lambda: unified_pattern_builder (NEW)
        # Combines LearnLineItemPatterns and BuildMerchantPatterns
        # Reduces cold starts by running both in a single Lambda
        # ============================================================
        unified_pattern_builder_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes (Lambda max), Step Function handles longer waits
            "memory_size": 10240,  # Same as compute_patterns
            "tags": {"environment": stack},
            "ephemeral_storage": 512,
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "DYNAMODB_TABLE_NAME": dynamodb_table_name,
                # OpenRouter LLM provider
                "OPENROUTER_API_KEY": openrouter_api_key,
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
                "OPENROUTER_MODEL": "openai/gpt-oss-120b",
                **tracing_env,
            },
        }

        unified_pattern_builder_docker_image = CodeBuildDockerImage(
            f"{name}-upb-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.unified_pattern_builder"
            ),
            build_context_path=".",
            source_paths=[
                "receipt_dynamo",
                "receipt_dynamo_stream",
                "receipt_chroma",
                "receipt_places",
                "receipt_agent",
                "infra/label_evaluator_step_functions/lambdas",
            ],
            lambda_function_name=f"{name}-unified-pattern-builder",
            lambda_config=unified_pattern_builder_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[lambda_role]),
        )

        unified_pattern_builder_lambda = (
            unified_pattern_builder_docker_image.lambda_function
        )

        # ============================================================
        # LangSmith Export Lambdas (for viz-cache integration)
        # ============================================================
        trigger_export_lambda = None
        check_export_lambda = None

        if self.viz_cache_enabled:
            import pulumi_aws as aws
            from pulumi import StringAsset

            region = aws.get_region().name
            account_id = aws.get_caller_identity().account_id

            # Trigger Export Lambda Role
            trigger_export_role = Role(
                f"{name}-trigger-export-role",
                name=f"{name}-trigger-export-role",
                assume_role_policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {
                                    "Service": "lambda.amazonaws.com"
                                },
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    }
                ),
                opts=ResourceOptions(parent=self),
            )

            RolePolicyAttachment(
                f"{name}-trigger-export-basic-exec",
                role=trigger_export_role.name,
                policy_arn=(
                    "arn:aws:iam::aws:policy/service-role/"
                    "AWSLambdaBasicExecutionRole"
                ),
                opts=ResourceOptions(parent=trigger_export_role),
            )

            # SSM access for destination_id
            RolePolicy(
                f"{name}-trigger-export-ssm-policy",
                role=trigger_export_role.id,
                policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "ssm:GetParameter",
                                    "ssm:DeleteParameter",
                                ],
                                "Resource": (
                                    f"arn:aws:ssm:{region}:{account_id}"
                                    f":parameter/langsmith/{stack}/*"
                                ),
                            }
                        ],
                    }
                ),
                opts=ResourceOptions(parent=self),
            )

            # Permission to invoke setup lambda
            RolePolicy(
                f"{name}-trigger-export-invoke-setup-policy",
                role=trigger_export_role.id,
                policy=Output.from_input(self.setup_lambda_arn).apply(
                    lambda arn: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": ["lambda:InvokeFunction"],
                                    "Resource": arn,
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=self),
            )

            trigger_export_code = '''
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LANGSMITH_API_URL = "https://api.smith.langchain.com"


def _ensure_destination_exists(ssm, http, headers, stack, setup_lambda_name):
    """Ensure destination exists in LangSmith, create if needed."""
    param_name = f"/langsmith/{stack}/destination_id"
    lambda_client = boto3.client("lambda")

    destination_id = None
    try:
        response = ssm.get_parameter(Name=param_name)
        destination_id = response["Parameter"]["Value"]
        logger.info("Found destination_id in SSM: %s", destination_id)
    except ssm.exceptions.ParameterNotFound:
        logger.info("No destination_id in SSM, will create new one")

    if destination_id:
        response = http.request(
            "GET",
            f"{LANGSMITH_API_URL}/api/v1/bulk-export-destinations/{destination_id}",
            headers=headers,
        )
        if response.status == 200:
            logger.info("Destination verified: %s", destination_id)
            return destination_id
        else:
            logger.warning("Destination %s not found, will recreate", destination_id)
            try:
                ssm.delete_parameter(Name=param_name)
            except Exception:
                pass

    logger.info("Invoking setup lambda: %s", setup_lambda_name)
    response = lambda_client.invoke(
        FunctionName=setup_lambda_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"prefix": "traces"}),
    )

    result = json.loads(response["Payload"].read().decode())
    if result.get("statusCode") != 200:
        raise Exception(f"Setup lambda failed: {result}")

    destination_id = result.get("destination_id")
    if not destination_id:
        raise Exception(f"No destination_id returned: {result}")

    logger.info("Created destination: %s", destination_id)
    return destination_id


def handler(event, context):
    """Trigger LangSmith bulk export."""
    logger.info("Event: %s", json.dumps(event))

    langchain_project = event.get("langchain_project") or event.get("project_name", "label-evaluator")
    days_back = event.get("days_back", 1)
    end_time_value = event.get("end_time")
    if end_time_value:
        end_time = datetime.fromisoformat(end_time_value.replace("Z", "+00:00"))
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
    else:
        end_time = datetime.now(timezone.utc)
    end_time_iso = end_time.astimezone(timezone.utc).isoformat()
    start_time_iso = event.get("start_time")
    if not start_time_iso:
        start_time_iso = (end_time - timedelta(days=days_back)).isoformat()

    export_fields = event.get(
        "export_fields",
        [
            "id",
            "trace_id",
            "parent_run_id",
            "is_root",
            "name",
            "inputs",
            "outputs",
            "extra",
            "start_time",
            "end_time",
            "run_type",
            "status",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        ],
    )

    api_key = os.environ["LANGCHAIN_API_KEY"]
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID")
    stack = os.environ.get("STACK", "dev")
    setup_lambda_name = os.environ.get("SETUP_LAMBDA_NAME")

    ssm = boto3.client("ssm")
    http = urllib3.PoolManager()

    headers = {"x-api-key": api_key}
    if tenant_id:
        headers["x-tenant-id"] = tenant_id

    destination_id = _ensure_destination_exists(
        ssm, http, headers, stack, setup_lambda_name
    )

    # Get project_id
    response = http.request(
        "GET", f"{LANGSMITH_API_URL}/api/v1/sessions", headers=headers
    )
    if response.status != 200:
        raise Exception(f"Failed to list projects: {response.data.decode()}")

    projects = json.loads(response.data.decode("utf-8"))
    project_id = None
    for proj in projects:
        if proj.get("name") == langchain_project:
            project_id = proj.get("id")
            break

    if not project_id:
        raise Exception(f"Project not found: {langchain_project}")

    export_body = {
        "bulk_export_destination_id": destination_id,
        "session_id": project_id,
        "start_time": start_time_iso,
        "end_time": end_time_iso,
        "export_fields": export_fields,
    }

    post_headers = dict(headers)
    post_headers["Content-Type"] = "application/json"

    response = http.request(
        "POST",
        f"{LANGSMITH_API_URL}/api/v1/bulk-exports",
        headers=post_headers,
        body=json.dumps(export_body),
    )

    if response.status not in (200, 201, 202):
        raise Exception(f"Failed to trigger export: {response.data.decode()}")

    result = json.loads(response.data.decode("utf-8"))
    export_id = result.get("id")
    logger.info("Triggered export: %s", export_id)

    return {"export_id": export_id, "status": result.get("status", "pending")}
'''

            trigger_export_lambda = Function(
                f"{name}-trigger-export",
                name=f"{name}-trigger-export",
                role=trigger_export_role.arn,
                runtime="python3.12",
                architectures=["arm64"],
                handler="index.handler",
                code=AssetArchive(
                    {"index.py": StringAsset(trigger_export_code)}
                ),
                timeout=30,
                memory_size=128,
                tags={"environment": stack},
                environment=FunctionEnvironmentArgs(
                    variables={
                        "LANGCHAIN_API_KEY": self.langsmith_api_key,
                        "LANGSMITH_TENANT_ID": self.langsmith_tenant_id or "",
                        "SETUP_LAMBDA_NAME": self.setup_lambda_name,
                        "STACK": stack,
                    }
                ),
                opts=ResourceOptions(parent=self),
            )

            LogGroup(
                f"{name}-trigger-export-logs",
                name=trigger_export_lambda.name.apply(
                    lambda n: f"/aws/lambda/{n}"
                ),
                retention_in_days=14,
                opts=ResourceOptions(parent=self),
            )

            # Check Export Lambda Role
            check_export_role = Role(
                f"{name}-check-export-role",
                name=f"{name}-check-export-role",
                assume_role_policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {
                                    "Service": "lambda.amazonaws.com"
                                },
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    }
                ),
                opts=ResourceOptions(parent=self),
            )

            RolePolicyAttachment(
                f"{name}-check-export-basic-exec",
                role=check_export_role.name,
                policy_arn=(
                    "arn:aws:iam::aws:policy/service-role/"
                    "AWSLambdaBasicExecutionRole"
                ),
                opts=ResourceOptions(parent=check_export_role),
            )

            check_export_code = '''
import json
import logging
import os

import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LANGSMITH_API_URL = "https://api.smith.langchain.com"


def handler(event, context):
    """Check LangSmith export status."""
    logger.info("Event: %s", json.dumps(event))

    export_id = event.get("export_id")
    if not export_id:
        raise ValueError("export_id is required")

    api_key = os.environ["LANGCHAIN_API_KEY"]
    http = urllib3.PoolManager()

    response = http.request(
        "GET",
        f"{LANGSMITH_API_URL}/api/v1/bulk-exports/{export_id}",
        headers={"x-api-key": api_key},
    )

    if response.status != 200:
        logger.error("Failed to check status: %s", response.data.decode())
        return {"status": "error", "export_id": export_id}

    result = json.loads(response.data.decode("utf-8"))
    status = result.get("status", "unknown")

    # Normalize status
    if status in ("Complete", "Completed"):
        status = "completed"
    elif status in ("Failed", "Cancelled"):
        status = "failed"
    elif status in ("Pending", "Running", "InProgress"):
        status = "pending"

    logger.info("Export %s status: %s", export_id, status)
    return {"export_id": export_id, "status": status}
'''

            check_export_lambda = Function(
                f"{name}-check-export",
                name=f"{name}-check-export",
                role=check_export_role.arn,
                runtime="python3.12",
                architectures=["arm64"],
                handler="index.handler",
                code=AssetArchive(
                    {"index.py": StringAsset(check_export_code)}
                ),
                timeout=30,
                memory_size=128,
                tags={"environment": stack},
                environment=FunctionEnvironmentArgs(
                    variables={
                        "LANGCHAIN_API_KEY": self.langsmith_api_key,
                    }
                ),
                opts=ResourceOptions(parent=self),
            )

            LogGroup(
                f"{name}-check-export-logs",
                name=check_export_lambda.name.apply(
                    lambda n: f"/aws/lambda/{n}"
                ),
                retention_in_days=14,
                opts=ResourceOptions(parent=self),
            )

        # Store Lambda references for use in step function
        self.trigger_export_lambda = trigger_export_lambda
        self.check_export_lambda = check_export_lambda

        # ============================================================
        # Step Function role policies
        # ============================================================
        # Build list of Lambda ARNs (including export lambdas if enabled)
        lambda_arn_list = [
            list_merchants_lambda.arn,
            list_all_receipts_lambda.arn,
            fetch_receipt_data_lambda.arn,
            compute_patterns_lambda.arn,
            evaluate_labels_lambda.arn,
            evaluate_currency_lambda.arn,
            evaluate_metadata_lambda.arn,
            evaluate_financial_lambda.arn,
            close_trace_lambda.arn,
            aggregate_results_lambda.arn,
            final_aggregate_lambda.arn,
            discover_patterns_lambda.arn,
            llm_review_lambda.arn,
            unified_evaluator_lambda.arn,
            unified_pattern_builder_lambda.arn,  # NEW: combined pattern builder
        ]

        if trigger_export_lambda and check_export_lambda:
            lambda_arn_list.append(trigger_export_lambda.arn)
            lambda_arn_list.append(check_export_lambda.arn)

        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(*lambda_arn_list).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": list(arns),
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=sfn_role),
        )

        # CloudWatch Logs policy
        RolePolicy(
            f"{name}-sfn-logs-policy",
            role=sfn_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogDelivery",
                                "logs:GetLogDelivery",
                                "logs:UpdateLogDelivery",
                                "logs:DeleteLogDelivery",
                                "logs:ListLogDeliveries",
                                "logs:PutResourcePolicy",
                                "logs:DescribeResourcePolicies",
                                "logs:DescribeLogGroups",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=sfn_role),
        )

        # EMR Serverless policy (if EMR analytics enabled)
        if self.emr_enabled:
            import pulumi_aws as aws

            region = aws.get_region().name
            account_id = aws.get_caller_identity().account_id

            RolePolicy(
                f"{name}-sfn-emr-serverless-policy",
                role=sfn_role.id,
                policy=Output.all(
                    self.emr_application_id,
                    self.emr_job_execution_role_arn,
                ).apply(
                    lambda args: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                # EMR Serverless job management
                                {
                                    "Effect": "Allow",
                                    "Action": ["emr-serverless:StartJobRun"],
                                    "Resource": (
                                        f"arn:aws:emr-serverless:{region}:{account_id}"
                                        f":/applications/{args[0]}"
                                    ),
                                },
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "emr-serverless:GetJobRun",
                                        "emr-serverless:CancelJobRun",
                                    ],
                                    "Resource": (
                                        f"arn:aws:emr-serverless:{region}:{account_id}"
                                        f":/applications/{args[0]}/jobruns/*"
                                    ),
                                },
                                # Pass role to EMR Serverless
                                {
                                    "Effect": "Allow",
                                    "Action": "iam:PassRole",
                                    "Resource": args[1],
                                    "Condition": {
                                        "StringEquals": {
                                            "iam:PassedToService": (
                                                "emr-serverless.amazonaws.com"
                                            ),
                                        }
                                    },
                                },
                                # EventBridge for .sync waiter pattern
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "events:PutTargets",
                                        "events:PutRule",
                                        "events:DescribeRule",
                                    ],
                                    "Resource": (
                                        f"arn:aws:events:{region}:{account_id}:rule/"
                                        "StepFunctionsGetEventsForEMRServerlessJobRule"
                                    ),
                                },
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=sfn_role),
            )

        # ============================================================
        # CloudWatch Log Group
        # ============================================================
        log_group = LogGroup(
            f"{name}-sf-logs",
            name=f"/aws/stepfunctions/{name}-sf",
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        # ============================================================
        # Step Function State Machine
        # ============================================================
        logging_config = log_group.arn.apply(
            lambda arn: StateMachineLoggingConfigurationArgs(
                level="ALL",
                include_execution_data=True,
                log_destination=f"{arn}:*",
            )
        )

        # Build Output.all with optional EMR and viz-cache parameters
        # Base Lambda ARNs (indices 0-14)
        base_outputs = [
            list_merchants_lambda.arn,  # 0
            list_all_receipts_lambda.arn,  # 1
            fetch_receipt_data_lambda.arn,  # 2
            compute_patterns_lambda.arn,  # 3
            evaluate_labels_lambda.arn,  # 4
            evaluate_currency_lambda.arn,  # 5
            evaluate_metadata_lambda.arn,  # 6
            evaluate_financial_lambda.arn,  # 7
            close_trace_lambda.arn,  # 8
            aggregate_results_lambda.arn,  # 9
            final_aggregate_lambda.arn,  # 10
            discover_patterns_lambda.arn,  # 11
            llm_review_lambda.arn,  # 12
            unified_evaluator_lambda.arn,  # 13
            unified_pattern_builder_lambda.arn,  # 14 - NEW
            self.batch_bucket.bucket,  # 15
        ]

        # Add EMR outputs if enabled (indices 16-20)
        if self.emr_enabled:
            base_outputs.extend(
                [
                    self.emr_application_id,  # 16
                    self.emr_job_execution_role_arn,  # 17
                    self.langsmith_export_bucket,  # 18
                    self.analytics_output_bucket,  # 19
                    self.spark_artifacts_bucket,  # 20
                ]
            )

        # Add viz-cache outputs if enabled (indices 21-23)
        if (
            self.viz_cache_enabled
            and trigger_export_lambda
            and check_export_lambda
        ):
            base_outputs.extend(
                [
                    self.cache_bucket,  # 21
                    trigger_export_lambda.arn,  # 22
                    check_export_lambda.arn,  # 23
                ]
            )

        # Helper to safely get EMR and viz-cache params
        def build_emr_config(args):
            """Build EmrConfig with optional viz-cache params."""
            emr_base_idx = 16  # After batch_bucket at 15
            viz_base_idx = 21  # After EMR params

            config = EmrConfig(
                application_id=(
                    args[emr_base_idx] if self.emr_enabled else None
                ),
                job_execution_role_arn=(
                    args[emr_base_idx + 1] if self.emr_enabled else None
                ),
                langsmith_export_bucket=(
                    args[emr_base_idx + 2] if self.emr_enabled else None
                ),
                analytics_output_bucket=(
                    args[emr_base_idx + 3] if self.emr_enabled else None
                ),
                spark_artifacts_bucket=(
                    args[emr_base_idx + 4] if self.emr_enabled else None
                ),
            )

            # Add viz-cache params if enabled
            if self.viz_cache_enabled and len(args) > viz_base_idx:
                config.cache_bucket = args[viz_base_idx]
                config.batch_bucket = args[15]  # batch_bucket from base
                config.trigger_export_lambda_arn = args[viz_base_idx + 1]
                config.check_export_lambda_arn = args[viz_base_idx + 2]

            return config

        self.state_machine = StateMachine(
            f"{name}-sf",
            name=f"{name}-sf",
            role_arn=sfn_role.arn,
            type="STANDARD",
            tags={"environment": stack},
            definition=Output.all(*base_outputs).apply(
                lambda args: create_step_function_definition(
                    lambdas=LambdaArns(
                        list_merchants=args[0],
                        list_all_receipts=args[1],
                        fetch_receipt_data=args[2],
                        compute_patterns=args[3],
                        evaluate_labels=args[4],
                        evaluate_currency=args[5],
                        evaluate_metadata=args[6],
                        evaluate_financial=args[7],
                        close_trace=args[8],
                        aggregate_results=args[9],
                        final_aggregate=args[10],
                        discover_patterns=args[11],
                        llm_review=args[12],
                        unified_evaluator=args[13],
                        unified_pattern_builder=args[14],  # NEW
                    ),
                    runtime=RuntimeConfig(
                        batch_bucket=args[15],  # Updated index
                    ),
                    emr=build_emr_config(args),
                )
            ),
            logging_configuration=logging_config,
            opts=ResourceOptions(parent=self, depends_on=[log_group]),
        )

        # ============================================================
        # Outputs
        # ============================================================
        self.state_machine_arn = self.state_machine.arn
        self.batch_bucket_name = self.batch_bucket.bucket

        self.register_outputs(
            {
                "state_machine_arn": self.state_machine.arn,
                "batch_bucket_name": self.batch_bucket.bucket,
                "list_merchants_lambda_arn": list_merchants_lambda.arn,
                "list_all_receipts_lambda_arn": list_all_receipts_lambda.arn,
                "evaluate_labels_lambda_arn": evaluate_labels_lambda.arn,
                "evaluate_currency_lambda_arn": evaluate_currency_lambda.arn,
                "evaluate_metadata_lambda_arn": evaluate_metadata_lambda.arn,
                "evaluate_financial_lambda_arn": evaluate_financial_lambda.arn,
                "close_trace_lambda_arn": close_trace_lambda.arn,
                "llm_review_lambda_arn": llm_review_lambda.arn,
                "aggregate_results_lambda_arn": aggregate_results_lambda.arn,
                "final_aggregate_lambda_arn": final_aggregate_lambda.arn,
                "discover_patterns_lambda_arn": discover_patterns_lambda.arn,
                "unified_evaluator_lambda_arn": unified_evaluator_lambda.arn,
                "unified_pattern_builder_lambda_arn": (
                    unified_pattern_builder_lambda.arn
                ),
            }
        )
