"""Lambda functions component for embedding infrastructure."""

import json
from pathlib import Path
from typing import Optional, Dict, Any

from pulumi import (
    ComponentResource,
    Output,
    ResourceOptions,
    FileArchive,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import (
    Function,
    FunctionEnvironmentArgs,
    FunctionEphemeralStorageArgs,
    FunctionTracingConfigArgs,
)
from pulumi_aws.s3 import Bucket

from .base import stack, openai_api_key, label_layer, dynamodb_table


GIGABYTE = 1024
MINUTE = 60


class LambdaFunctionsComponent(ComponentResource):
    """Component for creating Lambda functions and related resources."""

    def __init__(
        self,
        name: str,
        chromadb_buckets,
        chromadb_queues,
        docker_image_component,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize Lambda functions component.

        Args:
            name: Component name
            chromadb_buckets: ChromaDB S3 buckets
            chromadb_queues: ChromaDB SQS queues
            docker_image_component: Docker image component
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
                ],
            }
        )

    def _create_zip_lambda_functions(self):
        """Create zip-based Lambda functions."""
        self.zip_lambda_functions = {}

        # Define zip-based Lambda configurations
        zip_configs = {
            "embedding-list-pending": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 0.5,
                "timeout": MINUTE * 15,
                "source_dir": "list_pending",
            },
            "embedding-line-find": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "find_unembedded",
            },
            "embedding-word-find": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "find_unembedded_words",
            },
            "embedding-line-submit": {
                "handler": "handler.lambda_handler",
                "memory": GIGABYTE * 1,
                "timeout": MINUTE * 15,
                "source_dir": "submit_openai",
            },
            "embedding-word-submit": {
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

        # Create the Lambda function
        layers = []
        if label_layer:
            layers = [label_layer.arn]

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
        container_configs = {
            "embedding-line-poll": {
                "memory": GIGABYTE * 3,
                "timeout": MINUTE * 15,  # Fix: Use 15 minutes to match config.py
                "ephemeral_storage": GIGABYTE * 5,
                "handler_type": "line_polling",
            },
            "embedding-word-poll": {
                "memory": GIGABYTE * 3,
                "timeout": MINUTE * 15,  # Fix: Use 15 minutes to match config.py
                "ephemeral_storage": GIGABYTE * 5,
                "handler_type": "word_polling",
            },
            "embedding-vector-compact": {
                "memory": GIGABYTE * 8,
                "timeout": MINUTE * 15,
                "ephemeral_storage": GIGABYTE * 10,
                "handler_type": "compaction",
            },
        }

        for name, lambda_config in container_configs.items():
            lambda_func = self._create_container_lambda(name, lambda_config)
            self.container_lambda_functions[name] = lambda_func

    def _create_container_lambda(
        self, name: str, config: Dict[str, Any]
    ) -> Function:
        """Create a single container-based Lambda function."""
        env_vars = {
            "HANDLER_TYPE": config["handler_type"],
            "DYNAMODB_TABLE_NAME": dynamodb_table.name,
            "CHROMADB_BUCKET": self.chromadb_buckets.bucket_name,
            "COMPACTION_QUEUE_URL": self.chromadb_queues.lines_queue_url,
            "OPENAI_API_KEY": openai_api_key,
            "S3_BUCKET": self.batch_bucket.bucket,
            "CHROMA_PERSIST_DIRECTORY": "/tmp/chroma",
            # Observability configuration
            "ENABLE_XRAY": "true",
            "ENABLE_METRICS": "true",
            "LOG_LEVEL": "INFO"
        }

        # Add handler-specific environment variables
        if config["handler_type"] == "compaction":
            env_vars.update(
                {
                    "CHUNK_SIZE": "10",
                    "HEARTBEAT_INTERVAL_SECONDS": "60",
                    "LOCK_DURATION_MINUTES": "5",
                    "DELETE_PROCESSED_DELTAS": "false",
                    "DELETE_INTERMEDIATE_CHUNKS": "true",
                }
            )

        return Function(
            f"{name}-lambda-{stack}",
            package_type="Image",
            image_uri=Output.all(
                self.docker_image.ecr_repo.repository_url,
                self.docker_image.docker_image.digest,
            ).apply(lambda args: f"{args[0].split(':')[0]}@{args[1]}"),
            role=self.lambda_role.arn,
            architectures=["arm64"],
            memory_size=config["memory"],
            timeout=config["timeout"],
            environment=FunctionEnvironmentArgs(variables=env_vars),
            ephemeral_storage=(
                FunctionEphemeralStorageArgs(
                    size=config.get("ephemeral_storage", 512)
                )
                if config.get("ephemeral_storage", 512) > 512
                else None
            ),
            # Enable X-Ray tracing for observability
            tracing_config=FunctionTracingConfigArgs(mode="Active"),
            tags={"environment": stack},
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.docker_image.docker_image],
            ),
        )
