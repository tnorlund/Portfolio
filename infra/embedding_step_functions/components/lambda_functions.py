"""Lambda functions component for embedding infrastructure."""

import json
import os

# Import CodeBuildDockerImage for container Lambdas (matches compactor approach)
# Use absolute import path like compactor does
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from pulumi import (
    ComponentResource,
    FileArchive,
    Output,
    ResourceOptions,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
    FunctionFileSystemConfigArgs,
    FunctionTracingConfigArgs,
    FunctionVpcConfigArgs,
)
from pulumi_aws.s3 import Bucket

from .base import config as portfolio_config
from .base import (
    dynamo_layer,
    dynamodb_table,
    label_layer,
    openai_api_key,
    stack,
)

# Add infra directory to path for imports
infra_path = Path(__file__).parent.parent.parent
if str(infra_path) not in sys.path:
    sys.path.insert(0, str(infra_path))
from codebuild_docker_image import CodeBuildDockerImage

GIGABYTE = 1024
MINUTE = 60


# Helper to express memory/ephemeral storage in MiB (AWS expects MiB integers).
# Example: GiB(0.5) == 512, GiB(2) == 2048
def GiB(n: float | int) -> int:
    return int(n * 1024)


class LambdaFunctionsComponent(ComponentResource):
    """Component for creating Lambda functions and related resources."""

    def __init__(
        self,
        name: str,
        chromadb_buckets,
        chromadb_queues,
        docker_image_component,
        vpc_subnet_ids=None,
        lambda_security_group_id=None,
        efs_access_point_arn=None,
        efs_mount_targets=None,  # Mount targets dependency for Lambda
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize Lambda functions component.

        Args:
            name: Component name
            chromadb_buckets: ChromaDB S3 buckets
            chromadb_queues: ChromaDB SQS queues
            docker_image_component: Docker image component
            vpc_subnet_ids: Subnet IDs for Lambda VPC configuration
            lambda_security_group_id: Security group ID for Lambda VPC access
            efs_access_point_arn: EFS access point ARN for ChromaDB storage
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:LambdaFunctions",
            name,
            None,
            opts,
        )

        self.chromadb_buckets = chromadb_buckets
        self.chromadb_queues = chromadb_queues
        self.docker_image = docker_image_component
        self.vpc_subnet_ids = vpc_subnet_ids
        self.lambda_security_group_id = lambda_security_group_id
        self.efs_access_point_arn = efs_access_point_arn
        self.efs_mount_targets = (
            efs_mount_targets  # Store mount targets dependency
        )

        # Create S3 bucket for NDJSON batch files
        self.batch_bucket = Bucket(
            f"{name}-batch-bucket",
            force_destroy=True,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Create Lambda execution role
        self._create_lambda_role()

        # Create zip-based Lambda functions
        self._create_zip_lambda_functions()

        # Create container-based Lambda functions
        self._create_container_lambda_functions()

        # Store all functions for easy access
        self.all_functions = {
            **self.zip_lambda_functions,
            **self.container_lambda_functions,
        }

        # Register outputs
        self.register_outputs(
            {
                "batch_bucket_name": self.batch_bucket.bucket,
                "lambda_role_arn": self.lambda_role.arn,
                "function_arns": {
                    name: func.arn for name, func in self.all_functions.items()
                },
            }
        )

    def _create_lambda_role(self):
        """Create shared IAM role for all Lambda functions."""
        self.lambda_role = Role(
            f"unified-lambda-role-{stack}",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ],
                }
            ),
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Attach basic execution policy
        RolePolicyAttachment(
            f"lambda-basic-execution-{stack}",
            role=self.lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole"
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach VPC access policy if VPC is configured
        if self.vpc_subnet_ids and self.lambda_security_group_id:
            RolePolicyAttachment(
                f"lambda-vpc-access-{stack}",
                role=self.lambda_role.name,
                policy_arn=(
                    "arn:aws:iam::aws:policy/service-role/"
                    "AWSLambdaVPCAccessExecutionRole"
                ),
                opts=ResourceOptions(parent=self),
            )

        # Add permissions for DynamoDB, S3, and SQS
        RolePolicy(
            f"lambda-permissions-{stack}",
            role=self.lambda_role.id,
            policy=Output.all(
                dynamodb_table.name,
                self.chromadb_buckets.bucket_name,
                self.chromadb_queues.lines_queue_arn,
                self.chromadb_queues.words_queue_arn,
                self.batch_bucket.bucket,
            ).apply(self._create_lambda_policy),
            opts=ResourceOptions(parent=self),
        )

    def _create_lambda_policy(self, args: list) -> str:
        """Create IAM policy for Lambda functions."""
        return json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:PutItem",
                            "dynamodb:GetItem",
                            "dynamodb:Query",
                            "dynamodb:UpdateItem",
                            "dynamodb:DeleteItem",
                            "dynamodb:BatchWriteItem",
                            "dynamodb:BatchGetItem",
                            "dynamodb:DescribeTable",
                        ],
                        "Resource": [
                            f"arn:aws:dynamodb:*:*:table/{args[0]}",
                            f"arn:aws:dynamodb:*:*:table/{args[0]}/index/*",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [
                            f"arn:aws:s3:::{args[1]}",
                            f"arn:aws:s3:::{args[1]}/*",
                            f"arn:aws:s3:::{args[4]}",
                            f"arn:aws:s3:::{args[4]}/*",
                        ],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "sqs:SendMessage",
                            "sqs:GetQueueAttributes",
                        ],
                        "Resource": [args[2], args[3]],
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "xray:PutTraceSegments",
                            "xray:PutTelemetryRecords",
                            "xray:GetSamplingRules",
                            "xray:GetSamplingTargets",
                        ],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "cloudwatch:PutMetricData",
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                            "logs:PutLogEvents",
                        ],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "ecr:GetAuthorizationToken",
                            "ecr:BatchGetImage",
                            "ecr:GetDownloadUrlForLayer",
                        ],
                        "Resource": "*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": [
                            "elasticfilesystem:ClientMount",
                            "elasticfilesystem:ClientWrite",
                            "elasticfilesystem:DescribeMountTargets",
                        ],
                        "Resource": "*",
                    },
                ],
            }
        )

    def _create_zip_lambda_functions(self):
        """Create zip-based Lambda functions."""
        self.zip_lambda_functions = {}

        # Define zip-based Lambda configurations
        # Naming convention: operation-first (embedding-{operation}-{entity})
        zip_configs = {
            "embedding-list-pending": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 15,
                "source_dir": "list_pending",
            },
            "embedding-find-lines": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "find_unembedded",
            },
            "embedding-find-words": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "find_unembedded_words",
            },
            "embedding-submit-lines": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "submit_openai",
            },
            "embedding-submit-words": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "submit_words_openai",
            },
            "embedding-split-chunks": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 15,
                "source_dir": "split_into_chunks",
            },
            "embedding-normalize-batches": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 5,
                "source_dir": "normalize_poll_batches_data",
            },
            "embedding-create-chunk-groups": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 5,
                "source_dir": "create_chunk_groups",
            },
            "embedding-prepare-chunk-groups": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 5,
                "source_dir": "prepare_chunk_groups",
            },
            "embedding-prepare-merge-pairs": {
                "handler": "handler.handle",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 5,
                "source_dir": "prepare_merge_pairs",
            },
            "embedding-mark-complete": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 5,
                "source_dir": "mark_batches_complete",
            },
            "embedding-find-receipts": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "find_receipts_realtime",
            },
            "embedding-process-receipt": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE
                * 2,  # Higher memory for embedding processing
                "timeout": MINUTE * 15,
                "source_dir": "process_receipt_realtime",
            },
        }

        for name, lambda_config in zip_configs.items():
            lambda_func = self._create_zip_lambda(name, lambda_config)
            self.zip_lambda_functions[name] = lambda_func

    def _create_zip_lambda(
        self, name: str, config: Dict[str, Any]
    ) -> Function:
        """Create a single zip-based Lambda function."""
        source_path = (
            Path(__file__).parent.parent
            / "simple_lambdas"
            / config["source_dir"]
        )

        # Common environment variables
        env_vars = {
            "DYNAMODB_TABLE_NAME": dynamodb_table.name,
            "OPENAI_API_KEY": openai_api_key,
            "S3_BUCKET": self.batch_bucket.bucket,
        }

        # Add ChromaDB bucket for realtime processing Lambdas, split_into_chunks, normalize_poll_batches_data, create_chunk_groups, prepare_chunk_groups, and prepare_merge_pairs
        if config["source_dir"] in [
            "find_receipts_realtime",
            "process_receipt_realtime",
            "split_into_chunks",
            "normalize_poll_batches_data",
            "create_chunk_groups",
            "prepare_chunk_groups",
            "prepare_merge_pairs",
        ]:
            env_vars["CHROMADB_BUCKET"] = self.chromadb_buckets.bucket_name
            if config["source_dir"] in [
                "find_receipts_realtime",
                "process_receipt_realtime",
            ]:
                env_vars["GOOGLE_PLACES_API_KEY"] = (
                    portfolio_config.get_secret("GOOGLE_PLACES_API_KEY") or ""
                )
                env_vars["CHROMA_HTTP_ENDPOINT"] = (
                    os.environ.get("CHROMA_HTTP_ENDPOINT") or ""
                )

        # Create the Lambda function
        # Determine which layers are needed based on imports
        # - label_layer: Includes receipt_label[lambda] + receipt_dynamo (as dependency)
        # - dynamo_layer: Only receipt_dynamo (for Lambdas that don't need receipt_label)
        layers = []

        # Source directories that use receipt_label (need label_layer, which includes receipt_dynamo)
        uses_receipt_label = config["source_dir"] in [
            "find_receipts_realtime",
            "process_receipt_realtime",
            "submit_openai",
            "submit_words_openai",
            "find_unembedded",
            "find_unembedded_words",
        ]

        # Source directories that only use receipt_dynamo (need dynamo_layer only)
        uses_only_receipt_dynamo = config["source_dir"] in [
            "list_pending",
            "mark_batches_complete",
        ]

        # Add appropriate layers
        if uses_receipt_label and label_layer:
            # label_layer includes receipt_dynamo as a dependency, so this is sufficient
            layers.append(label_layer.arn)
        elif uses_only_receipt_dynamo and dynamo_layer:
            # Only need dynamo_layer for Lambdas that don't use receipt_label
            layers.append(dynamo_layer.arn)
        # Lambdas that don't use either (like split_into_chunks, create_chunk_groups) get no layers

        return Function(
            f"{name}-lambda-{stack}",
            runtime="python3.12",
            handler=config["handler"],
            code=FileArchive(str(source_path)),
            role=self.lambda_role.arn,
            memory_size=config["memory"],
            timeout=config["timeout"],
            environment=FunctionEnvironmentArgs(variables=env_vars),
            layers=layers,
            architectures=["arm64"],
            tags={"environment": stack},
            opts=ResourceOptions(parent=self, ignore_changes=["layers"]),
        )

    def _create_container_lambda_functions(self):
        """Create container-based Lambda functions."""
        self.container_lambda_functions = {}

        # Define container-based Lambda configurations
        # Naming convention: operation-first (embedding-{operation}-{entity})
        # CodeBuild will append -lambda-{stack} suffix automatically
        # Optimized based on actual usage patterns from observability data
        container_configs = {
            "embedding-poll-lines": {
                "memory": GiB(
                    1.5
                ),  # Reduced from 3GB, usage was 668-818MB (22-27%)
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(
                    4
                ),  # Increased back - ChromaDB needs disk space for snapshots/SQLite
                "handler_type": "line_polling",
            },
            "embedding-poll-words": {
                "memory": GiB(
                    1
                ),  # Reduced from 3GB, usage was 322-360MB (11-12%)
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(
                    4
                ),  # Increased back - ChromaDB operations require disk space
                "handler_type": "word_polling",
            },
            "embedding-compact": {
                "memory": GiB(
                    4
                ),  # Increased from 2GB to ensure heartbeat thread gets CPU time
                # Final merge operations are CPU-intensive and need sufficient resources
                # for both main processing and heartbeat thread
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(
                    6
                ),  # Increased - compaction downloads/uploads large snapshots
                "handler_type": "compaction",
            },
        }

        # Create all container Lambdas using CodeBuildDockerImage with lambda_config
        # This ensures CodeBuild automatically updates them when images are built
        for name, config in container_configs.items():
            if config["handler_type"] == "compaction":
                lambda_func = self._create_compaction_lambda_with_codebuild(
                    name, config
                )
            else:
                # Create polling Lambdas using CodeBuildDockerImage (same approach as compaction)
                lambda_func = self._create_polling_lambda_with_codebuild(
                    name, config
                )
            self.container_lambda_functions[name] = lambda_func

    def _create_compaction_lambda_with_codebuild(
        self, name: str, config: Dict[str, Any]
    ):
        """Create compaction Lambda using CodeBuildDockerImage with lambda_config (matches compactor approach)."""
        # Build lambda_config dict matching compactor format
        lambda_config_dict = {
            "role_arn": self.lambda_role.arn,
            "timeout": config["timeout"],
            "memory_size": config["memory"],
            "ephemeral_storage": config.get("ephemeral_storage", 512),
            "description": "Embedding vector compaction handler for ChromaDB operations",
            "tags": {
                "Project": "Embedding",
                "Component": "Compaction",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            "environment": {
                "HANDLER_TYPE": config["handler_type"],
                "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                "COMPACTION_QUEUE_URL": self.chromadb_queues.lines_queue_url,
                "OPENAI_API_KEY": openai_api_key,
                "S3_BUCKET": self.batch_bucket.bucket,
                "CHROMA_PERSIST_DIRECTORY": (
                    "/mnt/chroma"
                    if (
                        self.vpc_subnet_ids
                        and self.lambda_security_group_id
                        and self.efs_access_point_arn
                    )
                    else "/tmp/chroma"
                ),
                "CHROMA_ROOT": (
                    "/mnt/chroma"
                    if (
                        self.vpc_subnet_ids
                        and self.lambda_security_group_id
                        and self.efs_access_point_arn
                    )
                    else "/tmp/chroma"
                ),
                "CHROMADB_STORAGE_MODE": "auto",
                "ENABLE_XRAY": "true",
                "ENABLE_METRICS": "true",
                "LOG_LEVEL": "INFO",
            },
        }

        # Add VPC config if available (matches compactor format)
        if (
            self.vpc_subnet_ids is not None
            and self.lambda_security_group_id is not None
        ):
            lambda_config_dict["vpc_config"] = {
                "subnet_ids": self.vpc_subnet_ids,
                "security_group_ids": [self.lambda_security_group_id],
            }

        # Add EFS config if available (matches compactor format)
        if self.efs_access_point_arn is not None:
            lambda_config_dict["file_system_config"] = {
                "arn": self.efs_access_point_arn,
                "local_mount_path": "/mnt/chroma",
            }

        # Create CodeBuildDockerImage with lambda_config (matches compactor approach)
        # Depend on EFS mount targets if available (matches compactor dependency handling)
        # The compactor passes depends_on to DockerImageComponent, which passes it to CodeBuildDockerImage
        compaction_docker_image = CodeBuildDockerImage(
            f"{name}-docker",
            dockerfile_path="infra/embedding_step_functions/unified_embedding/Dockerfile",
            build_context_path=".",  # Project root for monorepo access
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=f"{name}-lambda-{stack}",
            lambda_config=lambda_config_dict,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self,
                depends_on=(
                    self.efs_mount_targets
                    if self.efs_mount_targets
                    else [self.lambda_role]
                ),
            ),
        )

        # Return the Lambda function created by CodeBuildDockerImage
        return compaction_docker_image.lambda_function

    def _create_polling_lambda_with_codebuild(
        self, name: str, config: Dict[str, Any]
    ):
        """Create polling Lambda (line/word) using CodeBuildDockerImage with lambda_config."""
        # Build lambda_config dict matching compaction Lambda format
        lambda_config_dict = {
            "role_arn": self.lambda_role.arn,
            "timeout": config["timeout"],
            "memory_size": config["memory"],
            "ephemeral_storage": config.get("ephemeral_storage", 512),
            "description": f"Embedding {config['handler_type']} handler for ChromaDB operations",
            "tags": {
                "Project": "Embedding",
                "Component": config["handler_type"].title(),
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            "environment": {
                "HANDLER_TYPE": config["handler_type"],
                "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                "COMPACTION_QUEUE_URL": self.chromadb_queues.lines_queue_url,
                "OPENAI_API_KEY": openai_api_key,
                "S3_BUCKET": self.batch_bucket.bucket,
                "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",  # Polling Lambdas don't use EFS
                "GOOGLE_PLACES_API_KEY": portfolio_config.get_secret(
                    "GOOGLE_PLACES_API_KEY"
                )
                or "",
                "OLLAMA_API_KEY": portfolio_config.get_secret("OLLAMA_API_KEY")
                or "",
                "LANGCHAIN_API_KEY": portfolio_config.get_secret(
                    "LANGCHAIN_API_KEY"
                )
                or "",
                "ENABLE_XRAY": "true",
                "ENABLE_METRICS": "true",
                "LOG_LEVEL": "INFO",
            },
        }

        # Polling Lambdas don't use VPC/EFS (they only write deltas, not read snapshots)
        # No VPC or EFS configuration needed

        # Create CodeBuildDockerImage with lambda_config (same approach as compaction)
        # Use the shared DockerImageComponent's docker image (same Dockerfile, same build)
        polling_docker_image = CodeBuildDockerImage(
            f"{name}-docker",
            dockerfile_path="infra/embedding_step_functions/unified_embedding/Dockerfile",
            build_context_path=".",  # Project root for monorepo access
            source_paths=None,  # Use default rsync with exclusions
            lambda_function_name=f"{name}-lambda-{stack}",
            lambda_config=lambda_config_dict,
            platform="linux/arm64",
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.lambda_role],
            ),
        )

        # Return the Lambda function created by CodeBuildDockerImage
        return polling_docker_image.lambda_function
