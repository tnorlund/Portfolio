"""Lambda functions component for embedding infrastructure."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from pulumi import (
    ComponentResource,
    FileArchive,
    Output,
    ResourceOptions,
    create_urn,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
    FunctionVpcConfigArgs,
)
from pulumi_aws.s3 import Bucket

from .base import config as portfolio_config
from .base import dynamo_layer, dynamodb_table, openai_api_key, stack

GIGABYTE = 1024
MINUTE = 60


class UnknownHandlerTypeError(ValueError):
    """Raised when an unknown handler type is encountered."""

    def __init__(self, handler_type: str) -> None:
        super().__init__(f"Unknown handler type: {handler_type}")


# Helper to express memory/ephemeral storage in MiB (AWS expects MiB integers).
# Example: GiB(0.5) == 512, GiB(2) == 2048
def GiB(n: float | int) -> int:
    return int(n * 1024)


CONTAINER_FUNCTION_NAMES = (
    "embedding-poll-lines",
    "embedding-poll-words",
    "embedding-submit-words",
    "embedding-submit-lines",
    "embedding-compact",
)


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
                        "Action": ["lambda:InvokeFunction"],
                        "Resource": f"arn:aws:lambda:*:*:function:fix-place-{stack}-fix-place",
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

        # Add ChromaDB bucket for split_into_chunks, normalize_poll_batches_data, create_chunk_groups, prepare_chunk_groups, and prepare_merge_pairs
        if config["source_dir"] in [
            "split_into_chunks",
            "normalize_poll_batches_data",
            "create_chunk_groups",
            "prepare_chunk_groups",
            "prepare_merge_pairs",
        ]:
            env_vars["CHROMADB_BUCKET"] = self.chromadb_buckets.bucket_name

        # SIMPLIFIED ARCHITECTURE (v2): Configuration for big chunks
        # TARGET_PARALLEL_LAMBDAS: Number of parallel Lambdas for chunk processing
        # Each Lambda processes many deltas, creating one intermediate
        # No reduce loop needed since we only have ~8 intermediates to merge
        if config["source_dir"] == "normalize_poll_batches_data":
            env_vars["TARGET_PARALLEL_LAMBDAS"] = "8"  # Creates ~8 big chunks
            env_vars["MIN_DELTAS_PER_CHUNK"] = "5"  # Minimum deltas per chunk
            # Legacy config (kept for backward compatibility, not used in simplified mode)
            env_vars["CHUNKS_PER_LAMBDA"] = "4"

        # Add optimization configuration for N-way merge (legacy, not used in simplified mode)
        # MERGE_GROUP_SIZE: Group size for parallel reduce (default: 10 instead of 2)
        if config["source_dir"] == "prepare_merge_pairs":
            env_vars["MERGE_GROUP_SIZE"] = "10"

        # Create the Lambda function
        # Determine which layers are needed based on imports
        # - dynamo_layer: Only receipt_dynamo
        layers = []

        # Source directories that only use receipt_dynamo (need dynamo_layer only)
        uses_only_receipt_dynamo = config["source_dir"] in [
            "list_pending",
            "mark_batches_complete",
        ]

        # Add appropriate layers
        if uses_only_receipt_dynamo and dynamo_layer:
            # Only need dynamo_layer for Lambdas that don't use extra deps
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
        """Create five Lambdas backed by the one shared embedding image."""
        self.container_lambda_functions = {}

        container_configs = {
            "embedding-poll-lines": {
                "memory": GiB(1.5),
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(4),
                "handler_type": "line_polling",
            },
            "embedding-poll-words": {
                "memory": GiB(1),
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(4),
                "handler_type": "word_polling",
            },
            "embedding-submit-words": {
                "memory": GiB(1),
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(2),
                "handler_type": "submit_words_openai",
            },
            "embedding-submit-lines": {
                "memory": GiB(1),
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(2),
                "handler_type": "submit_openai",
            },
            "embedding-compact": {
                "memory": GiB(8),
                "timeout": MINUTE * 15,
                "ephemeral_storage": GiB(10),
                "handler_type": "compaction",
            },
        }

        if tuple(container_configs) != CONTAINER_FUNCTION_NAMES:
            raise RuntimeError("Container Lambda names are out of sync")

        for name, config in container_configs.items():
            lambda_config = self._container_lambda_config(config)
            depends_on = [
                self.lambda_role,
                self.docker_image.docker_image.ready,
            ]
            lambda_args: Dict[str, Any] = {
                "name": f"{name}-lambda-{stack}",
                "package_type": "Image",
                "image_uri": self.docker_image.image_uri,
                "role": self.lambda_role.arn,
                "architectures": ["arm64"],
                "timeout": lambda_config["timeout"],
                "memory_size": lambda_config["memory_size"],
                "ephemeral_storage": FunctionEphemeralStorageArgs(
                    size=lambda_config["ephemeral_storage"]
                ),
                "description": lambda_config["description"],
                "environment": FunctionEnvironmentArgs(
                    variables=lambda_config["environment"]
                ),
                "tags": lambda_config["tags"],
            }
            if lambda_config.get("vpc_config"):
                lambda_args["vpc_config"] = FunctionVpcConfigArgs(
                    subnet_ids=lambda_config["vpc_config"]["subnet_ids"],
                    security_group_ids=lambda_config["vpc_config"][
                        "security_group_ids"
                    ],
                )
            self.container_lambda_functions[name] = Function(
                f"{name}-shared-image-function",
                **lambda_args,
                opts=ResourceOptions(
                    parent=self,
                    depends_on=depends_on,
                    aliases=[self._legacy_container_lambda_urn(name)],
                    # CodeBuild advances every function to the shared digest.
                    ignore_changes=["image_uri"],
                ),
            )

    def _legacy_container_lambda_urn(self, name: str) -> Output[str]:
        """URN of the Lambda formerly owned by its per-function builder."""
        old_parent = create_urn(
            f"{name}-docker",
            f"codebuild-docker:{name}-docker",
            parent=self,
        )
        return create_urn(
            f"{name}-docker-function",
            "aws:lambda/function:Function",
            parent=old_parent,
        )

    def _container_lambda_config(
        self, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Return the per-function settings layered on the shared image."""
        handler_type = config["handler_type"]
        common = {
            "timeout": config["timeout"],
            "memory_size": config["memory"],
            "ephemeral_storage": config.get("ephemeral_storage", 512),
        }

        if handler_type == "compaction":
            result = {
                **common,
                "description": (
                    "Embedding vector compaction handler for ChromaDB "
                    "operations"
                ),
                "tags": {
                    "Project": "Embedding",
                    "Component": "Compaction",
                    "Environment": stack,
                    "ManagedBy": "Pulumi",
                },
                "environment": {
                    "HANDLER_TYPE": handler_type,
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                    "COMPACTION_QUEUE_URL": (
                        self.chromadb_queues.lines_queue_url
                    ),
                    "OPENAI_API_KEY": openai_api_key,
                    "S3_BUCKET": self.batch_bucket.bucket,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                    "ENABLE_XRAY": "true",
                    "ENABLE_METRICS": "true",
                    "LOG_LEVEL": "INFO",
                    "CHROMA_CLOUD_ENABLED": (
                        portfolio_config.get("CHROMA_CLOUD_ENABLED") or "false"
                    ),
                    "CHROMA_CLOUD_API_KEY": (
                        portfolio_config.get_secret("CHROMA_CLOUD_API_KEY")
                        or ""
                    ),
                    "CHROMA_CLOUD_TENANT": (
                        portfolio_config.get("CHROMA_CLOUD_TENANT") or ""
                    ),
                    "CHROMA_CLOUD_DATABASE": (
                        portfolio_config.get("CHROMA_CLOUD_DATABASE") or ""
                    ),
                },
            }
            if self.vpc_subnet_ids and self.lambda_security_group_id:
                result["vpc_config"] = {
                    "subnet_ids": self.vpc_subnet_ids,
                    "security_group_ids": [self.lambda_security_group_id],
                }
            return result

        if handler_type in {"line_polling", "word_polling"}:
            return {
                **common,
                "description": (
                    f"Embedding {handler_type} handler for ChromaDB operations"
                ),
                "tags": {
                    "Project": "Embedding",
                    "Component": handler_type.title(),
                    "Environment": stack,
                    "ManagedBy": "Pulumi",
                },
                "environment": {
                    "HANDLER_TYPE": handler_type,
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                    "COMPACTION_QUEUE_URL": (
                        self.chromadb_queues.lines_queue_url
                    ),
                    "OPENAI_API_KEY": openai_api_key,
                    "S3_BUCKET": self.batch_bucket.bucket,
                    "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
                    "FIX_PLACE_LAMBDA_NAME": (f"fix-place-{stack}-fix-place"),
                    "ENABLE_XRAY": "true",
                    "ENABLE_METRICS": "true",
                    "LOG_LEVEL": "INFO",
                },
            }

        if handler_type in {"submit_words_openai", "submit_openai"}:
            component = (
                "SubmitWords"
                if handler_type == "submit_words_openai"
                else "SubmitLines"
            )
            return {
                **common,
                "description": (
                    f"Embedding {handler_type} handler using receipt_chroma"
                ),
                "tags": {
                    "Project": "Embedding",
                    "Component": component,
                    "Environment": stack,
                    "ManagedBy": "Pulumi",
                },
                "environment": {
                    "HANDLER_TYPE": handler_type,
                    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
                    "OPENAI_API_KEY": openai_api_key,
                    "S3_BUCKET": self.batch_bucket.bucket,
                    "ENABLE_XRAY": "true",
                    "ENABLE_METRICS": "true",
                    "LOG_LEVEL": "INFO",
                },
            }

        raise UnknownHandlerTypeError(handler_type)
