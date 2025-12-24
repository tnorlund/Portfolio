"""
Pulumi infrastructure for Label Evaluator Step Function with LangSmith Tracing.

This component creates a Step Function with full trace propagation across
all Lambda invocations, providing unified visibility in LangSmith.

The trace hierarchy:
- ListMerchants: Root trace for the workflow
  - ProcessMerchant: Child trace per merchant
    - ListReceipts: List receipts for merchant
    - DiscoverLineItemPatterns: LLM discovers line item patterns
    - ComputePatterns: Compute spatial patterns (visible sub-spans)
    - EvaluateLabels: Per-receipt evaluation (each as child span)
    - CollectIssues: Collect flagged issues
    - BatchIssues: Batch issues for LLM review
    - LLMReviewBatch: LLM reviews each batch (visible LLM calls)
    - AggregateResults: Aggregate merchant results
  - FinalAggregate: Aggregate across all merchants

All business logic is identical to the non-traced version. The only
difference is trace context propagation via `langsmith_headers`.
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
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")

# Label evaluator specific config
evaluator_config = Config("label-evaluator")
max_concurrency_default = evaluator_config.get_int("max_concurrency") or 10
batch_size_default = evaluator_config.get_int("batch_size") or 10


class LabelEvaluatorTracedStepFunction(ComponentResource):
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
        max_concurrency: Optional[int] = None,
        batch_size: Optional[int] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(
            f"label-evaluator-traced-step-function:{name}", name, None, opts
        )
        stack = pulumi.get_stack()

        self.max_concurrency = max_concurrency or max_concurrency_default
        self.batch_size = batch_size or batch_size_default
        self.chromadb_bucket_name = chromadb_bucket_name
        self.chromadb_bucket_arn = chromadb_bucket_arn

        # ============================================================
        # S3 Bucket for batch files and results
        # ============================================================
        is_prod = stack in ("prod", "production")
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=not is_prod,
            tags={"environment": stack, "purpose": "label-evaluator-traced"},
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
        HANDLERS_TRACED_DIR = os.path.join(CURRENT_DIR, "handlers_traced")
        HANDLERS_DIR = os.path.join(CURRENT_DIR, "handlers")
        UTILS_DIR = os.path.join(CURRENT_DIR, "lambdas", "utils")

        # Common environment for tracing
        tracing_env = {
            "LANGCHAIN_API_KEY": langchain_api_key,
            "LANGCHAIN_TRACING_V2": "true",
            "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
            "LANGCHAIN_PROJECT": config.get("langchain_project")
            or "label-evaluator-traced",
        }

        # ============================================================
        # Zip Lambdas (Traced versions)
        # ============================================================

        # list_merchants_traced Lambda
        list_merchants_lambda = Function(
            f"{name}-list-merchants",
            name=f"{name}-list-merchants",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_merchants_traced.handler",
            code=AssetArchive(
                {
                    "list_merchants_traced.py": FileAsset(
                        os.path.join(HANDLERS_TRACED_DIR, "list_merchants_traced.py")
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

        # list_receipts_traced Lambda
        list_receipts_lambda = Function(
            f"{name}-list-receipts",
            name=f"{name}-list-receipts",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="list_receipts_traced.handler",
            code=AssetArchive(
                {
                    "list_receipts_traced.py": FileAsset(
                        os.path.join(HANDLERS_TRACED_DIR, "list_receipts_traced.py")
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

        # fetch_receipt_data_traced Lambda
        fetch_receipt_data_lambda = Function(
            f"{name}-fetch-receipt-data",
            name=f"{name}-fetch-receipt-data",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="fetch_receipt_data_traced.handler",
            code=AssetArchive(
                {
                    "fetch_receipt_data_traced.py": FileAsset(
                        os.path.join(HANDLERS_TRACED_DIR, "fetch_receipt_data_traced.py")
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

        # aggregate_results_traced Lambda
        aggregate_results_lambda = Function(
            f"{name}-aggregate-results",
            name=f"{name}-aggregate-results",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="aggregate_results_traced.handler",
            code=AssetArchive(
                {
                    "aggregate_results_traced.py": FileAsset(
                        os.path.join(HANDLERS_TRACED_DIR, "aggregate_results_traced.py")
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

        # collect_issues_traced Lambda
        collect_issues_lambda = Function(
            f"{name}-collect-issues",
            name=f"{name}-collect-issues",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="collect_issues_traced.handler",
            code=AssetArchive(
                {
                    "collect_issues_traced.py": FileAsset(
                        os.path.join(HANDLERS_TRACED_DIR, "collect_issues_traced.py")
                    ),
                    "tracing.py": FileAsset(
                        os.path.join(UTILS_DIR, "tracing.py")
                    ),
                    "s3_helpers.py": FileAsset(
                        os.path.join(UTILS_DIR, "s3_helpers.py")
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

        # batch_issues_traced Lambda
        batch_issues_lambda = Function(
            f"{name}-batch-issues",
            name=f"{name}-batch-issues",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="batch_issues_traced.handler",
            code=AssetArchive(
                {
                    "batch_issues_traced.py": FileAsset(
                        os.path.join(HANDLERS_TRACED_DIR, "batch_issues_traced.py")
                    ),
                    "tracing.py": FileAsset(
                        os.path.join(UTILS_DIR, "tracing.py")
                    ),
                    "s3_helpers.py": FileAsset(
                        os.path.join(UTILS_DIR, "s3_helpers.py")
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

        # final_aggregate_traced Lambda
        final_aggregate_lambda = Function(
            f"{name}-final-aggregate",
            name=f"{name}-final-aggregate",
            role=lambda_role.arn,
            runtime="python3.12",
            architectures=["arm64"],
            handler="final_aggregate_traced.handler",
            code=AssetArchive(
                {
                    "final_aggregate_traced.py": FileAsset(
                        os.path.join(HANDLERS_TRACED_DIR, "final_aggregate_traced.py")
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
        # Container Lambda: compute_patterns_traced
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
                "OLLAMA_API_KEY": ollama_api_key,
                "OLLAMA_BASE_URL": "https://ollama.com",
                "OLLAMA_MODEL": "gpt-oss:120b-cloud",
                **tracing_env,
            },
        }

        compute_patterns_docker_image = CodeBuildDockerImage(
            f"{name}-cp-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.compute_patterns_traced"
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
        # Container Lambda: evaluate_labels_traced
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
                "Dockerfile.evaluate_traced"
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
        # Container Lambda: discover_patterns_traced (LLM)
        # Uses httpx for Ollama API calls - requires container for dependencies
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
                "OLLAMA_API_KEY": ollama_api_key,
                "OLLAMA_BASE_URL": "https://ollama.com",
                "OLLAMA_MODEL": "gpt-oss:120b-cloud",
                **tracing_env,
            },
        }

        discover_patterns_docker_image = CodeBuildDockerImage(
            f"{name}-dp-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.discover_patterns_traced"
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

        discover_patterns_lambda = discover_patterns_docker_image.lambda_function

        # ============================================================
        # Container Lambda: llm_review_traced (LLM)
        # ============================================================
        llm_review_lambda_config = {
            "role_arn": lambda_role.arn,
            "timeout": 900,  # 15 minutes
            "memory_size": 3072,  # 3 GB for ChromaDB + LLM
            "tags": {"environment": stack},
            "ephemeral_storage": 10240,  # 10 GB for ChromaDB
            "environment": {
                "BATCH_BUCKET": self.batch_bucket.bucket,
                "CHROMADB_BUCKET": chromadb_bucket_name or "",
                "RECEIPT_AGENT_DYNAMO_TABLE_NAME": dynamodb_table_name,
                "RECEIPT_AGENT_OPENAI_API_KEY": openai_api_key,
                "RECEIPT_AGENT_OLLAMA_API_KEY": ollama_api_key,
                "RECEIPT_AGENT_OLLAMA_BASE_URL": "https://ollama.com",
                "RECEIPT_AGENT_OLLAMA_MODEL": "gpt-oss:120b-cloud",
                "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY": "/tmp/chromadb",
                **tracing_env,
                "MAX_ISSUES_PER_LLM_CALL": "15",
                "CIRCUIT_BREAKER_THRESHOLD": "5",
                "LLM_MAX_JITTER_SECONDS": "0.25",
            },
        }

        llm_review_docker_image = CodeBuildDockerImage(
            f"{name}-llm-img",
            dockerfile_path=(
                "infra/label_evaluator_step_functions/lambdas/"
                "Dockerfile.llm_traced"
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
        # Step Function role policies
        # ============================================================
        RolePolicy(
            f"{name}-sfn-lambda-policy",
            role=sfn_role.id,
            policy=Output.all(
                list_merchants_lambda.arn,
                list_receipts_lambda.arn,
                fetch_receipt_data_lambda.arn,
                compute_patterns_lambda.arn,
                evaluate_labels_lambda.arn,
                aggregate_results_lambda.arn,
                collect_issues_lambda.arn,
                batch_issues_lambda.arn,
                final_aggregate_lambda.arn,
                discover_patterns_lambda.arn,
                llm_review_lambda.arn,
            ).apply(
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

        self.state_machine = StateMachine(
            f"{name}-sf",
            name=f"{name}-sf",
            role_arn=sfn_role.arn,
            type="STANDARD",
            tags={"environment": stack},
            definition=Output.all(
                list_merchants_lambda.arn,
                list_receipts_lambda.arn,
                fetch_receipt_data_lambda.arn,
                compute_patterns_lambda.arn,
                evaluate_labels_lambda.arn,
                aggregate_results_lambda.arn,
                collect_issues_lambda.arn,
                batch_issues_lambda.arn,
                final_aggregate_lambda.arn,
                discover_patterns_lambda.arn,
                llm_review_lambda.arn,
                self.batch_bucket.bucket,
            ).apply(
                lambda args: self._create_step_function_definition(
                    list_merchants_arn=args[0],
                    list_receipts_arn=args[1],
                    fetch_receipt_data_arn=args[2],
                    compute_patterns_arn=args[3],
                    evaluate_labels_arn=args[4],
                    aggregate_results_arn=args[5],
                    collect_issues_arn=args[6],
                    batch_issues_arn=args[7],
                    final_aggregate_arn=args[8],
                    discover_patterns_arn=args[9],
                    llm_review_arn=args[10],
                    batch_bucket=args[11],
                    max_concurrency=self.max_concurrency,
                    batch_size=self.batch_size,
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
                "list_receipts_lambda_arn": list_receipts_lambda.arn,
                "evaluate_labels_lambda_arn": evaluate_labels_lambda.arn,
                "llm_review_lambda_arn": llm_review_lambda.arn,
                "aggregate_results_lambda_arn": aggregate_results_lambda.arn,
                "final_aggregate_lambda_arn": final_aggregate_lambda.arn,
                "collect_issues_lambda_arn": collect_issues_lambda.arn,
                "batch_issues_lambda_arn": batch_issues_lambda.arn,
                "discover_patterns_lambda_arn": discover_patterns_lambda.arn,
            }
        )

    def _create_step_function_definition(
        self,
        list_merchants_arn: str,
        list_receipts_arn: str,
        fetch_receipt_data_arn: str,
        compute_patterns_arn: str,
        evaluate_labels_arn: str,
        aggregate_results_arn: str,
        collect_issues_arn: str,
        batch_issues_arn: str,
        final_aggregate_arn: str,
        discover_patterns_arn: str,
        llm_review_arn: str,
        batch_bucket: str,
        max_concurrency: int,
        batch_size: int,
    ) -> str:
        """Create Step Function definition with trace propagation.

        Key difference from non-traced version:
        - Container-based Lambdas handle LangSmith tracing
        - DiscoverLineItemPatterns STARTS the trace (first container Lambda)
        - Other container Lambdas receive `langsmith_headers` from trace origin
        - Zip-based Lambdas don't have langsmith access, no headers needed

        Runtime inputs (from Step Function execution input):
        - dry_run: bool (default: False) - Don't write to DynamoDB
        - merchant_name: str (optional) - Process single merchant
        - limit: int (optional) - Limit receipts per merchant

        Note: Unlike the original Step Function, this traced version always
        runs the full LLM workflow (no skip_llm_review option) since the
        purpose is LangSmith observability of LLM calls.
        """
        definition = {
            "Comment": "Label Evaluator with LangSmith Trace Propagation",
            "StartAt": "NormalizeInput",
            "States": {
                # Capture original input
                "NormalizeInput": {
                    "Type": "Pass",
                    "Parameters": {
                        "original_input.$": "$",
                        "merged_input.$": "$",
                    },
                    "ResultPath": "$.normalized",
                    "Next": "SetDefaults",
                },
                # Set defaults
                "SetDefaults": {
                    "Type": "Pass",
                    "Result": {
                        "dry_run": False,
                        "force_rediscovery": False,
                        "limit": None,
                    },
                    "ResultPath": "$.defaults",
                    "Next": "MergeInputWithDefaults",
                },
                # Merge user input with defaults (input takes precedence)
                "MergeInputWithDefaults": {
                    "Type": "Pass",
                    "Parameters": {
                        "merged.$": "States.JsonMerge($.defaults, $.normalized.merged_input, false)",
                    },
                    "ResultPath": "$.config",
                    "Next": "CheckInputMode",
                },
                # Check if merchant_name is in input
                "CheckInputMode": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.config.merged.merchant_name",
                            "IsPresent": True,
                            "Next": "InitializeSingleMerchant",
                        }
                    ],
                    "Default": "InitializeAllMerchants",
                },
                # Initialize for single merchant mode
                "InitializeSingleMerchant": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "batch_bucket": batch_bucket,
                        "batch_size": batch_size,
                        "merchant_name.$": "$.config.merged.merchant_name",
                        "dry_run.$": "$.config.merged.dry_run",
                        "force_rediscovery.$": "$.config.merged.force_rediscovery",
                        "max_training_receipts": 50,
                        "min_receipts": 5,
                        "limit.$": "$.config.merged.limit",
                        "original_input.$": "$.normalized.original_input",
                    },
                    "ResultPath": "$.init",
                    "Next": "SingleMerchantMode",
                },
                # Initialize for all merchants mode
                "InitializeAllMerchants": {
                    "Type": "Pass",
                    "Parameters": {
                        "execution_id.$": "$$.Execution.Name",
                        "start_time.$": "$$.Execution.StartTime",
                        "batch_bucket": batch_bucket,
                        "batch_size": batch_size,
                        "merchant_name": None,
                        "dry_run.$": "$.config.merged.dry_run",
                        "force_rediscovery.$": "$.config.merged.force_rediscovery",
                        "max_training_receipts": 50,
                        "min_receipts": 5,
                        "limit.$": "$.config.merged.limit",
                        "original_input.$": "$.normalized.original_input",
                    },
                    "ResultPath": "$.init",
                    "Next": "ListMerchants",
                },
                # Single merchant mode - process just one merchant
                "SingleMerchantMode": {
                    "Type": "Pass",
                    "Parameters": {
                        "merchants": [
                            {
                                "merchant_name.$": "$.init.merchant_name",
                                "receipt_count": 0,
                            }
                        ],
                        "total_merchants": 1,
                        "mode": "single",
                    },
                    "ResultPath": "$.merchants_data",
                    "Next": "ProcessMerchants",
                },
                # List all merchants (zip-based, no tracing)
                "ListMerchants": {
                    "Type": "Task",
                    "Resource": list_merchants_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "min_receipts.$": "$.init.min_receipts",
                        "max_training_receipts.$": "$.init.max_training_receipts",
                    },
                    "ResultPath": "$.merchants_data",
                    "Retry": [
                        {
                            "ErrorEquals": ["States.TaskFailed"],
                            "IntervalSeconds": 2,
                            "MaxAttempts": 3,
                            "BackoffRate": 2.0,
                        }
                    ],
                    "Next": "HasMerchants",
                },
                "HasMerchants": {
                    "Type": "Choice",
                    "Choices": [
                        {
                            "Variable": "$.merchants_data.total_merchants",
                            "NumericGreaterThan": 0,
                            "Next": "ProcessMerchants",
                        }
                    ],
                    "Default": "NoMerchants",
                },
                "NoMerchants": {
                    "Type": "Pass",
                    "Result": {"message": "No merchants found"},
                    "End": True,
                },
                # Process each merchant with trace propagation
                "ProcessMerchants": {
                    "Type": "Map",
                    "ItemsPath": "$.merchants_data.merchants",
                    "MaxConcurrency": max_concurrency,
                    "Parameters": {
                        "merchant.$": "$$.Map.Item.Value",
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "batch_size.$": "$.init.batch_size",
                        "max_training_receipts.$": "$.init.max_training_receipts",
                        "limit.$": "$.init.limit",
                        "dry_run.$": "$.init.dry_run",
                        "force_rediscovery.$": "$.init.force_rediscovery",
                    },
                    "ItemProcessor": {
                        "ProcessorConfig": {"Mode": "INLINE"},
                        "StartAt": "ListReceipts",
                        "States": {
                            # List receipts (zip-based, no tracing)
                            "ListReceipts": {
                                "Type": "Task",
                                "Resource": list_receipts_arn,
                                "TimeoutSeconds": 300,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "batch_size.$": "$.batch_size",
                                    "merchant.$": "$.merchant",
                                    "max_training_receipts.$": "$.max_training_receipts",
                                    "limit.$": "$.limit",
                                },
                                "ResultPath": "$.receipts_data",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["States.TaskFailed"],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 3,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Next": "HasReceipts",
                            },
                            "HasReceipts": {
                                "Type": "Choice",
                                "Choices": [
                                    {
                                        "Variable": "$.receipts_data.total_receipts",
                                        "NumericGreaterThan": 0,
                                        "Next": "DiscoverLineItemPatterns",
                                    }
                                ],
                                "Default": "NoReceipts",
                            },
                            "NoReceipts": {
                                "Type": "Pass",
                                "Parameters": {
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "status": "skipped",
                                    "reason": "No receipts",
                                },
                                "End": True,
                            },
                            # Discover line item patterns with LLM - STARTS trace
                            "DiscoverLineItemPatterns": {
                                "Type": "Task",
                                "Resource": discover_patterns_arn,
                                "TimeoutSeconds": 600,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "force_rediscovery.$": "$.force_rediscovery",
                                    # Pass execution ARN for deterministic trace ID
                                    "execution_arn.$": "$$.Execution.Id",
                                },
                                "ResultPath": "$.line_item_patterns",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["States.TaskFailed"],
                                        "IntervalSeconds": 5,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Next": "ComputePatterns",
                            },
                            # Compute spatial patterns - resumes trace
                            "ComputePatterns": {
                                "Type": "Task",
                                "Resource": compute_patterns_arn,
                                "TimeoutSeconds": 600,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "merchant.$": "$.merchant",
                                    "max_training_receipts.$": "$.max_training_receipts",
                                    # Deterministic trace propagation
                                    "execution_arn.$": "$$.Execution.Id",
                                    "trace_id.$": "$.line_item_patterns.trace_id",
                                    "root_run_id.$": "$.line_item_patterns.root_run_id",
                                    "root_dotted_order.$": "$.line_item_patterns.root_dotted_order",
                                },
                                "ResultPath": "$.patterns_result",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["States.TaskFailed"],
                                        "IntervalSeconds": 5,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Next": "ProcessBatches",
                            },
                            # Process receipt batches
                            "ProcessBatches": {
                                "Type": "Map",
                                "ItemsPath": "$.receipts_data.receipt_batches",
                                "MaxConcurrency": max_concurrency,
                                "Parameters": {
                                    "batch.$": "$$.Map.Item.Value",
                                    "batch_index.$": "$$.Map.Item.Index",
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "patterns_s3_key.$": "$.patterns_result.patterns_s3_key",
                                    # Deterministic trace propagation
                                    "execution_arn.$": "$$.Execution.Id",
                                    "trace_id.$": "$.line_item_patterns.trace_id",
                                    "root_run_id.$": "$.line_item_patterns.root_run_id",
                                    "root_dotted_order.$": "$.line_item_patterns.root_dotted_order",
                                },
                                "ItemProcessor": {
                                    "ProcessorConfig": {"Mode": "INLINE"},
                                    "StartAt": "ProcessReceipts",
                                    "States": {
                                        "ProcessReceipts": {
                                            "Type": "Map",
                                            "ItemsPath": "$.batch",
                                            "MaxConcurrency": 5,
                                            "Parameters": {
                                                "receipt.$": "$$.Map.Item.Value",
                                                "receipt_index.$": "$$.Map.Item.Index",
                                                "batch_index.$": "$.batch_index",
                                                "execution_id.$": "$.execution_id",
                                                "batch_bucket.$": "$.batch_bucket",
                                                "patterns_s3_key.$": "$.patterns_s3_key",
                                                # Deterministic trace propagation
                                                "execution_arn.$": "$.execution_arn",
                                                "trace_id.$": "$.trace_id",
                                                "root_run_id.$": "$.root_run_id",
                                                "root_dotted_order.$": "$.root_dotted_order",
                                            },
                                            "ItemProcessor": {
                                                "ProcessorConfig": {"Mode": "INLINE"},
                                                "StartAt": "FetchReceiptData",
                                                "States": {
                                                    "FetchReceiptData": {
                                                        "Type": "Task",
                                                        "Resource": fetch_receipt_data_arn,
                                                        "TimeoutSeconds": 60,
                                                        "Parameters": {
                                                            "receipt.$": "$.receipt",
                                                            "execution_id.$": "$.execution_id",
                                                            "batch_bucket.$": "$.batch_bucket",
                                                        },
                                                        "ResultPath": "$.receipt_data",
                                                        "Retry": [
                                                            {
                                                                "ErrorEquals": ["States.TaskFailed"],
                                                                "IntervalSeconds": 1,
                                                                "MaxAttempts": 2,
                                                                "BackoffRate": 2.0,
                                                            }
                                                        ],
                                                        "Next": "EvaluateLabels",
                                                    },
                                                    "EvaluateLabels": {
                                                        "Type": "Task",
                                                        "Resource": evaluate_labels_arn,
                                                        "TimeoutSeconds": 300,
                                                        "Parameters": {
                                                            "data_s3_key.$": "$.receipt_data.data_s3_key",
                                                            "patterns_s3_key.$": "$.patterns_s3_key",
                                                            "execution_id.$": "$.execution_id",
                                                            "batch_bucket.$": "$.batch_bucket",
                                                            # Deterministic trace propagation
                                                            "execution_arn.$": "$.execution_arn",
                                                            "trace_id.$": "$.trace_id",
                                                            "root_run_id.$": "$.root_run_id",
                                                            "root_dotted_order.$": "$.root_dotted_order",
                                                            "batch_index.$": "$.batch_index",
                                                            "receipt_index.$": "$.receipt_index",
                                                        },
                                                        "ResultPath": "$.eval_result",
                                                        "Retry": [
                                                            {
                                                                "ErrorEquals": ["States.TaskFailed"],
                                                                "IntervalSeconds": 2,
                                                                "MaxAttempts": 2,
                                                                "BackoffRate": 2.0,
                                                            }
                                                        ],
                                                        "Next": "ReturnResult",
                                                    },
                                                    "ReturnResult": {
                                                        "Type": "Pass",
                                                        "Parameters": {
                                                            "status.$": "$.eval_result.status",
                                                            "image_id.$": "$.eval_result.image_id",
                                                            "receipt_id.$": "$.eval_result.receipt_id",
                                                            "issues_found.$": "$.eval_result.issues_found",
                                                            "results_s3_key.$": "$.eval_result.results_s3_key",
                                                        },
                                                        "End": True,
                                                    },
                                                },
                                            },
                                            "End": True,
                                        },
                                    },
                                },
                                "ResultPath": "$.batch_results",
                                "Next": "CollectIssues",
                            },
                            # Collect all issues from evaluations
                            "CollectIssues": {
                                "Type": "Task",
                                "Resource": collect_issues_arn,
                                "TimeoutSeconds": 300,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "process_results.$": "$.batch_results",
                                },
                                "ResultPath": "$.collected_issues",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["States.TaskFailed"],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Next": "CheckHasIssues",
                            },
                            # Check if there are issues to review
                            "CheckHasIssues": {
                                "Type": "Choice",
                                "Choices": [
                                    {
                                        "Variable": "$.collected_issues.total_issues",
                                        "NumericGreaterThan": 0,
                                        "Next": "BatchIssues",
                                    }
                                ],
                                "Default": "AggregateResults",
                            },
                            # Batch issues for parallel LLM review
                            "BatchIssues": {
                                "Type": "Task",
                                "Resource": batch_issues_arn,
                                "TimeoutSeconds": 300,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "merchant_receipt_count.$": "$.receipts_data.total_receipts",
                                    "issues_s3_key.$": "$.collected_issues.issues_s3_key",
                                    "batch_size": 25,
                                    "dry_run.$": "$.dry_run",
                                },
                                "ResultPath": "$.batched_issues",
                                "Retry": [
                                    {
                                        "ErrorEquals": ["States.TaskFailed"],
                                        "IntervalSeconds": 2,
                                        "MaxAttempts": 2,
                                        "BackoffRate": 2.0,
                                    }
                                ],
                                "Next": "CheckHasBatches",
                            },
                            "CheckHasBatches": {
                                "Type": "Choice",
                                "Choices": [
                                    {
                                        "Variable": "$.batched_issues.batch_count",
                                        "NumericGreaterThan": 0,
                                        "Next": "ProcessLLMBatches",
                                    }
                                ],
                                "Default": "AggregateResults",
                            },
                            # Process LLM review batches in parallel (one per receipt)
                            "ProcessLLMBatches": {
                                "Type": "Map",
                                "ItemsPath": "$.batched_issues.batches",
                                "MaxConcurrency": 5,
                                "Parameters": {
                                    "batch_info.$": "$$.Map.Item.Value",
                                    "llm_batch_index.$": "$$.Map.Item.Index",
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "merchant_receipt_count.$": "$.receipts_data.total_receipts",
                                    "line_item_patterns_s3_key.$": "$.line_item_patterns.patterns_s3_key",
                                    "dry_run.$": "$.dry_run",
                                    # Receipt identification (one batch per receipt)
                                    "image_id.$": "$$.Map.Item.Value.image_id",
                                    "receipt_id.$": "$$.Map.Item.Value.receipt_id",
                                    # Deterministic trace propagation
                                    "execution_arn.$": "$$.Execution.Id",
                                    "trace_id.$": "$.line_item_patterns.trace_id",
                                    "root_run_id.$": "$.line_item_patterns.root_run_id",
                                    "root_dotted_order.$": "$.line_item_patterns.root_dotted_order",
                                },
                                "ItemProcessor": {
                                    "ProcessorConfig": {"Mode": "INLINE"},
                                    "StartAt": "LLMReviewBatch",
                                    "States": {
                                        "LLMReviewBatch": {
                                            "Type": "Task",
                                            "Resource": llm_review_arn,
                                            "TimeoutSeconds": 900,
                                            "Parameters": {
                                                "execution_id.$": "$.execution_id",
                                                "batch_bucket.$": "$.batch_bucket",
                                                "merchant_name.$": "$.merchant_name",
                                                "merchant_receipt_count.$": "$.merchant_receipt_count",
                                                "batch_s3_key.$": "$.batch_info.batch_s3_key",
                                                "batch_index.$": "$.batch_info.batch_index",
                                                "llm_batch_index.$": "$.llm_batch_index",
                                                "line_item_patterns_s3_key.$": "$.line_item_patterns_s3_key",
                                                "dry_run.$": "$.dry_run",
                                                # Receipt identification (one batch per receipt)
                                                "image_id.$": "$.image_id",
                                                "receipt_id.$": "$.receipt_id",
                                                # Deterministic trace propagation
                                                "execution_arn.$": "$.execution_arn",
                                                "trace_id.$": "$.trace_id",
                                                "root_run_id.$": "$.root_run_id",
                                                "root_dotted_order.$": "$.root_dotted_order",
                                            },
                                            "Retry": [
                                                {
                                                    "ErrorEquals": [
                                                        "OllamaRateLimitError"
                                                    ],
                                                    "IntervalSeconds": 30,
                                                    "MaxAttempts": 5,
                                                    "BackoffRate": 2.0,
                                                },
                                                {
                                                    "ErrorEquals": ["States.TaskFailed"],
                                                    "IntervalSeconds": 5,
                                                    "MaxAttempts": 2,
                                                    "BackoffRate": 2.0,
                                                },
                                            ],
                                            "End": True,
                                        },
                                    },
                                },
                                "ResultPath": "$.llm_review_results",
                                "Next": "AggregateResults",
                            },
                            # Aggregate results
                            "AggregateResults": {
                                "Type": "Task",
                                "Resource": aggregate_results_arn,
                                "TimeoutSeconds": 120,
                                "Parameters": {
                                    "execution_id.$": "$.execution_id",
                                    "batch_bucket.$": "$.batch_bucket",
                                    "process_results.$": "$.batch_results",
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "dry_run.$": "$.dry_run",
                                },
                                "ResultPath": "$.summary",
                                "Next": "ReturnMerchantResult",
                            },
                            "ReturnMerchantResult": {
                                "Type": "Pass",
                                "Parameters": {
                                    "merchant_name.$": "$.merchant.merchant_name",
                                    "status": "completed",
                                    "total_receipts.$": "$.receipts_data.total_receipts",
                                    "total_issues.$": "$.summary.total_issues",
                                    "summary.$": "$.summary",
                                },
                                "End": True,
                            },
                        },
                    },
                    "ResultPath": "$.all_results",
                    "Next": "FinalAggregate",
                },
                # Final aggregation across all merchants
                "FinalAggregate": {
                    "Type": "Task",
                    "Resource": final_aggregate_arn,
                    "TimeoutSeconds": 300,
                    "Parameters": {
                        "execution_id.$": "$.init.execution_id",
                        "batch_bucket.$": "$.init.batch_bucket",
                        "all_merchant_results.$": "$.all_results",
                    },
                    "End": True,
                },
            },
        }

        return json.dumps(definition)
